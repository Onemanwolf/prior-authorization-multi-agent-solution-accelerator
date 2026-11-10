// Azure Bastion Standard with native client support (Spec 002 section 5.1, D4).
// Tunnelling enables `az network bastion tunnel / ssh / rdp` from a laptop,
// which VS Code Remote-SSH needs.

param name string
param publicIpName string
param location string
param tags object = {}
param subnetId string

resource publicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: publicIpName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource bastion 'Microsoft.Network/bastionHosts@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    enableTunneling: true
    scaleUnits: 2
    ipConfigurations: [
      {
        name: 'ipconfig'
        properties: {
          subnet: { id: subnetId }
          publicIPAddress: { id: publicIp.id }
        }
      }
    ]
  }
}

output name string = bastion.name
output id string = bastion.id
