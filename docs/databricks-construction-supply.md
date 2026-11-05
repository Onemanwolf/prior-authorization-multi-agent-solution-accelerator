# Construction Supplies Databricks Experiment

This branch adds an optional Azure Databricks workspace, a Databricks bundle for synthetic construction-supplier data, a curated Genie Agent, and an isolated Foundry supply-pricing agent. The existing healthcare prior-authorization agents and workflow are not changed. Set `ENABLE_DATABRICKS=true` to opt in to Databricks bootstrap during root `azd up`; it remains disabled by default.

## Architecture and Research

The root `postprovision` hook first authenticates to the workspace with the Databricks CLI, then deploys the data bundle, explicitly runs the seed job, deploys the Genie bundle, and sets `DATABRICKS_READY=true` only when these steps complete. Root `postdeploy` then invokes the nested supply-agent azd project only when readiness and the Genie ID are present. If Databricks is disabled, its CLI is missing, workspace authentication fails, or bootstrap fails, the optional agent is skipped and the prior-authorization deployment continues.

Bicep provisions the Azure Databricks workspace, a dedicated ADLS Gen2 account and `unity-catalog` container, and an access connector with a system-assigned managed identity. The connector receives Storage Blob Data Contributor on that dedicated account. Shared-key and anonymous blob access are disabled. The demo storage endpoint remains publicly reachable for serverless compute; network-isolated production deployments require additional serverless networking configuration.

The azd `postprovision` hook creates or verifies a connector-backed Unity Catalog storage credential, then uses Databricks Declarative Automation Bundles to create an external location, a catalog with an explicit managed storage root, the SQL warehouse, and seed job. It explicitly runs the synthetic seed and deploys the Genie Agent. The external-location reference orders catalog creation after storage validation. File events are disabled, so no queue or Event Grid permissions are needed. `azd package` alone does not deploy workspace bundles. No metastore-level storage root is required or changed.

The supply agent calls the single-space Genie Agent MCP endpoint:

```text
https://<workspace-hostname>/api/2.0/mcp/genie/<genie-space-id>
```

The Genie Agent MCP server is **Public Preview** and requires a workspace administrator to enable **Managed MCP Servers**. It is read-only and scoped to one curated Genie Agent. Calls bypass Unity Gateway, so Unity Gateway policies, grants, tracing, and guardrails do not apply. This is intentionally narrower than a raw SQL MCP. Databricks now recommends the generally available Genie One MCP (`system.ai.genie_one_mcp`) for workspace-wide ontology-grounded business analytics; Genie One is a different integration and is not interchangeable with the single-space endpoint used in this experiment.

The agent obtains an Azure Databricks audience token using its Foundry managed identity and attaches it only to requests for the configured workspace origin. No Databricks client secret or Key Vault secret is used. Databricks' Python SDK does not yet implement native Azure managed-identity authentication, so this experiment uses Azure Identity for token acquisition and the MCP HTTP client for the REST call. After deploying the supply agent, `postdeploy` runs `grant_agent_rbac.py --agent supply-pricing-agent --databricks`. This reuses or registers the deployed client identity, assigns USER workspace access and an explicit Databricks SQL access entitlement, and adds CAN RUN on Genie, CAN USE on its warehouse, USE CATALOG, USE SCHEMA, and SELECT on only the five expected demo tables. Existing user permissions are preserved. The deployer requires Databricks administrative access and authority to grant these privileges; the agent is not granted administrator access. Azure RBAC alone does not grant Databricks data permissions.

## Synthetic Data

Every row is marked `is_mock=true`; the vendor, quotes, history, inventory and delivery charges are invented. The dataset includes 12 construction material SKUs, three customer tiers, quantity breaks, effective-date windows, selling/base units, two fictional branches, inventory snapshots, freight thresholds, and 90 sample order lines. Historical order totals are not repriced from the current quote table.

The data bundle creates the `construction_supply` Unity Catalog catalog; the seed job creates its dedicated `construction_supply_demo` schema. Seed writes are deterministic, repeatable Delta `MERGE`s. The job validates a `demo.dataset=construction-supply-v1` ownership marker before modifying any table; it refuses to overwrite an unrelated table. The azd hook explicitly runs the seed job after bundle deployment.

Genie instructions require one date/tier/quantity price row, disallow stacked tiers, distinguish sell units from base units, report inventory's snapshot date, calculate freight once per branch shipment, and exclude tax and installation. This is a demo, not a quotation system; no real supplier API, live availability, tax logic, or purchase capability is included.

## Prerequisites

- An Azure subscription with `Microsoft.Databricks` and `Microsoft.Storage` provider registration and permission to create a Premium workspace, storage account, and access connector. The deployer needs permission to assign Azure RBAC on the dedicated storage account (Owner or User Access Administrator with resource-creation permissions).
- A Databricks workspace with a Unity Catalog metastore attached and Genie/partner-powered AI enabled. The deployment identity needs `CREATE STORAGE CREDENTIAL`, `CREATE EXTERNAL LOCATION`, and `CREATE CATALOG` on the metastore, or metastore-admin privileges. It owns the credential and external location it creates; reusing them requires the corresponding privileges, including `CREATE EXTERNAL LOCATION` on the credential and `CREATE MANAGED STORAGE` on the location. Non-account-admin deployers also require Azure Contributor access on the access connector. Azure workspace ownership alone does not grant metastore privileges.
- A workspace administrator to enable the **Managed MCP Servers** Public Preview from the profile menu > Previews. The installed CLI exposes no supported toggle for this preview. The postdeploy identity/grant automation must run with workspace-admin access and authority to grant privileges on the demo catalog, schema, and tables.
- Databricks CLI 1.3.0 or later installed locally. The Azure CLI identity used by the postprovision hook must be allowed to deploy bundles, create demo-schema objects, and run the seed job; the hook selects `DATABRICKS_AUTH_TYPE=azure-cli`.
- A Foundry project and model deployment already provisioned by the existing root template.

Workspace creation, SQL compute, Genie queries, and Foundry Hosted Agent usage can incur charges. The warehouse starts stopped, is limited to one 2X-Small cluster, and has a five-minute auto-stop. Starting the warehouse for Genie can still incur usage. Review current Azure and Databricks pricing before deploying. Destroy demo resources when finished; `azd down` for the application does not remove workspace objects deployed by a Databricks bundle.

## Permissions and Identity Checklist

Azure resource permissions, Databricks workspace membership, workspace entitlements, and Unity Catalog grants are separate controls. A successful container deployment does not prove that the agent can query Genie.

| Identity | Scope | Required permission or setting | Purpose |
| --- | --- | --- | --- |
| Azure infrastructure deployer | Target resource group/subscription | Contributor or equivalent resource-creation permissions | Create the workspace, ADLS Gen2 account, and access connector |
| Azure RBAC administrator/deployer | Dedicated storage account and Foundry account | Owner, or resource-creation permissions plus User Access Administrator/RBAC administration rights at the required scope | Assign the connector's storage role and the agent's Foundry role |
| Databricks data-bundle deployer | Unity Catalog metastore | CREATE STORAGE CREDENTIAL, CREATE EXTERNAL LOCATION, CREATE CATALOG, or applicable metastore-admin authority | Create storage credentials, locations, and catalog |
| Databricks data-bundle deployer | Storage credential/external location | Ownership or CREATE EXTERNAL LOCATION on the credential; CREATE MANAGED STORAGE on the location | Configure managed catalog storage |
| Databricks data-bundle deployer | Access connector in Azure | Contributor for non-account-admin callers creating a managed-identity storage credential | Register the connector-backed credential |
| Databricks workspace administrator | Workspace | Enable Managed MCP Servers; register/assign the agent principal and manage its entitlements | Make the MCP endpoint available to the agent |
| Databricks object owner/grant administrator | Genie, warehouse, catalog, schema, and tables | Authority to grant the permissions listed below | Apply the agent's narrowly scoped read access |
| Databricks access connector identity | Dedicated ADLS Gen2 account | Storage Blob Data Contributor | Read/write managed table files; this is not the Foundry agent identity |
| Foundry project identity | Agent image registry | AcrPull, or the appropriate repository-reader role for an ABAC-enabled registry | Pull the hosted agent container image |
| Foundry supply-agent identity | Foundry account | Foundry User, assigned by the sample's RBAC helper | Foundry data-plane access; not a Databricks grant |
| Foundry supply-agent identity | Databricks workspace | USER assignment; active Microsoft Entra-managed principal | Allow this identity into the workspace, without workspace-admin privileges |
| Foundry supply-agent identity | Databricks workspace entitlement | Databricks SQL access (`databricks-sql-access`) | Use shared Genie and SQL warehouse features |
| Foundry supply-agent identity | One Genie space | CAN RUN | Query the curated Genie space |
| Foundry supply-agent identity | Genie's SQL warehouse | CAN USE | Execute the SQL used by Genie |
| Foundry supply-agent identity | `construction_supply` catalog | USE CATALOG | Resolve the demo catalog |
| Foundry supply-agent identity | `construction_supply.construction_supply_demo` schema | USE SCHEMA | Resolve the demo schema |
| Foundry supply-agent identity | Five attached demo tables | SELECT on each table | Read products, prices, branches, inventory, and order lines |

The runtime agent does not need CREATE CATALOG, CREATE SCHEMA, MODIFY, storage credentials, unrestricted cluster creation, account-admin, or workspace-admin privileges. Storage-file access is handled by Unity Catalog through the access connector.

Use the deployed agent's `instance_identity.client_id` when registering a Microsoft Entra-managed Databricks principal. Use `instance_identity.principal_id` for Azure RBAC. Do not substitute the Foundry project identity, the blueprint identity, your interactive user identity, or the access connector identity. Recheck the deployed identity after a new agent version is created.

New Databricks principals must have an explicit access entitlement. Do not rely on membership in the `users` group: the 2026 workspace-entitlement migration removed that inherited default for newly added principals. See [Manage entitlements](https://learn.microsoft.com/en-us/azure/databricks/security/auth/entitlements) and [Manage service principals](https://learn.microsoft.com/en-us/azure/databricks/admin/users-groups/manage-service-principals).

## Automated Deployment

Install the Databricks CLI and sign in to Azure CLI as an identity allowed to deploy Databricks bundles and run the seed job. Then run the existing deployment from the repository root:

```powershell
azd env set ENABLE_DATABRICKS true
azd up
```

The postprovision hook uses the Bicep `DATABRICKS_HOST`, `DATABRICKS_ACCESS_CONNECTOR_ID`, and `DATABRICKS_STORAGE_URL` outputs and existing Azure CLI authentication. It passes storage settings as `BUNDLE_VAR_storage_credential_name` and `BUNDLE_VAR_storage_url`. The credential name is derived from the connector ID; reruns reuse a matching credential and refuse a conflicting identity or read-only credential. Creation and external-location validation are not bypassed. New Azure RBAC assignments can take time to propagate; if storage validation fails immediately after provisioning, retry after propagation. Workspace provisioning alone does not grant Databricks data-plane privileges.

During `azd up`, `postprovision` deploys the data bundle, runs its seed job, and deploys Genie. After the four existing Foundry agents deploy, `postdeploy` deploys the nested supply agent only if `DATABRICKS_READY=true` and a Genie ID exists. The hooks do not install the Databricks CLI or sign in interactively. If the CLI is missing, authentication fails, or bootstrap fails, the clinical deployment continues and the supply agent is skipped. `DATABRICKS_READY` records bootstrap success, not the subsequent Databricks identity grants.

```powershell
azd hooks run postprovision
```

After `azd up`, verify the five tables in `<catalog>.construction_supply_demo`, `is_mock`, sample prices, and the Genie Agent. The bundle hook uses the fixed `dev` target and `construction_supply` default catalog; review these in the bundle files before opting in. To inspect the bundles manually:

```powershell
Set-Location databricks
databricks bundle summary --target dev
Set-Location genie
databricks bundle summary --target dev
Set-Location ../..
```

The agent identity is created by Foundry during hosted-agent deployment. The postdeploy helper applies the Databricks-side identity and grants automatically, then checks MCP initialization with the deployer's identity. It sets `SUPPLY_AGENT_DATABRICKS_ACCESS_READY=true` only if these steps succeed. If Managed MCP Servers is disabled, it leaves this flag false and prints admin guidance. `SUPPLY_AGENT_DEPLOYED=true` means the container was published, not that MCP access is ready. Neither flag replaces a live query with the agent's own identity. Enable the preview, then rerun the helper from the repository root without reprovisioning or rebuilding containers:

```powershell
python scripts/grant_agent_rbac.py --agent supply-pricing-agent --databricks
```

For a newly provisioned workspace, the first run can finish before its administrator enables the preview. In the Databricks workspace, open the profile menu > **Previews** and turn on **Managed MCP Servers**, then rerun the helper above. The template checks this setting through MCP initialization but does not turn it on using an undocumented API. See [Manage Databricks previews](https://learn.microsoft.com/en-us/azure/databricks/admin/workspace-settings/manage-previews).

| Recorded value | What it establishes |
| --- | --- |
| `DATABRICKS_READY=true` | The data bootstrap, seed job, and Genie deployment completed |
| `SUPPLY_AGENT_DEPLOYED=true` | The nested hosted-agent deployment completed |
| `SUPPLY_AGENT_DATABRICKS_ACCESS_READY=true` | Identity/grant setup and MCP initialization with the deployer's identity completed |
| Successful Foundry query with a Genie tool result | The deployed runtime identity can use the tool and retrieve data |

The final check is necessary even when all three flags are true. A user query succeeding in the Databricks UI only verifies that user's access, not the Foundry agent's access.

Test a read-only question in the Genie UI and through the Foundry agent before relying on the MCP endpoint.

The postdeploy hook syncs required nonsecret Foundry, ACR, workspace, and Genie outputs into an ignored nested azd environment, deploys the isolated agent manifest, and grants the deployed agent Foundry User. It does not copy Databricks or Key Vault secrets. Do not include clinical MCP endpoints or patient data in this agent.

## Manual Configuration with an Existing Databricks Workspace

This path reuses an existing workspace, catalog, warehouse, and Genie space. It does not provision another Databricks workspace or seed existing business data. It assumes the Foundry project, model deployment, ACR, and deployer permissions from the accelerator are already configured. See [DeploymentGuide.md](DeploymentGuide.md) for that setup.

The current supply agent is deliberately a **synthetic construction-supply demo**. To use the unchanged agent and automated grant helper, the existing Genie space must contain these five synthetic tables:

```text
construction_supply.construction_supply_demo.branches
construction_supply.construction_supply_demo.inventory
construction_supply.construction_supply_demo.order_lines
construction_supply.construction_supply_demo.prices
construction_supply.construction_supply_demo.products
```

For different catalogs, tables, schemas, or real business data, review and adapt [agents/supply/main.py](../agents/supply/main.py), the Genie instructions, and the grant allowlist in [scripts/grant_agent_rbac.py](../scripts/grant_agent_rbac.py) before deployment. Do not label production data as synthetic or run the seed job against unrelated tables. The helper intentionally refuses grants when the Genie table set differs from the five demo tables.

### 1. Identify and Verify Existing Resources

1. Open the existing Azure Databricks workspace and copy its per-workspace HTTPS URL, such as `https://adb-<workspace-id>.<number>.azuredatabricks.net`. Do not use the account-console URL.
2. Verify that the workspace is attached to a Unity Catalog metastore and that the intended catalog is accessible from this workspace.
3. Choose a supported SQL warehouse for Genie and record its ID. Verify the administrator/deployer can use it.
4. Identify the tables to attach to Genie and their catalog/schema names. Verify a read-only query in the Databricks SQL editor before connecting Foundry.

References: [Workspace details](https://learn.microsoft.com/en-us/azure/databricks/workspace/workspace-details), [Unity Catalog](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/), and [Genie setup](https://learn.microsoft.com/en-us/azure/databricks/genie-agents/set-up).

### 2. Verify Catalog Storage and Prepare Demo Data if Needed

If the existing tables and Genie space already work, reuse their storage configuration. The Foundry runtime agent does not need a new storage account or access connector just to read them.

For a new demo catalog in an existing workspace, have a storage/catalog administrator configure an ADLS Gen2 managed location using an access connector, storage credential, and external location. Grant Storage Blob Data Contributor to the connector identity on the dedicated storage account, or use the documented container-scoped alternative. A metastore without a storage root requires an explicit catalog managed location; setting a custom workspace initial-catalog name does not solve this requirement.

To populate an otherwise empty demo catalog manually, upload [databricks/src/seed.py](../databricks/src/seed.py) as a workspace file, create a Python-script job on supported Unity Catalog/serverless compute, and pass `--catalog construction_supply`. Run it explicitly and verify the five tables and `is_mock=true`. The script creates the demo schema and rejects tables without its expected ownership marker. Do not use this procedure to overwrite existing supplier data.

Do not deploy the data bundle over preexisting objects merely to reuse them: bundle-managed resources can be updated or deleted by later bundle operations. Adoption/binding requires a separate ownership and lifecycle review.

References: [Azure managed identities for Unity Catalog storage](https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/azure-managed-identities), [ADLS Gen2 external locations](https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/external-locations-adls), [Create catalogs](https://learn.microsoft.com/en-us/azure/databricks/catalogs/create-catalog), and [Databricks bundle lifecycle and binding](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/cli/bundle-commands).

### 3. Enable the MCP Preview

1. Sign in to the existing Databricks workspace as a workspace administrator.
2. Open the profile menu at the top right and select **Previews**.
3. Enable **Managed MCP Servers**.

This is a workspace-wide feature setting, distinct from sharing an individual Genie space. An MCP initialization error saying the servers are not enabled requires this action, not a new container deployment.

References: [Manage previews](https://learn.microsoft.com/en-us/azure/databricks/admin/workspace-settings/manage-previews) and [Genie Agent MCP prerequisites](https://learn.microsoft.com/en-us/azure/databricks/agents/mcp-tools/genie-agent).

### 4. Configure and Test the Genie Space

1. Reuse an existing Genie space or create one in Databricks, select the intended warehouse, and attach only the approved tables.
2. Add instructions describing joins, units, pricing tiers, quantity breaks, date validity, freight, and the inventory snapshot. For this sample, retain the synthetic-data and read-only restrictions.
3. Ask a test question in the Genie UI and inspect its SQL and returned rows.
4. Copy the Genie space ID from its settings. The sample expects 32 hexadecimal characters.

The MCP endpoint is:

```text
https://<workspace-hostname>/api/2.0/mcp/genie/<genie-space-id>
```

When authoring an exported `serialized_space` for the bundle, sort `data_sources.tables` by `identifier`; the Genie API rejects an unsorted list. Use the resource ID from `databricks bundle summary --output json` for bundle-managed spaces rather than searching the workspace by title.

References: [Create and manage Genie](https://learn.microsoft.com/en-us/azure/databricks/genie-agents/set-up), [Genie MCP endpoint](https://learn.microsoft.com/en-us/azure/databricks/agents/mcp-tools/genie-agent), and [Databricks Genie API](https://docs.databricks.com/api/workspace/genie).

### 5. Configure Local Authentication and the Existing Foundry Environment

Install the current Databricks CLI and authenticate Azure CLI as the deployer. This is the deployer's authentication for setup; it is not the deployed agent's runtime identity. Never paste credentials into source files, commands committed to Git, or container images.

From the accelerator repository root, select the existing azd environment. Replace the placeholders with the resources verified above:

```powershell
azd env select <existing-azd-environment>
azd env set ENABLE_DATABRICKS false
azd env set DATABRICKS_HOST "https://adb-<workspace-id>.<number>.azuredatabricks.net"
azd env set DATABRICKS_GENIE_SPACE_ID "<32-hexadecimal-character-space-id>"
```

Keep the existing `AZURE_SUBSCRIPTION_ID`, `AZURE_LOCATION`, `AZURE_TENANT_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_AI_PROJECT_ID`, Foundry project endpoints, model deployment name, and ACR outputs configured. The nested supply deployment syncs these values from the root environment; it does not create a Foundry project or registry.

References: [Databricks CLI installation](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/cli/install), [Authenticate with Azure CLI](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/azure-cli), and [Managed-identity runtime authentication](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/azure-mi).

### 6. Deploy Only the Supply Agent

After manually verifying the existing Genie/data setup, mark readiness and use the nested deployment helper:

```powershell
azd env set DATABRICKS_READY true
python scripts/deploy_supply_agent.py
```

This manual readiness value is your assertion that the existing data and Genie space have been verified; the helper does not rerun data bootstrap. Check for the successful supply-agent deployment message.

**Do not run root `azd up` as a reuse-existing-workspace shortcut.** With Databricks disabled, root provisioning emits empty Databricks outputs and bootstrap resets readiness; with it enabled, Bicep provisions the template's workspace and bootstrap manages its bundles. Those paths can replace the manually configured host/ID or deploy unintended demo assets. Use the nested helper for this manual workflow, and reapply the verified existing-workspace values after any later root provisioning.

### 7. Get the Agent's Runtime Identity

Inspect the deployed supply agent from its nested project:

```powershell
Push-Location agents/supply
azd ai agent show --output json
Pop-Location
```

Record `instance_identity.client_id` for Databricks and `instance_identity.principal_id` for Azure RBAC. Confirm the project endpoint, container version, `DATABRICKS_HOST`, and `DATABRICKS_GENIE_SPACE_ID` match the intended resources. The agent's instructions and tools are defined inside its hosted container; an empty portal Tools list does not prove its runtime MCP tool is absent.

Reference: [Foundry hosted-agent identity and endpoints](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents).

### 8. Assign Workspace Access and Entitlements

1. In Databricks, open the profile menu > **Settings** > **Identity and access** > **Service principals** > **Manage**.
2. Add an existing account principal if it already exists. Otherwise choose **Add new**, select **Microsoft Entra ID managed**, and enter the agent's client/application ID. Give it an identifiable display name.
3. Assign it to this workspace as a regular user, not an administrator. Ensure the principal is active.
4. Enable **Databricks SQL access** on the principal. Do not grant unrestricted cluster or pool creation.

With identity federation, account registration and workspace assignment are separate operations. The automated helper uses account SCIM to reuse/register the identity and the workspace assignment API to grant USER access. Use the documented account/workspace API routes if performing those steps outside the UI.

References: [Microsoft Learn service-principal management](https://learn.microsoft.com/en-us/azure/databricks/admin/users-groups/manage-service-principals), [Microsoft Learn entitlements](https://learn.microsoft.com/en-us/azure/databricks/security/auth/entitlements), [Databricks workspace service-principal API](https://docs.databricks.com/api/workspace/serviceprincipals), and [Databricks workspace assignment API](https://docs.databricks.com/api/account/workspaceassignment/update).

### 9. Grant Genie, Warehouse, and Table Permissions

1. Open the Genie space's sharing/permissions dialog and grant the agent principal **CAN RUN**.
2. Open the selected warehouse's permissions and grant the same principal **CAN USE**.
3. In Catalog Explorer, grant **USE CATALOG** on the catalog, **USE SCHEMA** on the demo schema, and **SELECT** on each of the five attached tables. Preserve the existing owner and user/group grants.
4. Verify the Foundry runtime identity has the Foundry User role required by this sample. The helper below applies both the sample's Azure RBAC and Databricks grants when the deployer has the necessary authority.

For the unchanged five-table sample, this is equivalent to the following SQL, executed by an authorized catalog/grant administrator. Replace `<agent-client-id>` with the Databricks principal's Microsoft Entra application ID:

```sql
GRANT USE CATALOG ON CATALOG construction_supply TO `<agent-client-id>`;
GRANT USE SCHEMA ON SCHEMA construction_supply.construction_supply_demo TO `<agent-client-id>`;
GRANT SELECT ON TABLE construction_supply.construction_supply_demo.branches TO `<agent-client-id>`;
GRANT SELECT ON TABLE construction_supply.construction_supply_demo.inventory TO `<agent-client-id>`;
GRANT SELECT ON TABLE construction_supply.construction_supply_demo.order_lines TO `<agent-client-id>`;
GRANT SELECT ON TABLE construction_supply.construction_supply_demo.prices TO `<agent-client-id>`;
GRANT SELECT ON TABLE construction_supply.construction_supply_demo.products TO `<agent-client-id>`;
```

For the sample's exact table set, an authorized deployer can use the repeatable helper from the repository root instead of assigning the grants manually:

```powershell
python scripts/grant_agent_rbac.py --agent supply-pricing-agent --databricks
```

It adds scoped permissions without replacing existing ACLs, assigns the explicit SQL entitlement, and checks that the attached sources match the demo allowlist. It cannot substitute for Databricks admin/grant authority or enable the preview on your behalf. For an adapted real-data integration, use reviewed permissions for its actual tables rather than the unchanged helper.

References: [Unity Catalog privilege management](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/manage-privileges/), [Azure RBAC assignment](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-portal), [Databricks additive object permissions API](https://docs.databricks.com/api/workspace/permissions/update), and [Databricks Unity Catalog grants API](https://docs.databricks.com/api/workspace/grants/update).

### 10. Verify Through the Deployed Foundry Agent

Start a new conversation against the latest deployed version in Foundry or invoke it from the nested project:

```powershell
Push-Location agents/supply
azd ai agent invoke "Which SKUs had zero available inventory on 2026-11-01? Include the snapshot date, branch, source table, and SQL reference."
Pop-Location
```

For the unchanged deterministic demo, the verified result is `NAIL-16` at NORTH and SOUTH, each with `on_hand=0`, `reserved=0`, and zero available units on `2026-11-01`. The answer must identify the data as synthetic and a dated snapshot. An answer obtained only in the Genie UI is not the end-to-end test.

Permissions and preview changes do not require rebuilding the container. Changes to Python code, instructions, MCP configuration, or version environment variables require a new hosted-agent version. The nested deployment helper can publish that version without provisioning another workspace; recheck its identity/grants afterward.

## Manual Foundry Portal Integration with OAuth

This is an alternative to the direct managed-identity hosted container above, not an additional requirement for it. It creates a portal-managed **Azure Databricks Genie** tool and uses the signed-in user's Databricks identity through **OAuth identity passthrough**. It requires custom OAuth application setup and interactive user consent; it is not interchangeable with the agent's managed identity.

1. In the existing Databricks workspace, enable Managed MCP Servers and verify the existing Genie space and warehouse.
2. Have an account administrator open the Databricks account console > **Settings** > **App connections** > **Add connection**. Register a custom OAuth application using the redirect URL required by the Foundry connection and the scopes/endpoints required by the linked guides. Do not guess the callback URL or use the hosted agent's client ID as the OAuth application's client ID.
3. Securely retain the application's client ID and, for a confidential application, its generated client secret. Enter secrets only in the approved connection UI or secret store, never in this repository or a container image.
4. In Microsoft Foundry, select **Tools**, find **Azure Databricks Genie**, and select **Connect**.
5. Enter the existing Genie's MCP endpoint and the OAuth application's client ID, secret if applicable, authorization URL, token URL, and refresh URL. The documented authentication mode is **OAuth identity passthrough**.
6. Select **Use in an agent**, send a test message, select **Open consent**, sign in to Databricks, and approve the connection.
7. Ensure that the consenting user has the required workspace entitlement, Genie access, warehouse access, and Unity Catalog read privileges, then test the response against the Genie UI result. The user's access, not the hosted service principal's grants, controls this flow.

For a hosted Agent Framework agent using a Foundry-managed toolbox, follow the Foundry Toolbox integration guidance rather than assuming a portal connection automatically changes this sample's `MCPStreamableHTTPTool` code.

References: [Use Azure Databricks Genie in Microsoft Foundry](https://learn.microsoft.com/en-us/azure/databricks/integrations/microsoft-foundry), [Custom OAuth applications](https://learn.microsoft.com/en-us/azure/databricks/integrations/enable-disable-oauth), [Databricks custom OAuth integration API](https://docs.databricks.com/api/account/customappintegration/create), and [Foundry Toolbox](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/toolbox).

## Troubleshooting and Polling

| Symptom | Check and resolution |
| --- | --- |
| Databricks CLI not installed warning after installation | Check the CLI in a fresh terminal. Bootstrap refreshes Windows user/machine PATH; the CLI still must be installed and authenticated. |
| `Metastore storage root URL does not exist` | Create a catalog with an explicit managed location backed by a credential/external location, or use an already configured catalog. Do not change the workspace initial-catalog name. |
| `data_sources.tables must be sorted by identifier` | Sort the serialized Genie table entries lexicographically before deploying the Genie bundle. |
| Title-based lookup finds no Genie | Obtain the deployed resource ID from bundle summary/state, not a first-page exact-title search. |
| `Managed MCP servers are not enabled` | A workspace admin must enable Managed MCP Servers in the workspace Previews page. |
| MCP returns 401/403 | Verify the current runtime client ID, active account principal, workspace assignment, explicit SQL entitlement, Genie CAN RUN, warehouse CAN USE, and table grants. A successful admin/user request does not establish runtime access. |
| Genie UI succeeds but Foundry says polling failed | Inspect the tool-call error and ensure the Genie conversation/message IDs survive the MCP client's argument filtering. |
| Windows RBAC helper reports `WinError 2` for `az` | Use the updated helper, which resolves executables and uses the Azure CLI's native Python launcher when available. Verify Azure CLI installation/authentication. |
| Nested deployment requires `AZURE_AI_PROJECT_ID` | Use the updated deployment helper, which syncs the existing project ARM ID into the nested environment. No new Foundry project is needed. |

Genie can return an asynchronous query result with `conversationId`, `messageId`, and a pending status. Poll with those exact values as `conversation_id` and `message_id` until the result completes or fails. These are Databricks Genie IDs, not Foundry conversation/response IDs.

Agent Framework treats `conversation_id` as internal runtime context and filters it out of generic MCP calls by default. The supply agent explicitly allows this server argument:

```python
tool = MCPStreamableHTTPTool(
    name="construction-supply-genie",
    url=endpoint,
    http_client=http_client,
    load_prompts=False,
    additional_tool_argument_names=["conversation_id"],
)
```

Do not remove the override when adapting the sample. A required field in the MCP server's schema or an extra instruction to the model does not override this framework filter. The regression test checks that the Genie conversation/message IDs are forwarded while internal thread objects remain excluded.

Inspect logs from the failed Foundry request's session ID:

```powershell
Push-Location agents/supply
azd ai agent monitor --session-id <session-id-from-the-failed-request> --tail 100
Pop-Location
```

The outer telemetry span can say `Ok` even when its nested exception/tool result failed. Inspect the actual tool result, HTTP status, and exception details. Do not turn on bearer-token logging to troubleshoot authentication; the supply transport logs only bounded denial details and allowlisted identity IDs.

## Validation and Teardown

Offline checks:

```powershell
uv run --no-project --python 3.12 --with pyyaml python scripts/test_databricks.py -v
uv run --no-project --python 3.12 --with-requirements agents/supply/requirements.txt python scripts/test_supply_agent.py -v
```

These tests verify deterministic synthetic rows, arithmetic, effective-date and quantity-break boundaries, demo-table ownership checks, bundle semantics, readiness gating, HTTP origin restrictions, and managed-identity token requests without calling Azure or Databricks. Neither a live deployment nor a Genie invocation is implied by passing the offline tests.

Remove the Genie bundle and data bundle resources using their respective authenticated bundle targets when they are no longer needed. Manual data-bundle commands require the two storage bundle variables above. Check retained Unity Catalog tables before deletion. The bootstrap-created storage credential is not bundle-managed; remove it separately only after removing dependent locations and checking other consumers. `azd down` deletes the Bicep-managed storage account, including its demo data, along with the accelerator resources; it does not clean up metastore objects shared with other workspaces. Do not run it unless that full teardown is intended.

## References

- [Databricks-provided MCPs and Genie One guidance](https://learn.microsoft.com/en-us/azure/databricks/agents/mcp-tools/built-in-mcp-services)
- [Genie Agent MCP server](https://learn.microsoft.com/en-us/azure/databricks/agents/mcp-tools/genie-agent)
- [Create and manage Genie Agents](https://learn.microsoft.com/en-us/azure/databricks/genie-agents/set-up)
- [Declarative Automation Bundles resources](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/resources)
- [Azure Databricks managed identity authentication](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/azure-mi)
- [Azure Databricks workspace Bicep resource](https://learn.microsoft.com/en-us/azure/templates/microsoft.databricks/2024-05-01/workspaces)
- [Microsoft Learn: Databricks service principals](https://learn.microsoft.com/en-us/azure/databricks/admin/users-groups/manage-service-principals)
- [Microsoft Learn: Databricks entitlements](https://learn.microsoft.com/en-us/azure/databricks/security/auth/entitlements)
- [Microsoft Learn: Unity Catalog privileges](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/manage-privileges/)
- [Microsoft Learn: Azure managed identity storage configuration](https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/azure-managed-identities)
- [Microsoft Learn: Foundry Genie OAuth integration](https://learn.microsoft.com/en-us/azure/databricks/integrations/microsoft-foundry)
- [Databricks: Genie API](https://docs.databricks.com/api/workspace/genie)
- [Databricks: workspace assignment API](https://docs.databricks.com/api/account/workspaceassignment/update)
- [Databricks: object permissions API](https://docs.databricks.com/api/workspace/permissions/update)
- [Databricks: Unity Catalog grants API](https://docs.databricks.com/api/workspace/grants/update)
- [Databricks: custom OAuth application API](https://docs.databricks.com/api/account/customappintegration/create)
