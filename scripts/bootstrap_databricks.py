"""Provision the optional Databricks demo bundles from the azd postprovision hook."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Callable
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
GENIE_TITLE = "Construction Supply Vendor - Synthetic Pricing"


def _azd_value(name: str) -> str:
    result = subprocess.run(
        ["azd", "env", "get-value", name], text=True, capture_output=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _set_azd_value(name: str, value: str) -> None:
    result = subprocess.run(
        ["azd", "env", "set", name, value], text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError(f"azd env set failed for {name}: {result.stderr.strip()}")


def _windows_path_entries() -> list[str]:
    if sys.platform != "win32":
        return []

    import winreg

    locations = (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    )
    entries = []
    for hive, key_path in locations:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
            entries.append(os.path.expandvars(value))
        except OSError:
            continue
    return entries


def _refresh_windows_path() -> None:
    entries = _windows_path_entries()
    if entries:
        entries.append(os.environ.get("PATH", ""))
        os.environ["PATH"] = os.pathsep.join(entry for entry in entries if entry)


def _run_cli(arguments: list[str], cwd: Path, environment: dict[str, str]) -> str:
    result = subprocess.run(
        ["databricks", *arguments], cwd=cwd, env=environment,
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Databricks CLI {' '.join(arguments[:3])} failed: {message}")
    return result.stdout


def _find_warehouse(payload: str, expected_name: str) -> str:
    data = json.loads(payload)
    warehouses = data.get("warehouses", []) if isinstance(data, dict) else data
    matches = [item["id"] for item in warehouses if item.get("name") == expected_name and item.get("id")]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one Databricks warehouse named {expected_name}; found {len(matches)}")
    return matches[0]


def _find_genie_space(payload: str) -> str:
    data = json.loads(payload)
    space = data.get("resources", {}).get("genie_spaces", {}).get("supply_genie", {})
    space_id = space.get("id")
    if not isinstance(space_id, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", space_id):
        raise RuntimeError("Genie bundle summary has no valid deployed ID for resources.genie_spaces.supply_genie")
    return space_id


def _ensure_storage_credential(
    connector_id: str,
    run_cli: Callable[[list[str], Path, dict[str, str]], str],
    cwd: Path,
    environment: dict[str, str],
) -> str:
    suffix = hashlib.sha256(connector_id.lower().encode()).hexdigest()[:12]
    name = f"construction_supply_storage_{suffix}"
    endpoint = "/api/2.1/unity-catalog/storage-credentials"
    page_token = ""
    while True:
        query = {"max_results": "0"}
        if page_token:
            query["page_token"] = page_token
        payload = json.loads(run_cli(
            ["api", "get", f"{endpoint}?{urlencode(query)}", "--output", "json"], cwd, environment,
        ))
        for credential in payload.get("storage_credentials", []):
            if credential.get("name") != name:
                continue
            identity = credential.get("azure_managed_identity") or {}
            if (identity.get("access_connector_id", "").lower() != connector_id.lower()
                    or identity.get("managed_identity_id") or credential.get("read_only")):
                raise RuntimeError(f"Refusing to reuse storage credential {name}: identity or write access differs")
            return name
        page_token = payload.get("next_page_token", "")
        if not page_token:
            break

    body = {
        "name": name,
        "azure_managed_identity": {"access_connector_id": connector_id},
        "comment": "Synthetic construction supply demo storage; created by azd bootstrap",
        "read_only": False,
        "skip_validation": False,
    }
    created = json.loads(run_cli(
        ["api", "post", endpoint, "--json", json.dumps(body), "--output", "json"], cwd, environment,
    ))
    if created.get("name") != name:
        raise RuntimeError("Storage credential creation returned an unexpected name")
    return name


def bootstrap(
    get_value: Callable[[str], str],
    set_value: Callable[[str, str], None],
    run_cli: Callable[[list[str], Path, dict[str, str]], str],
    cli_available: bool,
) -> bool:
    set_value("DATABRICKS_READY", "false")
    set_value("DATABRICKS_GENIE_SPACE_ID", "")
    if get_value("ENABLE_DATABRICKS").lower() != "true":
        print("Databricks is disabled; skipping workspace bundles and supply-agent deployment.")
        return False
    if not cli_available:
        print("WARNING: Databricks CLI is not installed; supply-agent deployment will be skipped.")
        return False

    host = get_value("DATABRICKS_HOST")
    if not host:
        print("WARNING: no Databricks workspace host was provisioned; supply-agent deployment will be skipped.")
        return False

    environment = os.environ.copy()
    environment["DATABRICKS_HOST"] = host
    environment.setdefault("DATABRICKS_AUTH_TYPE", "azure-cli")
    data_bundle = ROOT / "databricks"
    genie_bundle = data_bundle / "genie"
    target = "dev"

    try:
        current_user = json.loads(run_cli(
            ["api", "get", "/api/2.0/preview/scim/v2/Me", "--output", "json"],
            data_bundle,
            environment,
        ))
        if not isinstance(current_user, dict) or not current_user.get("id"):
            raise RuntimeError("Databricks workspace identity check returned no current user")
        connector_id = get_value("DATABRICKS_ACCESS_CONNECTOR_ID")
        storage_url = get_value("DATABRICKS_STORAGE_URL")
        if not connector_id or not storage_url:
            raise RuntimeError("Missing DATABRICKS_ACCESS_CONNECTOR_ID or DATABRICKS_STORAGE_URL; reprovision Bicep")
        credential_name = _ensure_storage_credential(connector_id, run_cli, data_bundle, environment)
        environment["BUNDLE_VAR_storage_credential_name"] = credential_name
        environment["BUNDLE_VAR_storage_url"] = storage_url
        run_cli(["bundle", "validate", "--target", target], data_bundle, environment)
        run_cli(["bundle", "deploy", "--target", target], data_bundle, environment)
        run_cli(["bundle", "run", "--target", target, "seed_supply"], data_bundle, environment)

        warehouse_payload = run_cli(["warehouses", "list", "--output", "json"], data_bundle, environment)
        warehouse_id = _find_warehouse(warehouse_payload, "construction-supply-demo-dev")

        genie_args = ["--target", target, f"--var=warehouse_id={warehouse_id}"]
        run_cli(["bundle", "validate", *genie_args], genie_bundle, environment)
        run_cli(["bundle", "deploy", *genie_args], genie_bundle, environment)
        summary = run_cli(["bundle", "summary", *genie_args, "--force-pull", "--output", "json"], genie_bundle, environment)
        genie_id = _find_genie_space(summary)
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"WARNING: Databricks bootstrap did not complete; supply-agent deployment will be skipped. {exc}")
        return False

    set_value("DATABRICKS_GENIE_SPACE_ID", genie_id)
    set_value("DATABRICKS_READY", "true")
    print("Databricks workspace, synthetic seed data, and Genie Agent are ready.")
    return True


def main() -> int:
    try:
        _refresh_windows_path()
        bootstrap(_azd_value, _set_azd_value, _run_cli, shutil.which("databricks") is not None)
    except RuntimeError as exc:
        print(f"WARNING: {exc}; supply-agent deployment will be skipped.")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())