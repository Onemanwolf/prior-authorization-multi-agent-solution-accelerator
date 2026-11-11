// Container Apps Environment
// Private mode (Specs/002-private-network.md section 6.6): workload-profiles
// environment injected into snet-mcp with internal-only ingress, logs through
// Azure Monitor diagnostic settings (Log Analytics public ingestion is off), and a
// private DNS zone for the environment's default domain.
param name string
param location string
param tags object = {}
param logAnalyticsWorkspaceId string

param enablePrivateNetworking bool = false

@description('Infrastructure subnet (snet-mcp, delegated to Microsoft.App/environments)')
param infrastructureSubnetId string = ''

@description('VNet linked to the environment private DNS zone')
param vnetId string = ''

var publicProperties = {
  appLogsConfiguration: {
    destination: 'log-analytics'
    logAnalyticsConfiguration: {
      customerId: reference(logAnalyticsWorkspaceId, '2023-09-01').customerId
      sharedKey: listKeys(logAnalyticsWorkspaceId, '2023-09-01').primarySharedKey
    }
  }
}

var privateProperties = {
  appLogsConfiguration: {
    destination: 'azure-monitor'
  }
  vnetConfiguration: {
    infrastructureSubnetId: infrastructureSubnetId
    internal: true
  }
  workloadProfiles: [
    {
      name: 'Consumption'
      workloadProfileType: 'Consumption'
    }
  ]
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: name
  location: location
  tags: tags
  properties: enablePrivateNetworking ? privateProperties : publicProperties
}

resource logs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (enablePrivateNetworking) {
  name: 'logs-to-log-analytics'
  scope: containerAppsEnvironment
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
  }
}

module privateDns './container-apps-dns.bicep' = if (enablePrivateNetworking) {
  name: 'container-apps-private-dns'
  params: {
    zoneName: containerAppsEnvironment.properties.defaultDomain
    staticIp: containerAppsEnvironment.properties.staticIp
    vnetId: vnetId
    tags: tags
  }
}

output environmentId string = containerAppsEnvironment.id
output defaultDomain string = containerAppsEnvironment.properties.defaultDomain
output staticIp string = containerAppsEnvironment.properties.staticIp
