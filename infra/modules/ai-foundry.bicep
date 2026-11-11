// ---------------------------------------------------------------------------
// Microsoft Foundry — Resource + Project (new architecture)
// Creates the Foundry resource (CognitiveServices/accounts) and a project
// for deploying Azure OpenAI models (e.g., gpt-5.4) from the model catalog.
//
// Reference: https://learn.microsoft.com/en-us/azure/foundry/how-to/create-resource-template
// ---------------------------------------------------------------------------

@description('Base name for Foundry resources')
param name string

@description('Location for all resources')
param location string

@description('Tags for all resources')
param tags object = {}

@description('Full Application Insights connection string used by the Foundry project tracing connection')
@secure()
param appInsightsConnectionString string

@description('Application Insights resource ID — the target resource for the AppInsights connection')
param appInsightsResourceId string

@description('Name for the model deployment (used in API calls, e.g. gpt-5.4)')
param deploymentName string = 'gpt-5.4'

@description('Model version to deploy.')
param modelVersion string = '2026-03-05'

@description('Deployment SKU: DataZoneStandard (data residency within geographic zone) or GlobalStandard (no data residency, wider region availability).')
@allowed(['DataZoneStandard', 'GlobalStandard'])
param deploymentSkuName string = 'GlobalStandard'

@description('Capacity in thousands of tokens per minute (default: 100 = 100K TPM)')
param deploymentCapacityK int = 100

// -- Private mode (Specs/002-private-network.md section 6.3) --------------------

@description('Private mode: network injection for hosted agents, public access off, private endpoint')
param enablePrivateNetworking bool = false

@description('Delegated agent subnet (snet-agent). Network injection can only be set when the account is created.')
param agentSubnetId string = ''

@description('Private endpoint subnet (snet-pe)')
param peSubnetId string = ''

@description('DNS zone IDs for the account endpoint: cognitiveservices, openai, services.ai')
param accountDnsZoneIds array = []

@description('Private mode: public IPs/CIDRs (for example the corporate VPN egress) that may still reach Foundry, so people can use the Foundry portal from laptops. Empty keeps public access disabled.')
param allowedIpRanges array = []

var restrictedPublicAccess = enablePrivateNetworking && !empty(allowedIpRanges)
var allowedIpRules = map(allowedIpRanges, ip => { value: ip })

// ── Microsoft Foundry Resource ──────────────────────────────────────────────

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-12-01' = {
  name: 'foundry-${name}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'S0'
  }
  kind: 'AIServices'
  properties: {
    allowProjectManagement: true
    customSubDomainName: 'foundry-${name}'
    disableLocalAuth: true
    // Private mode: disabled, or "restricted" (deny by default, allow-listed IPs only)
    // when allowedIpRanges is set. Either satisfies "public access disabled or restricted".
    publicNetworkAccess: !enablePrivateNetworking || restrictedPublicAccess ? 'Enabled' : 'Disabled'
    networkAcls: enablePrivateNetworking ? {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      ipRules: allowedIpRules
      virtualNetworkRules: []
    } : null
    // Hosted agents run in snet-agent and send their outbound traffic through the VNet.
    networkInjections: enablePrivateNetworking ? [
      {
        scenario: 'agent'
        subnetArmId: agentSubnetId
        useMicrosoftManagedNetwork: false
      }
    ] : null
  }
}

module accountPrivateEndpoint './private-endpoint.bicep' = if (enablePrivateNetworking) {
  name: 'pe-foundry'
  params: {
    name: 'pe-${foundryAccount.name}'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetResourceId: foundryAccount.id
    groupId: 'account'
    dnsZoneIds: accountDnsZoneIds
  }
}

// ── Microsoft Foundry Project ───────────────────────────────────────────────

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-12-01' = {
  name: 'proj-${name}'
  parent: foundryAccount
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

// ── App Insights Connection — links Foundry Traces view to App Insights ─────
// category 'AppInsights' + authType 'ApiKey' is the connection pattern that
// the Foundry portal uses when you click "Connect" under Agents → Traces.
// Without this, the Foundry portal Traces tab shows nothing even though agent
// spans are correctly exported to App Insights by client-side instrumentation.

resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-12-01' = {
  name: 'app-insights'
  parent: foundryProject
  properties: {
    category: 'AppInsights'
    target: appInsightsResourceId
    metadata: {
      ResourceId: appInsightsResourceId
    }
    authType: 'ApiKey'
    credentials: {
      key: appInsightsConnectionString
    }
  }
}
// ── gpt-5.4 Model Deployment ─────────────────────────────────────────────
// GlobalStandard  = no data residency guarantee, available in more regions.
// DataZoneStandard = data residency bounded to a geographic zone (US/EU),
//                    available in East US 2 only (NOT Sweden Central).
// Deployment SKU is selected by the user during azd up.
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-12-01' = {
  name: deploymentName
  parent: foundryAccount
  sku: {
    name: deploymentSkuName
    capacity: deploymentCapacityK
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5.4'
      version: modelVersion
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
  dependsOn: [appInsightsConnection]
}
// ── Outputs ─────────────────────────────────────────────────────────────────

output accountName string = foundryAccount.name
output accountId string = foundryAccount.id
output projectName string = foundryProject.name
output projectId string = foundryProject.id
output endpoint string = foundryAccount.properties.endpoint
output portalUrl string = 'https://ai.azure.com/manage/project?wsid=${foundryProject.id}'

// Project endpoint: used by the backend orchestrator to invoke Foundry Hosted
// Agents via the Responses API on per-agent dedicated endpoints.
// MUST use the services.ai.azure.com subdomain (the "AI Foundry API" endpoint)
// rather than the default cognitiveservices.azure.com one — the Agent Service
// runtime only routes requests to the AI Foundry API subdomain. Hitting the
// cognitiveservices subdomain returns 404 "Subdomain does not map to a resource".
// Format: https://<resource>.services.ai.azure.com/api/projects/<project>
output projectEndpoint string = 'https://${foundryAccount.name}.services.ai.azure.com/api/projects/${foundryProject.name}'

// Project system-assigned managed identity — needs AcrPull on ACR so Foundry
// Agent Service can pull the 4 agent container images.
output projectPrincipalId string = foundryProject.identity.principalId

// Foundry account system-assigned managed identity — ALSO needs AcrPull on
// ACR. The hosted agents service uses the *account* MI (not the project MI)
// to validate agent images at `create_version()` time. Without it, the call
// returns a generic `(server_error) 500`. Discovered empirically; required
// in addition to the project MI grant.
output accountPrincipalId string = foundryAccount.identity.principalId
