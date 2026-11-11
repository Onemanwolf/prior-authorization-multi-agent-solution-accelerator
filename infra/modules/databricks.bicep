param name string
param location string
param managedResourceGroupName string
param tags object = {}

// -- Private mode (Specs/002-private-network.md section 6.8) --------------------
// VNet injection into snet-dbx-host / snet-dbx-container, public access off,
// one databricks_ui_api private endpoint in the same VNet (serves front end and
// back end), a browser_authentication endpoint for SSO, and private Unity Catalog
// storage (blob + dfs endpoints, access connector allowed by resource instance rule).
// Serverless SQL (Genie) reaches private storage only after a Network Connectivity
// Configuration (NCC) is attached by a Databricks account admin (see the spec).

param enablePrivateNetworking bool = false
param vnetId string = ''
param hostSubnetName string = 'snet-dbx-host'
param containerSubnetName string = 'snet-dbx-container'
param peSubnetId string = ''

@description('DNS zone IDs: databricks, blob, dfs')
param dnsZoneIds object = {}

module accessConnector 'br/public:avm/res/databricks/access-connector:0.4.0' = {
  params: {
    name: '${name}-storage'
    location: location
    tags: tags
    enableTelemetry: false
    managedIdentities: {
      systemAssigned: true
    }
  }
}

module storage 'br/public:avm/res/storage/storage-account:0.33.0' = {
  params: {
    name: 'stuc${uniqueString(resourceGroup().id, name)}'
    location: location
    tags: tags
    enableTelemetry: false
    kind: 'StorageV2'
    skuName: 'Standard_LRS'
    enableHierarchicalNamespace: true
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: enablePrivateNetworking ? 'Disabled' : 'Enabled'
    networkAcls: enablePrivateNetworking ? {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      resourceAccessRules: [
        {
          tenantId: tenant().tenantId
          resourceId: accessConnector.outputs.resourceId
        }
      ]
    } : {
      defaultAction: 'Allow'
      bypass: 'None'
    }
    blobServices: {
      containers: [
        {
          name: 'unity-catalog'
          publicAccess: 'None'
        }
      ]
    }
    roleAssignments: [
      {
        roleDefinitionIdOrName: 'Storage Blob Data Contributor'
        principalId: accessConnector.outputs.systemAssignedMIPrincipalId!
        principalType: 'ServicePrincipal'
      }
    ]
  }
}

resource workspace 'Microsoft.Databricks/workspaces@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'premium'
  }
  properties: union({
    managedResourceGroupId: subscriptionResourceId('Microsoft.Resources/resourceGroups', managedResourceGroupName)
    publicNetworkAccess: enablePrivateNetworking ? 'Disabled' : 'Enabled'
    parameters: union({
      enableNoPublicIp: {
        value: true
      }
    }, enablePrivateNetworking ? {
      customVirtualNetworkId: { value: vnetId }
      customPublicSubnetName: { value: hostSubnetName }
      customPrivateSubnetName: { value: containerSubnetName }
    } : {})
  }, enablePrivateNetworking ? {
    // Public access off requires back-end private link instead of the Databricks NSG rules.
    requiredNsgRules: 'NoAzureDatabricksRules'
  } : {})
}

// Front end + back end (simplified deployment: one endpoint in the workspace VNet).
module uiApiPrivateEndpoint './private-endpoint.bicep' = if (enablePrivateNetworking) {
  name: 'pe-databricks-ui-api'
  params: {
    name: 'pe-${name}-ui-api'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetResourceId: workspace.id
    groupId: 'databricks_ui_api'
    dnsZoneIds: [dnsZoneIds.databricks]
  }
}

// Browser SSO callback (one per region per DNS zone).
module browserAuthPrivateEndpoint './private-endpoint.bicep' = if (enablePrivateNetworking) {
  name: 'pe-databricks-browser-auth'
  params: {
    name: 'pe-${name}-browser-auth'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetResourceId: workspace.id
    groupId: 'browser_authentication'
    dnsZoneIds: [dnsZoneIds.databricks]
  }
  dependsOn: [uiApiPrivateEndpoint]
}

module storageBlobPrivateEndpoint './private-endpoint.bicep' = if (enablePrivateNetworking) {
  name: 'pe-uc-storage-blob'
  params: {
    name: 'pe-${storage.outputs.name}-blob'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetResourceId: storage.outputs.resourceId
    groupId: 'blob'
    dnsZoneIds: [dnsZoneIds.blob]
  }
}

module storageDfsPrivateEndpoint './private-endpoint.bicep' = if (enablePrivateNetworking) {
  name: 'pe-uc-storage-dfs'
  params: {
    name: 'pe-${storage.outputs.name}-dfs'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetResourceId: storage.outputs.resourceId
    groupId: 'dfs'
    dnsZoneIds: [dnsZoneIds.dfs]
  }
}

output workspaceId string = workspace.id
output host string = 'https://${workspace.properties.workspaceUrl}'
output accessConnectorId string = accessConnector.outputs.resourceId
output storageUrl string = 'abfss://unity-catalog@${storage.outputs.name}.dfs.${environment().suffixes.storage}/construction-supply'
output storageAccountId string = storage.outputs.resourceId
