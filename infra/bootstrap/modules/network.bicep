// VNet, subnets and NSGs for the private deployment (Spec 002 section 5.2).
// Subnet names are fixed: Stage 2 looks them up by name.
// NSG rules follow the research design's tables, plus the baseline's jumpbox
// and build subnets as the "engineer access range". Validate with NSG flow logs
// during the spikes.

param vnetName string
param location string
param tags object = {}
param addressSpace string

@description('Subnet prefixes keyed by role: agent, mcp, pe, apim, build, jumpbox, bastion, dbxHost, dbxContainer, psql')
param prefixes object

param nsgNamePrefix string
param resourceToken string

@description('NAT gateway for outbound internet (stands in for the hub firewall)')
param natGatewayId string

// -- Helpers -----------------------------------------------------------------

func nsgRule(name string, priority int, direction string, access string, protocol string, sources string[], destinations string[], ports string[]) object => {
  name: name
  properties: union(
    {
      priority: priority
      direction: direction
      access: access
      protocol: protocol
      sourcePortRange: '*'
    },
    length(sources) == 1 ? { sourceAddressPrefix: sources[0] } : { sourceAddressPrefixes: sources },
    length(destinations) == 1 ? { destinationAddressPrefix: destinations[0] } : { destinationAddressPrefixes: destinations },
    length(ports) == 1 ? { destinationPortRange: ports[0] } : { destinationPortRanges: ports }
  )
}

func subnetDef(name string, prefix string, nsgId string, natId string, delegation string, peNetworkPolicies string) object => {
  name: name
  properties: union(
    {
      addressPrefix: prefix
      // Private subnet: no implicit internet access; egress only through the NAT gateway.
      defaultOutboundAccess: false
      privateEndpointNetworkPolicies: peNetworkPolicies
    },
    empty(nsgId) ? {} : { networkSecurityGroup: { id: nsgId } },
    empty(natId) ? {} : { natGateway: { id: natId } },
    empty(delegation) ? {} : { delegations: [{ name: replace(delegation, '/', '.'), properties: { serviceName: delegation } }] }
  )
}

// Who may reach private endpoints and the internal Container Apps environment.
var engineerRange = [prefixes.jumpbox, prefixes.build]

// -- NSGs --------------------------------------------------------------------

resource nsgAgent 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${nsgNamePrefix}agent-${resourceToken}'
  location: location
  tags: tags
  properties: {
    securityRules: [
      nsgRule('allow-https-to-vnet', 100, 'Outbound', 'Allow', 'Tcp', [prefixes.agent], ['VirtualNetwork'], ['443'])
      nsgRule('allow-https-to-entra', 120, 'Outbound', 'Allow', 'Tcp', [prefixes.agent], ['AzureActiveDirectory'], ['443'])
      // Public MCP servers and other HTTPS destinations; NAT here, hub firewall in hub mode.
      nsgRule('allow-https-to-internet', 130, 'Outbound', 'Allow', 'Tcp', [prefixes.agent], ['Internet'], ['443'])
      nsgRule('deny-other-outbound', 4000, 'Outbound', 'Deny', '*', ['*'], ['*'], ['*'])
      nsgRule('deny-internet-inbound', 4000, 'Inbound', 'Deny', '*', ['Internet'], ['*'], ['*'])
    ]
  }
}

resource nsgMcp 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${nsgNamePrefix}mcp-${resourceToken}'
  location: location
  tags: tags
  properties: {
    securityRules: [
      // Toolbox / agent calls, plus people opening the app from the jumpbox.
      nsgRule('allow-https-from-agents-and-engineers', 100, 'Inbound', 'Allow', 'Tcp', concat([prefixes.agent], engineerRange), [prefixes.mcp], ['443', '80'])
      nsgRule('deny-internet-inbound', 4000, 'Inbound', 'Deny', '*', ['Internet'], ['*'], ['*'])
      nsgRule('allow-https-to-vnet', 100, 'Outbound', 'Allow', 'Tcp', [prefixes.mcp], ['VirtualNetwork'], ['443'])
      nsgRule('allow-https-to-entra', 120, 'Outbound', 'Allow', 'Tcp', [prefixes.mcp], ['AzureActiveDirectory'], ['443'])
      // Container Apps platform (MCR, AKS packages); filtered by the firewall in hub mode.
      nsgRule('allow-https-to-internet', 130, 'Outbound', 'Allow', 'Tcp', [prefixes.mcp], ['Internet'], ['443'])
    ]
  }
}

resource nsgPe 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${nsgNamePrefix}pe-${resourceToken}'
  location: location
  tags: tags
  properties: {
    securityRules: [
      nsgRule('allow-https-from-workloads', 100, 'Inbound', 'Allow', 'Tcp', [prefixes.agent, prefixes.mcp, prefixes.apim, prefixes.dbxHost, prefixes.dbxContainer], [prefixes.pe], ['443'])
      nsgRule('allow-https-from-engineers', 110, 'Inbound', 'Allow', 'Tcp', engineerRange, [prefixes.pe], ['443'])
      nsgRule('deny-other-inbound', 4000, 'Inbound', 'Deny', '*', ['*'], ['*'], ['*'])
    ]
  }
}

resource nsgApim 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${nsgNamePrefix}apim-${resourceToken}'
  location: location
  tags: tags
  properties: {
    securityRules: [
      // Required API Management dependency.
      nsgRule('allow-https-to-keyvault', 100, 'Outbound', 'Allow', 'Tcp', [prefixes.apim], ['AzureKeyVault'], ['443'])
      // Foundry model endpoint, Databricks and other private endpoints.
      nsgRule('allow-https-to-vnet', 110, 'Outbound', 'Allow', 'Tcp', [prefixes.apim], ['VirtualNetwork'], ['443'])
      nsgRule('allow-https-to-entra', 120, 'Outbound', 'Allow', 'Tcp', [prefixes.apim], ['AzureActiveDirectory'], ['443'])
      nsgRule('allow-https-to-internet', 130, 'Outbound', 'Allow', 'Tcp', [prefixes.apim], ['Internet'], ['443'])
    ]
  }
}

resource nsgJumpbox 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${nsgNamePrefix}jumpbox-${resourceToken}'
  location: location
  tags: tags
  properties: {
    securityRules: [
      // SSH and RDP only from Azure Bastion.
      nsgRule('allow-ssh-rdp-from-bastion', 100, 'Inbound', 'Allow', 'Tcp', [prefixes.bastion], [prefixes.jumpbox], ['22', '3389'])
      nsgRule('deny-other-inbound-from-vnet', 3900, 'Inbound', 'Deny', '*', ['VirtualNetwork'], ['*'], ['*'])
      nsgRule('deny-internet-inbound', 4000, 'Inbound', 'Deny', '*', ['Internet'], ['*'], ['*'])
    ]
  }
}

resource nsgBuild 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${nsgNamePrefix}build-${resourceToken}'
  location: location
  tags: tags
  properties: {
    securityRules: [
      nsgRule('deny-internet-inbound', 4000, 'Inbound', 'Deny', '*', ['Internet'], ['*'], ['*'])
    ]
  }
}

// Databricks adds its own rules (network intent policies) when a workspace is injected.
resource nsgDbx 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${nsgNamePrefix}dbx-${resourceToken}'
  location: location
  tags: tags
  properties: {
    securityRules: []
  }
}

// -- VNet and subnets --------------------------------------------------------

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [addressSpace]
    }
    subnets: [
      subnetDef('snet-agent', prefixes.agent, nsgAgent.id, natGatewayId, 'Microsoft.App/environments', 'Disabled')
      subnetDef('snet-mcp', prefixes.mcp, nsgMcp.id, natGatewayId, 'Microsoft.App/environments', 'Disabled')
      // NSG rules apply to private endpoints only when network policies are enabled.
      subnetDef('snet-pe', prefixes.pe, nsgPe.id, '', '', 'Enabled')
      subnetDef('snet-apim', prefixes.apim, nsgApim.id, natGatewayId, 'Microsoft.Web/serverFarms', 'Disabled')
      subnetDef('snet-build', prefixes.build, nsgBuild.id, natGatewayId, '', 'Disabled')
      subnetDef('snet-jumpbox', prefixes.jumpbox, nsgJumpbox.id, natGatewayId, '', 'Disabled')
      // Bastion brings its own public IP; NSG on this subnet is optional and left off.
      subnetDef('AzureBastionSubnet', prefixes.bastion, '', '', '', 'Disabled')
      subnetDef('snet-dbx-host', prefixes.dbxHost, nsgDbx.id, natGatewayId, 'Microsoft.Databricks/workspaces', 'Disabled')
      subnetDef('snet-dbx-container', prefixes.dbxContainer, nsgDbx.id, natGatewayId, 'Microsoft.Databricks/workspaces', 'Disabled')
      // Reserved for the PostgreSQL owner (VNet-integrated flexible server).
      subnetDef('snet-psql', prefixes.psql, '', '', 'Microsoft.DBforPostgreSQL/flexibleServers', 'Disabled')
    ]
  }
}

// -- Outputs -----------------------------------------------------------------

output vnetId string = vnet.id
output vnetName string = vnet.name
output subnetIds object = {
  agent: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'snet-agent')
  mcp: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'snet-mcp')
  pe: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'snet-pe')
  apim: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'snet-apim')
  build: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'snet-build')
  jumpbox: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'snet-jumpbox')
  bastion: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'AzureBastionSubnet')
  dbxHost: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'snet-dbx-host')
  dbxContainer: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'snet-dbx-container')
  psql: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'snet-psql')
}
