"""Local deployment contract checks; no Azure credentials or resources required."""

import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import yaml

import grant_agent_rbac


ROOT = Path(__file__).resolve().parents[1]


class DeploymentConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8-sig"))

    def test_unified_hosted_services(self):
        self.assertEqual(self.project["infra"]["provider"], "bicep")
        services = self.project["services"]
        self.assertEqual(set(services), set(grant_agent_rbac.HOSTED_AGENTS))
        for name, service in services.items():
            with self.subTest(service=name):
                self.assertEqual(service["name"], name)
                self.assertEqual(service["kind"], "hosted")
                self.assertEqual(service["host"], "azure.ai.agent")
                self.assertTrue(service["docker"]["remoteBuild"])
                self.assertEqual(service["protocols"], [{"protocol": "responses", "version": "2.0.0"}])
                for legacy in ("config", "$ref", "resources", "environment_variables", "env_vars"):
                    self.assertNotIn(legacy, service)
                expected = {"cpu": "0.5", "memory": "1Gi"} if name == "compliance-agent" else {"cpu": "1", "memory": "2Gi"}
                self.assertEqual(service["container"]["resources"], expected)

    def test_runtime_environment(self):
        endpoints = {
            "clinical-reviewer-agent": {"MCP_ICD10_CODES", "MCP_PUBMED", "MCP_CLINICAL_TRIALS"},
            "coverage-assessment-agent": {"MCP_NPI_REGISTRY", "MCP_CMS_COVERAGE"},
        }
        for name, service in self.project["services"].items():
            with self.subTest(service=name):
                environment = service["env"]
                self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
                self.assertEqual(environment["MODEL_DEPLOYMENT_NAME"], "${AZURE_OPENAI_DEPLOYMENT_NAME}")
                self.assertEqual(environment["AZURE_AI_PROJECT_ENDPOINT"], "${AI_FOUNDRY_PROJECT_ENDPOINT}")
                self.assertEqual(environment["OTEL_CONNECTION_STRING"], "${APPLICATION_INSIGHTS_CONNECTION_STRING}")
                for key in environment:
                    self.assertRegex(key, r"^[A-Za-z_][A-Za-z0-9_]*$")
                    self.assertFalse(key.startswith(("FOUNDRY_", "AGENT_")))
                self.assertEqual({key for key in environment if key.startswith("MCP_")}, endpoints.get(name, set()))

    def test_environment_bindings_have_infrastructure_outputs(self):
        source = (ROOT / "infra/main.bicep").read_text(encoding="utf-8-sig")
        outputs = set(re.findall(r"^output\s+(\w+)\s", source, re.MULTILINE))
        for service in self.project["services"].values():
            for value in service["env"].values():
                for variable in re.findall(r"\$\{(\w+)\}", value):
                    self.assertIn(variable, outputs)
        self.assertTrue({"AZURE_AI_PROJECT_ID", "FOUNDRY_PROJECT_ENDPOINT", "AZURE_CONTAINER_REGISTRY_NAME", "AZURE_CONTAINER_REGISTRY_RESOURCE_ID"} <= outputs)

    def test_image_tag_handoff(self):
        parameters = json.loads((ROOT / "infra/main.parameters.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(parameters["parameters"]["imageTag"]["value"], "${APPLICATION_IMAGE_TAG}")
        hooks = self.project["hooks"]["postprovision"]
        self.assertIn("azd env set APPLICATION_IMAGE_TAG $imageTag", hooks["windows"]["run"])
        self.assertIn('azd env set APPLICATION_IMAGE_TAG "$IMAGE_TAG"', hooks["posix"]["run"])
        container = (ROOT / "infra/modules/container-app.bicep").read_text(encoding="utf-8-sig")
        self.assertIn("${imageName}:${imageTag}", container)
        infrastructure = (ROOT / "infra/main.bicep").read_text(encoding="utf-8-sig")
        self.assertIn("param imageTag string = ''", infrastructure)
        self.assertEqual(infrastructure.count("useAcrImage: imagesBuilt == 'true' || !empty(imageTag)"), 2)

    def test_retired_capability_hosts_are_not_provisioned(self):
        source = (ROOT / "infra/modules/ai-foundry.bicep").read_text(encoding="utf-8-sig")
        self.assertNotRegex(source, r"resource\s+\w+\s+'[^']*/capabilityHosts@")

    def test_app_insights_connection_has_required_resource_metadata(self):
        source = (ROOT / "infra/modules/ai-foundry.bicep").read_text(encoding="utf-8-sig")
        connection = source.split("resource appInsightsConnection ", 1)[1].split("resource modelDeployment ", 1)[0]
        self.assertRegex(connection, r"metadata:\s*\{\s*ResourceId:\s*appInsightsResourceId\b")
        self.assertRegex(source, r"@secure\(\)\s*param appInsightsConnectionString string\b")
        self.assertRegex(connection, r"credentials:\s*\{\s*key:\s*appInsightsConnectionString\b")
        self.assertNotIn("appInsightsInstrumentationKey", source)
        infrastructure = (ROOT / "infra/main.bicep").read_text(encoding="utf-8-sig")
        self.assertIn("appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString", infrastructure)
        monitoring = (ROOT / "infra/modules/monitoring.bicep").read_text(encoding="utf-8-sig")
        self.assertIn("output appInsightsConnectionString string = appInsights.properties.ConnectionString", monitoring)

    def test_foundry_account_child_writes_are_serialized(self):
        source = (ROOT / "infra/modules/ai-foundry.bicep").read_text(encoding="utf-8-sig")
        model = source.split("resource modelDeployment ", 1)[1].split("//", 1)[0]
        self.assertRegex(model, r"dependsOn:\s*\[\s*appInsightsConnection\s*\]")
        self.assertRegex(source, r"resource appInsightsConnection\b[\s\S]*?parent:\s*foundryProject\b")
        self.assertRegex(source, r"resource foundryProject\b[\s\S]*?parent:\s*foundryAccount\b")

    def test_windows_hook_syntax(self):
        executables = [executable for shell in ("pwsh", "powershell") if (executable := shutil.which(shell))]
        if not executables:
            self.skipTest("PowerShell is not installed")
        command = "$tokens = $null; $parseErrors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($env:AZD_HOOK_TEST_PATH, [ref]$tokens, [ref]$parseErrors); if ($parseErrors.Count) { $parseErrors | Out-String | Write-Error; exit 1 }"
        with tempfile.TemporaryDirectory(prefix="azd-hook-test-") as temporary:
            for name, hook in self.project["hooks"].items():
                source = hook["windows"]["run"]
                path = Path(temporary) / f"{name}.ps1"
                path.write_text(source, encoding="utf-8")
                for executable in executables:
                    with self.subTest(hook=name, shell=executable):
                        self.assertEqual(hook["windows"]["shell"], "pwsh")
                        result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", command], env={**os.environ, "AZD_HOOK_TEST_PATH": str(path)}, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_posix_hook_syntax(self):
        executable = shutil.which("sh")
        if not executable:
            git_shell = Path("C:/Program Files/Git/bin/bash.exe")
            if git_shell.exists():
                executable = str(git_shell)
        if not executable:
            self.skipTest("POSIX shell is not installed")
        for name, hook in self.project["hooks"].items():
            with self.subTest(hook=name):
                result = subprocess.run([executable, "-n"], input=hook["posix"]["run"], text=True, encoding="utf-8", capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)


class AgentRbacTests(unittest.TestCase):
    def test_latest_version_uses_current_api(self):
        latest = {"version": "12", "instance_identity": {"principal_id": "test-principal"}}
        response = io.BytesIO(json.dumps({"versions": {"latest": latest}}).encode())
        with patch("urllib.request.urlopen", return_value=response) as request:
            self.assertEqual(grant_agent_rbac._agent_latest_version("https://example.services.ai.azure.com/api/projects/test/", "test-agent", "test-token"), latest)
        self.assertTrue(request.call_args.args[0].full_url.endswith("/agents/test-agent?api-version=v1"))

    def test_missing_version_or_identity_fails(self):
        for version in (None, {"version": "1"}):
            with self.subTest(version=version), patch.object(grant_agent_rbac, "_azd_env", return_value="test"), patch.object(grant_agent_rbac, "_get_token", return_value="test-token"), patch.object(grant_agent_rbac, "_agent_latest_version", return_value=version), patch.object(grant_agent_rbac, "_grant_role") as grant, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(grant_agent_rbac.main(["test-agent"]), 1)
                grant.assert_not_called()


if __name__ == "__main__":
    unittest.main()