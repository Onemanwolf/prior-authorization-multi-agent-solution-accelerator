// Reader on the network resource group for the jumpbox users group.
// Bastion sessions need Reader on the target VM, its NIC and the Bastion host.

param groupId string

var readerRoleId = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'

resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, groupId, readerRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', readerRoleId)
    principalId: groupId
    principalType: 'Group'
  }
}
