#!/usr/bin/env python3
"""Sync hosted-agent identities into the AI gateway and API Center (Spec 001).

Runs from the root `postdeploy` hook after `grant_agent_rbac.py`. No-op unless
ENABLE_AI_GATEWAY / ENABLE_API_CENTER are true.

1. AI gateway allowlist: reads each deployed hosted agent's
   `instance_identity.client_id` and writes the comma-separated list to the APIM
   named value `ai-gateway-allowed-client-ids`. The gateway policy rejects (403)
   any token whose appid/azp is not on that list, so this is also the per-agent
   kill switch. The list is persisted as AI_GATEWAY_ALLOWED_CLIENT_IDS so the
   next `azd provision` re-applies it instead of resetting it.
2. Genie MCP route: creates the APIM API `databricks-genie-mcp` that forwards
   the supply agent's Genie MCP calls to Databricks, and stores its URL as
   AI_GATEWAY_GENIE_MCP_URL. deploy_supply_agent.py calls this before deploying
   the agent, so the agent is deployed with the gateway URL.
3. API Center: registers the Databricks Genie MCP server (its space ID only
   exists after postprovision, so Bicep cannot register it).

Publishing an agent creates a new identity: re-run after every publish.
Safe to re-run manually:  python scripts/sync_ai_gateway.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from grant_agent_rbac import (
    HOSTED_AGENTS,
    _agent_latest_version,
    _azd_env,
    _get_token,
    _run,
)

SUPPLY_AGENT = "supply-pricing-agent"
ALLOWLIST_NAMED_VALUE = "ai-gateway-allowed-client-ids"
API_CENTER_API_VERSION = "2024-06-01-preview"
APIM_API_VERSION = "2024-05-01"
GENIE_API_ID = "databricks-genie-mcp"
GENIE_API_PATH = "mcp/databricks-genie"
GENIE_POLICY = Path(__file__).resolve().parents[1] / "infra" / "policies" / "databricks-genie-mcp-policy.xml"


def _enabled(name: str) -> bool:
    return _azd_env(name).lower() == "true"


def _agents() -> list[str]:
    agents = list(HOSTED_AGENTS)
    if _azd_env("SUPPLY_AGENT_DEPLOYED").lower() == "true":
        agents.append(SUPPLY_AGENT)
    return agents


def collect_client_ids(project_endpoint: str, agents: list[str]) -> tuple[dict[str, str], list[str]]:
    """Return ({agent: client_id}, [agents without an identity])."""
    token = _get_token()
    found: dict[str, str] = {}
    missing: list[str] = []
    for agent in agents:
        version = _agent_latest_version(project_endpoint, agent, token) or {}
        client_id = (version.get("instance_identity") or {}).get("client_id")
        if client_id:
            found[agent] = client_id
        else:
            missing.append(agent)
    return found, missing


def sync_allowlist() -> bool:
    apim = _azd_env("AI_GATEWAY_NAME")
    rg = _azd_env("AZURE_RESOURCE_GROUP")
    project_endpoint = _azd_env("AZURE_AI_PROJECT_ENDPOINT") or _azd_env("AI_FOUNDRY_PROJECT_ENDPOINT")
    if not (apim and rg and project_endpoint):
        print("  ERROR: AI_GATEWAY_NAME, AZURE_RESOURCE_GROUP or the project endpoint is missing", file=sys.stderr)
        return False

    found, missing = collect_client_ids(project_endpoint, _agents())
    for agent in missing:
        print(f"  WARNING: {agent}: no deployed instance identity; it cannot call the gateway", file=sys.stderr)
    if not found:
        print("  ERROR: no hosted-agent identities found; the gateway allowlist was not changed", file=sys.stderr)
        return False

    ids = ",".join(sorted(set(found.values())))
    _run([
        "az", "apim", "nv", "update",
        "--resource-group", rg,
        "--service-name", apim,
        "--named-value-id", ALLOWLIST_NAMED_VALUE,
        "--value", ids,
        "--output", "none",
    ])
    _run(["azd", "env", "set", "AI_GATEWAY_ALLOWED_CLIENT_IDS", ids])
    # name=client_id pairs: maps the "Agent Client ID" metric dimension back to agent names.
    _run(["azd", "env", "set", "AI_GATEWAY_AGENT_CLIENT_IDS",
          ";".join(f"{agent}={cid}" for agent, cid in sorted(found.items()))])
    for agent, cid in sorted(found.items()):
        print(f"  - {agent:30s}  {cid}  allowed")
    return not missing


def _put(url: str, body: dict) -> None:
    """az rest PUT with the body in a file (policy XML breaks Windows command-line quoting)."""
    handle, path = tempfile.mkstemp(suffix=".json")
    response = path + ".response"
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(body, stream)
        # --output-file: APIM policy responses start with a UTF-8 BOM, which az cannot
        # print on a Windows (cp1252) console and would crash on.
        _run(["az", "rest", "--method", "put", "--url", url, "--body", f"@{path}", "--output-file", response])
    finally:
        for leftover in (path, response):
            if os.path.exists(leftover):
                os.remove(leftover)


def genie_route_url(gateway_base_url: str, space_id: str) -> str:
    """https://<apim>.azure-api.net/mcp/databricks-genie/<space-id>"""
    parsed = urlsplit(gateway_base_url)
    return f"https://{parsed.hostname}/{GENIE_API_PATH}/{space_id}"


def ensure_genie_mcp_route() -> str:
    """Create or update the APIM route for Genie MCP calls; return its URL ('' when not applicable).

    Always writes AI_GATEWAY_GENIE_MCP_URL so turning the gateway off also clears it.
    """
    url = ""
    gateway = _azd_env("AI_GATEWAY_OPENAI_BASE_URL")
    host = _azd_env("DATABRICKS_HOST").rstrip("/")
    space_id = _azd_env("DATABRICKS_GENIE_SPACE_ID")
    sub = _azd_env("AZURE_SUBSCRIPTION_ID")
    rg = _azd_env("AZURE_RESOURCE_GROUP")
    apim = _azd_env("AI_GATEWAY_NAME")
    if _enabled("ENABLE_AI_GATEWAY") and gateway and host and space_id and sub and rg and apim:
        if not host.startswith("https://"):
            host = f"https://{host}"
        base = (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
                f"/providers/Microsoft.ApiManagement/service/{apim}/apis/{GENIE_API_ID}")
        version = f"?api-version={APIM_API_VERSION}"
        _put(base + version, {"properties": {
            "displayName": "Databricks Genie MCP (construction supply)",
            "description": "AI gateway route for the supply agent's Databricks Genie managed MCP server",
            "path": GENIE_API_PATH,
            "serviceUrl": f"{host}/api/2.0/mcp/genie",
            "protocols": ["https"],
            "subscriptionRequired": False,
        }})
        # MCP streamable HTTP uses POST (messages), GET (server stream) and DELETE (end session).
        for method in ("POST", "GET", "DELETE"):
            _put(f"{base}/operations/{method.lower()}-space{version}", {"properties": {
                "displayName": f"{method} Genie space",
                "method": method,
                "urlTemplate": "/{spaceId}",
                "templateParameters": [{"name": "spaceId", "type": "string", "required": True}],
            }})
        policy = GENIE_POLICY.read_text(encoding="utf-8").replace("__GENIE_SPACE_ID__", space_id)
        _put(f"{base}/policies/policy{version}", {"properties": {"format": "rawxml", "value": policy}})
        url = genie_route_url(gateway, space_id)
        print(f"  - {GENIE_API_ID:30s}  {url}")
    _run(["azd", "env", "set", "AI_GATEWAY_GENIE_MCP_URL", url])
    return url


def register_genie_mcp() -> bool:
    """Register the Databricks Genie MCP server in API Center (idempotent PUTs)."""
    host = _azd_env("DATABRICKS_HOST").rstrip("/")
    space_id = _azd_env("DATABRICKS_GENIE_SPACE_ID")
    if _azd_env("SUPPLY_AGENT_DEPLOYED").lower() != "true" or not (host and space_id):
        print("  Databricks Genie MCP not deployed; skipping its API Center registration.")
        return True
    sub = _azd_env("AZURE_SUBSCRIPTION_ID")
    rg = _azd_env("AZURE_RESOURCE_GROUP")
    apic = _azd_env("API_CENTER_NAME")
    if not (sub and rg and apic):
        print("  ERROR: AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP or API_CENTER_NAME is missing", file=sys.stderr)
        return False
    if not host.startswith("https://"):
        host = f"https://{host}"
    base = (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
            f"/providers/Microsoft.ApiCenter/services/{apic}/workspaces/default/apis/databricks-genie")
    calls = [
        (base, {"properties": {
            "title": "Databricks Genie MCP (construction supply)",
            "description": "Read-only curated Genie space used by supply-pricing-agent",
            "kind": "mcp",
        }}),
        (f"{base}/versions/v1-0", {"properties": {"title": "v1", "lifecycleStage": "development"}}),
        (f"{base}/deployments/external", {"properties": {
            "title": "Databricks Genie MCP (workspace)",
            "environmentId": "/workspaces/default/environments/external-mcp",
            "server": {"runtimeUri": [f"{host}/api/2.0/mcp/genie/{space_id}"]},
            "state": "active",
        }}),
    ]
    for url, body in calls:
        _run(["az", "rest", "--method", "put",
              "--url", f"{url}?api-version={API_CENTER_API_VERSION}",
              "--body", json.dumps(body), "--output", "none"])
    print("  - databricks-genie  registered in API Center")
    return True


def main() -> int:
    ok = True
    if _enabled("ENABLE_AI_GATEWAY"):
        print("  AI gateway: syncing hosted-agent allowlist...")
        ok = sync_allowlist() and ok
        if ok and _azd_env("SUPPLY_AGENT_DEPLOYED").lower() == "true":
            print("  AI gateway: updating the Genie MCP route...")
            ensure_genie_mcp_route()
    else:
        print("  AI gateway not enabled (ENABLE_AI_GATEWAY); skipping allowlist sync.")
    if _enabled("ENABLE_API_CENTER"):
        print("  API Center: registering post-deploy MCP servers...")
        ok = register_genie_mcp() and ok
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or ""
        print(f"ERROR: AI gateway sync could not complete: {exc} {detail}".strip(), file=sys.stderr)
        sys.exit(1)
