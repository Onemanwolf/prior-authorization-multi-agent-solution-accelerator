import importlib.util
import base64
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx2


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agents/supply'))
import databricks_auth

HOST = 'https://adb-1234567890123456.7.azuredatabricks.net'
SPACE = '1234567890abcdef1234567890abcdef'


class SupplyAgentTests(unittest.IsolatedAsyncioTestCase):
    def test_genie_conversation_id_is_forwarded_not_filtered(self):
        from agent_framework import MCPStreamableHTTPTool

        tool_name = 'poll_response_' + SPACE
        tool = MCPStreamableHTTPTool(name='supply-test', url=databricks_auth.mcp_endpoint(HOST, SPACE),
                                     additional_tool_argument_names=['conversation_id'])
        tool._tool_param_names_by_name[tool_name] = {'conversation_id', 'message_id', 'thread'}
        arguments, _ = tool._prepare_call_kwargs(tool_name, {
            'conversation_id': 'genie-conversation', 'message_id': 'genie-message', 'thread': object(),
        })
        self.assertEqual(arguments, {'conversation_id': 'genie-conversation', 'message_id': 'genie-message'})

    async def test_denial_diagnostics_never_log_bearer_token(self):
        claims = {'oid': '602db854-e956-4c6a-ad7f-27f100c86906', 'secret': 'do-not-log'}
        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
        token = 'header.' + encoded + '.signature'
        credential = Mock()
        credential.get_token.return_value = SimpleNamespace(token=token)
        transport = httpx2.MockTransport(lambda request: httpx2.Response(403, json={
            'error_code': 'PERMISSION_DENIED', 'message': 'Denied token ' + token,
        }))
        with self.assertLogs('databricks_auth', level='WARNING') as logs:
            async with httpx2.AsyncClient(auth=databricks_auth.DatabricksManagedIdentityAuth(HOST, credential),
                                          transport=transport) as client:
                response = await client.get(HOST)
        self.assertEqual(response.status_code, 403)
        self.assertIn(claims['oid'], logs.output[0])
        self.assertIn('PERMISSION_DENIED', logs.output[0])
        self.assertNotIn(token, logs.output[0])
        self.assertNotIn('do-not-log', logs.output[0])

    async def test_fresh_headers_for_each_request(self):
        credential = Mock()
        credential.get_token.side_effect = [SimpleNamespace(token='first'), SimpleNamespace(token='second')]
        headers = []

        def capture(request):
            headers.append(request.headers['Authorization'])
            return httpx2.Response(200)

        async with httpx2.AsyncClient(
            auth=databricks_auth.DatabricksManagedIdentityAuth(HOST, credential),
            transport=httpx2.MockTransport(capture),
        ) as client:
            await client.get(HOST)
            await client.get(HOST)
        self.assertEqual(headers, ['Bearer first', 'Bearer second'])
        self.assertEqual(
            [call.args[0] for call in credential.get_token.call_args_list],
            [databricks_auth.AZURE_DATABRICKS_RESOURCE_SCOPE] * 2,
        )

    async def test_no_cross_origin_credentials(self):
        credential = Mock()
        async with httpx2.AsyncClient(auth=databricks_auth.DatabricksManagedIdentityAuth(HOST, credential)) as client:
            for url in ['https://example.com', HOST.replace('https:', 'http:'), HOST + ':444']:
                with self.assertRaises(ValueError):
                    await client.get(url)
        credential.get_token.assert_not_called()

    def test_strict_endpoint(self):
        self.assertEqual(databricks_auth.mcp_endpoint(HOST, SPACE), HOST + '/api/2.0/mcp/genie/' + SPACE)
        for value in ['https://example.com', HOST + '/api', HOST + '?token=x', 'http://localhost']:
            with self.assertRaises(ValueError):
                databricks_auth.mcp_endpoint(value, SPACE)
        with self.assertRaises(ValueError):
            databricks_auth.mcp_endpoint(HOST, '../sql')

    def test_gateway_endpoint_is_strict(self):
        url = 'https://apim-x.azure-api.net/mcp/databricks-genie/' + SPACE
        self.assertEqual(databricks_auth.gateway_mcp_endpoint(url + '/'), url)
        for value in ['http://apim-x.azure-api.net/mcp/x', 'https://example.com/mcp/x',
                      'https://apim-x.azure-api.net', url + '?k=1', 'https://u:p@apim-x.azure-api.net/mcp/x']:
            with self.assertRaises(ValueError):
                databricks_auth.gateway_mcp_endpoint(value)

    async def test_gateway_mode_sends_databricks_token_only_to_apim(self):
        url = 'https://apim-x.azure-api.net/mcp/databricks-genie/' + SPACE
        credential = Mock()
        credential.get_token.return_value = SimpleNamespace(token='dbx')
        seen = []
        auth = databricks_auth.DatabricksManagedIdentityAuth(HOST, credential, url)
        async with httpx2.AsyncClient(auth=auth, transport=httpx2.MockTransport(
                lambda request: seen.append((request.url.host, request.headers['Authorization'])) or httpx2.Response(200))) as client:
            await client.post(url)
            with self.assertRaises(ValueError):
                await client.get(HOST)
        self.assertEqual(seen, [('apim-x.azure-api.net', 'Bearer dbx')])
        credential.get_token.assert_called_once_with(databricks_auth.AZURE_DATABRICKS_RESOURCE_SCOPE)

    def test_managed_identity_resource_scope(self):
        self.assertEqual(
            databricks_auth.AZURE_DATABRICKS_RESOURCE_SCOPE,
            '2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default',
        )

    def test_hosting_and_mcp_imports(self):
        from agent_framework import MCPStreamableHTTPTool
        from mcp import Client

        self.assertTrue(callable(Client))
        spec = importlib.util.spec_from_file_location('supply_main', ROOT / 'agents/supply/main.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.main))
        self.assertIn('conversationId as conversation_id', module.INSTRUCTIONS)
        self.assertIn('messageId as message_id', module.INSTRUCTIONS)
        self.assertIn('both arguments must be nonempty', module.INSTRUCTIONS)
        self.assertIn('Never use a Foundry conversation ID', module.INSTRUCTIONS)
        tool = MCPStreamableHTTPTool(name='supply-test', url=databricks_auth.mcp_endpoint(HOST, SPACE),
                         load_prompts=False, additional_tool_argument_names=['conversation_id'])
        self.assertIsNotNone(tool)


if __name__ == '__main__':
    unittest.main()