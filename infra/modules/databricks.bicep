param name string
param location string
param managedResourceGroupName string
param tags object = {}

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
    publicNetworkAccess: 'Enabled'
    networkAcls: {
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
  properties: {
    managedResourceGroupId: subscriptionResourceId('Microsoft.Resources/resourceGroups', managedResourceGroupName)
    publicNetworkAccess: 'Enabled'
    parameters: {
      enableNoPublicIp: {
        value: true
      }
    }
  }
}

output workspaceId string = workspace.id
output host string = 'https://${workspace.properties.workspaceUrl}'
output accessConnectorId string = accessConnector.outputs.resourceId
output storageUrl string = 'abfss://unity-catalog@${storage.outputs.name}.dfs.${environment().suffixes.storage}/construction-supply'
