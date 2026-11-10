# Specs

Design specs for changes to this azd template. Each spec is reviewed before its drafts move into `infra/`, `agents/` or `scripts/`.

| # | Spec | Status | Implementation |
|---|---|---|---|
| 001 | [APIM AI gateway and API Center](001-apim-ai-gateway-api-center.md) | Implemented on `Ai-gateway`; live spike pending | [`infra/policies/foundry-openai-v1-api-policy.xml`](../infra/policies/foundry-openai-v1-api-policy.xml) · [`infra/modules/apim-ai-gateway.bicep`](../infra/modules/apim-ai-gateway.bicep) · [`infra/modules/api-center.bicep`](../infra/modules/api-center.bicep) |
| 002 | [Private network deployment (VNet, jumpbox, private endpoints)](002-private-network.md) | Draft for review on `private-network`; Phase 1 built | [`infra/bootstrap/`](../infra/bootstrap/) (network + jumpbox). Tests: `python -m unittest discover -s scripts -p test_bootstrap.py` |

Spec 001 is wired into `infra/main.bicep` behind `ENABLE_AI_GATEWAY` / `ENABLE_API_CENTER` (both default `false`). Contract tests: `python -m unittest scripts.test_ai_gateway`.
