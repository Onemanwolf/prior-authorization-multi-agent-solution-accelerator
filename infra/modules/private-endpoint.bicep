// Private endpoint + DNS zone group (Spec 002 section 6.2). Used for every service
// in private mode. The DNS zones live in the network resource group (infra/bootstrap).

param name string
param location string
param tags object = {}

@description('Subnet for the private endpoint (snet-pe)')
param subnetId string

@description('Resource the endpoint connects to')
param targetResourceId string

@description('Private link sub-resource, for example account, registry, blob, Sql, searchService, azuremonitor')
param groupId string

@description('Private DNS zone resource IDs that receive the endpoint records')
param dnsZoneIds array

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetId }
    privateLinkServiceConnections: [
      {
        name: groupId
        properties: {
          privateLinkServiceId: targetResourceId
          groupIds: [groupId]
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
      for (zoneId, i) in dnsZoneIds: {
        name: 'zone-${i}'
        properties: { privateDnsZoneId: zoneId }
      }
    ]
  }
}

output id string = privateEndpoint.id
