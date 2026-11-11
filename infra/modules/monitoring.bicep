// Log Analytics Workspace + Application Insights
// Private mode (Specs/002-private-network.md section 6.7): public ingestion and
// query off on both, and an Azure Monitor Private Link Scope (AMPLS) with a private
// endpoint so telemetry and queries flow privately.
param logAnalyticsName string
param appInsightsName string
param location string
param tags object = {}

param enablePrivateNetworking bool = false
param peSubnetId string = ''

@description('DNS zone IDs for the AMPLS endpoint: monitor, oms, ods, agentsvc, blob')
param amplsDnsZoneIds array = []

var publicAccess = enablePrivateNetworking ? 'Disabled' : 'Enabled'

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    publicNetworkAccessForIngestion: publicAccess
    publicNetworkAccessForQuery: publicAccess
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspace.id
    publicNetworkAccessForIngestion: publicAccess
    publicNetworkAccessForQuery: publicAccess
  }
}

// -- Azure Monitor Private Link Scope ----------------------------------------------

resource ampls 'Microsoft.Insights/privateLinkScopes@2021-07-01-preview' = if (enablePrivateNetworking) {
  name: 'ampls-${logAnalyticsName}'
  location: 'global'
  tags: tags
  properties: {
    accessModeSettings: {
      ingestionAccessMode: 'PrivateOnly'
      queryAccessMode: 'PrivateOnly'
    }
  }
}

resource amplsWorkspace 'Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview' = if (enablePrivateNetworking) {
  parent: ampls
  name: 'log-analytics'
  properties: {
    linkedResourceId: logAnalyticsWorkspace.id
  }
}

resource amplsAppInsights 'Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview' = if (enablePrivateNetworking) {
  parent: ampls
  name: 'app-insights'
  properties: {
    linkedResourceId: appInsights.id
  }
}

module amplsPrivateEndpoint './private-endpoint.bicep' = if (enablePrivateNetworking) {
  name: 'pe-ampls'
  params: {
    name: 'pe-ampls-${logAnalyticsName}'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetResourceId: ampls.id
    groupId: 'azuremonitor'
    dnsZoneIds: amplsDnsZoneIds
  }
  dependsOn: [amplsWorkspace, amplsAppInsights]
}

output logAnalyticsWorkspaceId string = logAnalyticsWorkspace.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
output appInsightsResourceId string = appInsights.id
