#!/usr/bin/env python3
"""Turn off public access where Azure won't allow it at creation time (Spec 002 section 6.4).

API Management can't be created with public network access disabled. In private
mode this runs after provisioning: once the gateway private endpoint is approved it
disables public access and sets AI_GATEWAY_PUBLIC_ACCESS=Disabled, so later
`azd provision` runs keep it off. No-op unless ENABLE_PRIVATE_NETWORKING=true and the
AI gateway is deployed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

AZ = shutil.which("az") or "az"
AZD = shutil.which("azd") or "azd"


def env(name: str) -> str:
    result = subprocess.run([AZD, "env", "get-value", name], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def main() -> int:
    if env("ENABLE_PRIVATE_NETWORKING").lower() != "true":
        return 0
    apim, rg = env("AI_GATEWAY_NAME"), env("AZURE_RESOURCE_GROUP")
    if not (apim and rg):
        return 0
    show = subprocess.run([AZ, "apim", "show", "-g", rg, "-n", apim, "-o", "json", "--only-show-errors"],
                          capture_output=True, text=True)
    if show.returncode != 0:
        print(f"  WARNING: could not read APIM {apim}: {show.stderr.strip()}", file=sys.stderr)
        return 1
    service = json.loads(show.stdout)
    if service.get("publicNetworkAccess") == "Disabled":
        print(f"  [OK] APIM {apim}: public access already disabled")
        subprocess.run([AZD, "env", "set", "AI_GATEWAY_PUBLIC_ACCESS", "Disabled"], capture_output=True)
        return 0
    connections = service.get("privateEndpointConnections") or []
    approved = [c for c in connections
                if (c.get("properties", c).get("privateLinkServiceConnectionState") or {}).get("status") == "Approved"]
    if not approved:
        print(f"  WARNING: APIM {apim} has no approved private endpoint yet; public access left on. Re-run azd provision.",
              file=sys.stderr)
        return 1
    print(f"  Disabling public access on APIM {apim} (takes a few minutes)...")
    update = subprocess.run([AZ, "apim", "update", "-g", rg, "-n", apim, "--public-network-access", "false",
                             "-o", "none", "--only-show-errors"], capture_output=True, text=True)
    if update.returncode != 0:
        print(f"  WARNING: could not disable APIM public access: {update.stderr.strip()}", file=sys.stderr)
        return 1
    subprocess.run([AZD, "env", "set", "AI_GATEWAY_PUBLIC_ACCESS", "Disabled"], check=True, capture_output=True)
    print(f"  [OK] APIM {apim}: public access disabled (AI_GATEWAY_PUBLIC_ACCESS=Disabled)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
