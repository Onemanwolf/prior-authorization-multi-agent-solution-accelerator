# Spec 001: API Management AI gateway and API Center for the Foundry azd template

| | |
|---|---|
| **Status** | Implemented on branch `Ai-gateway`; live Azure spike pending (§12 step 1) |
| **Date** | 2026-11-06 |
| **Owner** | Tim Oleson |
| **Template** | `prior-auth-maf` (this repo, root `azure.yaml`) |
| **Related research** | *White Cap Supply Assistant: Azure Solution Research* (revised 2026-11-06), Control plane, decisions D7 and D8 |
| **Implementation** | `infra/modules/apim-ai-gateway.bicep` · `infra/modules/api-center.bicep` · `infra/policies/foundry-openai-v1-api-policy.xml` · `agents/*/model_client.py` · `scripts/sync_ai_gateway.py` · `scripts/test_ai_gateway.py` |

---

## 1. Summary

This spec adds an opt-in **AI gateway** to the template:
- **Azure API Management (APIM)** sits between every Foundry hosted agent and its model deployment.
- **Azure API Center** acts as the registry for the gateway API and the MCP servers the agents call.

The gateway enforces a **per-agent token limit of 75,000 tokens per minute** (`llm-token-limit`) and emits per-agent token metrics (`llm-emit-token-metric`). It authenticates agents keylessly with their Entra identities and reaches Foundry with its own managed identity. Both features are off by default (`ENABLE_AI_GATEWAY`, `ENABLE_API_CENTER`), so `azd up` behaves exactly as it does today until a developer opts in.

### Goals
1. All model calls from the four prior-auth agents and the optional supply agent pass through APIM when the gateway is enabled.
2. Token limits apply per agent: one counter per agent identity, 75k TPM to start (revised from 330k on 2026-11-06), and a parameter controls the value.
3. Token usage per agent appears in Application Insights custom metrics. Gateway and LLM logs land in Log Analytics, with prompt bodies off.
4. One switch per agent stops only that agent's model traffic (kill switch).
5. API Center lists the gateway API (synchronized from APIM) and the remote MCP servers, each with lifecycle stage and environment.
6. Everything deploys with `azd up` / `azd provision`, follows azd conventions, and uses no keys or secrets.

### Non-goals (this spec)
- Exposing the Function API or other REST APIs as APIM MCP servers. That is research-doc feature #1 and gets its own later spec (Spec 002 is private networking).
- Routing the backend → hosted-agent calls (the Foundry agent endpoints) through APIM.
- Private networking (research decision D1). The design supports a later move to StandardV2 with a private endpoint, but this spec stays public with Entra auth.
- Semantic caching, content safety in the gateway, PII masking. These are listed as follow-ups in §13.

---

## 2. Current state and the key constraint

Each hosted agent (`agents/*/main.py`) builds its model client like this:

```python
chat_client = FoundryChatClient(project_endpoint=project_endpoint, model=model, credential=credential)
```

The model call goes straight from the agent container to the Foundry project endpoint. The model deployment is `gpt-5.4`, GlobalStandard or DataZoneStandard, at **100K TPM capacity** (`deploymentCapacityK = 100` in `infra/modules/ai-foundry.bicep`).

**Finding that drives the design (verified against Microsoft Learn, 2026-11-06):**

| Option | What it does | Covers hosted-agent model traffic? |
|---|---|---|
| **Foundry "AI Gateway" portal integration** (*Manage › AI Gateway*), preview | Links a v2 APIM to the Foundry resource and sets per-project TPM/quota | **No.** Agent Service internal model calls bypass APIM (accepted Microsoft Q&A answer). Clients must call the project's *gateway URL*. There's no documented Bicep or CLI path. |
| **Foundry `ApiManagement` / `ModelGateway` project connection** (BYO model, GA) | Agent references `<connection>/<model>` and Foundry calls APIM | **Documented for prompt agents only.** Not supported with Foundry IQ, memory or guardrails. Unverified for hosted agents. |
| **Agent code calls APIM directly** (chosen) | Agent's chat client `base_url` = APIM `/openai/v1`; APIM calls Foundry with MI | **Yes.** This is the pattern in the official `AI-Gateway/labs/ai-foundry-hosted-agents` and `Azure-Samples/simple-foundry-hosted-agent-python-aigateway` samples. |

This settles **research decision D8** for hosted agents: use direct-to-gateway in agent code. Revisit the BYO-model connection if hosted-agent support is documented.

---

## 3. Decisions

| # | Decision | Choice | Rationale | Alternatives |
|---|---|---|---|---|
| A1 | APIM tier | **BasicV2, 1 unit** (parameter `AI_GATEWAY_SKU`, allowed `BasicV2`/`StandardV2`) | Supports all AI gateway policies, provisions in about 5–10 min, cheapest v2. Matches research D7. | **StandardV2**: needed for private endpoint / VNet integration (D1) and gives API Center Standard at no charge. Classic tiers take 30–45 min and can't use the Foundry AI Gateway integration. |
| A2 | Model routing | **Direct-to-gateway** in agent code, behind env var `AI_GATEWAY_OPENAI_BASE_URL` | Only documented pattern that covers hosted agents (§2) | Foundry AI Gateway (portal) or BYO-model connection, both of which bypass or exclude hosted agents |
| A3 | API surface | **OpenAI v1** (`/openai/v1/*`, model in body, no `api-version`) | Covers the Responses API and Chat Completions; `llm-token-limit` supports both | Azure OpenAI deployment-path surface (`/openai/deployments/{d}`) |
| A4 | Caller auth | **Keyless**: `validate-azure-ad-token` (tenant + audience) plus a client-ID allowlist named value | Keeps the template's *no keys anywhere* promise. Allowlist doubles as kill switch. | One APIM product subscription key per agent, stored in agent env: simpler, but adds secrets to agent definitions |
| A5 | Token-limit counter | **Agent identity client ID** (`appid`/`azp` claim) | Per-agent limit, metrics and kill switch without subscription keys | `context.Subscription.Id` (needs keys, A4 alternative) |
| A6 | Limit value | **75,000 TPM per agent**, `AI_GATEWAY_TOKENS_PER_MINUTE=75000`; no period quota yet | Starting value for the pilot (revised from 330k). Fits inside the 100K TPM deployment. A monthly `token-quota` can be added without code changes. | Per-project limit in the Foundry portal (doesn't apply to hosted agents) |
| A7 | Backend auth | **APIM system-assigned MI** with *Cognitive Services OpenAI User* on the Foundry account | Least privilege, keyless (Foundry has `disableLocalAuth: true`) | Foundry User, which is broader |
| A8 | Provisioning model | **Feature flags in the existing `main.bicep`** (`ENABLE_AI_GATEWAY`, `ENABLE_API_CENTER`) | Matches the existing `ENABLE_DATABRICKS` pattern. `azd` layered provisioning is still **beta**. | Separate `gateway` layer via `infra.layers` once layers are GA |
| A9 | API Center | **Free SKU**, separate region parameter (default `eastus`), APIM sync via `apiSources`, MCP servers registered in Bicep | API Center isn't offered in East US 2. Free covers 200 APIs and 1 linked source. | Standard: free only when linked to StandardV2/PremiumV2 APIM |
| A10 | IaC style | Hand-written modules (`infra/modules/`) | AVM `api-management/service` 0.15.0 defaults to **Premium ×3**, and AVM `api-center/service` 0.1.0 lacks verified `apiSources` support. Hand-written modules match this repo's style. | AVM modules with explicit `sku` overrides |

---

## 4. Target architecture

```text
                       ┌──────────── Foundry project (hosted agents) ─────────────┐
 Backend (ACA) ──────► │ clinical · compliance · coverage · synthesis · supply     │
 Responses API         │  OpenAIChatClient(base_url=$AI_GATEWAY_OPENAI_BASE_URL)   │
                       └───────────────┬──────────────────────────────────────────┘
                                       │ Bearer token (agent identity, aud = cognitiveservices)
                                       ▼
                ┌──────────── API Management (BasicV2) · API "foundry-openai-v1" /openai/v1/* ───────────┐
                │ 1 validate-azure-ad-token  → 401 if no/invalid token                                     │
                │ 2 allowlist(appid)         → 403 if agent not registered / switched off                  │
                │ 3 llm-token-limit 75k TPM per appid → 429 + Retry-After                                 │
                │ 4 llm-emit-token-metric (dimension: Agent Client ID) → App Insights                     │
                │ 5 authentication-managed-identity → Foundry ; backend circuit breaker on 429/5xx        │
                └──────────────┬───────────────────────────────────────────────┬──────────────────────────┘
                               │ APIM MI (Cognitive Services OpenAI User)      │ logs/metrics
                               ▼                                               ▼
                 Foundry account  https://<foundry>.openai.azure.com/openai/v1   App Insights · Log Analytics
                 deployment gpt-5.4                                                (ApiManagementGatewayLlmLog)

 API Center (eastus) ── apiSources sync ◄── APIM          (registry only, never in the request path)
                     └─ MCP servers: ICD-10, PubMed, Clinical Trials, NPI Registry, CMS Coverage, Databricks Genie
```

The **backend → agent** path (Foundry Responses API on agent endpoints) is unchanged. **MCP tool calls** from agents also stay direct to their servers in this spec. API Center registers those servers so they're governed, and Spec 002 can put them behind APIM.

---

## 5. azd template changes

### 5.1 New environment variables → Bicep parameters

Add the following to `infra/main.parameters.json`, using the `${VAR=default}` substitution form:

```json
"enableAiGateway":            { "value": "${ENABLE_AI_GATEWAY=false}" },
"aiGatewaySku":               { "value": "${AI_GATEWAY_SKU=BasicV2}" },
"aiGatewayTokensPerMinute":   { "value": "${AI_GATEWAY_TOKENS_PER_MINUTE=75000}" },
"aiGatewayPublisherEmail":    { "value": "${AI_GATEWAY_PUBLISHER_EMAIL}" },
"aiGatewayAllowedClientIds":  { "value": "${AI_GATEWAY_ALLOWED_CLIENT_IDS}" },
"enableApiCenter":            { "value": "${ENABLE_API_CENTER=false}" },
"apiCenterLocation":          { "value": "${API_CENTER_LOCATION=eastus}" },
"deploymentCapacityK":        { "value": "${AZURE_OPENAI_DEPLOYMENT_CAPACITY=100}" }
```

Developer workflow:

```bash
azd env set ENABLE_AI_GATEWAY true
azd env set AI_GATEWAY_PUBLISHER_EMAIL you@contoso.com
azd env set AI_GATEWAY_TOKENS_PER_MINUTE 75000      # default; change and re-provision to tune
azd env set ENABLE_API_CENTER true                  # optional
azd up
```

### 5.2 `infra/main.bicep`

- Add parameters to match §5.1. Keep the string-typed flag pattern already used by `enableDatabricks` (`param enableAiGateway string = 'false'`).
- Add abbreviations `"apiManagementService": "apim-"` and `"apiCenterService": "apic-"` to `infra/abbreviations.json`. CAF defines `apim`; it has no API Center abbreviation, and the samples use `apic`.
- Move the drafts into `infra/modules/` and wire them in:

```bicep
module aiGateway './modules/apim-ai-gateway.bicep' = if (enableAiGateway == 'true') {
  name: 'ai-gateway'
  scope: rg
  params: {
    name: '${abbrs.apiManagementService}${resourceToken}'
    location: location
    tags: tags
    skuName: aiGatewaySku
    publisherEmail: aiGatewayPublisherEmail
    foundryAccountName: aiFoundry.outputs.accountName
    tokensPerMinute: int(aiGatewayTokensPerMinute)
    allowedClientIds: aiGatewayAllowedClientIds
    appInsightsResourceId: monitoring.outputs.appInsightsResourceId
    appInsightsInstrumentationKey: monitoring.outputs.appInsightsInstrumentationKey
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
  }
}

module apiCenter './modules/api-center.bicep' = if (enableApiCenter == 'true') {
  name: 'api-center'
  scope: rg
  params: {
    name: '${abbrs.apiCenterService}${resourceToken}'
    location: apiCenterLocation
    tags: tags
    apimName: enableAiGateway == 'true' ? aiGateway!.outputs.name : ''
    mcpServers: [
      { name: 'icd10-codes',     title: 'ICD-10 Codes MCP',     description: 'ICD-10 code lookup (Anthropic healthcare gateway)', url: 'https://hcls.mcp.claude.com/icd10_codes/mcp' }
      { name: 'pubmed',          title: 'PubMed MCP',           description: 'PubMed literature search',                           url: 'https://pubmed.mcp.claude.com/mcp' }
      { name: 'clinical-trials', title: 'Clinical Trials MCP',  description: 'ClinicalTrials.gov search',                          url: 'https://hcls.mcp.claude.com/clinical_trials/mcp' }
      { name: 'npi-registry',    title: 'NPI Registry MCP',     description: 'Provider NPI lookup',                                url: 'https://hcls.mcp.claude.com/npi_registry/mcp' }
      { name: 'cms-coverage',    title: 'CMS Coverage MCP',     description: 'Medicare LCD/NCD coverage policies',                 url: 'https://hcls.mcp.claude.com/cms_coverage/mcp' }
    ]
  }
}
```

  To register the Databricks Genie MCP endpoint (`https://<workspace>/api/2.0/mcp/genie/<space-id>`), add a row when `enableDatabricks == 'true'`. Its space ID is only known after `postprovision`, so register it from the hook with `az apic api create` instead of from Bicep.

- **New outputs**, which become azd env vars:

```bicep
output AI_GATEWAY_NAME string = enableAiGateway == 'true' ? aiGateway!.outputs.name : ''
output AI_GATEWAY_URL string = enableAiGateway == 'true' ? aiGateway!.outputs.gatewayUrl : ''
output AI_GATEWAY_OPENAI_BASE_URL string = enableAiGateway == 'true' ? aiGateway!.outputs.openAiV1BaseUrl : ''
output API_CENTER_NAME string = enableApiCenter == 'true' ? apiCenter!.outputs.name : ''
output API_CENTER_MCP_REGISTRY_URL string = enableApiCenter == 'true' ? apiCenter!.outputs.mcpRegistryUrl : ''
```

- Wire `deploymentCapacityK` through to `ai-foundry.bicep`. See §6.3: the deployment's TPM is shared by every agent.

### 5.3 `azure.yaml` (root) and `agents/supply/azure.yaml`

For every hosted-agent service, add:

```yaml
    env:
      AI_GATEWAY_OPENAI_BASE_URL: ${AI_GATEWAY_OPENAI_BASE_URL}
```

When the gateway is disabled, the value is empty and agents keep using `FoundryChatClient` (§7).

### 5.4 Hooks

**`postdeploy`: new step `scripts/sync_ai_gateway.py`**
- Runs after `grant_agent_rbac.py` and before `check_agents.py`.
- No-op unless `ENABLE_AI_GATEWAY=true`.
- Steps:
  1. Reuse `grant_agent_rbac.py`'s agent lookup (`instance_identity`) to collect each hosted agent's **client ID**. Include `supply-pricing-agent` when `SUPPLY_AGENT_DEPLOYED=true`.
  2. Write the comma-separated list to the APIM named value `ai-gateway-allowed-client-ids`:
     `az apim nv update -g $RG -n $AI_GATEWAY_NAME --named-value-id ai-gateway-allowed-client-ids --value "<ids>"`.
  3. Persist the list with `azd env set AI_GATEWAY_ALLOWED_CLIENT_IDS <ids>` so the next `azd provision` doesn't reset it.
- Must be idempotent. Exits 0 with a warning on failure, matching existing hook style (`continueOnError: true`).
- Implement in Python so the `windows` (pwsh) and `posix` (sh) hook bodies stay one-line calls, as they are today.

**`postprovision`:** no change. APIM is fully configured by Bicep.

**`check_agents.py`:** when the gateway is on, add a smoke test.
- Send one Responses call through `AI_GATEWAY_OPENAI_BASE_URL` with the deployer's token. The deployer isn't on the allowlist, so expect **403**: this proves enforcement.
- Then confirm the agent health checks pass, which proves allowlisted agent traffic flows.

### 5.5 azd conventions applied

- Resource names use `abbrs` + `resourceToken`. Tags inherit `azd-env-name`; the gateway isn't an azd service, so it gets no `azd-service-name`.
- All configuration comes from `azd env`. Bicep outputs feed downstream services and hooks, and nothing is hard-coded.
- Use `azd provision --preview` (what-if) before the first enablement on a shared environment.
- **APIM soft-delete:** `azd down --purge` purges it. Without `--purge`, re-creating the same name fails until you run `az apim deletedservice purge`. Document this in `docs/troubleshooting.md`.
- **Cost:** BasicV2 bills hourly while it exists. Mention it alongside the Databricks cost note in the README.

---

## 6. Token-limit policy (75k)

Deployable file: [`infra/policies/foundry-openai-v1-api-policy.xml`](../infra/policies/foundry-openai-v1-api-policy.xml). Bicep loads it with `loadTextContent` and substitutes `__TOKENS_PER_MINUTE__` and `__BACKEND_ID__`, then applies it at **API scope** (`foundry-openai-v1`, `format: rawxml`).

### 6.1 Policy pipeline (inbound)

| Step | Policy | Behaviour |
|---|---|---|
| 1 | `validate-azure-ad-token` | Tenant `{{ai-gateway-tenant-id}}`, audience `{{ai-gateway-audience}}` (`https://cognitiveservices.azure.com`). Failure returns **401**. |
| 2 | `choose` over `{{ai-gateway-allowed-client-ids}}` | Caller `appid` (or `azp`) must be on the allowlist; otherwise **403** `AgentNotAuthorized`. |
| 3 | `llm-token-limit` | `counter-key = appid`, `tokens-per-minute = 75000`, `estimate-prompt-tokens = false`. Over the limit returns **429** with `Retry-After`. Adds `x-ratelimit-remaining-tokens` and `x-ai-gateway-tokens-consumed` headers. |
| 4 | `llm-emit-token-metric` | Namespace `ai-gateway`; dimensions `Agent Client ID`, `API ID`, `Operation ID`, `Backend ID`. 4 of the 5 allowed custom dimensions are used. |
| 5 | `authentication-managed-identity` | Replaces the caller's `Authorization` header with an APIM MI token for `https://cognitiveservices.azure.com`. |
| 6 | `set-backend-service` + `forward-request buffer-response="false"` | Sends to the Foundry OpenAI v1 backend. Unbuffered so Responses API **streaming** works. |

### 6.2 Policy behaviour to know (from the `llm-token-limit` reference)
- Tiers: Developer, Basic, Standard, Premium and all v2 tiers, but not Consumption. Section: inbound. Supports **Chat Completions and Responses API**.
- v2 tiers use a **token bucket**, and the counter is per key across all scopes. Keep one TPM value per key.
- Only prompt and completion tokens count. With `stream: true`, prompt and completion tokens are **estimated**, and concurrent requests can briefly overshoot.
- Counts are per gateway instance and not aggregated across regions. That doesn't matter with one BasicV2 unit.
- `estimate-prompt-tokens="false"` charges actual usage from the response. Switch to `true` to reject oversized prompts *before* they reach the model, at some estimation cost.
- **Optional quota:** add `token-quota="<n>" token-quota-period="Monthly"` to the same element for a hard monthly ceiling. Exceeding a quota returns **403**, unlike the **429** for TPM. Expose it as `AI_GATEWAY_TOKEN_QUOTA` / `AI_GATEWAY_TOKEN_QUOTA_PERIOD` when FinOps sets a number.

### 6.3 Capacity alignment (must resolve before enabling)
The gpt-5.4 deployment has **100K TPM**. One agent at 75k fits, but five agents at 75k each is a theoretical 375K TPM, so Foundry can still throttle (429) when several agents peak together.
- Raise `AZURE_OPENAI_DEPLOYMENT_CAPACITY` (§5.1) toward **375** if subscription quota allows and all agents must be able to use their full allowance at once.
- Treat the APIM limit as a **per-agent fairness cap**, not a capacity guarantee.
- The backend circuit breaker (3× 429/5xx in 1 min → trip 1 min, honours `Retry-After`) protects the deployment either way.

### 6.4 Deploying and changing the limit
- **Initial deploy:** `azd env set ENABLE_AI_GATEWAY true && azd provision`. This creates APIM, the API and the policy with 75000.
- **Change the limit:** `azd env set AI_GATEWAY_TOKENS_PER_MINUTE <n> && azd provision`. Only the policy resource changes.
- **Out-of-band hotfix:** edit the policy in the portal. The next `azd provision` overwrites it, by design.

---

## 7. Agent code change

Add one shared helper and use it in all five `agents/*/main.py`. Each agent folder is its own Docker build context, so copy the helper into each folder, or vendor it via a build step, as is done today for `schemas.py`.

```python
# model_client.py
import os

def build_chat_client(credential, project_endpoint: str, model: str):
    gateway = os.environ.get("AI_GATEWAY_OPENAI_BASE_URL", "").strip()
    if not gateway:
        from agent_framework.foundry import FoundryChatClient   # current behaviour
        return FoundryChatClient(project_endpoint=project_endpoint, model=model, credential=credential)
    from agent_framework.openai import OpenAIChatClient
    # credential= makes the client request cognitiveservices tokens and send them as Bearer
    return OpenAIChatClient(model=model, base_url=gateway.rstrip("/") + "/", credential=credential)
```

- The credential is the agent's existing `ManagedIdentityCredential(client_id=AZURE_CLIENT_ID)` or `DefaultAzureCredential`. The token's `appid` is the hosted-agent identity, which the allowlist matches.
- `model` stays the deployment name (`gpt-5.4`). The OpenAI v1 surface takes it in the request body.
- **Verified locally** (agent-framework-core 1.24.0, agent-framework-openai 1.18.0, openai 3.46.0) against a stub gateway, in `scripts/test_ai_gateway.py`:
  - `OpenAIChatClient` is the Responses-API client. It sends `POST /openai/v1/responses?api-version=preview` with the model in the body and `Authorization: Bearer <token for https://cognitiveservices.azure.com/.default>`.
  - Passing a synchronous `api_key=<token provider>` **fails** with this SDK version (`object str can't be used in 'await' expression`), so the implementation passes `credential=`.
  - `agent-framework-openai>=1.18.0,<2` is pinned in every agent's `requirements.txt`.
- Tracing: keep the OTEL setup. Spans now show the gateway host, and APIM's W3C correlation joins the traces in App Insights.

---

## 8. Identity and RBAC

| Principal | Scope | Role / grant | Created by |
|---|---|---|---|
| APIM system MI | Foundry account | **Cognitive Services OpenAI User** (`5e0bd9bd-7b93-4f28-af87-19fc36ad61bd`) | Bicep (`apim-ai-gateway.bicep`) |
| Hosted-agent identities (×4–5) | APIM named value `ai-gateway-allowed-client-ids` | Allowlist entry (client ID) | `postdeploy` → `sync_ai_gateway.py` |
| API Center system MI | APIM instance | **API Management Service Reader Role** (`71522526-b88f-4d52-b57f-d31fc3546d0d`) | Bicep (`api-center.bicep`) |
| Deployer | Resource group | Contributor (already required); RBAC write for the two Bicep role assignments (already required for existing assignments) | — |
| Deployer | APIM | API Management Service Contributor (inherited from RG Contributor) to update the named value | — |

**Bypass prevention (phase 2):**
- Hosted-agent identities currently get **Foundry User** on the account and project from `grant_agent_rbac.py`, so an agent could still call the model directly.
- Once the spike confirms that hosted-agent runtime operations don't need model data-plane rights on the account, narrow the grant to the **project scope only**.
- Then confirm that a direct `/openai/v1/responses` call from an agent identity returns 401/403.
- Until then, the gateway is **enforced by configuration** (`AI_GATEWAY_OPENAI_BASE_URL`), not by RBAC. Record this in the risk register.

**Re-publish note** (from the research doc): publishing to Teams/M365 creates a new agent identity. The allowlist sync must re-run after every publish, the same as the existing RBAC re-grant.

---

## 9. API Center

**Deploys** (`infra/modules/api-center.bicep`, preview API `2024-06-01-preview`):
- The service, with SKU **Free** and a system MI, in `API_CENTER_LOCATION`. The default is `eastus`.
  - API Center regions: Australia East, Canada Central, Central India, East US, France Central, Sweden Central, UK South and West Europe.
  - Choose Sweden Central when the template runs in `swedencentral`.
- The `default` workspace and an `external-mcp` environment (`kind: production`).
- One API of `kind: 'mcp'` per remote MCP server, each with:
  - version `v1-0` (lifecycle `production`)
  - a deployment pointing at the server URL
- **APIM sync** (`apiSources`, preview ARM API; the feature itself is GA):
  - Continuous one-way import of the `foundry-openai-v1` API, with target lifecycle `development`, plus an auto-created *Azure API Management* environment.
  - Usually lands in minutes; can take **up to 24 h**.
  - Synced APIs can't be deleted while the integration exists.

**Lifecycle demo** (research feature #4, SOW criterion b):
- Promote the synced gateway API from `development` → `production`, either in the portal or with `az apic api version update --lifecycle-stage production`.
- Show the MCP registry endpoint `API_CENTER_MCP_REGISTRY_URL`, which VS Code / GitHub Copilot can consume.

**Known gaps:**
- `kind: 'mcp'` and the `sku` property are outside the published schema. They work with the RP and with official samples (AI-Gateway `mcp-registry-apic`).
- No documented Bicep or CLI exists for **agent (A2A) assets**. Register agents through APIM A2A import or the portal in a later spec.
- **Free tier limits:** 200 APIs, 5 versions per API, **1 linked API source**, no SLA or support. Upgrade to Standard (free with StandardV2/PremiumV2 APIM) for production.

---

## 10. Observability and FinOps

- **App Insights custom metrics** (namespace `ai-gateway`): Total, Prompt and Completion tokens by `Agent Client ID`. Add a workbook tile; map client IDs to agent names with a static lookup the postdeploy hook writes.
- **Log Analytics:**
  - `ApiManagementGatewayLogs` holds status codes, 401/403/429 counts and latency.
  - `ApiManagementGatewayLlmLog` holds per-request token usage and model. Prompt and completion **message bodies stay off**: the API diagnostic `largeLanguageModel` message logging isn't set, and body bytes are 0.
- **Starter KQL, 429s and tokens per agent:**

```kusto
ApiManagementGatewayLlmLog
| where TimeGenerated > ago(1h)
| summarize PromptTokens = sum(PromptTokens), CompletionTokens = sum(CompletionTokens) by DeploymentName, bin(TimeGenerated, 5m)
```

```kusto
ApiManagementGatewayLogs
| where TimeGenerated > ago(1h) and ApiId == "foundry-openai-v1"
| summarize count() by ResponseCode, bin(TimeGenerated, 5m)
```

- *Verify the LLM log column names against your workspace schema in the spike; they aren't in this repo's tests.*
- **Alert:** a log alert on 429 rate over 5% for 15 min, plus the token-spike alert (research feature #15, pending SOW change). Cost Management anomaly detection lags about 36 h, so the alert has to come from Azure Monitor.

---

## 11. Kill switch (gateway layer)

| Action | Effect | Reversal |
|---|---|---|
| Remove an agent's client ID from `ai-gateway-allowed-client-ids` (`az apim nv update …`) | That agent's model calls get **403** immediately; other agents are unaffected | Re-add the ID, or re-run the postdeploy sync |
| Set `AI_GATEWAY_TOKENS_PER_MINUTE` very low and provision | All agents are throttled (429) | Restore the value |
| Disable the Entra agent identity | No tokens, so 401 at the gateway and failures everywhere else | Re-enable the identity |

---

## 12. Implementation plan

| Step | Work | Done when |
|---|---|---|
| 1 | Spike (½–1 day): deploy the drafts into a scratch env; run one hosted agent with `OpenAIChatClient` through APIM | Agent answers through APIM; `appid` claim = agent client ID; streaming works; 429 observed with a temporary 1k TPM limit |
| 2 | ✅ Modules in `infra/modules/`, policy in `infra/policies/`, wired into `main.bicep`, `main.parameters.json` and `abbreviations.json` | `azd provision --preview` clean with the flags off (no diff) and on |
| 3 | `model_client.py` + agent changes + `requirements.txt`; `azure.yaml` env | `docker compose up` (gateway off) unchanged; `azd deploy` with gateway on works |
| 4 | ✅ `scripts/sync_ai_gateway.py` (allowlist + Genie API Center registration) + postdeploy wiring (pwsh and sh) + `check_agents.py` smoke test | Fresh `azd up` with flags on goes green end-to-end |
| 5 | API Center module + Genie MCP registration in the hook | MCP servers and the synced API are visible in API Center; one lifecycle promotion done |
| 6 | Docs: README feature row, `docs/architecture.md`, `docs/DeploymentGuide.md` (flags, cost, soft-delete purge), `docs/troubleshooting.md` (401/403/429) | Reviewed |
| 7 | Bypass hardening (§8) after spike evidence | Direct model call from an agent identity is denied |

---

## 13. Acceptance criteria and tests

1. With `ENABLE_AI_GATEWAY=false`, `azd provision --preview` shows **no changes** compared with main, and agents use `FoundryChatClient`.
2. With the flag on, a full prior-auth review completes, and APIM `ApiManagementGatewayLogs` shows Responses calls from **each** of the four agents.
3. A request with no token → **401**; a valid token from a non-allowlisted identity (deployer) → **403**; an allowlisted agent → **200**.
4. With a temporary limit (`AI_GATEWAY_TOKENS_PER_MINUTE=1000`), a review produces **429 + Retry-After** for the agent that exceeds it, and the other agents are unaffected (per-key counters). Restoring **75000** clears it.
5. App Insights shows `ai-gateway` token metrics split by `Agent Client ID`.
6. No prompt or completion text appears in App Insights or Log Analytics gateway logs.
7. Removing one client ID from the allowlist stops only that agent within one minute.
8. API Center lists the five MCP servers (and Genie when Databricks is on) and the synced `foundry-openai-v1` API. One lifecycle stage change is recorded.
9. `azd down --purge` removes APIM and API Center without leaving a soft-deleted APIM that blocks the next `azd up`.

---

## 14. Risks and open questions

| # | Risk / question | Mitigation |
|---|---|---|
| R1 | Hosted-agent tokens may not carry the expected `appid`/`azp`, or a different identity may arrive | Spike step 1 logs the claims (diagnostic `trace` policy); fall back to `oid` |
| R2 | Agents can still reach Foundry directly while they hold Foundry User on the account | §8 phase 2 RBAC narrowing; meanwhile monitor Foundry direct-call metrics |
| R3 | `agent-framework` OpenAI client API differs from the sample | Pin the version; spike step 1 |
| R4 | Five agents at 75k each (375K) exceed the shared 100K deployment capacity | §6.3: raise capacity or accept deployment-level 429s with the circuit breaker |
| R5 | Preview surfaces: API Center `apiSources` / `kind: mcp` ARM, and the Foundry AI Gateway portal integration | Isolated in the optional API Center module; the core gateway uses only GA APIM features |
| R6 | Gateway is a new single point of failure (Architecture Center guidance) | BasicV2 SLA; StandardV2 with ≥2 units for production; the flag allows instant rollback (empty `AI_GATEWAY_OPENAI_BASE_URL`) |
| R7 | Using the `cognitiveservices` audience means a gateway token is also a valid Foundry token | Acceptable once R2 is fixed. Option: an Entra app registration `api://ai-gateway` with an app role (tenant admin action) as the dedicated audience |
| Q1 | Should the backend → agent invocation also go through APIM (A2A / agent API import)? | Spec 003 candidate |
| Q2 | Do we adopt the Foundry AI Gateway portal integration later, for prompt agents and project-level quotas? | Revisit when hosted-agent support is documented |
| Q3 | What monthly token quota per agent does FinOps want? | Add `token-quota` (§6.2) |

---

## 15. References (Microsoft Learn and official samples, checked 2026-11-06)

**API Management AI gateway**
- AI gateway capabilities: https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities
- `llm-token-limit`: https://learn.microsoft.com/azure/api-management/llm-token-limit-policy
- `llm-emit-token-metric`: https://learn.microsoft.com/azure/api-management/llm-emit-token-metric-policy
- Import a Microsoft Foundry API: https://learn.microsoft.com/azure/api-management/azure-ai-foundry-api
- Authenticate and authorize AI APIs: https://learn.microsoft.com/azure/api-management/api-management-authenticate-authorize-ai-apis
- LLM logging: https://learn.microsoft.com/azure/api-management/api-management-howto-llm-logs
- Bicep reference: https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service

**Foundry**
- AI Gateway in Foundry: https://learn.microsoft.com/azure/foundry/configuration/enable-ai-api-management-gateway-portal
- Enforce model limits: https://learn.microsoft.com/azure/foundry/control-plane/how-to-enforce-limits-models
- Bring your own model / AI gateway connections: https://learn.microsoft.com/azure/foundry/agents/how-to/ai-gateway
- Hosted agents with Agent Framework: https://learn.microsoft.com/azure/foundry/how-to/develop/framework-hosted-agents

**API Center**
- Overview and regions: https://learn.microsoft.com/azure/api-center/overview
- Sync from APIM: https://learn.microsoft.com/azure/api-center/synchronize-api-management-apis
- Register MCP servers: https://learn.microsoft.com/azure/api-center/register-discover-mcp-server
- Limits: https://learn.microsoft.com/azure/azure-resource-manager/management/azure-subscription-service-limits#azure-api-center-limits

**azd**
- `azure.yaml` schema: https://learn.microsoft.com/azure/developer/azure-developer-cli/azd-schema
- Layered provisioning (beta): https://learn.microsoft.com/azure/developer/azure-developer-cli/layered-provisioning
- Environment variables: https://learn.microsoft.com/azure/developer/azure-developer-cli/manage-environment-variables
- Feature stages: https://github.com/Azure/azure-dev/blob/main/cli/azd/docs/feature-stages.md

**Architecture**
- Generative AI gateway guide: https://learn.microsoft.com/azure/architecture/ai-ml/guide/generative-ai-gateway
- Baseline Foundry chat: https://learn.microsoft.com/azure/architecture/ai-ml/architecture/baseline-microsoft-foundry-chat

**Samples**
- AI-Gateway labs (`ai-foundry-hosted-agents`, `token-rate-limiting`, `finops-framework`, `mcp-registry-apic`): https://github.com/Azure-Samples/AI-Gateway
- Hosted agent via AI gateway (azd): https://github.com/Azure-Samples/simple-foundry-hosted-agent-python-aigateway
- AI Citadel governance hub (APIM + API Center, azd): https://github.com/Azure-Samples/ai-hub-gateway-solution-accelerator
- Foundry connection Bicep (`apim`, `model-gateway`, `project-ai-gateway`): https://github.com/microsoft-foundry/foundry-samples/tree/main/infrastructure/infrastructure-setup-bicep/01-connections
