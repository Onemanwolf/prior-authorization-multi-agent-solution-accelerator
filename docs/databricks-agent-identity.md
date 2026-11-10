# Foundry Agent Identity in Azure Databricks

This guide lists everything a Microsoft Foundry agent's identity needs before it can call the Databricks Genie MCP server, with or without the APIM AI gateway. It applies to:

- **Hosted agents** (for example `supply-pricing-agent`), which call Genie from their own code.
- **Prompt agents** created in the Foundry portal (for example `data-agent-test`), which call Genie through a custom MCP tool.

For the full Databricks experiment (workspace, data, Genie space), see [databricks-construction-supply.md](databricks-construction-supply.md). For the gateway itself, see [Specs/001-apim-ai-gateway-api-center.md](../Specs/001-apim-ai-gateway-api-center.md).

## Checklist

| # | Where | What | Who can do it |
|---|---|---|---|
| 1 | Foundry | Get the agent's **instance identity** client ID | Anyone with read access to the project |
| 2 | Databricks | Add the identity as a **service principal** and assign it to the workspace | Workspace admin (account admin if the account principal doesn't exist yet) |
| 3 | Databricks | Turn on **Databricks SQL access** for the service principal | Workspace admin |
| 4 | Databricks | **Can Run** on the Genie space | Genie space owner or workspace admin |
| 5 | Databricks | **Can Use** on the Genie space's SQL warehouse | Warehouse owner or workspace admin |
| 6 | Databricks | `USE CATALOG`, `USE SCHEMA`, `SELECT` on the Genie space's tables | Catalog owner or metastore admin |
| 7 | APIM | Add the client ID to the gateway allowlist (only when routing through the AI gateway) | APIM contributor |
| 8 | Foundry | Point the agent's tool at the gateway with **Agent identity** authentication (prompt agents only) | Project contributor |

Repeat steps 1–7 whenever the agent gets a new identity. **Publishing an agent (to Teams or Microsoft 365) creates a new identity.**

## 1. Get the Agent's Identity

Every Foundry agent has two identities. Use the **instance identity**.

| Field | Use it? | What it is |
|---|---|---|
| `instance_identity.client_id` | **Yes** | The identity the agent runs as and requests tokens with. Databricks and the gateway allowlist need this ID. |
| `blueprint.client_id` | No | The blueprint the instance identity is created from. Tokens are never issued to it for tool calls. |

For the Foundry Agent Service identity, the client ID and principal (object) ID are the same value.

**In the Foundry portal:** open **Agents**, select the agent, and look for **Agent identity** (or *Instance identity*) in its details panel. Ignore *Blueprint*.

**From the command line (any agent):**

```powershell
$project = "https://<foundry-account>.services.ai.azure.com/api/projects/<project-name>"
az rest --method get --url "$project/agents/<agent-name>?api-version=v1" `
  --resource https://ai.azure.com `
  --query "versions.latest.instance_identity.client_id" -o tsv
```

**Hosted agents deployed by this template** also show it here:

```powershell
Push-Location agents/supply
azd ai agent show --output json   # read instance_identity.client_id
Pop-Location
```

## 2. Add the Service Principal to the Workspace

The Databricks settings are in the **workspace web app**, not on the Azure portal resource page.

1. Open the workspace: in the Azure portal select **Launch Workspace**, or browse to `https://adb-<workspace-id>.<n>.azuredatabricks.net`.
2. Select your **profile icon** (top right), then **Settings**.
3. Under **Workspace admin**, select **Identity and access**.
   - If **Workspace admin** is missing, your account isn't a workspace admin. Ask an admin to do steps 2–6.
4. Next to **Service principals**, select **Manage**, then **Add service principal**.
5. If the principal already exists in the account, select it. Otherwise select **Add new**, choose **Microsoft Entra ID managed**, enter the agent's **instance identity client ID** as the *Application ID*, and give it a recognizable name (for example `Foundry data-agent-test`).
6. Make sure it's added as a **regular user**, not an admin, and that it is **active**.

With identity federation, adding the principal to the account and assigning it to the workspace are separate steps; the UI does both.

## 3. Turn On Databricks SQL Access

1. Open the service principal (**Settings → Identity and access → Service principals**).
2. Select **Configurations**.
3. Turn on **Databricks SQL access** and save. Don't grant cluster or pool creation.

Genie runs SQL on a warehouse, so without this entitlement every Genie call fails even when the other grants are in place.

## 4. Grant Can Run on the Genie Space

1. Open **Genie** and select the space the agent uses (its ID is the last part of the MCP URL `.../api/2.0/mcp/genie/<space-id>`).
2. Select **Share**.
3. Add the service principal with **Can Run**.

## 5. Grant Can Use on the SQL Warehouse

1. Open **SQL Warehouses** and select the warehouse attached to the Genie space (shown in the space's settings).
2. Select **Permissions**.
3. Add the service principal with **Can Use**.

## 6. Grant Unity Catalog Access to the Tables

In **Catalog**, grant `USE CATALOG` on the catalog, `USE SCHEMA` on the schema, and `SELECT` on each table the Genie space uses. For this sample, run the following in the SQL editor as a catalog owner or metastore admin. The principal is referenced by its application (client) ID:

```sql
GRANT USE CATALOG ON CATALOG construction_supply TO `<agent-client-id>`;
GRANT USE SCHEMA ON SCHEMA construction_supply.construction_supply_demo TO `<agent-client-id>`;
GRANT SELECT ON TABLE construction_supply.construction_supply_demo.branches    TO `<agent-client-id>`;
GRANT SELECT ON TABLE construction_supply.construction_supply_demo.inventory   TO `<agent-client-id>`;
GRANT SELECT ON TABLE construction_supply.construction_supply_demo.order_lines TO `<agent-client-id>`;
GRANT SELECT ON TABLE construction_supply.construction_supply_demo.prices      TO `<agent-client-id>`;
GRANT SELECT ON TABLE construction_supply.construction_supply_demo.products    TO `<agent-client-id>`;
```

For a real-data Genie space, grant `SELECT` only on the tables that space uses.

### Automated Alternative (Steps 2–6)

For the template's hosted supply agent, one command does steps 2–6. It adds permissions without replacing existing ones, and it refuses to grant anything if the Genie space's tables differ from the five demo tables:

```powershell
python scripts/grant_agent_rbac.py --agent supply-pricing-agent --databricks
```

It runs the Databricks CLI as the signed-in user, so that user still needs the admin rights listed in the checklist.

## 7. Allow the Identity Through the AI Gateway

Skip this step if the agent calls Databricks directly.

The APIM routes (`foundry-openai-v1` for models and `databricks-genie-mcp` for Genie) reject any caller whose token isn't on the allowlist, with HTTP 403 `AgentNotAuthorized`. The allowlist is the APIM named value `ai-gateway-allowed-client-ids`, a comma-separated list of client IDs.

- **Hosted agents:** the `postdeploy` hook (`scripts/sync_ai_gateway.py`) adds them automatically. To re-run it: `python scripts/sync_ai_gateway.py`.
- **Prompt agents and other identities:** add them by hand:

```powershell
$rg = "<resource-group>"; $apim = "<apim-name>"
$ids = az apim nv show -g $rg -n $apim --named-value-id ai-gateway-allowed-client-ids --query value -o tsv
az apim nv update -g $rg -n $apim --named-value-id ai-gateway-allowed-client-ids --value "$ids,<agent-client-id>"
```

> **Note:** the `postdeploy` hook rebuilds this list from the hosted agents only, so a later `azd up` or `azd deploy` removes IDs added by hand. Add them again after each deployment.

Removing an ID from the list immediately blocks that agent at the gateway. This is the per-agent kill switch.

## 8. Configure the Foundry Tool (Prompt Agents)

Only MCP tools that are **custom** and use **Microsoft Entra** authentication can be routed through the gateway. The catalog **Azure Databricks Genie** tool uses managed OAuth (`oAuthProvider: managed`), and Foundry never routes managed OAuth tools through the gateway, even after the gateway is connected.

1. In Foundry, open **Tools → Add tool → Custom → Model Context Protocol (MCP)**.
2. **Endpoint:** the APIM Genie route, `https://<apim-name>.azure-api.net/mcp/databricks-genie/<space-id>`. Get it with `azd env get-value AI_GATEWAY_GENIE_MCP_URL`.
3. **Authentication:** **Agent identity** (Microsoft Entra), audience `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d` (the Azure Databricks resource).
4. Attach the tool to the agent and remove the catalog Databricks tool.

**Access changes with this setup.** With managed OAuth, Genie answers with each signed-in user's own Databricks permissions. With Agent identity, every user gets the agent's permissions. Use Agent identity only when all users of the agent may see the same data.

Hosted agents don't need this step. The template sets `AI_GATEWAY_GENIE_MCP_URL` on the supply agent when the gateway is enabled, and the agent uses that URL instead of calling Databricks directly.

## Verify

**Gateway route** (expects 401 without a token, 403 for a caller not on the allowlist):

```powershell
$url = azd env get-value AI_GATEWAY_GENIE_MCP_URL
$body = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
curl.exe -s -o NUL -w "%{http_code}`n" -X POST -H "Content-Type: application/json" -d $body $url
```

**Agent calls,** in the Log Analytics workspace (**Logs**). From the APIM resource's *Logs* screen only APIM tables are available, so run these from the workspace itself:

```kql
// Genie calls by route: "via APIM" or "DIRECT"
AppDependencies
| where TimeGenerated > ago(24h)
| where Name has "genie"
| extend Route = iff(Target has "azure-api.net", "via APIM", "DIRECT")
| summarize Calls = count(), Last = max(TimeGenerated) by AppRoleName, Route
| order by Last desc
```

```kql
// What APIM forwarded to Databricks
ApiManagementGatewayLogs
| where TimeGenerated > ago(24h) and ApiId == "databricks-genie-mcp"
| project TimeGenerated, ResponseCode, BackendResponseCode, Backend = tostring(split(BackendUrl, "?")[0])
| order by TimeGenerated desc
```

In the Application Insights *Logs* screen, the first query uses `dependencies`, `timestamp`, `cloud_RoleName`, `name` and `target` instead.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| **Settings** or **Workspace admin** is missing in Databricks | You're on the Azure portal resource page (select **Launch Workspace**), or you aren't a workspace admin. |
| Gateway returns 401 `Unauthorized` | No token, or a token for the wrong audience. The tool must request audience `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d`. |
| Gateway returns 403 `AgentNotAuthorized` | The client ID isn't in `ai-gateway-allowed-client-ids` (step 7). Check that you used the **instance** identity, not the blueprint. |
| Databricks returns 401 or 403 `PERMISSION_DENIED` through the gateway | The gateway let the call through but Databricks refused it. Recheck steps 2–6 for the same client ID. The supply agent logs the token's identity IDs with the denial. |
| Genie answers "no access to table" | Missing `SELECT` or `USE SCHEMA` (step 6), or the warehouse grant (step 5). |
| Worked yesterday, fails after publishing | Publishing created a new identity. Repeat steps 1–7 with the new client ID. |
| Calls still show `DIRECT` in the query above | A catalog (managed OAuth) tool, or an older hosted-agent version that predates the gateway URL. Replace the tool (step 8) or delete the old versions. |

## References

- [Govern MCP tools by using an AI gateway (Foundry)](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/governance)
- [Foundry hosted agents](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents)
- [Manage service principals (Azure Databricks)](https://learn.microsoft.com/en-us/azure/databricks/admin/users-groups/manage-service-principals)
- [Entitlements (Azure Databricks)](https://learn.microsoft.com/en-us/azure/databricks/security/auth/entitlements)
- [Unity Catalog privileges](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/manage-privileges/)
- [Use Azure Databricks Genie in Microsoft Foundry](https://learn.microsoft.com/en-us/azure/databricks/integrations/microsoft-foundry)
- [AppDependencies table reference](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appdependencies)
