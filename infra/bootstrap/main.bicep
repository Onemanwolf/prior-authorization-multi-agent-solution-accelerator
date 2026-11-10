// ---------------------------------------------------------------------------
// Spec 002, Stage 1: network bootstrap for the private deployment
// Spec: Specs/002-private-network.md (section 5)
//
// Deploys into rg-<environment name>: VNet (research-design subnet layout), NSGs,
// NAT gateway, private DNS zones, Azure Bastion (Standard, tunnelling), Key Vault
// (private) and an Ubuntu 24.04 LTS jumpbox with the deployment toolchain.
// Run from a laptop: everything here is ARM, which is reachable publicly.
// Stage 2 (the main template) then runs `azd up` from the jumpbox.
// ---------------------------------------------------------------------------

targetScope = 'subscription'

// -- Parameters --------------------------------------------------------------

@minLength(1)
@maxLength(64)
@description('azd environment name; the resource group is rg-<environmentName>')
param environmentName string

@minLength(1)
@description('Region for every resource. Must match the region of the Stage 2 deployment (Foundry and its VNet must be in the same region).')
param location string

@description('VNet address space. The subnet layout (Spec 002 section 5.2) needs a /22 or larger.')
param addressSpace string = '10.50.0.0/22'

@description('Object ID of the person or identity running azd (azd sets AZURE_PRINCIPAL_ID). Gets VM administrator login.')
param principalId string = ''

@description('Principal type of principalId')
@allowed(['User', 'ServicePrincipal'])
param principalType string = 'User'

@description('Object ID of the Entra group whose members may use the jumpbox (empty: only the deployer)')
param jumpboxUsersGroupId string = ''

@description('"true" installs a GNOME desktop, xrdp, Microsoft Edge and VS Code; "false" is headless (Entra SSH + VS Code Remote-SSH)')
@allowed(['true', 'false'])
param jumpboxDesktop string = 'false'

@description('VM size; empty picks Standard_D2s_v5 (headless) or Standard_D4s_v5 (desktop)')
param jumpboxVmSize string = ''

@description('Break-glass local admin (SSH key only)')
param jumpboxAdminUsername string = 'azureadmin'

@description('SSH public key for the break-glass admin (the preprovision hook fills it from ~/.ssh)')
param jumpboxSshPublicKey string

@description('Daily auto-shutdown time, HHmm')
param jumpboxShutdownTime string = '1900'

@description('Windows time zone ID for the auto-shutdown schedule, for example "UTC" or "Eastern Standard Time"')
param jumpboxTimeZone string = 'UTC'

@description('"true" enables Key Vault purge protection (the vault then can\'t be purged for 7 days after deletion)')
@allowed(['true', 'false'])
param keyVaultPurgeProtection string = 'false'

// -- Variables ---------------------------------------------------------------

var abbrs = loadJsonContent('../abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  'solution-accelerator': 'prior-auth-maf'
  'deployment-stage': 'network'
}
var desktop = jumpboxDesktop == 'true'
var vmSize = !empty(jumpboxVmSize) ? jumpboxVmSize : (desktop ? 'Standard_D4s_v5' : 'Standard_D2s_v5')

// Subnet layout from Spec 002 section 5.2 / research design, as offsets into addressSpace.
// With 10.50.0.0/22: agent 10.50.0.0/24, mcp 10.50.1.0/27, pe 10.50.1.64/26,
// apim 10.50.1.128/27, build 10.50.1.160/27, jumpbox 10.50.1.192/27,
// bastion 10.50.2.0/26, dbxHost 10.50.2.64/26, dbxContainer 10.50.2.128/26, psql 10.50.2.192/28.
var prefixes = {
  agent: cidrSubnet(addressSpace, 24, 0)
  mcp: cidrSubnet(addressSpace, 27, 8)
  pe: cidrSubnet(addressSpace, 26, 5)
  apim: cidrSubnet(addressSpace, 27, 12)
  build: cidrSubnet(addressSpace, 27, 13)
  jumpbox: cidrSubnet(addressSpace, 27, 14)
  bastion: cidrSubnet(addressSpace, 26, 8)
  dbxHost: cidrSubnet(addressSpace, 26, 9)
  dbxContainer: cidrSubnet(addressSpace, 26, 10)
  psql: cidrSubnet(addressSpace, 28, 44)
}

// Private DNS zones used by Stage 2 (Spec 002 section 5.3). The Container Apps
// environment zone is created by Stage 2 because it needs the environment's IP.
var privateDnsZones = [
  'privatelink.cognitiveservices.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.services.ai.azure.com'
  'privatelink.search.windows.net'
  'privatelink.azurecr.io'
  'privatelink.vaultcore.azure.net'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.dfs.${environment().suffixes.storage}'
  'privatelink.documents.azure.com'
  'privatelink.monitor.azure.com'
  'privatelink.oms.opinsights.azure.com'
  'privatelink.ods.opinsights.azure.com'
  'privatelink.agentsvc.azure-automation.net'
  'privatelink.azure-api.net'
  'privatelink.azuredatabricks.net'
  'privatelink.postgres.database.azure.com'
]

var vnetName = '${abbrs.networkVirtualNetworks}${resourceToken}'
var keyVaultName = '${abbrs.keyVaultVaults}${resourceToken}'
var vmName = '${abbrs.computeVirtualMachines}jump-${resourceToken}'

var deployer = empty(principalId) ? [] : [{ id: principalId, type: principalType }]
var usersGroup = empty(jumpboxUsersGroupId) ? [] : [{ id: jumpboxUsersGroupId, type: 'Group' }]
var jumpboxPrincipals = concat(deployer, usersGroup)

// Setup script, run on the VM by a Custom Script extension (modules/jumpbox.bicep).
// Interpolation (not format()) because the script contains braces.
// CRs are stripped in case a Windows checkout converted the script to CRLF.
var setupScript = replace(loadTextContent('scripts/setup-jumpbox.sh'), '\r', '')
var setupScriptWithSettings = '#!/bin/bash\nexport JUMPBOX_DESKTOP="${jumpboxDesktop}"\nexport JUMPBOX_KEY_VAULT="${keyVaultName}"\nexport JUMPBOX_ADMIN="${jumpboxAdminUsername}"\nexport JUMPBOX_TENANT_ID="${tenant().tenantId}"\nexport JUMPBOX_SUBSCRIPTION_ID="${subscription().subscriptionId}"\n${setupScript}'

// -- Resource group ----------------------------------------------------------

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: '${abbrs.resourcesResourceGroups}${environmentName}'
  location: location
  tags: tags
}

// -- Network -----------------------------------------------------------------

module nat './modules/nat.bicep' = {
  name: 'nat-gateway'
  scope: rg
  params: {
    name: '${abbrs.networkNatGateways}${resourceToken}'
    publicIpName: '${abbrs.networkPublicIPAddresses}${abbrs.networkNatGateways}${resourceToken}'
    location: location
    tags: tags
  }
}

module network './modules/network.bicep' = {
  name: 'network'
  scope: rg
  params: {
    vnetName: vnetName
    location: location
    tags: tags
    addressSpace: addressSpace
    prefixes: prefixes
    nsgNamePrefix: abbrs.networkNetworkSecurityGroups
    resourceToken: resourceToken
    natGatewayId: nat.outputs.id
  }
}

module dns './modules/dns.bicep' = {
  name: 'private-dns'
  scope: rg
  params: {
    zones: privateDnsZones
    vnetId: network.outputs.vnetId
    vnetName: vnetName
    tags: tags
  }
}

module bastion './modules/bastion.bicep' = {
  name: 'bastion'
  scope: rg
  params: {
    name: '${abbrs.networkBastionHosts}${resourceToken}'
    publicIpName: '${abbrs.networkPublicIPAddresses}${abbrs.networkBastionHosts}${resourceToken}'
    location: location
    tags: tags
    subnetId: network.outputs.subnetIds.bastion
  }
}

// -- Key Vault (desktop password; private endpoint only) ---------------------

module keyVault './modules/keyvault.bicep' = {
  name: 'key-vault'
  scope: rg
  params: {
    name: keyVaultName
    privateEndpointName: '${abbrs.networkPrivateEndpoints}${keyVaultName}'
    location: location
    tags: tags
    peSubnetId: network.outputs.subnetIds.pe
    dnsZoneId: dns.outputs.zoneIds['privatelink.vaultcore.azure.net']
    enablePurgeProtection: keyVaultPurgeProtection == 'true'
    secretReaders: desktop ? jumpboxPrincipals : []
  }
}

// -- Jumpbox -----------------------------------------------------------------

module jumpbox './modules/jumpbox.bicep' = {
  name: 'jumpbox'
  scope: rg
  params: {
    vmName: vmName
    nicName: '${abbrs.networkNetworkInterfaces}${vmName}'
    location: location
    tags: tags
    subnetId: network.outputs.subnetIds.jumpbox
    vmSize: vmSize
    adminUsername: jumpboxAdminUsername
    sshPublicKey: jumpboxSshPublicKey
    setupScript: setupScriptWithSettings
    shutdownTime: jumpboxShutdownTime
    timeZone: jumpboxTimeZone
    // The VM writes the desktop password to Key Vault at first boot. Taking the
    // vault name from the module output makes the VM wait for the vault, its
    // private endpoint and its DNS record.
    keyVaultName: keyVault.outputs.name
    loginPrincipals: jumpboxPrincipals
  }
}

// Reader on the network resource group lets the users group open Bastion
// sessions (Bastion needs Reader on the VM, its NIC and the Bastion host).
module access './modules/access.bicep' = if (!empty(jumpboxUsersGroupId)) {
  name: 'jumpbox-access'
  scope: rg
  params: {
    groupId: jumpboxUsersGroupId
  }
}

// -- Outputs (read by Stage 2 and printed by the postprovision hook) ---------

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location
output NETWORK_RESOURCE_GROUP string = rg.name
output NETWORK_DNS_ZONE_RESOURCE_GROUP string = rg.name
output NETWORK_VNET_NAME string = vnetName
output NETWORK_VNET_ID string = network.outputs.vnetId
output NETWORK_ADDRESS_SPACE string = addressSpace
output NETWORK_SUBNET_AGENT_ID string = network.outputs.subnetIds.agent
output NETWORK_SUBNET_MCP_ID string = network.outputs.subnetIds.mcp
output NETWORK_SUBNET_PE_ID string = network.outputs.subnetIds.pe
output NETWORK_SUBNET_APIM_ID string = network.outputs.subnetIds.apim
output NETWORK_SUBNET_BUILD_ID string = network.outputs.subnetIds.build
output NETWORK_SUBNET_JUMPBOX_ID string = network.outputs.subnetIds.jumpbox
output NETWORK_SUBNET_DBX_HOST_ID string = network.outputs.subnetIds.dbxHost
output NETWORK_SUBNET_DBX_CONTAINER_ID string = network.outputs.subnetIds.dbxContainer
output NETWORK_SUBNET_PSQL_ID string = network.outputs.subnetIds.psql
output NETWORK_NAT_PUBLIC_IP string = nat.outputs.publicIpAddress
output BASTION_NAME string = bastion.outputs.name
output JUMPBOX_VM_NAME string = vmName
output JUMPBOX_VM_ID string = jumpbox.outputs.vmId
output JUMPBOX_PRIVATE_IP string = jumpbox.outputs.privateIp
output JUMPBOX_ADMIN_USERNAME string = jumpboxAdminUsername
output JUMPBOX_KEY_VAULT_NAME string = keyVault.outputs.name
output JUMPBOX_DESKTOP_ENABLED string = jumpboxDesktop
