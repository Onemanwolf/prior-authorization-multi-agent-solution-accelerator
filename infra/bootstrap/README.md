# Network bootstrap (Spec 002, Stage 1)

Creates the private network and a Linux jumpbox for deploying this template privately.
Run it **from a laptop**. Then deploy the solution **from the jumpbox** (Stage 2).
Design: [`Specs/002-private-network.md`](../../Specs/002-private-network.md).

## What it creates

Everything goes into a new resource group, `rg-<azd environment name>`. For example `wc01_pai_ai_network` creates `rg-wc01_pai_ai_network`. Existing resource groups aren't touched.

| Resource | Notes |
|---|---|
| VNet `vnet-<token>` (`10.50.0.0/22`) | Subnets below; no implicit internet access |
| NSGs `nsg-<role>-<token>` | Rules from the research design (Spec 002 section 5.2.1) |
| NAT gateway `ng-<token>` | Outbound internet for agents, apps, APIM, jumpbox, builds |
| 16 private DNS zones | Linked to the VNet; Stage 2 adds records |
| Azure Bastion `bas-<token>` (Standard) | Native client: `az network bastion tunnel / ssh / rdp` |
| Key Vault `kv-<token>` | Private endpoint only; holds the desktop password |
| Jumpbox `vm-jump-<token>` | Ubuntu 24.04 LTS, no public IP, Entra SSH sign-in, auto-shutdown |

| Subnet | Default range | For |
|---|---|---|
| `snet-agent` | 10.50.0.0/24 | Foundry hosted agents (network injection) |
| `snet-mcp` | 10.50.1.0/27 | Internal Container Apps (frontend, backend) |
| `snet-pe` | 10.50.1.64/26 | Private endpoints |
| `snet-apim` | 10.50.1.128/27 | APIM StandardV2 outbound integration |
| `snet-build` | 10.50.1.160/27 | Reserved: CI runner |
| `snet-jumpbox` | 10.50.1.192/27 | Jumpbox |
| `AzureBastionSubnet` | 10.50.2.0/26 | Bastion |
| `snet-dbx-host`, `snet-dbx-container` | 10.50.2.64/26, 10.50.2.128/26 | Databricks VNet injection (optional) |
| `snet-psql` | 10.50.2.192/28 | Reserved: PostgreSQL (other team) |

## Before you start

- **Owner** on the subscription (or Contributor + Role Based Access Control Administrator).
- Laptop: Azure CLI with `az extension add -n bastion` and `az extension add -n ssh`, `azd`, Python 3, and VS Code with the **Remote - SSH** extension.
- An Entra group for the people who will use the jumpbox (its object ID).
- Resource providers: `Microsoft.Network`, `Microsoft.Compute`, `Microsoft.KeyVault`, `Microsoft.DevTestLab`.

## Deploy

```powershell
cd infra/bootstrap
azd env new wc01_pai_ai_network
azd env set AZURE_LOCATION eastus2                   # same region as Stage 2
azd env set JUMPBOX_USERS_GROUP_ID <group-object-id>
azd env set JUMPBOX_DESKTOP false                    # true = GNOME + Edge + VS Code over RDP
azd up                                               # about 20-25 minutes
```

The `preprovision` hook checks the settings and uses `~/.ssh/id_ed25519.pub` or `~/.ssh/id_rsa.pub` for the break-glass admin. If you have neither, it creates `~/.ssh/jumpbox_ed25519`. At the end, `postprovision` prints the connect commands.

Optional settings:

| Setting | Default | Meaning |
|---|---|---|
| `NETWORK_ADDRESS_SPACE` | `10.50.0.0/22` | A private /22 or larger. All subnets are carved from it. |
| `JUMPBOX_VM_SIZE` | `Standard_D2s_v5` headless, `Standard_D4s_v5` desktop | |
| `JUMPBOX_SHUTDOWN_TIME` / `JUMPBOX_TIMEZONE` | `1900` / `UTC` | Daily auto-shutdown. Windows time zone IDs, for example `Eastern Standard Time`. |
| `JUMPBOX_SSH_PUBLIC_KEY` | from `~/.ssh` | Break-glass admin key (`azureadmin`) |
| `JUMPBOX_KEY_VAULT_PURGE_PROTECTION` | `false` | `true` if your policies require it (the vault then can't be purged for 7 days) |

After the VM is created, the `setup-jumpbox` extension installs its tools (and re-runs whenever `scripts/setup-jumpbox.sh` changes): about 10 minutes headless, 25 minutes with the desktop. Run `jumpbox-check` to see progress and versions. The full log is `/var/log/jumpbox-setup.log`.

## Connect

### Terminal (Entra ID)

```powershell
az network bastion ssh --name <BASTION_NAME> --resource-group <rg> --target-resource-id <JUMPBOX_VM_ID> --auth-type AAD
```

### VS Code (Remote-SSH, Entra ID)

1. Start a tunnel and leave it running:
   ```powershell
   az network bastion tunnel --name <BASTION_NAME> --resource-group <rg> --target-resource-id <JUMPBOX_VM_ID> --resource-port 22 --port 2222
   ```
2. Get an Entra SSH certificate. It's valid for about an hour, so repeat this when VS Code says the connection was refused:
   ```powershell
   az ssh cert --file $HOME\.ssh\jumpbox-cert.pub
   ```
   This uses `~/.ssh/id_rsa` (it creates one if needed). The certificate is written to `jumpbox-cert.pub-aadcert.pub`.
3. Add to `~/.ssh/config`:
   ```
   Host jumpbox
     HostName 127.0.0.1
     Port 2222
     User <you>@<your-tenant-domain>
     CertificateFile ~/.ssh/jumpbox-cert.pub-aadcert.pub
     IdentityFile ~/.ssh/id_rsa
     DynamicForward 1080
   ```
4. In VS Code: **Remote-SSH: Connect to Host...** and pick `jumpbox`.

The first time you sign in, the jumpbox adds you to the `docker` group and installs the `azd` agents extension. **Disconnect and reconnect once**: group changes (`docker`, and `aad_admins` for password-free `sudo`) only apply to new sessions. Until then `docker` says "permission denied" and `sudo` asks for a password that doesn't exist (press Ctrl+C). `newgrp docker` works for the current shell.

### Sign in to Azure on the jumpbox

Always pass the tenant. A plain `az login` uses your account's home tenant and default subscription, which may not be this one; the login banner prints these commands with the right IDs:

```bash
az login --tenant <tenant-id> --use-device-code       # enter the code on your laptop's browser
az account set --subscription <subscription-id>
azd auth login --tenant-id <tenant-id> --use-device-code
```

Check you're signed in as yourself, not the VM: `az account show --query user` should show your UPN and `"type": "user"`. (`az login --identity` signs in the VM's managed identity, which can read the jumpbox Key Vault but can't deploy.)

### Browse private sites from your laptop

While the VS Code (or `ssh jumpbox`) session is open, `DynamicForward 1080` provides a SOCKS proxy through the jumpbox. Start a separate Edge profile that uses it:

```powershell
msedge --proxy-server="socks5://127.0.0.1:1080" --user-data-dir="$env:TEMP\edge-private"
```

Names resolve on the jumpbox side, so private endpoints (the app, the Foundry portal's data calls, Azure portal Logs) work.

### Desktop (only with `JUMPBOX_DESKTOP=true`)

RDP to Linux uses a local account, `jumpuser`, because Entra ID sign-in covers SSH only. The VM creates the password itself and stores it in Key Vault. The vault is private, so read the password from the jumpbox:

```bash
# on the jumpbox (Entra SSH session)
az login --use-device-code
az keyvault secret show --vault-name <JUMPBOX_KEY_VAULT_NAME> --name jumpbox-desktop-password --query value -o tsv
```

Then, from a Windows laptop:

```powershell
az network bastion rdp --name <BASTION_NAME> --resource-group <rg> --target-resource-id <JUMPBOX_VM_ID>
```

Or use the portal: VM, then **Connect**, then **Bastion**, then RDP. To set a new password, for example when someone leaves the group, run `sudo jumpbox-rotate-password` on the jumpbox.

## Next: deploy the solution from the jumpbox

```bash
jumpbox-check
git clone <repo-url> && cd prior-authorization-multi-agent-solution-accelerator && git switch private-network
az login --use-device-code && azd auth login --use-device-code
azd env new wc01_pai_ai_private
azd env set AZURE_LOCATION eastus2
azd env set ENABLE_PRIVATE_NETWORKING true
azd env set NETWORK_RESOURCE_GROUP <NETWORK_RESOURCE_GROUP>
azd env set NETWORK_VNET_NAME <NETWORK_VNET_NAME>
```

The private version of the main template is Phase 2 of Spec 002. Until it lands, Stage 2 isn't available.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `jumpbox-check` shows `status=failed steps=...` | Read `/var/log/jumpbox-setup.log`, then re-run the failed step's commands. Package downloads need the NAT gateway. |
| Entra SSH: "Permission denied" | You need the **Virtual Machine Administrator Login** role on the VM. The deployer and `JUMPBOX_USERS_GROUP_ID` get it. Role changes can take a few minutes. |
| `AADSSHLoginForLinux` or `setup-jumpbox` extension failed | The setup runs after the Entra extension so their package installs don't collide. Other extensions added by Azure Policy (for example `QualysAgentLinux`) can still hold the package lock briefly. Re-run `azd provision`, or re-apply the Entra extension with `az vm extension set --publisher Microsoft.Azure.ActiveDirectory --name AADSSHLoginForLinux --resource-group <rg> --vm-name <vm>`. |
| Bastion says "network is unstable", or SSH logs show "Certificate invalid: expired" | Refresh the browser tab. The portal keeps an old Entra SSH certificate after the VM restarts. |
| VM agent "Not Ready" / "unresponsive" after a resize or restart (extensions stuck "Updating") | `az vm reapply -g <rg> -n <vm>` (portal: VM, then **Help**, then **Reapply**). A plain restart may not clear it. |
| Desktop login fails with the right password | The login screen always uses `jumpuser` and only asks for the password. If it still fails, read `/var/log/xrdp-sesman.log` (`AUTHFAIL`, `user unknown`). The Remote Desktop app through `az network bastion tunnel ... --resource-port 3389 --port 13389` supports paste. |
| No way in at all | Break-glass: the serial console (boot diagnostics are on), or `az network bastion ssh --auth-type ssh-key --username azureadmin --ssh-key <private key>`. |
| Desktop password secret missing | The VM retries for 15 minutes while its Key Vault role propagates. Run `sudo jumpbox-rotate-password` over SSH. |

## Remove

`azd down --purge` in this folder. Remove Stage 2 first: its Foundry account holds `snet-agent`, and the subnet can't be deleted until Foundry is purged.
