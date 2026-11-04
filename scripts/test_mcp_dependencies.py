"""Exercise the MCP client API and hosting imports without network or Azure calls."""

import importlib.metadata
import unittest


class McpDependencyTests(unittest.IsolatedAsyncioTestCase):
    def test_high_level_client_is_available(self):
        from mcp import Client

        self.assertTrue(callable(Client))

    def test_foundry_hosting_imports(self):
        from agent_framework.foundry import FoundryChatClient
        from agent_framework_foundry_hosting import ResponsesHostServer

        self.assertTrue(callable(FoundryChatClient))
        self.assertTrue(callable(ResponsesHostServer))

    async def test_streamable_http_transport_setup(self):
        import httpx2
        from agent_framework import MCPStreamableHTTPTool
        from mcp import Client

        async with httpx2.AsyncClient(timeout=httpx2.Timeout(60.0)) as http_client:
            tool = MCPStreamableHTTPTool(
                name="dependency-smoke",
                url="http://127.0.0.1:8088/mcp",
                http_client=http_client,
                load_prompts=False,
            )
            transport = tool.get_mcp_client()
            self.assertTrue(hasattr(transport, "__aenter__"))
            client = Client(server=transport)
            self.assertIsInstance(client, Client)


if __name__ == "__main__":
    for package in ("agent-framework-core", "agent-framework-foundry-hosting", "mcp", "httpx2"):
        print(f"{package}=={importlib.metadata.version(package)}", flush=True)
    unittest.main()