// ---------------------------------------------------------------------------
// Spec 001: Azure API Center registry for the AI gateway and MCP servers
// Spec: Specs/001-apim-ai-gateway-api-center.md
//
// Deploys: API Center (Free or Standard) with system-assigned MI, environments,
// MCP server registrations for the remote MCP servers the agents call, and a
// continuous APIM -> API Center sync (apiSources, preview API) with the
// API Management Service Reader role it needs.
//
// API Center is NOT available in East US 2; pass a supported region
// (e.g. eastus, swedencentral). It is metadata only and never in the request path.
//
// References:
//   https://learn.microsoft.com/azure/api-center/overview
//   https://learn.microsoft.com/azure/api-center/synchronize-api-management-apis
//   https://learn.microsoft.com/azure/api-center/register-discover-mcp-server
// ---------------------------------------------------------------------------

@description('API Center service name')
param name string

@description('API Center region (must be an API Center region; East US 2 is not)')
param location string

@description('Tags for all resources')
param tags object = {}

@description('Free (no SLA/support, 1 linked source) or Standard (free while linked to APIM Standard v2/Premium v2)')
@allowed(['Free', 'Standard'])
param skuName string = 'Free'

@description('Name of the APIM instance to synchronize (same resource group). Empty skips the sync.')
param apimName string = ''

@description('Remote MCP servers to register: [{ name, title, description, url, lifecycleStage }]')
param mcpServers array = []

var apimServiceReaderRoleId = '71522526-b88f-4d52-b57f-d31fc3546d0d'

resource apic 'Microsoft.ApiCenter/services@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  #disable-next-line BCP187 // sku is required by the RP but not in the published type schema
  sku: {
    name: skuName
  }
  properties: {}
}

resource workspace 'Microsoft.ApiCenter/services/workspaces@2024-06-01-preview' = {
  parent: apic
  name: 'default'
  properties: {
    title: 'Default workspace'
  }
}

resource externalEnv 'Microsoft.ApiCenter/services/workspaces/environments@2024-06-01-preview' = {
  parent: workspace
  name: 'external-mcp'
  properties: {
    title: 'External MCP servers'
    kind: 'production'
    description: 'Third-party remote MCP servers called directly by hosted agents'
  }
}

resource mcpApis 'Microsoft.ApiCenter/services/workspaces/apis@2024-06-01-preview' = [for server in mcpServers: {
  parent: workspace
  name: server.name
  properties: {
    title: server.title
    description: server.description
    // 'mcp' is accepted by the RP (modelAsString enum) but not yet in the published schema
    kind: 'mcp'
  }
}]

resource mcpVersions 'Microsoft.ApiCenter/services/workspaces/apis/versions@2024-06-01-preview' = [for (server, i) in mcpServers: {
  parent: mcpApis[i]
  name: 'v1-0'
  properties: {
    title: 'v1'
    lifecycleStage: server.?lifecycleStage ?? 'production'
  }
}]

resource mcpDeployments 'Microsoft.ApiCenter/services/workspaces/apis/deployments@2024-06-01-preview' = [for (server, i) in mcpServers: {
  parent: mcpApis[i]
  name: 'external'
  properties: {
    title: '${server.title} (remote)'
    environmentId: '/workspaces/default/environments/${externalEnv.name}'
    server: {
      runtimeUri: [server.url]
    }
    state: 'active'
  }
  dependsOn: [mcpVersions]
}]

// ── APIM -> API Center continuous sync ──────────────────────────────────────

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = if (!empty(apimName)) {
  name: empty(apimName) ? 'unused' : apimName
}

resource apicReadsApim 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(apimName)) {
  name: guid(resourceGroup().id, apimName, apic.id, apimServiceReaderRoleId)
  scope: apim
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', apimServiceReaderRoleId)
    principalId: apic.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource apimSource 'Microsoft.ApiCenter/services/workspaces/apiSources@2024-06-01-preview' = if (!empty(apimName)) {
  parent: workspace
  name: 'apim-ai-gateway'
  properties: {
    azureApiManagementSource: {
      resourceId: apim.id
    }
    importSpecification: 'always'
    targetLifecycleStage: 'development'
  }
  dependsOn: [apicReadsApim]
}

output name string = apic.name
output id string = apic.id
output dataApiUrl string = 'https://${apic.name}.data.${location}.azure-apicenter.ms'
output mcpRegistryUrl string = 'https://${apic.name}.data.${location}.azure-apicenter.ms/workspaces/default/v0.1/servers'
