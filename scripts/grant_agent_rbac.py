#!/usr/bin/env python3
"""Grant Foundry User to each Hosted Agent's instance identity.

The `azd ai agent` extension provisions per-agent `instance_identity` and
`blueprint` Application identities at `create_version` time (preview April
2026+) but does NOT grant them the data-plane RBAC needed to call the
Foundry Responses API (Microsoft.CognitiveServices/accounts/AIServices/agents/*).

Without this grant, every POST /responses returns:

    HTTP 500
    {"error":{"code":"PermissionDenied",
              "message":"Principal does not have access to API/Operation."}}

This script:
  1. Reads the project endpoint + Foundry account scope from `azd env`.
  2. Lists the latest version of each hosted agent declared in azure.yaml.
  3. Grants `Foundry User` (formerly `Azure AI User`) on the Foundry account
     scope to each `instance_identity.principal_id` (idempotent — `az role
     assignment create` returns success if the assignment already exists).

Run automatically from the `postdeploy` hook in azure.yaml. Safe to re-run
manually:  python scripts/grant_agent_rbac.py
"""
from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Iterable
from urllib.parse import urlencode

# Agent service keys and name fields in root azure.yaml must match.
HOSTED_AGENTS: tuple[str, ...] = (
    "clinical-reviewer-agent",
    "coverage-assessment-agent",
    "compliance-agent",
    "synthesis-agent",
)

# Data-plane role that includes Microsoft.CognitiveServices/accounts/AIServices/agents/*
# read + invoke. Project Manager is NOT needed for runtime — only for create_version().
# Referenced by role definition ID because the display name was renamed from
# "Azure AI User" to "Foundry User" and `az --role <name>` fails on updated tenants.
ROLE_ID = "53ca6127-db72-4b80-b1b0-d745d6d5456d"

# Current agent management API exposing instance_identity.principal_id.
AGENT_API_VERSION = "v1"


def _command(cmd: list[str]) -> list[str]:
    executable = shutil.which(cmd[0])
    if not executable and sys.platform == "win32":
        import winreg

        paths = [os.environ.get("PATH", "")]
        for hive, key_path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, "Path")
                    paths.append(os.path.expandvars(value))
            except OSError:
                continue
        os.environ["PATH"] = os.pathsep.join(paths)
        executable = shutil.which(cmd[0])
    if not executable:
        raise RuntimeError(f"{cmd[0]} is unavailable on PATH")
    if sys.platform == "win32" and cmd[0] == "az":
        cli_python = Path(executable).parent.parent / "python.exe"
        if cli_python.is_file():
            return [str(cli_python), "-IBm", "azure.cli", *cmd[1:]]
    return [executable, *cmd[1:]]


def _run(cmd: list[str], *, capture: bool = True) -> str:
    """Run a shell command, return stdout (or raise)."""
    result = subprocess.run(
        _command(cmd),
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def _azd_env(name: str) -> str:
    """Read a value from `azd env get-value`. Returns '' if missing."""
    try:
        return _run(["azd", "env", "get-value", name])
    except subprocess.CalledProcessError:
        return ""


def _get_token() -> str:
    """Acquire a bearer token for the ai.azure.com audience via Azure CLI."""
    return _run([
        "az", "account", "get-access-token",
        "--resource", "https://ai.azure.com",
        "--query", "accessToken",
        "-o", "tsv",
    ])


def _agent_latest_version(project_endpoint: str, agent_name: str, token: str) -> dict | None:
    """Return the latest version object for the named agent, or None if missing."""
    import urllib.request
    import urllib.error

    url = f"{project_endpoint.rstrip('/')}/agents/{agent_name}?api-version={AGENT_API_VERSION}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"  WARN: cannot list {agent_name} versions ({exc.code} {exc.reason})", file=sys.stderr)
        return None
    return (payload.get("versions") or {}).get("latest") or None


def _grant_role(principal_id: str, scope: str, role: str) -> bool:
    """Grant `role` to `principal_id` at `scope`. Returns True if newly created."""
    # Check existing first to provide cleaner output.
    existing = subprocess.run(
        _command(["az", "role", "assignment", "list",
         "--assignee-object-id", principal_id,
         "--role", role,
         "--scope", scope,
         "--query", "[].id", "-o", "tsv"]),
        check=False, text=True, capture_output=True,
    )
    if existing.returncode == 0 and existing.stdout.strip():
        return False
    create = subprocess.run(
        _command(["az", "role", "assignment", "create",
         "--assignee-object-id", principal_id,
         "--assignee-principal-type", "ServicePrincipal",
         "--role", role,
         "--scope", scope,
         "--only-show-errors"]),
        check=False, text=True, capture_output=True,
    )
    if create.returncode != 0:
        # Race-condition friendly: another concurrent run may have created it.
        if "RoleAssignmentExists" in (create.stderr or ""):
            return False
        print(f"  ERROR: role assignment failed: {create.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return True


def _databricks_request(host: str, method: str, endpoint: str, body: dict | None = None) -> dict:
    arguments = ["databricks", "api", method, endpoint, "--output", "json"]
    if body is not None:
        arguments.extend(["--json", json.dumps(body)])
    command = _command(arguments)
    environment = os.environ.copy()
    environment["DATABRICKS_HOST"] = host
    environment.setdefault("DATABRICKS_AUTH_TYPE", "azure-cli")
    result = subprocess.run(command, env=environment, check=False,
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"Databricks {method} {endpoint} failed: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    if payload.get("error"):
        raise RuntimeError(f"Databricks MCP check failed: {payload['error']}. Enable Managed MCP Servers in workspace Previews.")
    return payload


def _grant_databricks_access(client_id: str) -> None:
    _run(["azd", "env", "set", "SUPPLY_AGENT_DATABRICKS_ACCESS_READY", "false"])
    host = _azd_env("DATABRICKS_HOST")
    space_id = _azd_env("DATABRICKS_GENIE_SPACE_ID")
    if not host or not space_id:
        raise RuntimeError("Missing Databricks workspace host or Genie space ID")

    def request(method: str, endpoint: str, body: dict | None = None) -> dict:
        return _databricks_request(host, method, endpoint, body)

    query = urlencode({'filter': f'applicationId eq "{client_id}"'})
    account_endpoint = "/api/2.0/account/scim/v2/ServicePrincipals"
    principals = request("get", f"{account_endpoint}?{query}").get("Resources", [])
    if len(principals) > 1:
        raise RuntimeError("Multiple Databricks principals match the supply-agent identity")
    if principals:
        principal = principals[0]
        if principal.get("active") is False:
            raise RuntimeError("The Databricks supply-agent principal is deactivated; ask an administrator to review")
    else:
        principal = request("post", account_endpoint, {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"],
            "applicationId": client_id, "displayName": "Foundry supply-pricing-agent", "active": True,
        })
    principal_id = principal.get("id")
    if not principal_id or principal.get("applicationId") != client_id:
        raise RuntimeError("Databricks identity registration returned an unexpected principal")

    assignments = request("get", "/api/2.0/preview/permissionassignments").get("permission_assignments", [])
    assigned = any(str(item.get("principal", {}).get("principal_id")) == str(principal_id)
                   and set(item.get("permissions", [])).intersection({"USER", "ADMIN"}) for item in assignments)
    if not assigned:
        request("put", f"/api/2.0/preview/permissionassignments/principals/{principal_id}", {"permissions": ["USER"]})

    workspace_principal_endpoint = f"/api/2.0/preview/scim/v2/ServicePrincipals/{principal_id}"
    workspace_principal = request("get", workspace_principal_endpoint)
    entitlements = {item["value"] for item in workspace_principal.get("entitlements", [])}
    if "databricks-sql-access" not in entitlements:
        request("patch", workspace_principal_endpoint, {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "add", "path": "entitlements",
                            "value": [{"value": "databricks-sql-access"}]}],
        })

    space = request("get", f"/api/2.0/genie/spaces/{space_id}?include_serialized_space=true")
    definition = json.loads(space["serialized_space"])
    expected_tables = {f"construction_supply.construction_supply_demo.{name}"
                       for name in ("branches", "inventory", "order_lines", "prices", "products")}
    actual_tables = {table["identifier"] for table in definition["data_sources"]["tables"]}
    if actual_tables != expected_tables:
        raise RuntimeError("Refusing grants: Genie sources differ from the five expected demo tables")
    warehouse_id = space.get("warehouse_id")
    if not warehouse_id:
        raise RuntimeError("Genie space has no SQL warehouse")
    for object_type, object_id, level in (("genie", space_id, "CAN_RUN"), ("warehouses", warehouse_id, "CAN_USE")):
        request("patch", f"/api/2.0/permissions/{object_type}/{object_id}", {
            "access_control_list": [{"service_principal_name": client_id, "permission_level": level}],
        })
    grants = [("catalog", "construction_supply", "USE_CATALOG"),
              ("schema", "construction_supply.construction_supply_demo", "USE_SCHEMA")]
    grants.extend(("table", name, "SELECT") for name in sorted(expected_tables))
    for resource_type, name, privilege in grants:
        request("patch", f"/api/2.1/unity-catalog/permissions/{resource_type}/{name}", {
            "changes": [{"principal": client_id, "add": [privilege]}],
        })
    result = request("post", f"/api/2.0/mcp/genie/{space_id}", {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "azd-supply-access-check", "version": "1.0"}},
    })
    if not isinstance(result.get("result"), dict):
        raise RuntimeError("Genie MCP initialization returned no result")
    _run(["azd", "env", "set", "SUPPLY_AGENT_DATABRICKS_ACCESS_READY", "true"])
    print("  Supply-agent Databricks grants and MCP preview verified; test a live agent query next.")


def main(agents: Iterable[str] = HOSTED_AGENTS, *, databricks: bool = False) -> int:
    project_endpoint = _azd_env("AZURE_AI_PROJECT_ENDPOINT") or _azd_env("AI_FOUNDRY_PROJECT_ENDPOINT")
    sub_id = _azd_env("AZURE_SUBSCRIPTION_ID")
    rg = _azd_env("AZURE_RESOURCE_GROUP")
    foundry_account = _azd_env("AI_FOUNDRY_ACCOUNT_NAME")

    missing = [k for k, v in {
        "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
        "AZURE_SUBSCRIPTION_ID": sub_id,
        "AZURE_RESOURCE_GROUP": rg,
        "AI_FOUNDRY_ACCOUNT_NAME": foundry_account,
    }.items() if not v]
    if missing:
        print(f"ERROR: missing azd env values: {', '.join(missing)}", file=sys.stderr)
        return 1

    account_scope = (
        f"/subscriptions/{sub_id}/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{foundry_account}"
    )

    token = _get_token()
    granted = 0
    failed = 0
    for agent in agents:
        version = _agent_latest_version(project_endpoint, agent, token)
        if version is None:
            print(f"  ERROR: {agent}: no deployed version found", file=sys.stderr)
            failed += 1
            continue
        instance = version.get("instance_identity") or {}
        principal_id = instance.get("principal_id")
        if not principal_id:
            print(f"  ERROR: {agent}: deployed version has no instance identity", file=sys.stderr)
            failed += 1
            continue
        is_new = _grant_role(principal_id, account_scope, ROLE_ID)
        status = "granted" if is_new else "ok"
        granted += int(is_new)
        print(f"  - {agent:30s}  v{version.get('version'):<3}  {status}")
        if databricks and agent == "supply-pricing-agent":
            try:
                client_id = instance.get("client_id")
                if not client_id:
                    raise RuntimeError("Deployed supply agent has no client ID")
                _grant_databricks_access(client_id)
            except (OSError, RuntimeError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
                print(f"  WARNING: supply-agent Databricks access is incomplete: {exc}", file=sys.stderr)
                failed += 1

    if granted:
        print(f"\n  {granted} new role assignment(s) created. RBAC may take ~30-60s to propagate.")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent", action="append", dest="agents",
        help="Grant a specific deployed hosted agent; repeat to grant multiple agents. Defaults to the four root agents.",
    )
    parser.add_argument("--databricks", action="store_true", help="Configure scoped Databricks access for supply-pricing-agent")
    args = parser.parse_args()
    try:
        sys.exit(main(args.agents or HOSTED_AGENTS, databricks=args.databricks))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: agent RBAC could not complete: {exc}", file=sys.stderr)
        sys.exit(1)
