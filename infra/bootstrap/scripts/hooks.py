#!/usr/bin/env python3
"""azd hooks for the network bootstrap (Spec 002, Stage 1).

  python scripts/hooks.py preprovision    validate settings, find or create the SSH key
  python scripts/hooks.py postprovision   print how to connect and how to start Stage 2
"""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

AZD = shutil.which("azd") or "azd"
SSH_KEYGEN = shutil.which("ssh-keygen") or "ssh-keygen"
MIN_PREFIX = 22  # the subnet layout needs a /22 or larger


def env(name: str) -> str:
    result = subprocess.run([AZD, "env", "get-value", name], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def set_env(name: str, value: str) -> None:
    subprocess.run([AZD, "env", "set", name, value], check=True, capture_output=True, text=True)


def fail(message: str) -> int:
    print(f"  ERROR: {message}", file=sys.stderr)
    return 1


def ensure_ssh_key() -> str:
    """Return a public key for the break-glass admin, creating one if needed."""
    configured = env("JUMPBOX_SSH_PUBLIC_KEY")
    if configured:
        return configured
    ssh_dir = Path.home() / ".ssh"
    for name in ("id_ed25519.pub", "id_rsa.pub", "jumpbox_ed25519.pub"):
        candidate = ssh_dir / name
        if candidate.is_file():
            print(f"  Using SSH public key {candidate} for the break-glass admin")
            return candidate.read_text(encoding="utf-8").strip()
    key = ssh_dir / "jumpbox_ed25519"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    print(f"  No SSH key found; creating {key} (break-glass only, keep it safe)")
    subprocess.run([SSH_KEYGEN, "-t", "ed25519", "-N", "", "-C", "jumpbox-break-glass", "-f", str(key)],
                   check=True, capture_output=True)
    return Path(str(key) + ".pub").read_text(encoding="utf-8").strip()


def preprovision() -> int:
    print("\n  Network bootstrap: checking settings...")

    space = env("NETWORK_ADDRESS_SPACE") or "10.50.0.0/22"
    try:
        network = ipaddress.ip_network(space, strict=True)
    except ValueError as exc:
        return fail(f"NETWORK_ADDRESS_SPACE '{space}' is not a valid network: {exc}")
    if network.prefixlen > MIN_PREFIX:
        return fail(f"NETWORK_ADDRESS_SPACE '{space}' is too small; use a /{MIN_PREFIX} or larger")
    if not network.is_private:
        return fail(f"NETWORK_ADDRESS_SPACE '{space}' must be a private (RFC 1918) range")
    print(f"  [OK] Address space {space}")

    desktop = (env("JUMPBOX_DESKTOP") or "false").lower()
    if desktop not in ("true", "false"):
        return fail("JUMPBOX_DESKTOP must be true or false")
    print(f"  [OK] Jumpbox mode: {'desktop (GNOME, xrdp, Edge, VS Code)' if desktop == 'true' else 'headless (Entra SSH + VS Code Remote-SSH)'}")

    if not env("JUMPBOX_USERS_GROUP_ID"):
        print("  [!!] JUMPBOX_USERS_GROUP_ID is not set: only you will be able to sign in to the jumpbox.")
        print("       Set it to an Entra group's object ID to give your team access.")
    else:
        print("  [OK] Jumpbox users group set")

    key = ensure_ssh_key()
    if not key.startswith(("ssh-rsa ", "ssh-ed25519 ")):
        return fail("JUMPBOX_SSH_PUBLIC_KEY must be an ssh-rsa or ssh-ed25519 public key")
    set_env("JUMPBOX_SSH_PUBLIC_KEY", key)
    print("  [OK] Break-glass SSH key set")
    return 0


def postprovision() -> int:
    rg = env("NETWORK_RESOURCE_GROUP")
    bastion = env("BASTION_NAME")
    vm_id = env("JUMPBOX_VM_ID")
    vault = env("JUMPBOX_KEY_VAULT_NAME")
    vnet = env("NETWORK_VNET_NAME")
    location = env("AZURE_LOCATION")
    desktop = env("JUMPBOX_DESKTOP_ENABLED") == "true"
    if not (rg and bastion and vm_id):
        return fail("deployment outputs are missing; check the azd provision output")

    line = "=" * 63
    print(f"\n{line}\n NETWORK AND JUMPBOX READY\n{line}")
    print(f"  Resource group : {rg}")
    print(f"  VNet           : {vnet} ({env('NETWORK_ADDRESS_SPACE')})")
    print(f"  Jumpbox        : {env('JUMPBOX_VM_NAME')} ({env('JUMPBOX_PRIVATE_IP')}), no public IP")
    print(f"  Egress IP (NAT): {env('NETWORK_NAT_PUBLIC_IP')}")
    print("\n  The jumpbox installs its tools on first boot (about 10 min headless, 25 min desktop).")
    print("  Check with: jumpbox-check")

    print("\n  1. Connect (laptop needs: az extension add -n bastion; az extension add -n ssh)")
    if desktop:
        print("     Desktop (RDP, Windows laptop):")
        print(f"       az network bastion rdp --name {bastion} --resource-group {rg} --target-resource-id {vm_id}")
        print(f"     Sign in as 'jumpuser'. The password is in Key Vault {vault}, secret jumpbox-desktop-password")
        print("     (the vault is private: read it over SSH on the jumpbox, see README.md).")
        print("     Terminal (Entra ID):")
    print(f"       az network bastion ssh --name {bastion} --resource-group {rg} --target-resource-id {vm_id} --auth-type AAD")
    print("     VS Code Remote-SSH: start a tunnel and follow README.md 'Connect with VS Code':")
    print(f"       az network bastion tunnel --name {bastion} --resource-group {rg} --target-resource-id {vm_id} --resource-port 22 --port 2222")

    print("\n  2. On the jumpbox, deploy the solution (Stage 2):")
    print("       git clone <repo-url> && cd prior-authorization-multi-agent-solution-accelerator && git switch private-network")
    print("       az login --use-device-code && azd auth login --use-device-code")
    print("       azd env new <name>                # for example wc01_pai_ai_private")
    print(f"       azd env set AZURE_LOCATION {location}")
    print("       azd env set ENABLE_PRIVATE_NETWORKING true")
    print(f"       azd env set NETWORK_RESOURCE_GROUP {rg}")
    print(f"       azd env set NETWORK_VNET_NAME {vnet}")
    print(f"\n  Stage 2 (private template) is Phase 2 of Specs/002-private-network.md.\n{line}\n")
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("preprovision", "postprovision"):
        print(__doc__)
        return 2
    try:
        return preprovision() if sys.argv[1] == "preprovision" else postprovision()
    except (OSError, subprocess.CalledProcessError) as exc:
        return fail(f"{exc} {getattr(exc, 'stderr', '') or ''}".strip())


if __name__ == "__main__":
    sys.exit(main())
