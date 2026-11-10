// Key Vault for the jumpbox (Spec 002 section 5.1, section 5.4.3). Private endpoint only.
// Holds the desktop password, which the VM generates and stores at first boot.

param name string
param privateEndpointName string
param location string
param tags object = {}
param peSubnetId string
param dnsZoneId string
param enablePurgeProtection bool = false

@description('Principals ({id, type}) that may read secrets (desktop mode)')
param secretReaders array = []

var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: privateEndpointName
  location: location
  tags: tags
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [
      {
        name: 'vault'
        properties: {
          privateLinkServiceId: vault.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource zoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: { privateDnsZoneId: dnsZoneId }
      }
    ]
  }
}

resource readers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for r in secretReaders: {
    name: guid(vault.id, r.id, keyVaultSecretsUserRoleId)
    scope: vault
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
      principalId: r.id
      principalType: r.type
    }
  }
]

output name string = vault.name
output id string = vault.id
