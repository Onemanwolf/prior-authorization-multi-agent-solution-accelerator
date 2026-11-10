// Ubuntu 24.04 LTS jumpbox (Spec 002 section 5.4). No public IP; reached through Bastion.
// - Entra ID SSH sign-in (AADSSHLoginForLinux); local admin is SSH-key only.
// - A Custom Script extension runs scripts/setup-jumpbox.sh (toolchain, optional desktop)
//   after the Entra extension: both use apt, and running them together corrupted the
//   apt cache on the first deployment. Unlike VM customData (fixed at creation), the
//   extension re-runs when the script changes. The script is idempotent.
// - Daily auto-shutdown.

param vmName string
param nicName string
param location string
param tags object = {}
param subnetId string
param vmSize string
param adminUsername string
param sshPublicKey string

@description('Setup script (plain text; encoded here), run by the Custom Script extension')
param setupScript string

param shutdownTime string = '1900'
param timeZone string = 'UTC'

@description('Key Vault where the VM stores the desktop password')
param keyVaultName string

@description('Principals ({id, type}) granted Virtual Machine Administrator Login')
param loginPrincipals array = []

var vmAdministratorLoginRoleId = '1c0163c0-47e6-4577-8991-ea5c82e286e4'
var keyVaultSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: nicName
  location: location
  tags: tags
  properties: {
    enableAcceleratedNetworking: true
    ipConfigurations: [
      {
        name: 'ipconfig'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: { id: subnetId }
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: 128
        deleteOption: 'Delete'
        managedDisk: {
          storageAccountType: 'Premium_LRS'
        }
      }
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
        patchSettings: {
          // unattended-upgrades handles security patches (configured by the setup script).
          patchMode: 'ImageDefault'
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
          properties: {
            deleteOption: 'Delete'
          }
        }
      ]
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
    diagnosticsProfile: {
      // Managed boot diagnostics: enables the serial console for break-glass access.
      bootDiagnostics: {
        enabled: true
      }
    }
  }
}

// Entra ID sign-in over SSH (`az ssh`, `az network bastion ssh --auth-type AAD`).
resource aadSshLogin 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = {
  parent: vm
  name: 'AADSSHLoginForLinux'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.ActiveDirectory'
    type: 'AADSSHLoginForLinux'
    typeHandlerVersion: '1.0'
    autoUpgradeMinorVersion: true
  }
}

// Toolchain and optional desktop. Runs after the Entra extension (see header).
// The script is in public settings (it holds no secrets) so it re-runs only when it changes.
resource setup 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = {
  parent: vm
  name: 'setup-jumpbox'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.Extensions'
    type: 'CustomScript'
    typeHandlerVersion: '2.1'
    autoUpgradeMinorVersion: true
    settings: {
      script: base64(setupScript)
    }
  }
  dependsOn: [aadSshLogin]
}

// The resource name must be shutdown-computevm-<vmName> for the portal to show it.
resource autoShutdown 'Microsoft.DevTestLab/schedules@2018-09-15' = {
  name: 'shutdown-computevm-${vmName}'
  location: location
  tags: tags
  properties: {
    status: 'Enabled'
    taskType: 'ComputeVmShutdownTask'
    dailyRecurrence: {
      time: shutdownTime
    }
    timeZoneId: timeZone
    notificationSettings: {
      status: 'Disabled'
    }
    targetResourceId: vm.id
  }
}

resource adminLogin 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for p in loginPrincipals: {
    name: guid(vm.id, p.id, vmAdministratorLoginRoleId)
    scope: vm
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', vmAdministratorLoginRoleId)
      principalId: p.id
      principalType: p.type
    }
  }
]

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// The VM stores (and rotates) the desktop password itself, so it never leaves Azure.
resource vmSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, vm.id, keyVaultSecretsOfficerRoleId)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficerRoleId)
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output vmId string = vm.id
output vmName string = vm.name
output principalId string = vm.identity.principalId
output privateIp string = nic.properties.ipConfigurations[0].properties.privateIPAddress
