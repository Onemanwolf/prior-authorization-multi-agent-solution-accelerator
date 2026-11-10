"""AI gateway (Spec 001) contract checks; no Azure credentials or resources required."""

import asyncio
import importlib.util
import json
import sys
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sync_ai_gateway  # noqa: E402

AGENT_DIRS = ("clinical", "compliance", "coverage", "synthesis", "supply")
POLICY = ROOT / "infra" / "policies" / "foundry-openai-v1-api-policy.xml"


def _load_model_client(agent: str = "clinical"):
    spec = importlib.util.spec_from_file_location(f"model_client_{agent}", ROOT / "agents" / agent / "model_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GatewayPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = POLICY.read_text(encoding="utf-8")
        cls.inbound = ET.fromstring(cls.source).find("inbound")

    def test_inbound_order_auth_allowlist_limit_metric_backend(self):
        order = [child.tag for child in self.inbound]
        expected = ["base", "validate-azure-ad-token", "set-variable", "choose", "llm-token-limit",
                    "llm-emit-token-metric", "authentication-managed-identity", "set-backend-service"]
        self.assertEqual(order, expected)

    def test_token_limit_is_per_agent_and_parameterized(self):
        limit = self.inbound.find("llm-token-limit")
        self.assertEqual(limit.get("tokens-per-minute"), "__TOKENS_PER_MINUTE__")
        self.assertIn('callerAppId', limit.get("counter-key"))
        self.assertEqual(limit.get("estimate-prompt-tokens"), "false")
        self.assertEqual(limit.get("retry-after-header-name"), "Retry-After")

    def test_backend_uses_managed_identity_and_streams(self):
        self.assertEqual(self.inbound.find("authentication-managed-identity").get("resource"),
                         "https://cognitiveservices.azure.com")
        forward = ET.fromstring(self.source).find("backend/forward-request")
        self.assertEqual(forward.get("buffer-response"), "false")

    def test_bicep_substitutes_every_placeholder(self):
        bicep = (ROOT / "infra" / "modules" / "apim-ai-gateway.bicep").read_text(encoding="utf-8")
        for placeholder in ("__TOKENS_PER_MINUTE__", "__BACKEND_ID__"):
            self.assertIn(placeholder, self.source)
            self.assertIn(f"'{placeholder}'", bicep)
        self.assertIn("loadTextContent('../policies/foundry-openai-v1-api-policy.xml')", bicep)
        for named_value in ("ai-gateway-tenant-id", "ai-gateway-audience", "ai-gateway-allowed-client-ids"):
            self.assertIn("{{%s}}" % named_value, self.source)
            self.assertIn(f"name: '{named_value}'", bicep)


class TemplateWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8-sig"))
        cls.parameters = json.loads((ROOT / "infra" / "main.parameters.json").read_text(encoding="utf-8-sig"))["parameters"]

    def test_gateway_and_api_center_are_opt_in_with_75k_default(self):
        self.assertEqual(self.parameters["enableAiGateway"]["value"], "${ENABLE_AI_GATEWAY=false}")
        self.assertEqual(self.parameters["enableApiCenter"]["value"], "${ENABLE_API_CENTER=false}")
        self.assertEqual(self.parameters["aiGatewayTokensPerMinute"]["value"], "${AI_GATEWAY_TOKENS_PER_MINUTE=75000}")
        main = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")
        self.assertIn("param enableAiGateway string = 'false'", main)
        self.assertIn("param aiGatewayTokensPerMinute string = '75000'", main)
        self.assertIn("output AI_GATEWAY_OPENAI_BASE_URL string", main)

    def test_every_hosted_agent_receives_the_gateway_url(self):
        supply = yaml.safe_load((ROOT / "agents" / "supply" / "azure.yaml").read_text(encoding="utf-8-sig"))
        services = {**self.project["services"], **supply["services"]}
        for name, service in services.items():
            if service.get("host") == "azure.ai.agent":
                self.assertEqual(service["env"].get("AI_GATEWAY_OPENAI_BASE_URL"), "${AI_GATEWAY_OPENAI_BASE_URL}", name)

    def test_postdeploy_syncs_allowlist_after_supply_agent_and_before_health_check(self):
        postdeploy = self.project["hooks"]["postdeploy"]
        for shell in ("windows", "posix"):
            run = postdeploy[shell]["run"]
            self.assertLess(run.index("deploy_supply_agent.py"), run.index("sync_ai_gateway.py"))
            self.assertLess(run.index("sync_ai_gateway.py"), run.index("check_agents.py"))

    def test_agent_images_ship_the_shared_model_client(self):
        reference = (ROOT / "agents" / "clinical" / "model_client.py").read_bytes().replace(b"\r\n", b"\n")
        for agent in AGENT_DIRS:
            folder = ROOT / "agents" / agent
            self.assertEqual((folder / "model_client.py").read_bytes().replace(b"\r\n", b"\n"), reference, agent)
            self.assertIn("model_client.py", (folder / "Dockerfile").read_text(encoding="utf-8"), agent)
            self.assertIn("agent-framework-openai", (folder / "requirements.txt").read_text(encoding="utf-8"), agent)
            main = (folder / "main.py").read_text(encoding="utf-8")
            self.assertIn("build_chat_client(", main, agent)
            self.assertNotIn("FoundryChatClient(", main, agent)

    def test_supply_agent_sync_clears_the_gateway_url(self):
        spec = importlib.util.spec_from_file_location("supply_deploy", ROOT / "scripts" / "deploy_supply_agent.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        values = {"DATABRICKS_READY": "true", "DATABRICKS_HOST": "https://adb", "DATABRICKS_GENIE_SPACE_ID": "s",
                  "AZURE_ENV_NAME": "dev", "AZURE_SUBSCRIPTION_ID": "sub", "AZURE_LOCATION": "eastus2"}
        calls = []
        module.deploy(lambda key: values.get(key, ""), lambda *_: None, lambda cmd, _cwd: calls.append(cmd) or True)
        self.assertIn(["azd", "env", "set", "AI_GATEWAY_OPENAI_BASE_URL", "", "--environment", "dev-supply"], calls)


class GenieRouteTests(unittest.TestCase):
    SPACE = "01f1d8a071911c4b9ba1290b25b26f29"

    def _env(self, **overrides):
        values = {"ENABLE_AI_GATEWAY": "true", "AI_GATEWAY_NAME": "apim-x", "AZURE_RESOURCE_GROUP": "rg-x",
                  "AZURE_SUBSCRIPTION_ID": "sub", "AI_GATEWAY_OPENAI_BASE_URL": "https://apim-x.azure-api.net/openai/v1",
                  "DATABRICKS_HOST": "https://adb-1.2.azuredatabricks.net", "DATABRICKS_GENIE_SPACE_ID": self.SPACE}
        values.update(overrides)
        return lambda name: values.get(name, "")

    def test_policy_authenticates_allowlists_pins_space_and_streams(self):
        root = ET.fromstring((ROOT / "infra" / "policies" / "databricks-genie-mcp-policy.xml").read_text(encoding="utf-8"))
        inbound = [child.tag for child in root.find("inbound")]
        self.assertEqual(inbound[:4], ["base", "validate-azure-ad-token", "set-variable", "choose"])
        self.assertIn("2ff814a6-3304-4ab8-85cb-cd0e6f879c1d", [a.text for a in root.iter("audience")])
        self.assertIn("__GENIE_SPACE_ID__", ET.tostring(root, encoding="unicode"))
        self.assertIsNone(root.find("inbound/authentication-managed-identity"))  # caller token is passed through
        self.assertEqual(root.find("backend/forward-request").get("buffer-response"), "false")

    def test_route_is_created_and_url_is_stored(self):
        bodies = []
        with patch.object(sync_ai_gateway, "_azd_env", self._env()),                 patch.object(sync_ai_gateway, "_put", lambda url, body: bodies.append((url, body))),                 patch.object(sync_ai_gateway, "_run") as run:
            url = sync_ai_gateway.ensure_genie_mcp_route()
        self.assertEqual(url, f"https://apim-x.azure-api.net/mcp/databricks-genie/{self.SPACE}")
        api = bodies[0][1]["properties"]
        self.assertEqual(api["serviceUrl"], "https://adb-1.2.azuredatabricks.net/api/2.0/mcp/genie")
        self.assertEqual({b["properties"].get("method") for _, b in bodies[1:4]}, {"POST", "GET", "DELETE"})
        policy = bodies[-1][1]["properties"]["value"]
        self.assertIn(self.SPACE, policy)
        self.assertNotIn("__GENIE_SPACE_ID__", policy)
        run.assert_called_once_with(["azd", "env", "set", "AI_GATEWAY_GENIE_MCP_URL", url])

    def test_disabled_gateway_clears_the_url(self):
        with patch.object(sync_ai_gateway, "_azd_env", self._env(ENABLE_AI_GATEWAY="false")),                 patch.object(sync_ai_gateway, "_put") as put, patch.object(sync_ai_gateway, "_run") as run:
            self.assertEqual(sync_ai_gateway.ensure_genie_mcp_route(), "")
        put.assert_not_called()
        run.assert_called_once_with(["azd", "env", "set", "AI_GATEWAY_GENIE_MCP_URL", ""])

    def test_supply_deploy_prepares_route_then_syncs_genie_url(self):
        spec = importlib.util.spec_from_file_location("supply_deploy_g", ROOT / "scripts" / "deploy_supply_agent.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        values = {"DATABRICKS_READY": "true", "DATABRICKS_HOST": "https://adb", "DATABRICKS_GENIE_SPACE_ID": "s",
                  "AZURE_ENV_NAME": "dev", "AZURE_SUBSCRIPTION_ID": "sub", "AZURE_LOCATION": "eastus2"}
        calls = []
        prepare = lambda: values.__setitem__("AI_GATEWAY_GENIE_MCP_URL", "https://apim-x.azure-api.net/mcp/g/s")
        module.deploy(lambda key: values.get(key, ""), lambda *_: None,
                      lambda cmd, _cwd: calls.append(cmd) or True, prepare)
        self.assertIn(["azd", "env", "set", "AI_GATEWAY_GENIE_MCP_URL", "https://apim-x.azure-api.net/mcp/g/s",
                       "--environment", "dev-supply"], calls)


class AllowlistSyncTests(unittest.TestCase):
    def _env(self, **overrides):
        values = {"ENABLE_AI_GATEWAY": "true", "AI_GATEWAY_NAME": "apim-x", "AZURE_RESOURCE_GROUP": "rg-x",
                  "AZURE_AI_PROJECT_ENDPOINT": "https://p", "SUPPLY_AGENT_DEPLOYED": "true"}
        values.update(overrides)
        return lambda name: values.get(name, "")

    def test_disabled_gateway_is_a_no_op(self):
        with patch.object(sync_ai_gateway, "_azd_env", self._env(ENABLE_AI_GATEWAY="false")), \
                patch.object(sync_ai_gateway, "_run") as run:
            self.assertEqual(sync_ai_gateway.main(), 0)
        run.assert_not_called()

    def test_named_value_gets_every_agent_client_id_including_supply(self):
        with patch.object(sync_ai_gateway, "_azd_env", self._env()), \
                patch.object(sync_ai_gateway, "_run") as run, \
                patch.object(sync_ai_gateway, "collect_client_ids") as collect:
            collect.side_effect = lambda _endpoint, agents: ({a: f"cid-{a}" for a in agents}, [])
            self.assertEqual(sync_ai_gateway.main(), 0)
        agents = collect.call_args.args[1]
        self.assertIn("supply-pricing-agent", agents)
        nv_update = run.call_args_list[0].args[0]
        self.assertEqual(nv_update[:4], ["az", "apim", "nv", "update"])
        value = nv_update[nv_update.index("--value") + 1]
        self.assertEqual(value.split(","), sorted(f"cid-{a}" for a in agents))
        self.assertEqual(run.call_args_list[1].args[0][:4], ["azd", "env", "set", "AI_GATEWAY_ALLOWED_CLIENT_IDS"])

    def test_no_identities_never_overwrites_the_allowlist(self):
        with patch.object(sync_ai_gateway, "_azd_env", self._env()), \
                patch.object(sync_ai_gateway, "_run") as run, \
                patch.object(sync_ai_gateway, "collect_client_ids", return_value=({}, ["clinical-reviewer-agent"])):
            self.assertEqual(sync_ai_gateway.main(), 1)
        run.assert_not_called()


class ModelClientTests(unittest.TestCase):
    def setUp(self):
        try:
            import agent_framework.openai  # noqa: F401
            import agent_framework.foundry  # noqa: F401
        except ImportError:
            self.skipTest("agent-framework packages are not installed")

    def test_without_gateway_url_agents_keep_the_foundry_client(self):
        module = _load_model_client()
        with patch.dict("os.environ", {"AI_GATEWAY_OPENAI_BASE_URL": ""}):
            client = module.build_chat_client(_FakeCredential(), "https://x.services.ai.azure.com/api/projects/p", "gpt-5.4")
        self.assertEqual(type(client).__name__, "FoundryChatClient")

    def test_gateway_url_sends_responses_calls_with_agent_bearer_token(self):
        from agent_framework import Agent

        seen = []
        server = HTTPServer(("127.0.0.1", 0), _stub_handler(seen))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_port}/openai/v1"
        module = _load_model_client()
        credential = _FakeCredential()
        try:
            with patch.dict("os.environ", {"AI_GATEWAY_OPENAI_BASE_URL": base_url}):
                client = module.build_chat_client(credential, "https://unused", "gpt-5.4")
            reply = asyncio.run(Agent(client=client, instructions="test").run("hello"))
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(reply.text, "pong")
        path, authorization, model = seen[0]
        self.assertTrue(path.startswith("/openai/v1/responses"), path)
        self.assertEqual(authorization, "Bearer agent-token")
        self.assertEqual(model, "gpt-5.4")
        self.assertEqual(credential.scopes, [("https://cognitiveservices.azure.com/.default",)])


class _FakeCredential:
    def __init__(self):
        self.scopes = []

    def get_token(self, *scopes, **_kwargs):
        from azure.core.credentials import AccessToken

        self.scopes.append(scopes)
        return AccessToken("agent-token", int(time.time()) + 3600)


def _stub_handler(seen):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
            seen.append((self.path, self.headers.get("Authorization"), body.get("model")))
            payload = json.dumps({
                "id": "resp_1", "object": "response", "created_at": 1, "status": "completed", "model": "gpt-5.4",
                "output": [{"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": "pong", "annotations": []}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    return Handler


if __name__ == "__main__":
    unittest.main()
