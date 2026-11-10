// Private DNS zones linked to the VNet (Spec 002 section 5.3, standalone mode).
// In hub mode these zones live in the hub and resolve through the hub DNS resolver.

param zones string[]
param vnetId string
param vnetName string
param tags object = {}

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = [
  for z in zones: {
    name: z
    location: 'global'
    tags: tags
  }
]

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [
  for (z, i) in zones: {
    parent: zone[i]
    name: 'link-${vnetName}'
    location: 'global'
    tags: tags
    properties: {
      registrationEnabled: false
      virtualNetwork: {
        id: vnetId
      }
    }
  }
]

@description('Zone name -> zone resource ID')
output zoneIds object = toObject(zones, z => z, z => resourceId('Microsoft.Network/privateDnsZones', z))
