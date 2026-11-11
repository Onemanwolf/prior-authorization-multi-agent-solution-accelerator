// Azure Container Registry
// Private mode (Specs/002-private-network.md section 6.5): Premium SKU (needed for
// private endpoints), public access off, private endpoint in snet-pe. Images are
// then built and pushed from inside the VNet (jumpbox Docker), not by ACR Tasks.
param name string
param location string
param tags object = {}

param enablePrivateNetworking bool = false
param peSubnetId string = ''
param dnsZoneId string = ''

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: enablePrivateNetworking ? 'Premium' : 'Basic'
  }
  properties: {
    // Admin user disabled — Container Apps and the Foundry project pull images
    // via system-assigned managed identity + AcrPull role (see role-assignments.bicep).
    // `az acr build` (used by the postprovision hook) authenticates with the
    // deployer's Azure CLI credentials, not admin password.
    adminUserEnabled: false
    publicNetworkAccess: enablePrivateNetworking ? 'Disabled' : 'Enabled'
    networkRuleBypassOptions: 'AzureServices'
  }
}

module privateEndpoint './private-endpoint.bicep' = if (enablePrivateNetworking) {
  name: 'pe-acr'
  params: {
    name: 'pe-${name}'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetResourceId: containerRegistry.id
    groupId: 'registry'
    dnsZoneIds: [dnsZoneId]
  }
}

output name string = containerRegistry.name
output loginServer string = containerRegistry.properties.loginServer
output id string = containerRegistry.id
