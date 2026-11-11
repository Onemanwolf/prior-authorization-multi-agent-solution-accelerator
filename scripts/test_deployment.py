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
from unittest.mock import Mock, patch

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
                # Local Docker builds: a private registry (Spec 002) can't be reached by ACR Tasks.
                self.assertFalse(service["docker"]["remoteBuild"])
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

    def test_databricks_workspace_is_opt_in(self):
        parameters = json.loads((ROOT / "infra/main.parameters.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(parameters["parameters"]["enableDatabricks"]["value"], "${ENABLE_DATABRICKS}")
        infrastructure = (ROOT / "infra/main.bicep").read_text(encoding="utf-8-sig")
        self.assertIn("param enableDatabricks string = 'false'", infrastructure)
        self.assertRegex(infrastructure, r"module databricks '[^']+' = if \(enableDatabricks == 'true'\)")
        self.assertIn("output DATABRICKS_HOST string", infrastructure)
        self.assertIn("output DATABRICKS_AZURE_RESOURCE_ID string", infrastructure)
        self.assertIn("output DATABRICKS_ACCESS_CONNECTOR_ID string", infrastructure)
        self.assertIn("output DATABRICKS_STORAGE_URL string", infrastructure)
        module = (ROOT / "infra/modules/databricks.bicep").read_text(encoding="utf-8-sig")
        self.assertIn("Microsoft.Databricks/workspaces@2024-05-01", module)
        self.assertNotIn("initialName:", module)
        self.assertIn("br/public:avm/res/databricks/access-connector:", module)
        self.assertIn("br/public:avm/res/storage/storage-account:", module)
        for setting in ("enableHierarchicalNamespace: true", "allowSharedKeyAccess: false",
                        "allowBlobPublicAccess: false", "systemAssigned: true",
                        "roleDefinitionIdOrName: 'Storage Blob Data Contributor'",
                        "principalId: accessConnector.outputs.systemAssignedMIPrincipalId!"):
            self.assertIn(setting, module)
        self.assertIn("abfss://unity-catalog@", module)

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

    def test_windows_quota_queries_count_json_resources(self):
        source = self.project["hooks"]["preprovision"]["windows"]["run"]
        self.assertNotIn('--query "length(', source)
        for resource_command in (
            "containerapp env list",
            "acr list",
            "cognitiveservices account list",
        ):
            with self.subTest(command=resource_command):
                self.assertIn(f"az {resource_command} -o json", source)
        self.assertEqual(source.count("Where-Object { $_.location -eq $region }"), 3)

    def test_supply_postdeploy_configures_databricks_access(self):
        for shell in ("windows", "posix"):
            with self.subTest(shell=shell):
                source = self.project["hooks"]["postdeploy"][shell]["run"]
                self.assertIn("grant_agent_rbac.py --agent supply-pricing-agent --databricks", source)
                self.assertLess(source.index("deploy_supply_agent.py"), source.index("--databricks"))

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
    def _access_request_mock(self, *, preview_enabled=True, assigned=False, existing=True, unexpected_table=False):
        def respond(host, method, endpoint, body=None):
            principal = {"applicationId": "supply-client", "id": "123", "active": True}
            if method == "get" and "account/scim" in endpoint:
                return {"Resources": [principal] if existing else []}
            if method == "post" and "account/scim" in endpoint:
                return principal
            if method == "get" and endpoint.endswith("permissionassignments"):
                return {"permission_assignments": [{"principal": {"principal_id": 123}, "permissions": ["USER"]}] if assigned else []}
            if method == "get" and "/genie/spaces/" in endpoint:
                self.assertIn("include_serialized_space=true", endpoint)
                tables = [{"identifier": f"construction_supply.construction_supply_demo.{name}"}
                          for name in ("branches", "inventory", "order_lines", "prices", "products")]
                if unexpected_table:
                    tables.append({"identifier": "production.secret.table"})
                return {"warehouse_id": "demo-warehouse", "serialized_space": json.dumps({"data_sources": {"tables": tables}})}
            if "/mcp/genie/" in endpoint:
                if not preview_enabled:
                    raise RuntimeError("Managed MCP Servers are not enabled")
                return {"result": {"capabilities": {}}}
            return {}
        return Mock(side_effect=respond)

    def test_databricks_grants_are_additive_and_scoped(self):
        request = self._access_request_mock()
        with patch.object(grant_agent_rbac, "_databricks_request", request), \
                patch.object(grant_agent_rbac, "_azd_env", return_value="configured"), \
                patch.object(grant_agent_rbac, "_run") as run:
            grant_agent_rbac._grant_databricks_access("supply-client")
        patches = [call.args for call in request.call_args_list if call.args[1] == "patch"]
        self.assertEqual(len(patches), 10)
        for _, _, endpoint, body in patches:
            if "scim/v2/ServicePrincipals" in endpoint:
                self.assertEqual(body["Operations"], [{"op": "add", "path": "entitlements",
                                                       "value": [{"value": "databricks-sql-access"}]}])
            elif "unity-catalog" in endpoint:
                change = body["changes"][0]
                self.assertEqual(change["principal"], "supply-client")
                self.assertNotIn("remove", change)
                self.assertTrue(set(change["add"]) <= {"USE_CATALOG", "USE_SCHEMA", "SELECT"})
            else:
                self.assertIn(body["access_control_list"][0]["permission_level"], {"CAN_RUN", "CAN_USE"})
        assignments = [call.args for call in request.call_args_list if call.args[1] == "put"]
        self.assertEqual(assignments[0][3], {"permissions": ["USER"]})
        self.assertEqual(run.call_args.args[0][-2:], ["SUPPLY_AGENT_DATABRICKS_ACCESS_READY", "true"])

    def test_databricks_existing_identity_and_assignment_are_reused(self):
        request = self._access_request_mock(assigned=True)
        with patch.object(grant_agent_rbac, "_databricks_request", request), \
                patch.object(grant_agent_rbac, "_azd_env", return_value="configured"), \
                patch.object(grant_agent_rbac, "_run"):
            grant_agent_rbac._grant_databricks_access("supply-client")
        self.assertFalse(any(call.args[1] == "put" or (call.args[1] == "post" and "account/scim" in call.args[2])
                             for call in request.call_args_list))

    def test_databricks_missing_identity_is_registered(self):
        request = self._access_request_mock(existing=False)
        with patch.object(grant_agent_rbac, "_databricks_request", request), \
                patch.object(grant_agent_rbac, "_azd_env", return_value="configured"), \
                patch.object(grant_agent_rbac, "_run"):
            grant_agent_rbac._grant_databricks_access("supply-client")
        created = [call.args[3] for call in request.call_args_list if call.args[1] == "post" and "account/scim" in call.args[2]]
        self.assertEqual(created[0]["applicationId"], "supply-client")
        self.assertNotIn("groups", created[0])

    def test_disabled_mcp_preview_never_marks_access_ready(self):
        request = self._access_request_mock(preview_enabled=False)
        with patch.object(grant_agent_rbac, "_databricks_request", request), \
                patch.object(grant_agent_rbac, "_azd_env", return_value="configured"), \
                patch.object(grant_agent_rbac, "_run") as run, \
                self.assertRaisesRegex(RuntimeError, "Managed MCP Servers"):
            grant_agent_rbac._grant_databricks_access("supply-client")
        run.assert_called_once_with(["azd", "env", "set", "SUPPLY_AGENT_DATABRICKS_ACCESS_READY", "false"])

    def test_unexpected_genie_tables_never_receive_grants(self):
        request = self._access_request_mock(unexpected_table=True)
        with patch.object(grant_agent_rbac, "_databricks_request", request), \
                patch.object(grant_agent_rbac, "_azd_env", return_value="configured"), \
                patch.object(grant_agent_rbac, "_run"), self.assertRaisesRegex(RuntimeError, "Refusing grants"):
            grant_agent_rbac._grant_databricks_access("supply-client")
        self.assertFalse(any(call.args[1] == "patch" and "scim" not in call.args[2] for call in request.call_args_list))

    def test_windows_azure_cli_uses_native_python_launcher(self):
        with patch.object(grant_agent_rbac.sys, "platform", "win32"), \
                patch.object(grant_agent_rbac.shutil, "which", return_value="C:/CLI/wbin/az.cmd"), \
                patch.object(Path, "is_file", return_value=True):
            command = grant_agent_rbac._command(["az", "account", "show"])
        self.assertEqual(command[1:], ["-IBm", "azure.cli", "account", "show"])
        self.assertEqual(Path(command[0]), Path("C:/CLI/python.exe"))

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