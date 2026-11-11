// ---------------------------------------------------------------------------
// Spec 001: API Management as the AI gateway in front of Microsoft Foundry
// Spec: Specs/001-apim-ai-gateway-api-center.md
//
// Deploys: APIM (BasicV2/StandardV2) with system-assigned MI, Foundry OpenAI v1
// backend (MI auth, circuit breaker), "foundry-openai-v1" API with the AI gateway
// policy (llm-token-limit 75k TPM per agent identity, llm-emit-token-metric),
// App Insights logger + metrics-enabled diagnostic, LLM gateway logs to Log Analytics,
// and Cognitive Services OpenAI User for the APIM MI on the Foundry account.
//
// References:
//   https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities
//   https://learn.microsoft.com/azure/api-management/llm-token-limit-policy
//   https://learn.microsoft.com/azure/api-management/llm-emit-token-metric-policy
//   https://learn.microsoft.com/azure/api-management/azure-ai-foundry-api
//   https://learn.microsoft.com/azure/api-management/api-management-howto-llm-logs
// ---------------------------------------------------------------------------

@description('API Management service name')
param name string

@description('Location for API Management')
param location string

@description('Tags for all resources')
param tags object = {}

@description('v2 tier. BasicV2 for the pilot; StandardV2 to unlock private endpoint / VNet integration and free API Center Standard.')
@allowed(['BasicV2', 'StandardV2'])
param skuName string = 'BasicV2'

@description('Scale units')
@minValue(1)
param skuCapacity int = 1

@description('Publisher email (required by APIM)')
param publisherEmail string

@description('Publisher name (required by APIM)')
param publisherName string = 'Prior Auth MAF'

@description('Existing Foundry (CognitiveServices AIServices) account in the same resource group')
param foundryAccountName string

@description('Per-agent tokens-per-minute limit enforced by llm-token-limit')
@minValue(1)
param tokensPerMinute int = 75000

@description('Comma-separated client IDs of hosted-agent identities allowed to call the gateway. Maintained by the postdeploy hook; empty blocks every caller.')
param allowedClientIds string = ''

@description('Token audience the hosted agents request for the gateway')
param tokenAudience string = 'https://cognitiveservices.azure.com'

@description('Application Insights resource ID')
param appInsightsResourceId string

@description('Application Insights instrumentation key (APIM logger)')
@secure()
param appInsightsInstrumentationKey string

@description('Log Analytics workspace ID for gateway and LLM logs')
param logAnalyticsWorkspaceId string

// -- Private mode (Specs/002-private-network.md section 6.4) --------------------

@description('Private mode: StandardV2 with outbound VNet integration and an inbound private endpoint')
param enablePrivateNetworking bool = false

@description('Outbound integration subnet (snet-apim, delegated to Microsoft.Web/serverFarms)')
param apimSubnetId string = ''

param peSubnetId string = ''
param gatewayDnsZoneId string = ''

@description('APIM cannot be created with public access disabled. The postprovision hook disables it once the private endpoint exists and sets AI_GATEWAY_PUBLIC_ACCESS=Disabled so later provisions keep it off.')
@allowed(['Enabled', 'Disabled'])
param publicNetworkAccess string = 'Enabled'

// Cognitive Services OpenAI User: inference only, no management rights
var cognitiveServicesOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var backendId = 'foundry-openai-v1'

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-12-01' existing = {
  name: foundryAccountName
}

// ── API Management service ──────────────────────────────────────────────────

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: skuName
    capacity: skuCapacity
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: union({
    publisherEmail: publisherEmail
    publisherName: publisherName
  }, enablePrivateNetworking ? {
    // Outbound: APIM reaches the Foundry and Databricks private endpoints through the VNet.
    virtualNetworkType: 'External'
    virtualNetworkConfiguration: {
      subnetResourceId: apimSubnetId
    }
    publicNetworkAccess: publicNetworkAccess
  } : {})
}

// Inbound: hosted agents call the gateway through this private endpoint.
module gatewayPrivateEndpoint './private-endpoint.bicep' = if (enablePrivateNetworking) {
  name: 'pe-apim'
  params: {
    name: 'pe-${name}'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetResourceId: apim.id
    groupId: 'Gateway'
    dnsZoneIds: [gatewayDnsZoneId]
  }
}

// APIM MI -> Foundry inference (keyless backend auth)
resource apimFoundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, apim.id, cognitiveServicesOpenAIUserRoleId)
  scope: foundryAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Named values used by the policy ─────────────────────────────────────────

resource nvTenant 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'ai-gateway-tenant-id'
  properties: {
    displayName: 'ai-gateway-tenant-id'
    value: tenant().tenantId
  }
}

resource nvAudience 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'ai-gateway-audience'
  properties: {
    displayName: 'ai-gateway-audience'
    value: tokenAudience
  }
}

// APIM rejects empty named values, so a sentinel keeps the allowlist valid (and closed).
resource nvAllowedClients 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'ai-gateway-allowed-client-ids'
  properties: {
    displayName: 'ai-gateway-allowed-client-ids'
    value: empty(allowedClientIds) ? 'none' : allowedClientIds
  }
}

// ── Backend: Foundry OpenAI v1 surface ──────────────────────────────────────

resource foundryBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: backendId
  properties: {
    description: 'Microsoft Foundry OpenAI v1 endpoint (Responses + Chat Completions)'
    url: 'https://${foundryAccount.properties.customSubDomainName}.openai.azure.com/openai/v1'
    protocol: 'http'
    circuitBreaker: {
      rules: [
        {
          name: 'foundry-429-breaker'
          failureCondition: {
            count: 3
            interval: 'PT1M'
            statusCodeRanges: [
              { min: 429, max: 429 }
              { min: 500, max: 503 }
            ]
          }
          tripDuration: 'PT1M'
          acceptRetryAfter: true
        }
      ]
    }
  }
}

// ── API: /openai/v1/* passthrough with AI gateway policy ────────────────────

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'foundry-openai-v1'
  properties: {
    displayName: 'Foundry OpenAI v1 (AI gateway)'
    description: 'Model inference for Foundry hosted agents through the AI gateway'
    path: 'openai/v1'
    protocols: ['https']
    serviceUrl: foundryBackend.properties.url
    subscriptionRequired: false
    apiType: 'http'
  }
}

resource opPost 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'post-wildcard'
  properties: {
    displayName: 'POST /*'
    method: 'POST'
    urlTemplate: '/*'
  }
}

resource opGet 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'get-wildcard'
  properties: {
    displayName: 'GET /*'
    method: 'GET'
    urlTemplate: '/*'
  }
}

resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: replace(
      replace(loadTextContent('../policies/foundry-openai-v1-api-policy.xml'), '__TOKENS_PER_MINUTE__', string(tokensPerMinute)),
      '__BACKEND_ID__',
      backendId
    )
  }
  dependsOn: [nvTenant, nvAudience, nvAllowedClients]
}

// ── Observability: App Insights metrics + Log Analytics LLM logs ────────────

resource aiLogger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appInsightsResourceId
    credentials: {
      instrumentationKey: appInsightsInstrumentationKey
    }
  }
}

// metrics: true = "custom metrics with dimensions", required by llm-emit-token-metric.
// Body logging stays at 0 bytes so prompts never land in App Insights.
resource apiDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = {
  parent: api
  name: 'applicationinsights'
  properties: {
    loggerId: aiLogger.id
    alwaysLog: 'allErrors'
    httpCorrelationProtocol: 'W3C'
    verbosity: 'information'
    metrics: true
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    frontend: {
      request: { body: { bytes: 0 } }
      response: { body: { bytes: 0 } }
    }
    backend: {
      request: { body: { bytes: 0 } }
      response: { body: { bytes: 0 } }
    }
  }
}

// Gateway + generative AI gateway logs (ApiManagementGatewayLogs / ApiManagementGatewayLlmLog).
// Prompt/completion message logging is a separate, opt-in API diagnostic setting and stays off.
resource apimDiagnosticSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'apim-to-log-analytics'
  scope: apim
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      { categoryGroup: 'allLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// ── Outputs ─────────────────────────────────────────────────────────────────

output name string = apim.name
output id string = apim.id
output gatewayUrl string = apim.properties.gatewayUrl
output openAiV1BaseUrl string = '${apim.properties.gatewayUrl}/openai/v1'
output principalId string = apim.identity.principalId
