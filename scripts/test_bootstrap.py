#!/usr/bin/env python3
"""Offline tests for the network bootstrap (Spec 002, Stage 1: infra/bootstrap).

Run:  python -m unittest discover -s scripts -p test_bootstrap.py
Needs the Azure CLI with Bicep (az bicep build) and bash for the script syntax check.
"""
from __future__ import annotations

import importlib.util
import ipaddress
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "infra" / "bootstrap"
MAIN = BOOTSTRAP / "main.bicep"
NETWORK = BOOTSTRAP / "modules" / "network.bicep"
SETUP = BOOTSTRAP / "scripts" / "setup-jumpbox.sh"
HOOKS = BOOTSTRAP / "scripts" / "hooks.py"
AZ = shutil.which("az")
BASH = shutil.which("bash")
OUT = Path(tempfile.gettempdir()) / "prior-auth-bootstrap.json"

# Spec 002 §5.2 with the default 10.50.0.0/22.
EXPECTED_PREFIXES = {
    "agent": "10.50.0.0/24",
    "mcp": "10.50.1.0/27",
    "pe": "10.50.1.64/26",
    "apim": "10.50.1.128/27",
    "build": "10.50.1.160/27",
    "jumpbox": "10.50.1.192/27",
    "bastion": "10.50.2.0/26",
    "dbxHost": "10.50.2.64/26",
    "dbxContainer": "10.50.2.128/26",
    "psql": "10.50.2.192/28",
}
EXPECTED_SUBNETS = {
    # name: (prefix key, delegation, private endpoint network policies, has NSG, has NAT)
    "snet-agent": ("agent", "Microsoft.App/environments", "Disabled", True, True),
    "snet-mcp": ("mcp", "Microsoft.App/environments", "Disabled", True, True),
    "snet-pe": ("pe", "", "Enabled", True, False),
    "snet-apim": ("apim", "Microsoft.Web/serverFarms", "Disabled", True, True),
    "snet-build": ("build", "", "Disabled", True, True),
    "snet-jumpbox": ("jumpbox", "", "Disabled", True, True),
    "AzureBastionSubnet": ("bastion", "", "Disabled", False, False),
    "snet-dbx-host": ("dbxHost", "Microsoft.Databricks/workspaces", "Disabled", True, True),
    "snet-dbx-container": ("dbxContainer", "Microsoft.Databricks/workspaces", "Disabled", True, True),
    "snet-psql": ("psql", "Microsoft.DBforPostgreSQL/flexibleServers", "Disabled", False, False),
}


def bicep_prefixes(address_space: str) -> dict[str, ipaddress.IPv4Network]:
    """Evaluate main.bicep's cidrSubnet(addressSpace, n, i) table in Python."""
    source = MAIN.read_text(encoding="utf-8")
    network = ipaddress.ip_network(address_space)
    out = {}
    for key, new_prefix, index in re.findall(r"(\w+): cidrSubnet\(addressSpace, (\d+), (\d+)\)", source):
        out[key] = list(network.subnets(new_prefix=int(new_prefix)))[int(index)]
    return out


def walk_resources(template: dict):
    """Yield every resource, descending into nested module deployments."""
    resources = template.get("resources", [])
    items = resources.values() if isinstance(resources, dict) else resources
    for res in items:
        yield res
        inner = res.get("properties", {}).get("template")
        if inner:
            yield from walk_resources(inner)


class AddressPlanTests(unittest.TestCase):
    def test_default_plan_matches_spec(self):
        actual = {k: str(v) for k, v in bicep_prefixes("10.50.0.0/22").items()}
        self.assertEqual(actual, EXPECTED_PREFIXES)

    def test_subnets_do_not_overlap_and_fit(self):
        for space in ("10.50.0.0/22", "10.60.0.0/16", "172.20.4.0/22"):
            nets = list(bicep_prefixes(space).values())
            parent = ipaddress.ip_network(space)
            for i, a in enumerate(nets):
                self.assertTrue(a.subnet_of(parent), f"{a} not inside {space}")
                for b in nets[i + 1:]:
                    self.assertFalse(a.overlaps(b), f"{a} overlaps {b} in {space}")

    def test_minimum_sizes(self):
        plan = bicep_prefixes("10.50.0.0/22")
        self.assertLessEqual(plan["agent"].prefixlen, 24, "agent subnet: /24 recommended for production")
        self.assertLessEqual(plan["mcp"].prefixlen, 27, "Container Apps workload profiles need /27+")
        self.assertLessEqual(plan["apim"].prefixlen, 27, "APIM v2 VNet integration needs /27+")
        self.assertLessEqual(plan["bastion"].prefixlen, 26, "AzureBastionSubnet must be /26+")
        self.assertLessEqual(plan["dbxHost"].prefixlen, 26, "Databricks subnets need /26+")


class NetworkSourceTests(unittest.TestCase):
    source = NETWORK.read_text(encoding="utf-8")

    def test_subnets(self):
        calls = re.findall(r"subnetDef\('([^']+)', prefixes\.(\w+), ([^,]+), ([^,]+), '([^']*)', '(\w+)'\)", self.source)
        found = {name: (key, deleg, pol, nsg.strip() != "''", nat.strip() != "''") for name, key, nsg, nat, deleg, pol in calls}
        self.assertEqual(found, EXPECTED_SUBNETS)

    def test_private_subnets_have_no_default_outbound(self):
        self.assertIn("defaultOutboundAccess: false", self.source)

    def test_jumpbox_reachable_only_from_bastion(self):
        rules = re.findall(r"nsgRule\('([^']+)', \d+, 'Inbound', 'Allow', '\w+', \[([^\]]*)\], \[prefixes\.jumpbox\]", self.source)
        self.assertEqual(rules, [("allow-ssh-rdp-from-bastion", "prefixes.bastion")])

    def test_private_endpoint_subnet_denies_by_default(self):
        self.assertRegex(self.source, r"nsgRule\('deny-other-inbound', 4000, 'Inbound', 'Deny'")


@unittest.skipUnless(AZ, "Azure CLI not found")
class CompiledTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run([AZ, "bicep", "build", "--file", str(MAIN), "--outfile", str(OUT)],
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise AssertionError(f"bicep build failed:\n{result.stderr}")
        cls.template = json.loads(OUT.read_text(encoding="utf-8"))
        cls.resources = list(walk_resources(cls.template))

    def of_type(self, rtype: str) -> list[dict]:
        return [r for r in self.resources if r.get("type", "").lower() == rtype.lower()]

    def test_jumpbox_vm(self):
        (vm,) = self.of_type("Microsoft.Compute/virtualMachines")
        props = vm["properties"]
        self.assertTrue(props["osProfile"]["linuxConfiguration"]["disablePasswordAuthentication"])
        image = props["storageProfile"]["imageReference"]
        self.assertEqual((image["publisher"], image["offer"], image["sku"]), ("Canonical", "ubuntu-24_04-lts", "server"))
        self.assertEqual(props["securityProfile"]["securityType"], "TrustedLaunch")
        self.assertEqual(vm["identity"]["type"], "SystemAssigned")

    def test_no_public_ip_on_jumpbox(self):
        (nic,) = self.of_type("Microsoft.Network/networkInterfaces")
        for ipconfig in nic["properties"]["ipConfigurations"]:
            self.assertNotIn("publicIPAddress", ipconfig["properties"])

    def test_entra_ssh_extension(self):
        names = [e["properties"]["type"] for e in self.of_type("Microsoft.Compute/virtualMachines/extensions")]
        self.assertIn("AADSSHLoginForLinux", names)

    def test_key_vault_is_private(self):
        (vault,) = [v for v in self.of_type("Microsoft.KeyVault/vaults") if "existing" not in v]
        props = vault["properties"]
        self.assertEqual(props["publicNetworkAccess"], "Disabled")
        self.assertTrue(props["enableRbacAuthorization"])
        (pe,) = self.of_type("Microsoft.Network/privateEndpoints")
        self.assertEqual(pe["properties"]["privateLinkServiceConnections"][0]["properties"]["groupIds"], ["vault"])

    def test_bastion_supports_native_client(self):
        (bastion,) = self.of_type("Microsoft.Network/bastionHosts")
        self.assertEqual(bastion["sku"]["name"], "Standard")
        self.assertTrue(bastion["properties"]["enableTunneling"])

    def test_setup_runs_after_entra_extension(self):
        exts = {e["properties"]["type"]: e for e in self.of_type("Microsoft.Compute/virtualMachines/extensions")}
        setup = exts["CustomScript"]
        self.assertIn("script", setup["properties"]["settings"])
        self.assertTrue(any("AADSSHLoginForLinux" in d for d in setup.get("dependsOn", [])),
                        "setup must wait for the Entra extension (both use apt)")
        (vm,) = self.of_type("Microsoft.Compute/virtualMachines")
        self.assertNotIn("customData", vm["properties"]["osProfile"], "customData can't change after creation")

    def test_auto_shutdown(self):
        (schedule,) = self.of_type("Microsoft.DevTestLab/schedules")
        self.assertEqual(schedule["properties"]["taskType"], "ComputeVmShutdownTask")

    def test_outputs_for_stage_two(self):
        for name in ("NETWORK_RESOURCE_GROUP", "NETWORK_VNET_NAME", "NETWORK_DNS_ZONE_RESOURCE_GROUP",
                     "NETWORK_SUBNET_AGENT_ID", "NETWORK_SUBNET_PE_ID", "NETWORK_SUBNET_APIM_ID",
                     "NETWORK_SUBNET_MCP_ID", "BASTION_NAME", "JUMPBOX_VM_ID", "JUMPBOX_KEY_VAULT_NAME"):
            self.assertIn(name, self.template["outputs"])

    def test_dns_zones(self):
        source = MAIN.read_text(encoding="utf-8")
        for zone in ("privatelink.services.ai.azure.com", "privatelink.azure-api.net", "privatelink.azurecr.io",
                     "privatelink.documents.azure.com", "privatelink.search.windows.net",
                     "privatelink.monitor.azure.com", "privatelink.vaultcore.azure.net"):
            self.assertIn(f"'{zone}'", source)


class SetupScriptTests(unittest.TestCase):
    text = SETUP.read_text(encoding="utf-8")

    @unittest.skipUnless(BASH, "bash not found")
    def test_bash_syntax(self):
        subprocess.run([BASH, "-n", str(SETUP)], check=True)

    def test_lf_line_endings_or_stripped(self):
        # main.bicep strips CRs, but keep the source LF so the script also runs as-is.
        self.assertNotIn("\r\n", SETUP.read_bytes().decode("utf-8"))
        self.assertIn("replace(loadTextContent('scripts/setup-jumpbox.sh'), '\\r', '')", MAIN.read_text(encoding="utf-8"))

    def test_installs_toolchain(self):
        for marker in ("InstallAzureCLIDeb", "install-azd.sh", "docker-ce", "powershell", "astral.sh/uv",
                       "nodesource", "databricks/setup-cli", "azure.ai.agents", "unattended-upgrades"):
            self.assertIn(marker, self.text)

    def test_desktop_password_never_printed(self):
        rotate = self.text.split("install_rotate_password()", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("--file", rotate)          # secret value passed by file, not argv
        self.assertIn("umask 077", rotate)
        self.assertNotRegex(rotate, r"echo .*\$\(cat \"\$tmp\"\)")


class HookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("bootstrap_hooks", HOOKS)
        cls.hooks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.hooks)

    def run_pre(self, values: dict[str, str]) -> tuple[int, dict]:
        stored: dict[str, str] = {}
        with mock.patch.object(self.hooks, "env", side_effect=lambda n: values.get(n, "")), \
             mock.patch.object(self.hooks, "set_env", side_effect=lambda n, v: stored.__setitem__(n, v)), \
             mock.patch.object(self.hooks, "ensure_ssh_key", return_value="ssh-ed25519 AAAATEST test"):
            return self.hooks.preprovision(), stored

    def test_defaults_pass_and_store_key(self):
        rc, stored = self.run_pre({})
        self.assertEqual(rc, 0)
        self.assertEqual(stored["JUMPBOX_SSH_PUBLIC_KEY"], "ssh-ed25519 AAAATEST test")

    def test_rejects_small_or_public_address_space(self):
        self.assertEqual(self.run_pre({"NETWORK_ADDRESS_SPACE": "10.50.0.0/24"})[0], 1)
        self.assertEqual(self.run_pre({"NETWORK_ADDRESS_SPACE": "20.0.0.0/22"})[0], 1)
        self.assertEqual(self.run_pre({"NETWORK_ADDRESS_SPACE": "10.50.0.1/22"})[0], 1)

    def test_rejects_bad_desktop_flag(self):
        self.assertEqual(self.run_pre({"JUMPBOX_DESKTOP": "yes"})[0], 1)


if __name__ == "__main__":
    unittest.main()
