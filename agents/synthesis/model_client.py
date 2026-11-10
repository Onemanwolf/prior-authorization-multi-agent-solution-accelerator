"""Model client selection for Foundry hosted agents (Spec 001: AI gateway).

When AI_GATEWAY_OPENAI_BASE_URL is set (azd output of the optional API
Management AI gateway, e.g. https://apim-x.azure-api.net/openai/v1), model calls
go through the gateway, which enforces the per-agent token limit and emits token
metrics. Otherwise the agent calls the Foundry project endpoint directly, exactly
as before.

The gateway path uses OpenAIChatClient (Responses API) with the agent's own
credential: the bearer token's audience is https://cognitiveservices.azure.com,
which the gateway validates and maps to this agent's identity (appid claim).

This file is identical in every agents/<name>/ folder because each folder is its
own Docker build context. Keep the copies in sync.
"""
from __future__ import annotations

import os
from typing import Any


def build_chat_client(credential: Any, project_endpoint: str, model: str) -> Any:
    gateway_base_url = os.environ.get("AI_GATEWAY_OPENAI_BASE_URL", "").strip()
    if not gateway_base_url:
        from agent_framework.foundry import FoundryChatClient

        return FoundryChatClient(
            project_endpoint=project_endpoint,
            model=model,
            credential=credential,
            allow_preview=True,
        )

    from agent_framework.openai import OpenAIChatClient

    # `credential=` (not api_key) makes the client request
    # https://cognitiveservices.azure.com/.default tokens and send them as
    # `Authorization: Bearer`, which is what the gateway's validate-azure-ad-token expects.
    return OpenAIChatClient(
        model=model,
        base_url=gateway_base_url.rstrip("/") + "/",
        credential=credential,
    )
