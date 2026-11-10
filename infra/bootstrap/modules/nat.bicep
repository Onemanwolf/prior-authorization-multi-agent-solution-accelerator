// NAT gateway: outbound internet for the private subnets (Spec 002 D6).
// Stands in for the hub firewall of the research design.

param name string
param publicIpName string
param location string
param tags object = {}

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

resource natGateway 'Microsoft.Network/natGateways@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    idleTimeoutInMinutes: 4
    publicIpAddresses: [
      { id: publicIp.id }
    ]
  }
}

output id string = natGateway.id
@description('Egress IP: add it to allow-lists (for example a Databricks workspace IP access list)')
output publicIpAddress string = publicIp.properties.ipAddress
