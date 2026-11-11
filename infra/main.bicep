// ---------------------------------------------------------------------------
// Prior Auth MAF — Main Bicep template
// Deploys: Resource Group, Microsoft Foundry (Resource + Project), Container Registry,
//          Container Apps Environment, Backend + 4 Agent + Frontend Container Apps,
//          Log Analytics, App Insights, Role Assignments (Cognitive Services OpenAI User, Foundry User; deployer also receives Foundry Project Manager via postprovision hook)
// ---------------------------------------------------------------------------

targetScope = 'subscription'

// ── Parameters ──────────────────────────────────────────────────────────────

@minLength(1)
@maxLength(64)
@description('Name of the environment (e.g., dev, staging, prod)')
param environmentName string

@minLength(1)
@description('Primary location for all resources. gpt-5.4 GlobalStandard is available in East US 2 and Sweden Central. DataZoneStandard is available in East US 2 only.')
@allowed([
  'eastus2'
  'swedencentral'
])
param location string

@description('Azure OpenAI deployment name to use across all agent containers (e.g., gpt-5.4)')
param azureOpenAIDeploymentName string = 'gpt-5.4'

@description('Deployment SKU: GlobalStandard (default, wider region support) or DataZoneStandard (data residency, East US 2 only).')
@allowed(['GlobalStandard', 'DataZoneStandard'])
param deploymentSkuName string = 'GlobalStandard'

@description('Whether container images have been built to ACR (set automatically by postprovision hook)')
param imagesBuilt string = ''

@description('Tag of the last successfully deployed backend and frontend images')
param imageTag string = ''

@description('Set to true to provision the experimental construction-supplies Databricks workspace')
param enableDatabricks string = 'false'

@description('Model deployment capacity in thousands of tokens per minute. Must be >= the AI gateway per-agent limit (75 by default); raise it so several agents can use their allowance at once.')
param deploymentCapacityK string = '100'

// ── AI gateway (API Management) + API Center — Specs/001-apim-ai-gateway-api-center.md ──

@description('Set to true to route hosted-agent model calls through an API Management AI gateway')
param enableAiGateway string = 'false'

@description('API Management v2 tier for the AI gateway')
@allowed(['BasicV2', 'StandardV2'])
param aiGatewaySku string = 'BasicV2'

@description('Per-agent tokens-per-minute limit enforced by the llm-token-limit policy')
param aiGatewayTokensPerMinute string = '75000'

@description('API Management publisher email (APIM notifications only; a placeholder is used when empty)')
param aiGatewayPublisherEmail string = ''

@description('Comma-separated hosted-agent client IDs allowed through the gateway (maintained by the postdeploy hook)')
param aiGatewayAllowedClientIds string = ''

@description('Set to true to provision Azure API Center as the API and MCP server registry')
param enableApiCenter string = 'false'

@description('API Center region. API Center is not available in East US 2.')
@allowed([
  'australiaeast'
  'canadacentral'
  'centralindia'
  'eastus'
  'francecentral'
  'swedencentral'
  'uksouth'
  'westeurope'
])
param apiCenterLocation string = 'eastus'

// ── Private network mode — Specs/002-private-network.md ─────────────────────
// Needs the network from infra/bootstrap and must run from inside it (jumpbox).

@description('Set to true to deploy with private endpoints, Foundry network injection and public access disabled')
param enablePrivateNetworking string = 'false'

@description('Resource group of the network created by infra/bootstrap')
param networkResourceGroup string = ''

@description('VNet created by infra/bootstrap (subnets are looked up by name)')
param networkVnetName string = ''

@description('Resource group holding the privatelink DNS zones (defaults to networkResourceGroup)')
param dnsZoneResourceGroup string = ''

@description('APIM public network access. The postprovision hook sets this to Disabled after the first private deployment.')
@allowed(['Enabled', 'Disabled'])
param aiGatewayPublicNetworkAccess string = 'Enabled'

// MCP server URLs (ICD-10, PubMed, ClinicalTrials.gov, NPI Registry, CMS Coverage)
// are configured in each root azure.yaml service's env (`MCP_ICD10_CODES`, `MCP_PUBMED`,
// `MCP_CLINICAL_TRIALS`, `MCP_NPI_REGISTRY`, `MCP_CMS_COVERAGE`) and consumed
// in-container by `agents/<name>/main.py`. The `azd ai agent` extension
// propagates these into the running container at create_version() time, so they
// can be overridden by editing azure.yaml and re-running `azd up` without
// touching this Bicep template.

// ── Variables ───────────────────────────────────────────────────────────────

var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  'solution-accelerator': 'prior-auth-maf'
}

// Private mode: network and DNS from infra/bootstrap (plain strings, so nothing is
// resolved when private mode is off).
var isPrivate = enablePrivateNetworking == 'true'
var vnetId = '/subscriptions/${subscription().subscriptionId}/resourceGroups/${networkResourceGroup}/providers/Microsoft.Network/virtualNetworks/${networkVnetName}'
var subnetIds = {
  agent: '${vnetId}/subnets/snet-agent'
  mcp: '${vnetId}/subnets/snet-mcp'
  pe: '${vnetId}/subnets/snet-pe'
  apim: '${vnetId}/subnets/snet-apim'
}
var dnsZoneBase = '/subscriptions/${subscription().subscriptionId}/resourceGroups/${empty(dnsZoneResourceGroup) ? networkResourceGroup : dnsZoneResourceGroup}/providers/Microsoft.Network/privateDnsZones'
var dnsZoneIds = {
  cognitiveServices: '${dnsZoneBase}/privatelink.cognitiveservices.azure.com'
  openai: '${dnsZoneBase}/privatelink.openai.azure.com'
  aiServices: '${dnsZoneBase}/privatelink.services.ai.azure.com'
  acr: '${dnsZoneBase}/privatelink.azurecr.io'
  blob: '${dnsZoneBase}/privatelink.blob.${environment().suffixes.storage}'
  cosmos: '${dnsZoneBase}/privatelink.documents.azure.com'
  search: '${dnsZoneBase}/privatelink.search.windows.net'
  apim: '${dnsZoneBase}/privatelink.azure-api.net'
  monitor: '${dnsZoneBase}/privatelink.monitor.azure.com'
  oms: '${dnsZoneBase}/privatelink.oms.opinsights.azure.com'
  ods: '${dnsZoneBase}/privatelink.ods.opinsights.azure.com'
  agentsvc: '${dnsZoneBase}/privatelink.agentsvc.azure-automation.net'
  databricks: '${dnsZoneBase}/privatelink.azuredatabricks.net'
  dfs: '${dnsZoneBase}/privatelink.dfs.${environment().suffixes.storage}'
}

// ── Resource Group ──────────────────────────────────────────────────────────

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: '${abbrs.resourcesResourceGroups}${environmentName}'
  location: location
  tags: tags
}

// ── Container Registry ──────────────────────────────────────────────────────

module databricks './modules/databricks.bicep' = if (enableDatabricks == 'true') {
  name: 'databricks'
  scope: rg
  params: {
    name: 'dbw-${resourceToken}'
    location: location
    managedResourceGroupName: 'rg-dbw-managed-${resourceToken}'
    tags: tags
    enablePrivateNetworking: isPrivate
    vnetId: vnetId
    peSubnetId: subnetIds.pe
    dnsZoneIds: {
      databricks: dnsZoneIds.databricks
      blob: dnsZoneIds.blob
      dfs: dnsZoneIds.dfs
    }
  }
}

module containerRegistry './modules/container-registry.bicep' = {
  name: 'container-registry'
  scope: rg
  params: {
    name: '${abbrs.containerRegistryRegistries}${resourceToken}'
    location: location
    tags: tags
    enablePrivateNetworking: isPrivate
    peSubnetId: subnetIds.pe
    dnsZoneId: dnsZoneIds.acr
  }
}

// ── Log Analytics + Application Insights ────────────────────────────────────

module monitoring './modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    logAnalyticsName: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
    appInsightsName: '${abbrs.insightsComponents}${resourceToken}'
    location: location
    tags: tags
    enablePrivateNetworking: isPrivate
    peSubnetId: subnetIds.pe
    amplsDnsZoneIds: [dnsZoneIds.monitor, dnsZoneIds.oms, dnsZoneIds.ods, dnsZoneIds.agentsvc, dnsZoneIds.blob]
  }
}

// ── Microsoft Foundry (Resource + Project) ──────────────────────────────────

module aiFoundry './modules/ai-foundry.bicep' = {
  name: 'ai-foundry'
  scope: rg
  params: {
    name: '${abbrs.aiFoundry}${resourceToken}'
    location: location
    tags: tags
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    appInsightsResourceId: monitoring.outputs.appInsightsResourceId
    deploymentName: azureOpenAIDeploymentName
    deploymentSkuName: deploymentSkuName
    deploymentCapacityK: int(deploymentCapacityK)
    enablePrivateNetworking: isPrivate
    agentSubnetId: subnetIds.agent
    peSubnetId: subnetIds.pe
    accountDnsZoneIds: [dnsZoneIds.cognitiveServices, dnsZoneIds.openai, dnsZoneIds.aiServices]
  }
}

// Private mode: agent data in our own private Cosmos DB, Storage and AI Search
// (Foundry Agent Service standard setup, required for network isolation).
module foundryStandardSetup './modules/foundry-standard-setup.bicep' = if (isPrivate) {
  name: 'foundry-standard-setup'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    accountName: aiFoundry.outputs.accountName
    projectName: aiFoundry.outputs.projectName
    projectPrincipalId: aiFoundry.outputs.projectPrincipalId
    peSubnetId: subnetIds.pe
    dnsZoneIds: {
      blob: dnsZoneIds.blob
      cosmos: dnsZoneIds.cosmos
      search: dnsZoneIds.search
    }
  }
}

// ── AI gateway: API Management in front of Foundry model inference ──────────
// Hosted agents call AI_GATEWAY_OPENAI_BASE_URL instead of the project endpoint;
// the gateway enforces a per-agent token limit and emits token metrics.

module aiGateway './modules/apim-ai-gateway.bicep' = if (enableAiGateway == 'true') {
  name: 'ai-gateway'
  scope: rg
  params: {
    name: '${abbrs.apiManagementService}${resourceToken}'
    location: location
    tags: tags
    // Private mode needs StandardV2 (BasicV2 has no private endpoint or VNet integration).
    skuName: isPrivate ? 'StandardV2' : aiGatewaySku
    enablePrivateNetworking: isPrivate
    apimSubnetId: subnetIds.apim
    peSubnetId: subnetIds.pe
    gatewayDnsZoneId: dnsZoneIds.apim
    publicNetworkAccess: aiGatewayPublicNetworkAccess
    publisherEmail: empty(aiGatewayPublisherEmail) ? 'ai-gateway-admin@example.com' : aiGatewayPublisherEmail
    foundryAccountName: aiFoundry.outputs.accountName
    tokensPerMinute: int(aiGatewayTokensPerMinute)
    allowedClientIds: aiGatewayAllowedClientIds
    appInsightsResourceId: monitoring.outputs.appInsightsResourceId
    appInsightsInstrumentationKey: monitoring.outputs.appInsightsInstrumentationKey
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
  }
}

// ── API Center: registry for the gateway API and the remote MCP servers ─────
// The Databricks Genie MCP server is registered by the postdeploy hook once its
// space ID exists (scripts/sync_ai_gateway.py).

module apiCenter './modules/api-center.bicep' = if (enableApiCenter == 'true') {
  name: 'api-center'
  scope: rg
  params: {
    name: '${abbrs.apiCenterService}${resourceToken}'
    location: apiCenterLocation
    tags: tags
    apimName: enableAiGateway == 'true' ? aiGateway!.outputs.name : ''
    mcpServers: [
      { name: 'icd10-codes', title: 'ICD-10 Codes MCP', description: 'ICD-10 code lookup (Anthropic healthcare gateway)', url: 'https://hcls.mcp.claude.com/icd10_codes/mcp' }
      { name: 'pubmed', title: 'PubMed MCP', description: 'PubMed literature search', url: 'https://pubmed.mcp.claude.com/mcp' }
      { name: 'clinical-trials', title: 'Clinical Trials MCP', description: 'ClinicalTrials.gov search', url: 'https://hcls.mcp.claude.com/clinical_trials/mcp' }
      { name: 'npi-registry', title: 'NPI Registry MCP', description: 'Provider NPI lookup', url: 'https://hcls.mcp.claude.com/npi_registry/mcp' }
      { name: 'cms-coverage', title: 'CMS Coverage MCP', description: 'Medicare LCD/NCD coverage policies', url: 'https://hcls.mcp.claude.com/cms_coverage/mcp' }
    ]
  }
}

// ── Container Apps Environment ──────────────────────────────────────────────

module containerAppsEnv './modules/container-apps-env.bicep' = {
  name: 'container-apps-env'
  scope: rg
  params: {
    name: '${abbrs.appManagedEnvironments}${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    enablePrivateNetworking: isPrivate
    infrastructureSubnetId: subnetIds.mcp
    vnetId: vnetId
  }
}

// ── Backend Container App ────────────────────────────────────────────────────────

module backend './modules/container-app.bicep' = {
  name: 'backend'
  scope: rg
  params: {
    name: '${abbrs.appContainerApps}backend-${resourceToken}'
    location: location
    tags: union(tags, { 'azd-service-name': 'backend' })
    containerAppsEnvironmentId: containerAppsEnv.outputs.environmentId
    containerRegistryLoginServer: containerRegistry.outputs.loginServer
    imageName: 'backend'
    targetPort: 8000
    useAcrImage: imagesBuilt == 'true' || !empty(imageTag)
    imageTag: empty(imageTag) ? 'latest' : imageTag
    cpu: '1'
    memory: '2Gi'
    // Backend keeps in-memory state (`_review_store` in orchestrator, monotonic letter
    // counter in `services/notification.py`). Pinned to a single replica to preserve
    // correctness. For multi-replica scale-out, externalize this state into Cosmos DB
    // / Redis (see `docs/production-migration.md`).
    minReplicas: 1
    maxReplicas: 1
    env: [
      // Foundry project endpoint — backend calls Foundry Hosted Agents via the Responses API
      { name: 'AZURE_AI_PROJECT_ENDPOINT', value: aiFoundry.outputs.projectEndpoint }
      // Foundry Hosted Agent names (as registered by `azd deploy` via the
      // `services:` block in azure.yaml; must match the service-level `name`)
      { name: 'HOSTED_AGENT_CLINICAL_NAME', value: 'clinical-reviewer-agent' }
      { name: 'HOSTED_AGENT_COVERAGE_NAME', value: 'coverage-assessment-agent' }
      { name: 'HOSTED_AGENT_COMPLIANCE_NAME', value: 'compliance-agent' }
      { name: 'HOSTED_AGENT_SYNTHESIS_NAME', value: 'synthesis-agent' }
      { name: 'HOSTED_AGENT_TIMEOUT_SECONDS', value: '180' }
      { name: 'APPLICATION_INSIGHTS_CONNECTION_STRING', value: monitoring.outputs.appInsightsConnectionString }
      { name: 'FRONTEND_ORIGIN', value: 'https://${abbrs.appContainerApps}frontend-${resourceToken}.${containerAppsEnv.outputs.defaultDomain}' }
    ]
    secrets: []
    healthCheckPath: '/health'
  }
}
// ── Role Assignments ─────────────────────────────────────────────────────────
// Backend → CognitiveServicesOpenAIUser on Foundry (per-agent dedicated endpoints)
// Backend + Frontend → AcrPull on ACR (image pull via system-assigned MI)
// Foundry project identity → AcrPull on ACR (agent image pull for hosted agents)
// Deployer → Foundry User + Foundry Project Manager are assigned via `az role assignment create` in postprovision hook (idempotent).
// Project Manager is required by the refreshed Hosted Agents preview to call create_version() on HostedAgentDefinition / PromptAgentDefinition.

module roleAssignments './modules/role-assignments.bicep' = {
  name: 'role-assignments'
  scope: rg
  params: {
    foundryAccountName: aiFoundry.outputs.accountName
    backendPrincipalId: backend.outputs.principalId
    frontendPrincipalId: frontend.outputs.principalId
    containerRegistryName: containerRegistry.outputs.name
    foundryProjectPrincipalId: aiFoundry.outputs.projectPrincipalId
    foundryAccountPrincipalId: aiFoundry.outputs.accountPrincipalId
  }
}
// ── Frontend Container App ──────────────────────────────────────────────────

module frontend './modules/container-app.bicep' = {
  name: 'frontend'
  scope: rg
  params: {
    name: '${abbrs.appContainerApps}frontend-${resourceToken}'
    location: location
    tags: union(tags, { 'azd-service-name': 'frontend' })
    containerAppsEnvironmentId: containerAppsEnv.outputs.environmentId
    containerRegistryLoginServer: containerRegistry.outputs.loginServer
    imageName: 'frontend'
    targetPort: 80
    useAcrImage: imagesBuilt == 'true' || !empty(imageTag)
    imageTag: empty(imageTag) ? 'latest' : imageTag
    minReplicas: 1
    env: [
      { name: 'BACKEND_URL', value: 'https://${abbrs.appContainerApps}backend-${resourceToken}.${containerAppsEnv.outputs.defaultDomain}' }
    ]
    secrets: []
    healthCheckPath: '/'
  }
}

// ── Outputs ─────────────────────────────────────────────────────────────────

output AZURE_RESOURCE_GROUP string = rg.name
output DATABRICKS_HOST string = enableDatabricks == 'true' ? databricks!.outputs.host : ''
output DATABRICKS_AZURE_RESOURCE_ID string = enableDatabricks == 'true' ? databricks!.outputs.workspaceId : ''
output DATABRICKS_ACCESS_CONNECTOR_ID string = enableDatabricks == 'true' ? databricks!.outputs.accessConnectorId : ''
output DATABRICKS_STORAGE_URL string = enableDatabricks == 'true' ? databricks!.outputs.storageUrl : ''
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.outputs.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = containerRegistry.outputs.name
output AZURE_CONTAINER_REGISTRY_RESOURCE_ID string = containerRegistry.outputs.id
output AI_FOUNDRY_ACCOUNT_NAME string = aiFoundry.outputs.accountName
output AI_FOUNDRY_PROJECT_NAME string = aiFoundry.outputs.projectName
output AI_FOUNDRY_ENDPOINT string = aiFoundry.outputs.endpoint
output AI_FOUNDRY_PROJECT_ENDPOINT string = aiFoundry.outputs.projectEndpoint
output AI_FOUNDRY_PORTAL_URL string = aiFoundry.outputs.portalUrl
// Required by `azd ai agent` extension (azd deploy of host: azure.ai.agent
// services) so it can target the Foundry project that owns the agents.
// FOUNDRY_PROJECT_ENDPOINT is the exact name the extension looks up — the
// AI_FOUNDRY_* / AZURE_AI_* aliases below are consumed by this repo's own
// hooks, backend and service-level env mappings.
output AZURE_AI_PROJECT_ID string = aiFoundry.outputs.projectId
output AZURE_AI_PROJECT_ENDPOINT string = aiFoundry.outputs.projectEndpoint
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = azureOpenAIDeploymentName
output FOUNDRY_PROJECT_ENDPOINT string = aiFoundry.outputs.projectEndpoint
output BACKEND_CONTAINER_APP_NAME string = backend.outputs.name
output FRONTEND_CONTAINER_APP_NAME string = frontend.outputs.name
output AZURE_OPENAI_DEPLOYMENT_NAME string = azureOpenAIDeploymentName
output APPLICATION_INSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString
// AI gateway / API Center — empty when disabled, so hosted agents fall back to
// the Foundry project endpoint (see agents/*/model_client.py).
output AI_GATEWAY_NAME string = enableAiGateway == 'true' ? aiGateway!.outputs.name : ''
output AI_GATEWAY_URL string = enableAiGateway == 'true' ? aiGateway!.outputs.gatewayUrl : ''
output AI_GATEWAY_OPENAI_BASE_URL string = enableAiGateway == 'true' ? aiGateway!.outputs.openAiV1BaseUrl : ''
output API_CENTER_NAME string = enableApiCenter == 'true' ? apiCenter!.outputs.name : ''
output API_CENTER_MCP_REGISTRY_URL string = enableApiCenter == 'true' ? apiCenter!.outputs.mcpRegistryUrl : ''
// Private mode
output PRIVATE_NETWORKING string = enablePrivateNetworking
output CONTAINER_APPS_STATIC_IP string = isPrivate ? containerAppsEnv.outputs.staticIp : ''
output frontendUrl string = frontend.outputs.fqdn
output backendUrl string = backend.outputs.fqdn
