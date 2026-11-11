#!/usr/bin/env python3
"""Private-network checks for azd (Specs/002-private-network.md section 6.9).

  python scripts/check_network.py --stage pre    before provisioning (preprovision hook)
  python scripts/check_network.py --stage post   after provisioning: every service
                                                 name must resolve to a private IP
                                                 and accept HTTPS (postdeploy hook)

Both are no-ops unless ENABLE_PRIVATE_NETWORKING=true.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import shutil
import socket
import subprocess
import sys
import urllib.request
from urllib.parse import urlsplit

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

AZ = shutil.which("az") or "az"
AZD = shutil.which("azd") or "azd"
REQUIRED_SUBNETS = ("snet-agent", "snet-mcp", "snet-pe", "snet-apim")
REQUIRED_ZONES = (
    "privatelink.cognitiveservices.azure.com", "privatelink.openai.azure.com",
    "privatelink.services.ai.azure.com", "privatelink.azurecr.io",
    "privatelink.blob.core.windows.net", "privatelink.documents.azure.com",
    "privatelink.search.windows.net", "privatelink.monitor.azure.com",
)


def env(name: str) -> str:
    result = subprocess.run([AZD, "env", "get-value", name], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def az_json(*args: str):
    result = subprocess.run([AZ, *args, "-o", "json", "--only-show-errors"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout or "null")


def on_azure_vm() -> bool:
    """True when the Azure instance metadata service answers (we're on a VM in Azure)."""
    req = urllib.request.Request("http://169.254.169.254/metadata/instance/network?api-version=2021-02-01",
                                 headers={"Metadata": "true"})
    try:
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception:
        return False


def pre() -> int:
    print("\n  Private network: checking prerequisites...")
    errors: list[str] = []
    rg, vnet = env("NETWORK_RESOURCE_GROUP"), env("NETWORK_VNET_NAME")
    zone_rg = env("NETWORK_DNS_ZONE_RESOURCE_GROUP") or rg
    if not (rg and vnet):
        errors.append("Set NETWORK_RESOURCE_GROUP and NETWORK_VNET_NAME (outputs of infra/bootstrap).")
    else:
        subnets = az_json("network", "vnet", "subnet", "list", "-g", rg, "--vnet-name", vnet, "--query", "[].name")
        if subnets is None:
            errors.append(f"VNet {vnet} not found in {rg}.")
        else:
            missing = [s for s in REQUIRED_SUBNETS if s not in subnets]
            if missing:
                errors.append(f"VNet {vnet} is missing subnets: {', '.join(missing)}.")
            else:
                print(f"  [OK] VNet {vnet} with {', '.join(REQUIRED_SUBNETS)}")
        zones = az_json("network", "private-dns", "zone", "list", "-g", zone_rg, "--query", "[].name") or []
        missing_zones = [z for z in REQUIRED_ZONES if z not in zones]
        if missing_zones:
            errors.append(f"Private DNS zones missing in {zone_rg}: {', '.join(missing_zones)}.")
        else:
            print(f"  [OK] Private DNS zones in {zone_rg}")
    if env("ENABLE_DATABRICKS").lower() == "true":
        print("  [!!] Databricks: serverless SQL (Genie) needs a Network Connectivity Configuration")
        print("       attached by a Databricks account admin to reach the private Unity Catalog storage.")
    if not on_azure_vm():
        print("  [!!] This machine is not an Azure VM. Private endpoints are only reachable from inside")
        print("       the network: run azd from the jumpbox (infra/bootstrap/README.md).")
        if env("PRIVATE_NETWORK_ALLOW_OUTSIDE").lower() != "true":
            errors.append("Not running inside the network (set PRIVATE_NETWORK_ALLOW_OUTSIDE=true to override).")
    else:
        print("  [OK] Running on an Azure VM")
    for e in errors:
        print(f"  ERROR: {e}", file=sys.stderr)
    return 1 if errors else 0


def endpoints() -> list[str]:
    hosts = []
    account = env("AI_FOUNDRY_ACCOUNT_NAME")
    if account:
        hosts += [f"{account}.services.ai.azure.com", f"{account}.cognitiveservices.azure.com", f"{account}.openai.azure.com"]
    for name in ("AZURE_CONTAINER_REGISTRY_ENDPOINT", "AI_GATEWAY_URL", "DATABRICKS_HOST"):
        value = env(name)
        if value:
            hosts.append(urlsplit(value if "://" in value else f"https://{value}").hostname)
    for name in ("frontendUrl", "backendUrl"):
        value = env(name)
        if value:
            hosts.append(value.split("/")[0])
    return [h for h in dict.fromkeys(hosts) if h]


def post() -> int:
    print("\n  Private network: checking that every service resolves privately...")
    failed = 0
    for host in endpoints():
        try:
            ip = socket.gethostbyname(host)
        except OSError as exc:
            print(f"  [!!] {host:60s} does not resolve ({exc})")
            failed += 1
            continue
        private = ipaddress.ip_address(ip).is_private
        try:
            with socket.create_connection((host, 443), timeout=5):
                reachable = True
        except OSError:
            reachable = False
        status = "OK" if private and reachable else "!!"
        note = "" if private else " PUBLIC IP"
        note += "" if reachable else " port 443 unreachable"
        print(f"  [{status}] {host:60s} {ip}{note}")
        failed += 0 if status == "OK" else 1
    if failed:
        print(f"  {failed} endpoint(s) not private/reachable. Private DNS records can take a few minutes after provisioning.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("pre", "post"), required=True)
    args = parser.parse_args()
    if env("ENABLE_PRIVATE_NETWORKING").lower() != "true":
        return 0
    return pre() if args.stage == "pre" else post()


if __name__ == "__main__":
    sys.exit(main())
