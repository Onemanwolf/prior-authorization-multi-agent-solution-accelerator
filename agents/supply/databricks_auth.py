import asyncio
import base64
import binascii
import json
import logging
import os
import re
from urllib.parse import urlsplit

import httpx2


AZURE_DATABRICKS_RESOURCE_SCOPE = '2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default'
LOGGER = logging.getLogger(__name__)


def workspace_host(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname
            or not re.fullmatch(r'adb-[0-9]+\.[0-9]+\.azuredatabricks\.net', parsed.hostname)
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('DATABRICKS_HOST must be an Azure Databricks per-workspace HTTPS URL')
    return f'https://{parsed.hostname}'


def mcp_endpoint(host, space_id):
    if not re.fullmatch(r'[a-fA-F0-9]{32}', space_id):
        raise ValueError('DATABRICKS_GENIE_SPACE_ID must contain 32 hexadecimal characters')
    return f'{workspace_host(host)}/api/2.0/mcp/genie/{space_id}'


def gateway_mcp_endpoint(value):
    """Validate the APIM AI gateway route for Genie (AI_GATEWAY_GENIE_MCP_URL)."""
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname
            or not parsed.hostname.endswith('.azure-api.net')
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not parsed.path.strip('/') or parsed.query or parsed.fragment):
        raise ValueError('AI_GATEWAY_GENIE_MCP_URL must be an HTTPS API Management (*.azure-api.net) URL')
    return f'https://{parsed.hostname}{parsed.path.rstrip("/")}'


class DatabricksManagedIdentityAuth(httpx2.Auth):
    def __init__(self, host, authenticate, gateway_url=None):
        # With the AI gateway, the Databricks token goes to APIM, which checks the
        # caller and passes the token on to Databricks. It is never sent anywhere else.
        target = gateway_mcp_endpoint(gateway_url) if gateway_url else workspace_host(host)
        self.host = urlsplit(target).hostname
        self.credential = authenticate

    async def async_auth_flow(self, request):
        if (request.url.scheme != 'https' or request.url.host != self.host
                or request.url.port not in (None, 443)):
            raise ValueError('Refusing to send Databricks credentials to another origin')
        token = await asyncio.to_thread(
            self.credential.get_token, AZURE_DATABRICKS_RESOURCE_SCOPE
        )
        request.headers['Authorization'] = f'Bearer {token.token}'
        response = yield request
        if response.status_code in (401, 403):
            await response.aread()
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            try:
                encoded = token.token.split('.')[1]
                claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            except (IndexError, ValueError, binascii.Error):
                claims = {}
            identifiers = {
                key: value for key in ('oid', 'appid', 'azp', 'tid', 'aud')
                if isinstance(value := claims.get(key), str)
                and re.fullmatch(r'[a-fA-F0-9-]{36}', value)
            }
            code = str(payload.get('error_code', 'unknown')).replace(token.token, '[redacted]')[:100]
            message = str(payload.get('message', 'No JSON denial message')).replace(token.token, '[redacted]')[:500]
            LOGGER.warning('Databricks MCP denied HTTP %s: %s: %s; token identity IDs=%s',
                           response.status_code, code, message, identifiers)