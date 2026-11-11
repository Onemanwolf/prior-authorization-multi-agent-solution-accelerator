<#
.SYNOPSIS
  Connects this laptop to the jumpbox for VS Code Remote-SSH (Spec 002, section 5.4.3).

.DESCRIPTION
  Run it whenever you start work, or when VS Code says the connection was refused
  (the Entra SSH certificate is valid for about an hour). It:
    1. installs the Azure CLI 'ssh' and 'bastion' extensions if needed
    2. creates ~/.ssh/jumpbox_entra_rsa (Entra SSH certificates need an RSA key)
    3. gets a fresh Entra SSH certificate for your account (az ssh cert)
    4. writes a 'jumpbox' entry to ~/.ssh/config (between marker comments)
    5. opens a Bastion tunnel (localhost:2222 -> jumpbox:22) in its own window

  Then in VS Code: Remote-SSH: Connect to Host... -> jumpbox
  Or a terminal:   ssh jumpbox
  While connected, localhost:1080 is a SOCKS proxy through the jumpbox for a browser:
    msedge --proxy-server="socks5://127.0.0.1:1080" --user-data-dir="$env:TEMP\edge-private"

  Values default to the outputs of the bootstrap azd environment (run from infra/bootstrap).

.EXAMPLE
  cd infra/bootstrap; ./scripts/connect-jumpbox.ps1
#>
[CmdletBinding()]
param(
  [string]$ResourceGroup,
  [string]$BastionName,
  [string]$VmId,
  [int]$LocalPort = 2222,
  [int]$SocksPort = 1080,
  [string]$HostAlias = 'jumpbox'
)

# 'Continue', not 'Stop': Windows PowerShell 5.1 turns any stderr output from native
# commands (az prints informational messages there) into errors under 'Stop'.
# Failures are checked with $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'

function Get-AzdValue([string]$Name) {
  $value = & azd env get-value $Name 2>$null
  if ($LASTEXITCODE -eq 0) { return "$value".Trim() } else { return '' }
}

if (-not $ResourceGroup) { $ResourceGroup = Get-AzdValue 'NETWORK_RESOURCE_GROUP' }
if (-not $BastionName)   { $BastionName   = Get-AzdValue 'BASTION_NAME' }
if (-not $VmId)          { $VmId          = Get-AzdValue 'JUMPBOX_VM_ID' }
if (-not ($ResourceGroup -and $BastionName -and $VmId)) {
  throw 'ResourceGroup, BastionName and VmId are required. Run from infra/bootstrap (azd env with outputs) or pass them.'
}

# 1. CLI extensions
foreach ($ext in 'ssh', 'bastion') {
  & az extension show --name $ext --only-show-errors 1>$null 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Azure CLI extension '$ext'..."
    & az extension add --name $ext --yes --only-show-errors
  }
}

# 2. RSA key for Entra SSH certificates (Windows OpenSSH keygen sets the file ACLs it expects)
$sshDir  = Join-Path $HOME '.ssh'
$key     = Join-Path $sshDir 'jumpbox_entra_rsa'
$cert    = "$key-aadcert.pub"
$keygen  = Join-Path $env:WINDIR 'System32\OpenSSH\ssh-keygen.exe'
if (-not (Test-Path $keygen)) { $keygen = 'ssh-keygen' }
New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
if (-not (Test-Path $key)) {
  Write-Host "Creating SSH key $key"
  & $keygen -q -t rsa -b 4096 -N '""' -C 'jumpbox-entra' -f $key
  if (-not (Test-Path $key)) { & $keygen -q -t rsa -b 4096 -N '' -C 'jumpbox-entra' -f $key }
}

# 3. Fresh Entra SSH certificate (about an hour)
Write-Host 'Getting an Entra SSH certificate...'
& az ssh cert --public-key-file "$key.pub" --file $cert --only-show-errors | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'az ssh cert failed. Run az login first (with --tenant if your account has several).' }
$user = (& az account show --query user.name -o tsv).Trim()

# 4. ~/.ssh/config entry
$config = Join-Path $sshDir 'config'
$begin  = "# >>> $HostAlias (managed by connect-jumpbox.ps1)"
$end    = "# <<< $HostAlias"
$block  = @"
$begin
Host $HostAlias
  HostName 127.0.0.1
  Port $LocalPort
  User $user
  IdentityFile ~/.ssh/jumpbox_entra_rsa
  CertificateFile ~/.ssh/jumpbox_entra_rsa-aadcert.pub
  IdentitiesOnly yes
  DynamicForward $SocksPort
  ServerAliveInterval 30
  StrictHostKeyChecking accept-new
  UserKnownHostsFile ~/.ssh/known_hosts_$HostAlias
$end
"@
$existing = if (Test-Path $config) { Get-Content $config -Raw } else { '' }
$pattern  = "(?s)" + [regex]::Escape($begin) + ".*?" + [regex]::Escape($end) + "\r?\n?"
$updated  = ([regex]::Replace($existing, $pattern, '')).TrimEnd() + "`r`n`r`n" + $block + "`r`n"
[System.IO.File]::WriteAllText($config, $updated.TrimStart(), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Updated $config (Host $HostAlias, user $user)"

# 5. Bastion tunnel in its own window (skip if the port is already open)
$open = (Test-NetConnection -ComputerName 127.0.0.1 -Port $LocalPort -WarningAction SilentlyContinue -InformationLevel Quiet)
if ($open) {
  Write-Host "Tunnel already listening on localhost:$LocalPort"
} else {
  Write-Host "Opening Bastion tunnel localhost:$LocalPort -> jumpbox:22 (leave its window open)..."
  $tunnelArgs = "network bastion tunnel --name $BastionName --resource-group $ResourceGroup --target-resource-id $VmId --resource-port 22 --port $LocalPort"
  Start-Process -FilePath 'cmd.exe' -ArgumentList "/k title Jumpbox tunnel & az $tunnelArgs" -WindowStyle Minimized
  $deadline = (Get-Date).AddSeconds(45)
  while (-not (Test-NetConnection -ComputerName 127.0.0.1 -Port $LocalPort -WarningAction SilentlyContinue -InformationLevel Quiet)) {
    if ((Get-Date) -gt $deadline) { throw "The tunnel didn't open on port $LocalPort. Check the 'Jumpbox tunnel' window." }
    Start-Sleep -Seconds 2
  }
}

Write-Host ''
Write-Host 'Ready.' -ForegroundColor Green
Write-Host "  VS Code : Remote-SSH: Connect to Host... -> $HostAlias"
Write-Host "  Terminal: ssh $HostAlias"
Write-Host "  Browser : msedge --proxy-server=`"socks5://127.0.0.1:$SocksPort`" --user-data-dir=`"`$env:TEMP\edge-private`" (while an SSH session is open)"
Write-Host '  Re-run this script when VS Code says the connection was refused (certificate expired).'
