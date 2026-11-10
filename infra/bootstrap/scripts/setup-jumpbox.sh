#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Jumpbox setup (Spec 002 section 5.4). Runs as root through the VM's Custom Script
# extension, after the Entra SSH extension, and again whenever this script changes
# (it is idempotent). infra/bootstrap/main.bicep prepends these variables:
#   JUMPBOX_DESKTOP    true | false
#   JUMPBOX_KEY_VAULT  Key Vault name for the desktop password
#   JUMPBOX_ADMIN      break-glass admin user
#   JUMPBOX_TENANT_ID, JUMPBOX_SUBSCRIPTION_ID   used in the sign-in hint
#
# Every step is attempted even if an earlier one fails. Results:
#   /var/log/jumpbox-setup.log      full log
#   /var/lib/jumpbox-setup.done     status line (written last)
#   jumpbox-check                   prints every tool's version
# ---------------------------------------------------------------------------
set -uo pipefail

JUMPBOX_DESKTOP="${JUMPBOX_DESKTOP:-false}"
JUMPBOX_KEY_VAULT="${JUMPBOX_KEY_VAULT:-}"
JUMPBOX_ADMIN="${JUMPBOX_ADMIN:-azureadmin}"
JUMPBOX_TENANT_ID="${JUMPBOX_TENANT_ID:-}"
JUMPBOX_SUBSCRIPTION_ID="${JUMPBOX_SUBSCRIPTION_ID:-}"

LOG=/var/log/jumpbox-setup.log
exec > >(tee -a "$LOG") 2>&1

export DEBIAN_FRONTEND=noninteractive
export HOME="${HOME:-/root}"
AZ_EXT_DIR=/opt/az-extensions
APT=(apt-get -o DPkg::Lock::Timeout=900 -y)
FAILED=()

step() {
  local name="$1"; shift
  echo "=== $(date -u +%FT%TZ) START $name"
  if "$@"; then
    echo "=== $(date -u +%FT%TZ) OK    $name"
  else
    echo "=== $(date -u +%FT%TZ) FAIL  $name"
    FAILED+=("$name")
  fi
}

# -- Configuration file used by jumpbox-check and jumpbox-rotate-password ----
write_config() {
  cat > /etc/jumpbox.conf <<EOF
JUMPBOX_DESKTOP=${JUMPBOX_DESKTOP}
JUMPBOX_KEY_VAULT=${JUMPBOX_KEY_VAULT}
JUMPBOX_ADMIN=${JUMPBOX_ADMIN}
JUMPBOX_TENANT_ID=${JUMPBOX_TENANT_ID}
JUMPBOX_SUBSCRIPTION_ID=${JUMPBOX_SUBSCRIPTION_ID}
EOF
  chmod 644 /etc/jumpbox.conf
}

install_base() {
  "${APT[@]}" update &&
  "${APT[@]}" install ca-certificates curl gnupg lsb-release git jq unzip zip \
    build-essential python3-venv python3-pip software-properties-common \
    apt-transport-https unattended-upgrades openssl
}

# Microsoft signing key and the Ubuntu 24.04 "prod" repo (PowerShell and others).
install_microsoft_repo() {
  curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor --yes -o /usr/share/keyrings/microsoft.gpg &&
  chmod 644 /usr/share/keyrings/microsoft.gpg &&
  curl -fsSL https://packages.microsoft.com/config/ubuntu/24.04/packages-microsoft-prod.deb -o /tmp/packages-microsoft-prod.deb &&
  dpkg -i /tmp/packages-microsoft-prod.deb &&
  "${APT[@]}" update
}

install_azure_cli() {
  curl -fsSL https://aka.ms/InstallAzureCLIDeb | bash
}

# Extensions in a shared directory so every user (including Entra SSH users) has them.
install_azure_cli_extensions() {
  mkdir -p "$AZ_EXT_DIR" &&
  grep -q '^AZURE_EXTENSION_DIR=' /etc/environment || echo "AZURE_EXTENSION_DIR=$AZ_EXT_DIR" >> /etc/environment
  local ext rc=0
  for ext in containerapp application-insights log-analytics ssh databricks; do
    AZURE_EXTENSION_DIR="$AZ_EXT_DIR" az extension add --name "$ext" --upgrade --yes --only-show-errors || rc=1
  done
  chmod -R a+rX "$AZ_EXT_DIR"
  return $rc
}

install_powershell() {
  "${APT[@]}" install powershell
}

install_docker() {
  install -m 0755 -d /etc/apt/keyrings &&
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc &&
  chmod a+r /etc/apt/keyrings/docker.asc &&
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list &&
  "${APT[@]}" update &&
  "${APT[@]}" install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin &&
  systemctl enable --now docker &&
  usermod -aG docker "$JUMPBOX_ADMIN"
}

install_azd() {
  curl -fsSL https://aka.ms/install-azd.sh | bash &&
  # The archive keeps the build machine's file owner; make the install root-owned.
  chown -R root:root /opt/microsoft/azd
}

install_uv() {
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
}

install_node() {
  curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - &&
  "${APT[@]}" install nodejs
}

install_databricks_cli() {
  # The installer refuses to overwrite an existing CLI, so skip it on re-runs.
  if command -v databricks >/dev/null 2>&1; then
    echo "Databricks CLI already installed: $(databricks --version)"
    return 0
  fi
  curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
}

configure_unattended_upgrades() {
  cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
  cat > /etc/apt/apt.conf.d/52jumpbox-upgrades <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "03:00";
EOF
  systemctl enable --now unattended-upgrades
}

# Per-user setup at login: docker group membership and the azd agents extension
# (azd extensions are installed per user). Entra SSH users are created at first sign-in.
configure_login_profile() {
  cat > /etc/profile.d/jumpbox.sh <<'EOF'
# Jumpbox per-user setup (Spec 002 section 5.4.2)
if [ -n "${PS1:-}" ] && [ "$(id -u)" -ge 1000 ]; then
  [ -f /etc/jumpbox.conf ] && . /etc/jumpbox.conf
  # Group changes (docker, and aad_admins for sudo) apply to new sessions only.
  if getent group docker >/dev/null && ! id -nG "$USER" | grep -qw docker; then
    if sudo -n usermod -aG docker "$USER" 2>/dev/null; then
      echo "jumpbox: added you to the docker group. Disconnect and reconnect once to use docker (or run: newgrp docker)."
    else
      echo "jumpbox: first sign-in: disconnect and reconnect once so your sudo and docker access take effect."
    fi
  elif ! id -nG | grep -qw docker; then
    echo "jumpbox: disconnect and reconnect once to use docker and sudo (or run: newgrp docker)."
  fi
  # Sign-in hint: a plain "az login" uses the account's home tenant and default subscription.
  if [ -n "${JUMPBOX_TENANT_ID:-}" ] && ! timeout 5 az account show >/dev/null 2>&1; then
    echo "jumpbox: sign in to Azure with:"
    echo "  az login --tenant $JUMPBOX_TENANT_ID --use-device-code"
    echo "  az account set --subscription $JUMPBOX_SUBSCRIPTION_ID"
    echo "  azd auth login --tenant-id $JUMPBOX_TENANT_ID --use-device-code"
  fi
  if command -v azd >/dev/null 2>&1 && [ ! -f "$HOME/.azd/.jumpbox-agents-extension" ]; then
    echo "jumpbox: installing the azd azure.ai.agents extension for $USER..."
    mkdir -p "$HOME/.azd"
    azd extension install azure.ai.agents >/dev/null 2>&1 && touch "$HOME/.azd/.jumpbox-agents-extension"
  fi
  [ -f /var/lib/jumpbox-setup.done ] || echo "jumpbox: first-boot setup is still running (tail -f /var/log/jumpbox-setup.log)"
fi
EOF
  chmod 644 /etc/profile.d/jumpbox.sh
}

install_jumpbox_check() {
  cat > /usr/local/bin/jumpbox-check <<'EOF'
#!/usr/bin/env bash
# Prints the version of every jumpbox tool (Spec 002 section 5.4.2). Exit 1 if anything is missing.
[ -f /etc/jumpbox.conf ] && . /etc/jumpbox.conf
# azd needs HOME (unset under some remote-command tools).
export HOME="${HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export AZURE_EXTENSION_DIR="${AZURE_EXTENSION_DIR:-/opt/az-extensions}"
rc=0
check() {
  local label="$1"; shift
  local out
  out=$("$@" 2>/dev/null | head -n 1)
  if [ -n "$out" ]; then printf '  %-20s %s\n' "$label" "$out"; else printf '  %-20s MISSING\n' "$label"; rc=1; fi
}
echo "Jumpbox toolchain"
check git            git --version
check docker         docker --version
check "docker compose" docker compose version
check "docker buildx" docker buildx version
check az             az version --query '"azure-cli"' -o tsv
check "az extensions" bash -c "az extension list --query '[].name' -o tsv | paste -sd, -"
check azd            azd version
# azd extensions are per user; the login profile installs this one at each user's first sign-in.
if azd extension list --installed 2>/dev/null | grep -qi 'azure.ai.agents'; then
  check "azd ai agents" bash -c "azd extension list --installed 2>/dev/null | grep -i 'azure.ai.agents'"
else
  printf '  %-20s %s
' "azd ai agents" "not installed for $(id -un) yet (installs at first interactive sign-in, or: azd extension install azure.ai.agents)"
fi
check python3        python3 --version
check uv             uv --version
check pwsh           pwsh -NoLogo -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
check node           node --version
check npm            npm --version
check databricks     databricks --version
check jq             jq --version
if [ "${JUMPBOX_DESKTOP:-false}" = "true" ]; then
  check code           code --version
  check edge           microsoft-edge --version
  check xrdp           bash -c "systemctl is-active xrdp"
fi
echo
if [ -f /var/lib/jumpbox-setup.done ]; then
  echo "First-boot setup: $(cat /var/lib/jumpbox-setup.done)"
  grep -q 'status=ok' /var/lib/jumpbox-setup.done || rc=1
else
  echo "First-boot setup: still running (tail -f /var/log/jumpbox-setup.log)"
  rc=1
fi
exit $rc
EOF
  chmod 755 /usr/local/bin/jumpbox-check
}

# Generates a new desktop password for jumpuser and stores it in Key Vault with the
# VM's managed identity. The password is never printed or logged.
install_rotate_password() {
  cat > /usr/local/sbin/jumpbox-rotate-password <<'EOF'
#!/usr/bin/env bash
# Usage: sudo jumpbox-rotate-password
# Sets a new random passphrase for the desktop user and stores it in Key Vault
# (secret jumpbox-desktop-password). Run it when someone leaves the jumpbox group.
# Format: three everyday words + two digits, lowercase, no symbols. It has to be
# typeable: browser RDP can't paste into the xrdp login, and symbols get remapped
# by keyboard layouts. Bastion (Entra sign-in + RBAC) is the first lock in front of it.
set -euo pipefail
. /etc/jumpbox.conf
USER_NAME=jumpuser
SECRET_NAME=jumpbox-desktop-password
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
[ -n "${JUMPBOX_KEY_VAULT:-}" ] || { echo "JUMPBOX_KEY_VAULT is not set in /etc/jumpbox.conf" >&2; exit 1; }
id "$USER_NAME" >/dev/null 2>&1 || { echo "user $USER_NAME does not exist (desktop mode not enabled?)" >&2; exit 1; }

umask 077
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
python3 - > "$tmp" <<'PY'
import secrets
words = """apple river tiger maple cloud stone eagle lemon ocean piano rocket silver garden pencil
candle forest planet butter castle dragon falcon guitar island jungle kettle ladder magnet
orange parrot rabbit saddle tomato violet walnut yellow zebra anchor basket camera desert
engine finger hammer jacket lizard meadow needle pepper puzzle quartz ribbon sunset tunnel
velvet window bridge carrot circle copper cotton dinner donkey flower frozen golden harbor
honey kitten lantern marble mirror motor nickel orbit pillow pirate pocket rainbow salmon
shadow spider summer timber turtle valley wagon winter wizard bamboo banana beacon breeze
cactus cherry comet crystal dolphin ember feather glacier hazel iris juniper koala lotus
mango nectar olive panda pebble raven sierra tulip willow""".split()
print("".join(secrets.choice(words) for _ in range(3)) + str(secrets.randbelow(90) + 10), end="")
PY

stored=false
for attempt in $(seq 1 30); do
  if az login --identity --allow-no-subscriptions --only-show-errors --output none 2>/dev/null &&
     az keyvault secret set --vault-name "$JUMPBOX_KEY_VAULT" --name "$SECRET_NAME" \
        --file "$tmp" --encoding utf-8 --only-show-errors --output none 2>/dev/null; then
    stored=true
    break
  fi
  echo "Key Vault not reachable yet (role assignment or DNS still propagating); retry $attempt/30 in 30s"
  sleep 30
done
if [ "$stored" != true ]; then
  echo "Could not store the password in Key Vault $JUMPBOX_KEY_VAULT; the old password is unchanged." >&2
  exit 1
fi
# Change the password only after it is safely stored.
printf '%s:%s\n' "$USER_NAME" "$(cat "$tmp")" | chpasswd
echo "New desktop password for $USER_NAME stored in Key Vault $JUMPBOX_KEY_VAULT (secret $SECRET_NAME)."
EOF
  chmod 750 /usr/local/sbin/jumpbox-rotate-password
}

# -- Desktop mode ------------------------------------------------------------

install_desktop() {
  "${APT[@]}" install ubuntu-desktop-minimal xrdp || return 1
  adduser xrdp ssl-cert
  # xrdp doesn't need the local display manager; boot to multi-user to save memory.
  systemctl set-default multi-user.target

  # GNOME session settings for xrdp sessions.
  cat > /etc/skel/.xsessionrc <<'EOF'
export GNOME_SHELL_SESSION_MODE=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export XDG_CONFIG_DIRS=/etc/xdg/xdg-ubuntu:/etc/xdg
EOF
  # Avoid repeated "authentication required to create a color managed device" prompts over RDP.
  mkdir -p /etc/polkit-1/rules.d
  cat > /etc/polkit-1/rules.d/45-jumpbox-colord.rules <<'EOF'
polkit.addRule(function(action, subject) {
  if (action.id.indexOf("org.freedesktop.color-manager.") === 0) {
    return polkit.Result.YES;
  }
});
EOF

  # Microsoft Edge and VS Code from Microsoft's apt repos (same key as install_microsoft_repo).
  echo "code code/add-microsoft-repo boolean true" | debconf-set-selections
  cat > /etc/apt/sources.list.d/microsoft-edge.list <<'EOF'
deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/edge stable main
EOF
  cat > /etc/apt/sources.list.d/vscode.sources <<'EOF'
Types: deb
URIs: https://packages.microsoft.com/repos/code
Suites: stable
Components: main
Architectures: amd64
Signed-By: /usr/share/keyrings/microsoft.gpg
EOF
  # A key warning on another Microsoft repo (seen on the first deployment) can make
  # apt fail once; retry, and don't let `apt-get update` warnings stop the install.
  local attempt
  for attempt in 1 2 3; do
    "${APT[@]}" update || true
    "${APT[@]}" install microsoft-edge-stable code && break
    [ "$attempt" -eq 3 ] && return 1
    echo "Edge / VS Code install failed; retry $attempt/3 in 30s"
    sleep 30
  done

  # Always sign in as jumpuser; the login screen only asks for the password. Pasted or
  # client-supplied usernames can carry stray spaces, which PAM rejects as "user unknown".
  [ -f /etc/xrdp/xrdp.ini.orig ] || cp /etc/xrdp/xrdp.ini /etc/xrdp/xrdp.ini.orig
  awk 'BEGIN{s=0} /^\[/{s=($0=="[Xorg]")} s && /^username=/{print "username=jumpuser"; next} {print}' \
    /etc/xrdp/xrdp.ini > /tmp/xrdp.ini && install -m 644 /tmp/xrdp.ini /etc/xrdp/xrdp.ini && rm -f /tmp/xrdp.ini

  systemctl enable xrdp && systemctl restart xrdp
}

create_desktop_user() {
  if ! id jumpuser >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash --groups docker,users jumpuser || return 1
  fi
  /usr/local/sbin/jumpbox-rotate-password
}

# -- Run ---------------------------------------------------------------------

echo "=== Jumpbox setup starting (desktop=${JUMPBOX_DESKTOP})"
step "configuration file"            write_config
step "base packages"                 install_base
step "Microsoft package repository"  install_microsoft_repo
step "Azure CLI"                     install_azure_cli
step "Azure CLI extensions"          install_azure_cli_extensions
step "PowerShell 7"                  install_powershell
step "Docker Engine"                 install_docker
step "Azure Developer CLI"           install_azd
step "uv"                            install_uv
step "Node.js LTS"                   install_node
step "Databricks CLI"                install_databricks_cli
step "automatic security updates"    configure_unattended_upgrades
step "login profile"                 configure_login_profile
step "jumpbox-check"                 install_jumpbox_check
step "jumpbox-rotate-password"       install_rotate_password
if [ "$JUMPBOX_DESKTOP" = "true" ]; then
  step "desktop (GNOME, xrdp, Edge, VS Code)" install_desktop
  step "desktop user and Key Vault password"  create_desktop_user
fi

if [ "${#FAILED[@]}" -eq 0 ]; then
  echo "$(date -u +%FT%TZ) status=ok desktop=${JUMPBOX_DESKTOP}" > /var/lib/jumpbox-setup.done
else
  printf '%s status=failed desktop=%s steps=%s\n' "$(date -u +%FT%TZ)" "$JUMPBOX_DESKTOP" "$(IFS=';'; echo "${FAILED[*]}")" > /var/lib/jumpbox-setup.done
fi
echo "=== Jumpbox setup finished: $(cat /var/lib/jumpbox-setup.done)"
