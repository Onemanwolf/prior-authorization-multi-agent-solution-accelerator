# Spec 002: Private network deployment (VNet, Linux jumpbox, private endpoints)

| | |
|---|---|
| **Status** | Draft for review (rev 2). Branch `private-network` (based on `Ai-gateway`). Phase 1 (network bootstrap) built in `infra/bootstrap/`, not yet deployed. |
| **Date** | 2026-11-10 (rev 2: Linux jumpbox; subnets aligned with the research document's network design; separate resource groups §5.8) |
| **Owner** | Tim Oleson |
| **Template** | `prior-auth-maf` (this repo, root `azure.yaml`) |
| **Builds on** | [Spec 001](001-apim-ai-gateway-api-center.md) (APIM AI gateway). *White Cap Supply Assistant: Azure Solution Research* v2, "Network design (private option)" (revised 2026-11-09). Microsoft samples [`15-private-network-standard-agent-setup`](https://github.com/microsoft-foundry/foundry-samples/tree/main/infrastructure/infrastructure-setup-bicep/15-private-network-standard-agent-setup) and [`16-private-network-standard-agent-apim-setup`](https://github.com/microsoft-foundry/foundry-samples/tree/main/infrastructure/infrastructure-setup-bicep/16-private-network-standard-agent-apim-setup) |
| **Drivers** | Security findings on `rg-wc01_pai_ai_controlplane` (2026-11-10): Cognitive Services public access, Container Registry unrestricted access, Application Insights public ingestion/query, PostgreSQL public access |

---

## 1. Summary

The template deploys everything with public endpoints today. This spec adds an opt-in **private network mode** that deploys in three steps:

1. **Stage 1 – network bootstrap** (from a laptop, ARM only): a new azd project in `infra/bootstrap/` creates the VNet with the research document's subnet layout, NSGs, private DNS zones, a NAT gateway, Azure Bastion, Key Vault and an **Ubuntu 24.04 LTS jumpbox** with Docker, `az`, `azd`, Python, Git and the rest of the deployment toolchain. VS Code connects to it over Remote-SSH; an optional desktop mode adds GNOME, Microsoft Edge and VS Code on the box.
2. **Stage 2 – solution deployment** (from the jumpbox, inside the VNet): the existing template runs `azd up` with `ENABLE_PRIVATE_NETWORKING=true`. Every service gets a private endpoint, public network access is disabled, and Foundry hosted agents are injected into the VNet.
3. **Verify and lock**: a preflight script confirms DNS and connectivity, then the hooks switch off the last public endpoints (APIM).

Private mode is **off by default**. With `ENABLE_PRIVATE_NETWORKING=false` (or unset) `azd up` behaves exactly as it does on `Ai-gateway` today.

### Goals
1. No data-plane endpoint of the solution is reachable from the internet: Foundry, APIM gateway, Container Registry, Container Apps, Application Insights / Log Analytics, Storage, Cosmos DB, AI Search and (optional) Databricks.
2. Hosted agents (prior-auth agents and the supply agent) run with their outbound traffic in our VNet, and reach APIM, Foundry and Databricks through private endpoints.
3. A team member can go from an empty subscription to a working private deployment with two `azd up` runs and a Bastion connection, using documented steps.
4. The four security findings above are resolved for resources this template owns (§11).
5. The baseline uses **the same subnet names and address plan as the White Cap research design**, so moving to White Cap's hub is a change of DNS and egress, not a redesign (§5.7).
6. The public pilot path keeps working unchanged.

### Non-goals (this spec)
- Converting the existing environment (`wc01_pai_ai_controlplane`) in place. **Not possible for hosted agents**: Microsoft states that network injection "must be included when you first create the Foundry account". Private mode is always a **new environment**.
- Building White Cap's hub (firewall, DNS private resolver, VPN/ExpressRoute gateway). The baseline is a standalone VNet that stands in for the hub (§5.7); §5.6 covers deploying into a provided hub-and-spoke network.
- PostgreSQL, the Function API, Teams/Bot Service and SharePoint from the research design. They belong to the supply-assistant pilot, not to this template. §11 gives the PostgreSQL pattern for its owner.
- Azure Firewall with FQDN egress filtering. Phase 2 option (§7.3); the baseline uses a NAT gateway.
- Prompt agents that use the APIM connection (connected models). Microsoft routes those calls from its managed inference plane, which can't reach a private APIM without the separate `byom-cross-region` topology. Gateway-governed traffic in private mode is hosted-agent only.

---

## 2. Current state

| Component | Today (`Ai-gateway`) | File |
|---|---|---|
| Foundry account + project | `publicNetworkAccess: 'Enabled'`, basic agent setup, no network injection | `infra/modules/ai-foundry.bicep` |
| APIM AI gateway | `BasicV2`, public gateway | `infra/modules/apim-ai-gateway.bicep` |
| Container Registry | `Basic`, public, built with `az acr build` (ACR Tasks) and `docker.remoteBuild: true` | `infra/modules/container-registry.bicep`, `azure.yaml` |
| Container Apps (backend, frontend) | Public environment, external ingress | `infra/modules/container-apps-env.bicep`, `container-app.bicep` |
| Log Analytics + App Insights | Public ingestion and query | `infra/modules/monitoring.bicep` |
| Databricks (optional) | Not VNet-injected, public workspace and storage | `infra/modules/databricks.bicep` |
| API Center (optional) | Public management API | `infra/modules/api-center.bicep` |
| Hooks | `preprovision` checks, `postprovision` image builds, `postdeploy` RBAC, supply agent, gateway sync, health check. Both `windows` (pwsh) and `posix` (sh) variants exist. | `azure.yaml`, `scripts/*.py` |

Hosted agents also call **public MCP servers** (`hcls.mcp.claude.com`, `pubmed.mcp.claude.com`) and the frontend is opened by users in a browser. Both need a path in private mode (§7.3, §7.5).

---

## 3. Decisions

| # | Decision | Choice (default) | Why / alternatives |
|---|---|---|---|
| D1 | How the network gets created | **Separate azd project `infra/bootstrap/`** run from a laptop | Everything in Stage 1 is ARM, which is reachable publicly. Keeping it separate means the main template never needs to run from outside the VNet. Alternative: platform team supplies the VNet (§5.6). |
| D2 | Where `azd up` (Stage 2) runs | **Jumpbox VM in the VNet**, reached through Bastion | Simplest for a pilot. Phase 3 (as in the research doc): self-hosted GitHub Actions / Azure DevOps runner in `snet-build` with OIDC. |
| D3 | Jumpbox OS | **Ubuntu 24.04 LTS** (Canonical `ubuntu-24_04-lts`, server image), **headless by default**; optional desktop (`JUMPBOX_DESKTOP=true`) | Native Docker (no WSL or Docker Desktop licence), no Windows licence, cheaper VM, the template's `posix` hooks already exist. Azure has no official Ubuntu Desktop image, so desktop mode installs `ubuntu-desktop-minimal` on the official server image with cloud-init (§5.4). Windows 11 remains an alternative if a password-free desktop is a hard requirement. |
| D4 | Jumpbox sign-in | **Headless:** Microsoft Entra ID over SSH (`AADSSHLoginForLinux`), Bastion **Standard** with native client tunnelling; VS Code Remote-SSH from the laptop. **Desktop:** RDP through Bastion with a **local account** whose random password is created on the VM and stored in Key Vault. | Entra ID sign-in on Linux covers SSH only; xrdp (RDP) checks local accounts. Bastion still requires Entra sign-in and RBAC before any connection (§5.4.3). |
| D5 | Foundry agent setup | **Standard setup with network injection** (sample 15 pattern): BYO Cosmos DB, Storage, AI Search; project capability host | Required by Microsoft for outbound network isolation and for MCP tools through the VNet. |
| D6 | Outbound internet for agents, apps, jumpbox, builds | **NAT gateway** on `snet-agent`, `snet-mcp`, `snet-apim`, `snet-jumpbox`, `snet-build` | New VNets have no default outbound access. Agents must reach the public MCP servers and Entra; the jumpbox downloads packages. Research design: route table `rt-spoke-egress` to the hub firewall; the baseline swaps that for NAT and keeps the same subnets (Phase 6 `EGRESS_MODE=firewall`). |
| D7 | APIM tier in private mode | **StandardV2**: inbound private endpoint + outbound VNet integration in `snet-apim` | BasicV2 supports neither. Matches the research design. |
| D8 | Container images | **Premium ACR, public access disabled.** Build on the jumpbox with Docker (`remoteBuild: false`), push over the ACR private endpoint | ACR Tasks run on public build hosts and can't reach a private registry. Spike S3 tests ACR Tasks with trusted-service bypass as an alternative. |
| D9 | Monitoring | **Azure Monitor Private Link Scope (AMPLS)**, ingestion and query `PrivateOnly`, App Insights/Log Analytics public access disabled | Only way to satisfy the App Insights finding. Matches the research design (`pe-ampls`). Spike S4 checks Foundry platform tracing still arrives. |
| D10 | Users reaching the app | **Internal Container Apps environment in `snet-mcp`.** Baseline: users browse from the jumpbox (SOCKS proxy or desktop Edge). Phase 6: Application Gateway (WAF) or Front Door Premium with Private Link | No public ingress. The research design's `snet-mcp` environment hosts the MCP server; the baseline reuses it for the prior-auth frontend and backend. |
| D11 | Private DNS | **Standalone:** zones created in the network resource group by Stage 1, linked to the VNet (VNet uses Azure DNS). **Hub:** zones linked to the hub and resolved through the hub DNS private resolver (research design). | Same zone list either way (§5.3). |
| D12 | API Center | **Unchanged / optional.** Registry only, management plane, no request traffic (as in the research design) | Revisit if a finding is raised against it. |
| D13 | Address space | **`10.50.0.0/22`**, using the research document's example ranges for the four shared subnets | Same layout as White Cap's plan; White Cap IPAM replaces the ranges later. |

---

## 4. Target architecture (baseline, standalone network)

```
                         Internet
                            │  (no inbound to the solution)
          Laptop ── HTTPS ──┤
   az network bastion tunnel / rdp   (Entra sign-in + RBAC)
                            ▼
┌──────────────────── rg-wc01_pai_ai_network (Stage 1) ──────────────────────────────┐
│ VNet vnet-<env> 10.50.0.0/22          (stands in for White Cap's hub + spoke)     │
│                                                                                    │
│  AzureBastionSubnet ──► snet-jumpbox: vm-jump (Ubuntu 24.04: Docker, az, azd, …)   │
│                                  │ azd up (Stage 2)                                │
│                                  ▼                                                 │
│  snet-pe ── private endpoints ────────────────────────────────────────────────┐    │
│   pe-foundry · pe-apim · pe-acr · pe-blob · pe-cosmos · pe-search · pe-kv      │    │
│   pe-ampls · pe-dbx (ui_api, browser_auth) · pe-uc (blob, dfs)                 │    │
│                                                                                │    │
│  snet-agent (Microsoft.App/environments, Foundry network injection)            │    │
│   hosted agents ──► pe-apim ──(snet-apim VNet integration)──► pe-foundry       │    │
│                 └─► pe-apim ──► pe-dbx (Genie MCP route)                       │    │
│                 └─► NAT gateway ──► public MCP servers, Entra ID               │    │
│                                                                                │    │
│  snet-mcp (internal Container Apps env): frontend ─► backend ─► pe-foundry     │    │
│  snet-apim (APIM StandardV2 outbound)                                          │    │
│  snet-build (reserved: Phase 3 runner)                                         │    │
│  snet-dbx-host / snet-dbx-container (Databricks VNet injection, optional)      │    │
│  snet-psql (reserved: PostgreSQL VNet integration, other team)                 │    │
│                                                                                    │
│  Private DNS zones (linked to VNet) · NAT gateway · NSGs · Key Vault                │
└────────────────────────────────────────────────────────────────────────────────────┘
┌──────────────────── rg-wc01_pai_ai_private (Stage 2, existing template) ─────────┐
│ Foundry account (publicNetworkAccess Disabled, networkInjections: agent)           │
│ Foundry project + capability host → Cosmos DB, Storage, AI Search (all private)    │
│ APIM StandardV2 · ACR Premium · Container Apps env (internal) · Log Analytics +    │
│ App Insights + AMPLS · Databricks (optional) · API Center (optional)               │
└────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Stage 1: network bootstrap (`infra/bootstrap/`)

### 5.1 What it deploys

| Resource | Settings |
|---|---|
| Resource group | `rg-<bootstrap env name>`, same rule as the main template (§5.8) |
| VNet | `vnet-<env>`, `10.50.0.0/22` (`NETWORK_ADDRESS_SPACE`) |
| Subnets | §5.2 |
| NSGs | `nsg-agent`, `nsg-mcp`, `nsg-pe`, `nsg-apim` with the research document's rules (§5.2.1), plus `nsg-jumpbox`, `nsg-build`, `nsg-dbx` and the Microsoft-required Bastion NSG |
| NAT gateway | Standard, one public IP, on `snet-agent`, `snet-mcp`, `snet-apim`, `snet-jumpbox`, `snet-build` |
| Private DNS zones | §5.3, each with a VNet link (`registrationEnabled: false`) |
| Azure Bastion | **Standard** SKU, `enableTunneling: true` (native client: `az network bastion tunnel/ssh/rdp`) |
| Key Vault | RBAC mode, public access disabled, private endpoint `pe-kv`; holds the desktop password (desktop mode) |
| Jumpbox VM | §5.4 |
| Role assignments | `Virtual Machine Administrator Login` (or `User Login`) on the VM and `Reader` on the VM, NIC and Bastion for `JUMPBOX_USERS_GROUP_ID`; the VM's managed identity gets `Key Vault Secrets Officer` on the vault (to store the desktop password) |

### 5.2 Subnet plan

The first five rows use the research document's names and example ranges. The rest are baseline-only: in White Cap's environment the Bastion and jump host live in the hub, and Databricks is White Cap's existing workspace in its own VNet.

| Subnet | Prefix | Delegation | NSG | Egress (baseline) | Used by | Research doc |
|---|---|---|---|---|---|---|
| `snet-agent` | 10.50.0.0/24 | `Microsoft.App/environments` | `nsg-agent` | NAT | Foundry network injection (hosted agents). One Foundry account per subnet. | Same |
| `snet-mcp` | 10.50.1.0/27 | `Microsoft.App/environments` | `nsg-mcp` | NAT | Internal Container Apps workload-profiles environment: prior-auth **frontend and backend** (and a future MCP server) | Same (MCP server) |
| `snet-pe` | 10.50.1.64/26 | – | `nsg-pe` | – | All private endpoints. `privateEndpointNetworkPolicies: 'Enabled'` so NSG rules apply. | Same |
| `snet-apim` | 10.50.1.128/27 | `Microsoft.Web/serverFarms` | `nsg-apim` | NAT | APIM StandardV2 outbound VNet integration | Same |
| `snet-build` | 10.50.1.160/27 | – | `nsg-build` | NAT | Reserved: Phase 3 deployment runner | "Reserved for growth (deployment runners in Phase 3)" |
| `snet-jumpbox` | 10.50.1.192/27 | – | `nsg-jumpbox` | NAT | Jumpbox | Hub (jump host / AVD) |
| `AzureBastionSubnet` | 10.50.2.0/26 | – | Bastion rules | – | Azure Bastion | Hub (optional) |
| `snet-dbx-host` | 10.50.2.64/26 | `Microsoft.Databricks/workspaces` | `nsg-dbx` | NAT | Databricks VNet injection, host (optional, `ENABLE_DATABRICKS`) | White Cap workspace has its own VNet |
| `snet-dbx-container` | 10.50.2.128/26 | `Microsoft.Databricks/workspaces` | `nsg-dbx` | NAT | Databricks VNet injection, container (optional) | – |
| `snet-psql` | 10.50.2.192/28 | `Microsoft.DBforPostgreSQL/flexibleServers` | – | – | Reserved for the PostgreSQL owner (§11) | ODS (White Cap) |
| *free* | 10.50.2.208 – 10.50.3.255 | | | | Growth (for example a second agent subnet) | |

`snet-pe` capacity: /26 gives 59 usable addresses. The baseline needs about 25 (AMPLS alone uses ~10, Cosmos DB and ACR use two each).

#### 5.2.1 NSG rules (from the research document, plus baseline additions)

- `nsg-agent`: out 443 to `snet-pe` and `snet-mcp`; 53 to DNS (Azure DNS `168.63.129.16` standalone, hub resolver in hub mode); 443 to `AzureActiveDirectory`; 443 to `Internet` (NAT in the baseline, firewall in hub mode); deny other; in: deny Internet.
- `nsg-mcp`: in 443 from `snet-agent` **and from `snet-jumpbox`** (baseline: people open the frontend from the jumpbox); deny Internet. Out 443 to `snet-pe`; 53 DNS; 443 to `AzureActiveDirectory` and Internet (Container Apps platform: `mcr.microsoft.com`, `*.data.mcr.microsoft.com`, `packages.aks.azure.com`, `acs-mirror.azureedge.net`).
- `nsg-pe`: in 443 from `snet-agent`, `snet-mcp`, `snet-apim` and the **engineer access range = `snet-jumpbox` (and `snet-build`)**; deny other.
- `nsg-apim` (required by APIM): out 443 to `AzureKeyVault`; 443 to `snet-pe`; 443 to `AzureActiveDirectory`; 443 to Internet (dependencies such as App Insights until AMPLS is confirmed in spike S2/S4).
- `nsg-jumpbox`: in 22 and 3389 **from `AzureBastionSubnet` only**; out 443 to `snet-pe`, `snet-mcp` and Internet (packages, GitHub, Entra).
- Validate with NSG flow logs during the spikes, as the research document recommends.

### 5.3 Private DNS zones

Research document list plus the zones the baseline adds:

| Zone | For | In research list |
|---|---|---|
| `privatelink.cognitiveservices.azure.com`, `privatelink.openai.azure.com`, `privatelink.services.ai.azure.com` | Foundry | Yes |
| `privatelink.search.windows.net` | AI Search | Yes |
| `privatelink.azurecr.io` | ACR (login server and data endpoint) | Yes |
| `privatelink.vaultcore.azure.net` | Key Vault | Yes |
| `privatelink.blob.core.windows.net` | Storage (standard setup), AMPLS | Yes |
| `privatelink.monitor.azure.com`, `privatelink.oms.opinsights.azure.com`, `privatelink.ods.opinsights.azure.com`, `privatelink.agentsvc.azure-automation.net` | AMPLS | Yes |
| `privatelink.azure-api.net` | APIM gateway | Yes |
| `privatelink.azuredatabricks.net` | Databricks | Yes |
| **`privatelink.documents.azure.com`** | **Cosmos DB (standard setup thread storage)** | **No: add** |
| `privatelink.dfs.core.windows.net` | Unity Catalog storage (Databricks option) | No |
| `privatelink.postgres.database.azure.com`, `privatelink.azurewebsites.net` | ODS, Function API | Yes (not used by the baseline) |
| `<env-id>.<region>.azurecontainerapps.io` (wildcard A) | Internal Container Apps environment; created by Stage 2 (needs the environment's static IP) | Yes |

### 5.4 Jumpbox (Ubuntu 24.04 LTS)

#### 5.4.1 VM

| Setting | Headless (default) | Desktop (`JUMPBOX_DESKTOP=true`) |
|---|---|---|
| Image | Canonical `ubuntu-24_04-lts` / `server` (Gen2), Trusted Launch, Secure Boot, vTPM | Same |
| Size | `Standard_D2s_v5` (2 vCPU, 8 GB) | `Standard_D4s_v5` (4 vCPU, 16 GB) |
| OS disk | 128 GB Premium SSD | 128 GB Premium SSD |
| Network | `snet-jumpbox`, **no public IP** | Same |
| Identity | System-assigned managed identity | Same |
| Extensions | `AADSSHLoginForLinux`, Azure Monitor Agent | Same |
| Admin account | `azureadmin`, **SSH key only** (`disablePasswordAuthentication: true`), break-glass. The `preprovision` hook uses `JUMPBOX_SSH_PUBLIC_KEY` or `~/.ssh/id_rsa.pub`. | Same |
| Desktop account | – | `jumpuser`, local, random password (§5.4.3) |
| Auto-shutdown | 19:00 local daily (`JUMPBOX_SHUTDOWN_TIME`) | Same |
| Patching | `unattended-upgrades` (security updates), reboot window 03:00 | Same |

#### 5.4.2 Toolchain (`infra/bootstrap/scripts/setup-jumpbox.sh`, run by cloud-init at first boot, both modes)

Installed for all users, logged to `/var/log/jumpbox-setup.log`; a marker file `/var/lib/jumpbox-setup.done` and `jumpbox-check` (prints every tool version) confirm completion.

| Tool | Source |
|---|---|
| Git, jq, unzip, curl, build-essential | Ubuntu apt |
| Docker Engine + buildx + compose plugin | Docker apt repo (`docker-ce`); Entra-created users are added to the `docker` group at first login via `/etc/profile.d` script |
| Azure CLI + extensions `containerapp`, `application-insights`, `log-analytics`, `ssh`, `databricks` | Microsoft apt repo |
| Azure Developer CLI (`azd`) | `https://aka.ms/install-azd.sh`; `azd extension install azure.ai.agents` runs at each user's first login (azd extensions are per user) |
| Python 3.12 (Ubuntu 24.04 default) + `python3-venv` + `uv` | apt + Astral installer |
| PowerShell 7 | Microsoft apt repo |
| Node.js LTS | NodeSource repo |
| Databricks CLI | Databricks install script |
| VS Code | **Headless:** VS Code Server is installed automatically by Remote-SSH on first connect (needs egress to `update.code.visualstudio.com` and `*.vscode-cdn.net`). **Desktop:** `code` from the Microsoft apt repo. |
| Desktop only | `ubuntu-desktop-minimal` (GNOME), `xrdp` (`adduser xrdp ssl-cert`, `gnome-session` default), `microsoft-edge-stable` (Microsoft apt repo; avoids snap Firefox issues over RDP) |

First boot takes about 10 minutes headless and 20–25 minutes with the desktop. Both need the NAT gateway.

#### 5.4.3 How people sign in

**Headless (default): Entra ID over SSH.**
1. `az network bastion tunnel --name <bastion> --resource-group <net-rg> --target-resource-id <vm-id> --resource-port 22 --port 2222` (keep running).
2. `az ssh cert --file ~/.ssh/jumpbox-cert.pub` (Entra SSH certificate, valid about an hour).
3. `~/.ssh/config` entry `jumpbox` → `127.0.0.1:2222`, `User <upn>`, `CertificateFile`, `IdentityFile`, `DynamicForward 1080`.
4. VS Code → Remote-SSH → `jumpbox`. Code, terminal, Docker and `azd` run on the jumpbox inside the VNet.
5. Private web pages (frontend, Foundry portal data calls, Azure portal Logs) from the laptop: `msedge --proxy-server="socks5://127.0.0.1:1080" --user-data-dir=<temp>`. DNS resolves on the jumpbox side, so private endpoint names work.
- Quick terminal only: `az network bastion ssh ... --auth-type AAD`.
- Access is controlled by the `Virtual Machine User/Administrator Login` role; removing the role removes access. MFA and Conditional Access apply.

**Desktop (`JUMPBOX_DESKTOP=true`): RDP with a local password.**
- RDP into Linux goes through **xrdp**, which only checks local Linux accounts. Microsoft's Entra extension covers SSH, not RDP, on Linux.
- cloud-init creates `jumpuser`, generates a random 32-character password **on the VM**, sets it, and stores it in Key Vault (`jumpbox-desktop-password`) using the VM's managed identity over `pe-kv`. The password never leaves Azure or appears in deployment output.
- Users with `Key Vault Secrets User` read it, then connect with `az network bastion rdp ...` or the portal's Bastion RDP.
- Entra ID still guards the path: reaching the VM at all requires Azure sign-in (MFA) and Bastion/VM roles; the local password is a second lock behind it. Bastion session logs record the Entra user who opened each session.
- Rotation: `sudo jumpbox-rotate-password` on the jumpbox (over Entra SSH) stores a new password in Key Vault first, then applies it; run it when someone leaves the group.
- Spike S6 tests a per-person variant (Entra SSH creates the user's Linux account, the user sets a desktop password for it), which would remove the shared account if xrdp accepts UPN-style usernames.

### 5.5 Outputs (written to the Stage 1 azd env and printed)

`NETWORK_RESOURCE_GROUP`, `NETWORK_VNET_NAME`, `NETWORK_VNET_ID`, subnet IDs, `NETWORK_DNS_ZONE_RESOURCE_GROUP`, `JUMPBOX_VM_ID`, `BASTION_NAME`, `JUMPBOX_KEY_VAULT_NAME`, and the exact tunnel / RDP commands.

### 5.6 Bring-your-own network (White Cap hub-and-spoke)

`infra/bootstrap` is optional. When White Cap's platform team provides the spoke (`vnet-supplyassist`) peered to its hub:
- Skip Stage 1 network resources; deploy only the jumpbox module into the provided subnet, or use White Cap's own jump host / AVD / point-to-site VPN.
- Set the same `NETWORK_*` values for Stage 2. `NETWORK_DNS_ZONE_RESOURCE_GROUP` points at White Cap's central zones; spoke DNS points at the hub resolver.
- Egress follows `rt-spoke-egress` (0.0.0.0/0 → hub firewall) instead of NAT; the firewall rules in §7.3 apply.

### 5.7 Alignment with the research document's network design

| Research design | Baseline in this spec | Status |
|---|---|---|
| Spoke `10.50.0.0/22` with `snet-agent`, `snet-mcp`, `snet-pe`, `snet-apim` | Same names and ranges | Aligned |
| Hub: Azure Firewall + `rt-spoke-egress` | NAT gateway on the same subnets; firewall is Phase 6 / hub mode | Baseline substitute |
| Hub: DNS private resolver, zones linked to hub | Zones linked directly to the VNet (Azure DNS) | Baseline substitute |
| Hub: Bastion + jump host / AVD, point-to-site for EPAM | Bastion + Ubuntu jumpbox inside the VNet | Baseline substitute |
| PaaS list: Foundry, AI Search, ACR, Key Vault, Storage, App Insights + Log Analytics, managed identities, APIM StandardV2, API Center | Same, **plus Cosmos DB** | **Correct the research diagram:** the standard agent setup that private networking requires stores threads in Cosmos DB (`pe-cosmos`, groupId `Sql`, zone `privatelink.documents.azure.com`). |
| `snet-mcp`: Postgres MCP server | Prior-auth frontend and backend (and future MCP server) | Template-specific |
| Flow 4: "Toolbox calls Databricks through its front-end private endpoint"; flow 9: "Model and MCP calls enter API Management through pe-apim" | Supply agent calls Genie **through APIM** (`databricks-genie-mcp` route) → `pe-dbx` | **Correct the research diagram:** flows 4 and 9 disagree; make flow 4 go through `pe-apim`. |
| Firewall application rules: Entra, Container Apps platform, Databricks, APIM dependencies | Same, **plus** the prior-auth MCP servers (`hcls.mcp.claude.com`, `pubmed.mcp.claude.com`) and build/package hosts for the jumpbox | Template-specific additions |
| `nsg-pe` engineer access range (P2S or Bastion) | `snet-jumpbox` / `snet-build` | Aligned |
| ODS, Function API, Teams/Bot Service, SharePoint | Not in this template | Out of scope |

### 5.8 Resource groups and naming

Both stages create **new resource groups** and leave the existing environment (`rg-wc01_pai_ai_controlplane`) untouched. Both use the template's existing rule: resource group = `rg-` + azd environment name (`abbrs.resourcesResourceGroups`). Resource names inside use the template's `resourceToken` (`uniqueString(subscription, environment name, location)`), so a new environment name produces new, non-clashing resource names.

| Stage | azd environment name (example) | Resource group | Example resource names |
|---|---|---|---|
| Existing public pilot | `wc01_pai_ai_controlplane` | `rg-wc01_pai_ai_controlplane` | `foundry-aif-jvs7hw2v3w2ek`, `apim-jvs7hw2v3w2ek` |
| Stage 1 – network + jumpbox | `wc01_pai_ai_network` | `rg-wc01_pai_ai_network` | `vnet-<token>`, `bas-<token>`, `vm-jump-<token>`, `ng-<token>`, `kv-<token>` |
| Stage 2 – private solution | `wc01_pai_ai_private` | `rg-wc01_pai_ai_private` | `foundry-aif-<token>`, `apim-<token>`, `cr<token>`, `cosmos-<token>`, `srch-<token>` |
| Databricks managed RG (optional) | – | `rg-dbw-managed-<token>` | Created by Databricks |
| Supply agent nested env | `wc01_pai_ai_private-supply` | none (deploys into Stage 2's project) | `supply-pricing-agent` |

Stage 1 names follow `infra/abbreviations.json` (new entries added for VNet, Bastion, NAT gateway, VM and NSG where missing). The environment names above are examples; any name works as long as it stays within the resource group name limit (90 characters) and avoids the existing names.

---

## 6. Stage 2: template changes (`ENABLE_PRIVATE_NETWORKING=true`)

### 6.1 New environment variables → Bicep parameters

| azd env | Bicep param | Default | Notes |
|---|---|---|---|
| `ENABLE_PRIVATE_NETWORKING` | `enablePrivateNetworking` | `false` | Master switch |
| `NETWORK_RESOURCE_GROUP` | `networkResourceGroup` | – | From Stage 1 |
| `NETWORK_VNET_NAME` | `networkVnetName` | – | Subnets are looked up by the names in §5.2 |
| `NETWORK_DNS_ZONE_RESOURCE_GROUP` | `dnsZoneResourceGroup` | = `NETWORK_RESOURCE_GROUP` | Central DNS option (§5.6) |
| `IMAGE_BUILD_MODE` | – (hooks only) | `acr-tasks` public / `local` private | Backend/frontend image build path |
| `AI_GATEWAY_SKU` | `aiGatewaySku` | forced to `StandardV2` in private mode | Bicep fails fast if `BasicV2` + private |
| `EGRESS_MODE` | `egressMode` | `nat` | `firewall` reserved for Phase 6 / hub mode |

Stage 1 (`infra/bootstrap`) variables: `AZURE_LOCATION`, `NETWORK_ADDRESS_SPACE` (`10.50.0.0/22`), `JUMPBOX_USERS_GROUP_ID`, `JUMPBOX_DESKTOP` (`false`), `JUMPBOX_VM_SIZE` (by mode), `JUMPBOX_SSH_PUBLIC_KEY`, `JUMPBOX_SHUTDOWN_TIME` (`1900`), `JUMPBOX_TIMEZONE`.

### 6.2 `infra/main.bicep`

- `existing` references to the VNet and subnets in `networkResourceGroup` (cross-RG `scope: resourceGroup(networkResourceGroup)`).
- New module **`infra/modules/private-endpoint.bicep`**: one generic module (target resource ID, `groupId`, subnet ID, DNS zone IDs) used for every private endpoint.
- Every module gets `enablePrivateNetworking` and the subnet/DNS inputs it needs. The public path is untouched when the flag is `false`.
- Outputs add `PRIVATE_NETWORKING=true` and the internal frontend/backend FQDNs.

### 6.3 Foundry (`ai-foundry.bicep`)

Following sample 15 (`ai-account-identity.bicep`, API `2025-04-01-preview` or later):

```bicep
properties: {
  allowProjectManagement: true
  customSubDomainName: 'foundry-${name}'
  disableLocalAuth: true
  publicNetworkAccess: enablePrivateNetworking ? 'Disabled' : 'Enabled'
  networkAcls: enablePrivateNetworking ? { defaultAction: 'Deny', bypass: 'AzureServices', ipRules: [], virtualNetworkRules: [] } : null
  networkInjections: enablePrivateNetworking ? [
    { scenario: 'agent', subnetArmId: agentSubnetId /* snet-agent */, useMicrosoftManagedNetwork: false }
  ] : null
}
```

- `pe-foundry`: `groupId: 'account'`, zones `privatelink.cognitiveservices.azure.com`, `privatelink.openai.azure.com`, `privatelink.services.ai.azure.com`.
- **New module `foundry-standard-setup.bicep`** (private mode only): Cosmos DB (`publicNetworkAccess: 'Disabled'`, `disableLocalAuth: true`), Storage (ZRS, `allowSharedKeyAccess: false`, public disabled), AI Search (`standard`, public disabled, AAD auth), with `pe-cosmos` (`Sql`), `pe-blob` (`blob`), `pe-search` (`searchService`); project connections (`CosmosDB`, `AzureStorageAccount`, `CognitiveSearch`, all `authType: 'AAD'`); project capability host `capabilityHostKind: 'Agents'`.
- Role ordering from sample 15: Cosmos DB Operator, Storage Blob Data Contributor, Search Index Data Contributor + Search Service Contributor **before** the capability host; Storage Blob Data Owner (conditioned) and Cosmos built-in Data Contributor **after** it. Capability host creation can take **30–35 minutes**.
- The account capability host is created by the platform when `networkInjections` is set; the template must not declare a second one (409).
- The App Insights project connection stays (tracing), pointing at the AMPLS-scoped component.

### 6.4 APIM AI gateway (`apim-ai-gateway.bicep`)

- `skuName: 'StandardV2'`.
- Outbound: `virtualNetworkType: 'External'` + `virtualNetworkConfiguration.subnetResourceId: snet-apim`, so APIM reaches `pe-foundry` and `pe-dbx`.
- Inbound: `pe-apim`, `groupId: 'Gateway'`, zone `privatelink.azure-api.net`.
- `publicNetworkAccess`: APIM doesn't allow `Disabled` at creation time. The first provision creates it `Enabled`; the new postprovision step `scripts/lock_public_access.py` sets it to `Disabled` once `pe-apim` is approved (idempotent, re-run every `azd up`).
- Policies, named values, the 75k token limit and the Genie MCP route from Spec 001 are unchanged. `AI_GATEWAY_OPENAI_BASE_URL` keeps the same hostname; private DNS makes it resolve to `pe-apim`.
- Logger and diagnostics continue to target App Insights / Log Analytics, now through AMPLS.

### 6.5 Container Registry (`container-registry.bicep`)

- SKU `Premium`, `publicNetworkAccess: 'Disabled'`, `networkRuleBypassOptions: 'AzureServices'`, `adminUserEnabled: false`.
- `pe-acr`: `groupId: 'registry'`, zone `privatelink.azurecr.io` (login server and regional data endpoint).
- Hosted agents pull with their per-agent identity (`AcrPull` granted by `azd deploy`). Projects created after 2026-06-25 support a private ACR; a new environment qualifies.

### 6.6 Container Apps (`container-apps-env.bicep`, `container-app.bicep`)

- Workload-profiles environment with `vnetConfiguration: { infrastructureSubnetId: snet-mcp, internal: true }` (workload profiles are required for UDR in hub mode).
- Private DNS zone for the environment's default domain with a wildcard `A` record to its static IP, linked to the VNet (hub in hub mode).
- Ingress stays `external: true` *within the environment* (reachable from the VNet, not from the internet). `FRONTEND_ORIGIN` / `BACKEND_URL` keep using the environment domain.
- Backend reaches Foundry through `pe-foundry`; no code change.

### 6.7 Monitoring (`monitoring.bicep`)

- Log Analytics and App Insights: `publicNetworkAccessForIngestion: 'Disabled'`, `publicNetworkAccessForQuery: 'Disabled'`.
- **AMPLS** with both as scoped resources, `ingestionAccessMode: 'PrivateOnly'`, `queryAccessMode: 'PrivateOnly'`, and `pe-ampls` (`groupId: 'azuremonitor'`) with the four Monitor zones plus `privatelink.blob.core.windows.net`.
- Container Apps environment logging switches from shared-key Log Analytics to Azure Monitor (diagnostic settings), so no keys are used.
- Querying works from the jumpbox (SOCKS proxy or desktop browser). One AMPLS per DNS scope: in hub mode use White Cap's AMPLS if one exists.

### 6.8 Databricks (`databricks.bicep`, only with `ENABLE_DATABRICKS=true`)

- VNet injection: `customVirtualNetworkId`, `customPublicSubnetName: snet-dbx-host`, `customPrivateSubnetName: snet-dbx-container`, `enableNoPublicIp: true`.
- `publicNetworkAccess: 'Disabled'`, `requiredNsgRules: 'NoAzureDatabricksRules'`.
- `pe-dbx`: `databricks_ui_api` (front end) and `browser_authentication` (one per region per DNS zone), zone `privatelink.azuredatabricks.net`.
- Unity Catalog storage: public access disabled, `networkAcls.defaultAction: 'Deny'`, resource-instance rule for the access connector, `pe-uc` (`blob`, `dfs`).
- **Genie runs on a serverless SQL warehouse**, which reaches UC storage through a **Network Connectivity Configuration (NCC)** with private endpoint rules. NCC is a Databricks *account-level* object (not ARM): `scripts/bootstrap_databricks.py` gets a step that creates/attaches the NCC and approves its private endpoints. Needs a **Databricks account admin** (spike S5).
- In White Cap's environment the workspace is theirs (research design); the same `pe-dbx` pattern applies, or the hub firewall's public IP on the workspace IP access list.

### 6.9 Hooks and scripts

| Hook / script | Change |
|---|---|
| `preprovision` | If private mode: refuse to run unless `NETWORK_*` values are set **and** the Foundry private DNS zone exists; warn if not running inside the VNet (`check_network.py --stage pre`). Both `windows` and `posix` variants; the jumpbox uses `posix`. |
| `postprovision` (image builds) | `IMAGE_BUILD_MODE=local`: `docker build` + `docker push` over `pe-acr` instead of `az acr build`. |
| **new** `scripts/lock_public_access.py` | Disables APIM public access after `pe-apim` is approved; asserts every other resource reports public access `Disabled`. |
| **new** `scripts/check_network.py` | For every service FQDN: resolves to a `10.50.x.x` address and TCP 443 connects. Clear errors (wrong DNS, not on the VNet, private endpoint pending). Runs at `predeploy` and at the start of `check_agents.py`. |
| `azure.yaml` hosted agents | `docker.remoteBuild` → `false` in private mode (spike S3 decides whether azd allows this per environment; if not, the branch uses `false` and the public path uses ACR Tasks through `IMAGE_BUILD_MODE`). |
| `grant_agent_rbac.py`, `deploy_supply_agent.py`, `sync_ai_gateway.py`, `check_agents.py` | No logic change; they reach private endpoints through the jumpbox's DNS. Verify on Linux (they were last run on Windows). Add `AcrPush` / `Container Registry Tasks Contributor` grants for the deployer. |
| `bootstrap_databricks.py` | NCC creation and private endpoint approval (§6.8). |

---

## 7. Traffic flows

### 7.1 Hosted agent → model (through the AI gateway)
`agent (snet-agent)` → DNS `apim-*.azure-api.net` → `pe-apim` → policy (Entra token, allow-list, 75k TPM) → APIM VNet integration (`snet-apim`) → `pe-foundry` → model deployment.

### 7.2 Supply agent → Databricks Genie
`agent` → `pe-apim` (`/mcp/databricks-genie/<space>`) → APIM (`snet-apim`) → `pe-dbx` → serverless SQL warehouse → (NCC) → `pe-uc`.

### 7.3 Agents → public MCP servers and Entra
Baseline: `agent` → `snet-agent` → NAT gateway → `hcls.mcp.claude.com`, `pubmed.mcp.claude.com`, `login.microsoftonline.com`.
Hub mode / Phase 6: `rt-spoke-egress` → firewall with application rules from the research document (Entra and managed identity FQDNs, Container Apps platform, Databricks workspace if no private link, APIM dependencies) **plus** `hcls.mcp.claude.com`, `pubmed.mcp.claude.com`, `mcr.microsoft.com`, and `agent365.svc.cloud.microsoft` if Agent 365 export is used. No TLS inspection.

### 7.4 Telemetry
Agents, APIM and Container Apps → `pe-ampls` → App Insights / Log Analytics. Foundry platform-side traces: spike S4.

### 7.5 People
- Headless: laptop → Bastion tunnel (Entra) → SSH (Entra) → jumpbox; VS Code Remote-SSH; laptop browser through the SOCKS proxy to the frontend, Foundry portal (the `ai.azure.com` UI is public; its data calls resolve to private endpoints through the proxy) and Azure portal Logs.
- Desktop: laptop → Bastion RDP (Entra to Azure, local password to the VM) → GNOME desktop → Edge.

---

## 8. Deployment runbook

### 8.1 Prerequisites
- Subscription **Owner** (or Contributor + Role Based Access Control Administrator) for the person deploying; Databricks account admin if Databricks is enabled.
- Resource providers: `Microsoft.Network`, `Microsoft.App`, `Microsoft.ContainerService`, `Microsoft.CognitiveServices`, `Microsoft.DocumentDB`, `Microsoft.Search`, `Microsoft.Storage`, `Microsoft.ApiManagement`, `Microsoft.ContainerRegistry`, `Microsoft.Insights`, `Microsoft.OperationalInsights`, `Microsoft.KeyVault`, `Microsoft.Compute`, `Microsoft.Databricks`. (`Microsoft.App` and `Microsoft.ContainerService` are mandatory for network injection.)
- An Entra group for jumpbox users; confirmed address space; an SSH key pair on the laptop (`ssh-keygen -t rsa -b 4096`) for the break-glass admin.
- Laptop tools: Azure CLI with the `ssh` and `bastion` extensions, `azd`, VS Code with the Remote-SSH extension.

### 8.2 Stage 1 (laptop)
```powershell
git switch private-network
cd infra/bootstrap
azd env new wc01_pai_ai_network            # → resource group rg-wc01_pai_ai_network
azd env set AZURE_LOCATION eastus2
azd env set NETWORK_ADDRESS_SPACE 10.50.0.0/22
azd env set JUMPBOX_USERS_GROUP_ID <entra-group-object-id>
azd env set JUMPBOX_DESKTOP false          # true = GNOME + Edge + VS Code over RDP
azd up                                     # ~20–25 min (Bastion is the slowest part)
```

### 8.3 Connect to the jumpbox

Headless (default):
```powershell
az network bastion tunnel --name <BASTION_NAME> --resource-group <NETWORK_RESOURCE_GROUP> `
  --target-resource-id <JUMPBOX_VM_ID> --resource-port 22 --port 2222      # leave running
az ssh cert --file $HOME\.ssh\jumpbox-cert.pub
# ~/.ssh/config:
#   Host jumpbox
#     HostName 127.0.0.1
#     Port 2222
#     User <you>@<tenant-domain>
#     CertificateFile ~/.ssh/jumpbox-cert.pub-aadcert.pub
#     IdentityFile ~/.ssh/id_rsa
#     DynamicForward 1080
# VS Code → Remote-SSH: Connect to Host → jumpbox
```

Desktop:
```powershell
az keyvault secret show --vault-name <JUMPBOX_KEY_VAULT_NAME> --name jumpbox-desktop-password --query value -o tsv
az network bastion rdp --name <BASTION_NAME> --resource-group <NETWORK_RESOURCE_GROUP> --target-resource-id <JUMPBOX_VM_ID>
# sign in as jumpuser with the password above
```
(Reading the secret requires a route to `pe-kv`: run it on the jumpbox over SSH, or allow your access path. Spike S6 settles the most convenient method.)

### 8.4 Stage 2 (on the jumpbox)
```bash
jumpbox-check                                   # confirms every tool is installed
git clone <repo-url> && cd prior-authorization-multi-agent-solution-accelerator
git switch private-network
az login --use-device-code && azd auth login --use-device-code
azd env new wc01_pai_ai_private                 # → resource group rg-wc01_pai_ai_private
azd env set AZURE_LOCATION eastus2
azd env set ENABLE_PRIVATE_NETWORKING true
azd env set NETWORK_RESOURCE_GROUP rg-wc01_pai_ai_network
azd env set NETWORK_VNET_NAME <from Stage 1>
azd env set ENABLE_AI_GATEWAY true
azd env set AI_GATEWAY_SKU StandardV2
python3 scripts/check_network.py --stage pre
azd up                                          # ~60–90 min first time (capability host 30–35 min)
```

### 8.5 Verify
`python3 scripts/check_network.py` (all private), `python3 scripts/check_agents.py`, the KQL in `docs/app-insights.md`, and from a laptop **outside** the VNet (without the proxy): every endpoint refuses access.

### 8.6 Open the app
Laptop browser through the SOCKS proxy, or Edge on the desktop jumpbox → `https://<frontendUrl>` (printed at the end of `azd up`).

### 8.7 Tear down
Delete the project capability host first, then the account capability host, then `azd down --purge` (Foundry must be purged before the subnet can be reused; allow ~20 minutes to unlink). Then `azd down` in `infra/bootstrap`.

---

## 9. Identity and RBAC additions

| Principal | Role | Scope | Why |
|---|---|---|---|
| Foundry project MI | Cosmos DB Operator; Cosmos built-in Data Contributor | Cosmos account / DB | Thread storage |
| Foundry project MI | Storage Blob Data Contributor; Storage Blob Data Owner (conditioned) | Storage | File/agent storage |
| Foundry project MI | Search Index Data Contributor; Search Service Contributor | AI Search | Vector store |
| Deployer | AcrPush (local builds); Container Registry Tasks Contributor (if ACR Tasks) | ACR | Image push |
| Jumpbox users group | Virtual Machine User Login (Administrator Login for those who need `sudo`) | Jumpbox VM | Entra SSH |
| Jumpbox users group | Reader | Jumpbox VM, NIC, Bastion | Bastion connect |
| Jumpbox users group (desktop mode) | Key Vault Secrets User | Jumpbox Key Vault | Read the desktop password |
| Jumpbox VM MI | Key Vault Secrets Officer | Jumpbox Key Vault | Store / rotate the desktop password |
| APIM MI | Cognitive Services OpenAI User (unchanged) | Foundry | Backend auth |
| Databricks access connector | Storage Blob Data Contributor (unchanged) | UC storage | Unity Catalog |

---

## 10. Cost (indicative, East US 2, per month, pay-as-you-go)

| Item | Rough cost |
|---|---|
| APIM StandardV2 (1 unit) vs BasicV2 | ~$700 vs ~$150 |
| ACR Premium vs Basic | ~$50 vs ~$5 |
| Azure Bastion Standard | ~$210 |
| NAT gateway + IP | ~$35 + data |
| Jumpbox, headless `D2s_v5` (auto-shutdown, ~10 h/day weekdays) | ~$25–35 (~$70 always on) |
| Jumpbox, desktop `D4s_v5` (same schedule) | ~$50–70 (~$140 always on) |
| Private endpoints (~15) | ~$110 + data |
| Cosmos DB, AI Search (standard), Storage (standard setup) | AI Search standard ~$250; Cosmos/Storage usage-based |
| Databricks Premium + private link | Usage-based |

No Windows licence is needed. Confirm with the Azure pricing calculator before approval; Bastion and the jumpbox can be deallocated when not in use.

---

## 11. Security findings: how this spec resolves them

| Finding | Resolution in private mode | Resource scope |
|---|---|---|
| Cognitive Services public network access disabled or restricted | `publicNetworkAccess: 'Disabled'`, `networkAcls.defaultAction: 'Deny'` (§6.3) | Template |
| Container Registry disallows unrestricted network access | Premium, `publicNetworkAccess: 'Disabled'`, `pe-acr` (§6.5) | Template |
| Application Insights blocks public ingestion and query | Both `Disabled` + AMPLS `PrivateOnly` (§6.7). Log Analytics gets the same, which pre-empts the likely next finding. | Template |
| PostgreSQL flexible server public access disabled | **Not a template resource.** Pattern for its owner (matches the research design's ODS options): recreate with **private access (VNet integration)** in `snet-psql` and zone `privatelink.postgres.database.azure.com`, or keep public-access mode with a private endpoint in `snet-pe` and `publicNetworkAccess: Disabled`. Remove the `0.0.0.0` "Allow all Azure services" rule now. | Other team |

The **current** public environment still carries these findings. Until it's replaced, the interim options from 2026-11-10 apply: IP-restrict Foundry, upgrade ACR to Premium with IP rules, and log a time-limited exception for App Insights (turning public ingestion off without AMPLS stops all telemetry, which happened on 2026-11-10 at ~09:35 UTC).

---

## 12. Implementation plan

| Phase | Work | Output | Depends on |
|---|---|---|---|
| **0. Spikes** (sandbox RG) | **S1** hosted agent on a network-injected account with a private Premium ACR (local Docker build + push from Linux, agent pulls, agent calls a public MCP server through NAT). **S2** APIM StandardV2: VNet integration in a /27 + `pe-apim` + disabling public access after creation; agent → APIM → `pe-foundry`. **S3** Can `docker.remoteBuild` be per-environment; does ACR Tasks work with trusted-service bypass on a private ACR? **S4** Foundry tracing / App Insights connection with AMPLS `PrivateOnly`. **S5** Databricks Genie serverless with NCC + private UC storage. **S6** Jumpbox: cloud-init toolchain on Ubuntu 24.04; Entra SSH through a Bastion tunnel with VS Code Remote-SSH; SOCKS browsing of the private frontend and Foundry portal; desktop mode (GNOME + xrdp + Edge) through Bastion RDP; per-person desktop accounts. | Findings appended to this spec (§14), go/no-go per decision | Subscription with quota; Databricks account admin for S5 |
| **1. Bootstrap** (built 2026-11-10; what-if validated, not yet deployed) | `infra/bootstrap/azure.yaml`, `main.bicep`, `main.parameters.json`, `modules/network.bicep` (VNet, subnets, NSGs), `nat.bicep`, `dns.bicep`, `bastion.bicep`, `keyvault.bicep`, `jumpbox.bicep`, `access.bicep`, `scripts/setup-jumpbox.sh` (first boot: toolchain, desktop, `jumpbox-check`, `jumpbox-rotate-password`), `scripts/hooks.py`, `README.md`; tests `scripts/test_bootstrap.py` | Stage 1 deployable from a laptop | S6 |
| **2. Core private template** | `private-endpoint.bicep`; Foundry network injection + `foundry-standard-setup.bicep`; ACR Premium private; monitoring + AMPLS; internal Container Apps in `snet-mcp` + DNS; `main.bicep` wiring and parameters; `preprovision`/`postprovision` changes (posix first); `check_network.py` | `azd up` from the jumpbox deploys the prior-auth agents privately, without the gateway | Phase 1, S1, S3, S4 |
| **3. Private AI gateway** | APIM StandardV2 VNet integration + `pe-apim`; `lock_public_access.py`; Spec 001 hooks verified privately on Linux; `check_agents.py` uses `check_network.py` | Agents → APIM → Foundry, all private; 75k limit and logs working | Phase 2, S2 |
| **4. Private Databricks (optional)** | VNet-injected workspace, `pe-dbx`, private UC storage, NCC in `bootstrap_databricks.py`, Genie route through APIM | Supply agent works privately | Phase 3, S5 |
| **5. Docs, tests, hand-over** | `docs/private-deployment.md` (runbook §8), `docs/troubleshooting.md` private section, `scripts/test_private_network.py`, Specs/README update; research-document corrections (§5.7) sent to the doc owner | Team can deploy unaided | Phases 1–4 |
| **6. Hub and production options** | Hub mode (§5.6: White Cap spoke, hub resolver, `rt-spoke-egress` → firewall), Azure Firewall egress for the standalone network (`EGRESS_MODE=firewall`), Application Gateway/Front Door for user access, Phase 3 runner in `snet-build` instead of the jumpbox | Production hardening | White Cap network team |

---

## 13. Acceptance criteria and tests

1. `azd up` with `ENABLE_PRIVATE_NETWORKING=false` produces the same what-if result as `Ai-gateway` (no regressions). `scripts/test_deployment.py` and `scripts/test_ai_gateway.py` pass.
2. `scripts/test_private_network.py` (new, offline): compiles `main.bicep` with the flag on and asserts every Foundry, ACR, APIM, Storage, Cosmos, Search, Log Analytics, App Insights and Databricks resource has public access `Disabled` (APIM after the lock step), every private endpoint has a DNS zone group, BasicV2 + private mode is rejected, and the bootstrap subnets match §5.2 (names, prefixes, delegations).
3. Stage 1 `azd up` from a laptop succeeds; Entra SSH through the Bastion tunnel works; VS Code Remote-SSH connects; `jumpbox-check` reports every tool in §5.4.2. Desktop mode: Bastion RDP shows the GNOME desktop with Edge and VS Code, and the password exists only in Key Vault.
4. Stage 2 `azd up` from the jumpbox succeeds; `check_network.py` reports all FQDNs on private IPs; `check_agents.py` passes.
5. A prior-auth review runs end to end from the frontend (SOCKS proxy or desktop Edge); `ApiManagementGatewayLogs` shows the agents' model calls with 200s; App Insights receives traces through AMPLS.
6. From outside the VNet: Foundry, APIM, ACR, App Insights ingestion/query, Container Apps and Databricks all refuse access.
7. The four findings (§11) clear for template-owned resources in the new environment.

---

## 14. Risks and open questions

| # | Risk / question | Mitigation |
|---|---|---|
| R1 | Hosted agents + network injection are recent features; behaviour may differ from docs | Spike S1 before Phase 2 |
| R2 | Foundry platform-side tracing may not reach App Insights with public ingestion disabled | Spike S4; fall back to `ingestionAccessMode: Open` on the AMPLS with a documented exception |
| R3 | `azd ai agent` with private ACR: build path and registry connection | Spike S1/S3; pre-built image path (`image:` in `azure.yaml`) as fallback |
| R4 | APIM `publicNetworkAccess` can't be disabled at creation | Two-step lock in `lock_public_access.py` |
| R5 | Databricks serverless (Genie) privately needs account-level NCC and an account admin | Spike S5; Databricks stays optional |
| R6 | Desktop mode uses a local password (xrdp can't use Entra ID); GNOME over xrdp can be sluggish | Headless is the default; password generated on the VM, stored only in Key Vault, rotated by script; Bastion keeps Entra in front; S6 tests per-person accounts. Windows 11 jumpbox if a password-free desktop is mandatory. |
| R7 | Entra SSH certificates expire after about an hour, interrupting VS Code sessions | Document `az ssh cert` refresh; spike S6 checks a helper that refreshes it |
| R8 | Hook scripts were last exercised on Windows | Run `scripts/test_*.py` and the hooks on the jumpbox in Phase 2 |
| R9 | Capability host / subnet teardown order; subnet "in use" errors | Documented teardown (§8.7) and a `scripts/teardown_private.py` helper |
| R10 | `10.50.0.0/22` may overlap networks it's peered with later | White Cap IPAM assigns final ranges; all prefixes are parameters |
| R11 | Prompt agents using the APIM connection won't work privately (managed inference plane), and have the 64-character tool-name limit | Out of scope; hosted agents carry gateway-governed traffic |
| R12 | Purview integration for Foundry doesn't support network isolation | Accept; revisit when Microsoft adds support |
| R13 | Cost increase (§10) | Approval before Phase 1; auto-shutdown; deallocate Bastion in idle periods |
| Q1 | When will the baseline move into White Cap's hub (§5.6), and who owns DNS and firewall rules there? | White Cap network team |
| Q2 | Egress policy: is NAT acceptable for the baseline, or is an FQDN firewall required now? | Security team |
| Q3 | Who needs the frontend, and from where? (jumpbox only vs App Gateway/Front Door) | Product owner |
| Q4 | Is a password-based desktop acceptable, or is headless (Entra-only) required? | Security team |

---

## 15. References (checked 2026-11-10)

- Foundry Agent Service private networking: https://learn.microsoft.com/azure/foundry/agents/how-to/virtual-networks
- Agents networking deep dive: https://learn.microsoft.com/azure/foundry/agents/concepts/agents-networking-deep-dive
- Hosted agents with a private ACR: https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent-private-azure-container-registry
- Toolbox network isolation: https://learn.microsoft.com/azure/foundry/agents/how-to/tools/toolbox-network-isolation
- Configure private link for Foundry: https://learn.microsoft.com/azure/foundry/how-to/configure-private-link
- Bring your own model / AI gateway (private gateway networking): https://learn.microsoft.com/azure/foundry/agents/how-to/ai-gateway
- Sample 15 (private standard agent setup): https://github.com/microsoft-foundry/foundry-samples/tree/main/infrastructure/infrastructure-setup-bicep/15-private-network-standard-agent-setup
- Sample 16 (private standard agent setup with APIM): https://github.com/microsoft-foundry/foundry-samples/tree/main/infrastructure/infrastructure-setup-bicep/16-private-network-standard-agent-apim-setup
- APIM v2 tiers: https://learn.microsoft.com/azure/api-management/v2-service-tiers-overview
- APIM virtual network concepts: https://learn.microsoft.com/azure/api-management/virtual-network-concepts
- APIM inbound private endpoint: https://learn.microsoft.com/azure/api-management/private-endpoint
- Azure Monitor Private Link Scope: https://learn.microsoft.com/azure/azure-monitor/logs/private-link-security
- Container Apps networking: https://learn.microsoft.com/azure/container-apps/networking
- ACR private link: https://learn.microsoft.com/azure/container-registry/container-registry-private-link
- Private endpoint network policies: https://learn.microsoft.com/azure/private-link/disable-private-endpoint-network-policy
- Azure Bastion native client (tunnel, SSH, RDP): https://learn.microsoft.com/azure/bastion/connect-vm-native-client-windows
- Entra ID SSH sign-in for Linux VMs: https://learn.microsoft.com/entra/identity/devices/howto-vm-sign-in-azure-ad-linux
- Ubuntu on Azure (Canonical images): https://documentation.ubuntu.com/azure/
- xrdp on Azure Linux VMs: https://learn.microsoft.com/azure/virtual-machines/linux/use-remote-desktop
- VS Code Remote-SSH: https://code.visualstudio.com/docs/remote/ssh
- Databricks VNet injection: https://learn.microsoft.com/azure/databricks/security/network/classic/vnet-inject
- Databricks private link: https://learn.microsoft.com/azure/databricks/security/network/classic/private-link
- Databricks serverless private connectivity (NCC): https://learn.microsoft.com/azure/databricks/security/network/serverless-network-security/
