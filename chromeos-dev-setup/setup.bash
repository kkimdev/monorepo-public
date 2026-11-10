#!/usr/bin/env bash
set -euo pipefail

export CROS_SETUP_SCRIPT_FILE="$(readlink -f "${BASH_SOURCE[0]}")"
export SCRIPT_DIR="$(dirname "$CROS_SETUP_SCRIPT_FILE")"

# TODO
# - [ ] https://github.com/sigoden/aichat setup
#
# ── W1 "ignoring untrusted substituter 'https://codex-desktop-linux.cachix.org'" ──
#  Rank 1: Add user to trusted-users in /etc/nix/nix.custom.conf
#    NOTE: Must expand $USER in the OUTER shell. Using `sudo bash -c '...$USER...'`
#    is a trap: single quotes block outer expansion and the inner bash runs under
#    sudo's env_reset, where $USER=root — so it writes `trusted-users = root` (no-op).
#    grep runs as the regular user (file is 644); only the write needs sudo.
#    grep -qxF "trusted-users = $USER" /etc/nix/nix.custom.conf 2>/dev/null || echo "trusted-users = $USER" | sudo tee -a /etc/nix/nix.custom.conf > /dev/null
#    sudo systemctl restart nix-daemon   # daemon caches nix.conf at startup; required for trusted-users/substituters changes
#    (Line 49 adds this at install time, but if Nix was pre-existing the if-branch is skipped.)
#  Rank 2: Trust codex cache via extra-trusted-substituters in nix.custom.conf
#    sudo bash -c 'grep -qxF "extra-trusted-substituters = https://codex-desktop-linux.cachix.org" /etc/nix/nix.custom.conf 2>/dev/null || echo "extra-trusted-substituters = https://codex-desktop-linux.cachix.org" >> /etc/nix/nix.custom.conf'
#    sudo bash -c 'grep -qxF "extra-trusted-public-keys = codex-desktop-linux.cachix.org-1:nX/xy6AdK9hQE24A8ALGjkCKj2ObFmcnemiL5Cid4nk=" /etc/nix/nix.custom.conf 2>/dev/null || echo "extra-trusted-public-keys = codex-desktop-linux.cachix.org-1:nX/xy6AdK9hQE24A8ALGjkCKj2ObFmcnemiL5Cid4nk=" >> /etc/nix/nix.custom.conf'
#  Rank 3: Ignore
#
# ── W2 "GitHub API rate limit exceeded (HTTP 403)" ──
#  Rank 1: Add access-tokens to /etc/nix/nix.custom.conf
#    Create PAT at https://github.com/settings/tokens (no scopes needed), then:
#    export GITHUB_TOKEN=ghp_xxxx
#    grep -qxF "access-tokens = github.com=$GITHUB_TOKEN" /etc/nix/nix.custom.conf 2>/dev/null || echo "access-tokens = github.com=$GITHUB_TOKEN" >> /etc/nix/nix.custom.conf
#  Rank 2: Ignore
#
# ── W3 "unknown setting 'eval-cores'" / "unknown setting 'lazy-trees'" ──
#  Rank 1: Ignore — Nix 2.34.7 supports both; warnings are transient (HM internals, Determinate config)
#  Rank 2: Upgrade Nix — sudo determinate-nixd upgrade (unreliable, failed earlier due to download error)
#  Rank 3: Override in nix.custom.conf — same values already in /etc/nix/nix.conf, won't suppress
#
# ── W4 "Using 'builtins.derivation' to create a derivation named 'options.json'" ──
#  Rank 1: Ignore — upstream home-manager internal, harmless
#  Rank 2: Update home-manager flake input — nix flake update home-manager (may break unrelated deps)

BUILD_ONLY=0
case "${1:-}" in
  "") ;;
  --build-only) BUILD_ONLY=1; shift ;;
  *) echo "Usage: bash $CROS_SETUP_SCRIPT_FILE [--build-only]" >&2; exit 2 ;;
esac
if [ "$#" -ne 0 ]; then
  echo "Usage: bash $CROS_SETUP_SCRIPT_FILE [--build-only]" >&2
  exit 2
fi

# Fail before apt or generated-file writes when first-install prerequisites
# are missing. Git identity is serialized into host.json, never shell code.
if [ -z "${GIT_USER_NAME:-}" ] || [ -z "${GIT_USER_EMAIL:-}" ]; then
  echo "ERROR: export GIT_USER_NAME and GIT_USER_EMAIL before running setup." >&2
  echo "See $SCRIPT_DIR/README.md for the first-install commands." >&2
  exit 1
fi
if [ "$(uname -m)" != x86_64 ]; then
  echo "ERROR: this package set currently supports x86_64 Crostini only." >&2
  exit 1
fi
for required_file in flake.nix flake.lock home.nix; do
  if [ ! -f "$SCRIPT_DIR/$required_file" ]; then
    echo "ERROR: missing $SCRIPT_DIR/$required_file; use the complete repository checkout." >&2
    exit 1
  fi
done
if [ "$BUILD_ONLY" -eq 1 ] && ! command -v nix >/dev/null 2>&1; then
  echo "ERROR: --build-only requires an existing Nix installation." >&2
  exit 1
fi

if [ "$BUILD_ONLY" -eq 0 ]; then
  sudo apt-get update -y
  sudo apt-get install uidmap cros-im btrfs-progs curl ca-certificates git xz-utils python3 -y
  sudo apt-get remove vim vim-tiny command-not-found -y
  sudo apt-get autoremove -y
fi

# Keep /tmp independent of Nix/Home Manager.  The single public function below
# owns the complete root-level bootstrap; its generated systemd/tmpfiles config
# remains effective when Nix is absent.
TMP_QUOTA_GIB=""
TMP_MOUNT_PENDING=0

configure_disk_backed_tmp() {
  local tmp_subvolume_path=/var/lib/tmp
  local tmp_mount_override_dir=/etc/systemd/system/tmp.mount.d
  local tmp_mount_override_file="$tmp_mount_override_dir/override.conf"
  local tmp_tmpfiles_config=/etc/tmpfiles.d/tmp.conf
  local tmp_mem_total_kib
  local tmp_quota_bytes
  local tmp_subvolume_id
  local tmp_mount_source

  tmp_mem_total_kib="$(awk '$1 == "MemTotal:" { print $2; exit }' /proc/meminfo)"
  if [ -z "$tmp_mem_total_kib" ] || [ "$tmp_mem_total_kib" -le 0 ]; then
    echo "ERROR: could not determine MemTotal for the /tmp quota." >&2
    exit 1
  fi
  tmp_quota_bytes=$((tmp_mem_total_kib * 1024 / 2))
  TMP_QUOTA_GIB="$(awk -v bytes="$tmp_quota_bytes" 'BEGIN { printf "%.1f GiB", bytes / 1024 / 1024 / 1024 }')"

  if [ "$(findmnt -no FSTYPE -T /)" != btrfs ]; then
    echo "ERROR: the root filesystem is not btrfs; refusing to configure a btrfs /tmp subvolume." >&2
    exit 1
  fi
  if ! command -v btrfs >/dev/null 2>&1; then
    echo "ERROR: btrfs-progs is unavailable after installation." >&2
    exit 1
  fi

  sudo install -d -m 0755 /var/lib
  if ! sudo btrfs subvolume show "$tmp_subvolume_path" >/dev/null 2>&1; then
    if [ -e "$tmp_subvolume_path" ]; then
      echo "ERROR: $tmp_subvolume_path exists but is not a btrfs subvolume; refusing to replace it." >&2
      exit 1
    fi
    sudo btrfs subvolume create "$tmp_subvolume_path"
  fi
  sudo chmod 1777 "$tmp_subvolume_path"

  # qgroup enable is idempotent in practice but reports an error when already
  # enabled; the limit command below is the authoritative success check.  The
  # Debian btrfs-progs version requires the explicit level-0 qgroup ID.
  sudo btrfs quota enable / >/dev/null 2>&1 || true
  tmp_subvolume_id="$(sudo btrfs subvolume show "$tmp_subvolume_path" | awk '$1 == "Subvolume" && $2 == "ID:" { print $3; exit }')"
  if [ -z "$tmp_subvolume_id" ]; then
    echo "ERROR: could not determine the btrfs subvolume ID for $tmp_subvolume_path." >&2
    exit 1
  fi
  sudo btrfs qgroup limit "$tmp_quota_bytes" "0/$tmp_subvolume_id" "$tmp_subvolume_path"

  # Disable data CoW for this disposable build area.  Keep relatime (the mount
  # default) instead of noatime so age-based cleanup still has useful access
  # timestamps without updating metadata on every read.
  sudo chattr +C "$tmp_subvolume_path"

  sudo install -d -m 0755 "$tmp_mount_override_dir" /etc/tmpfiles.d
  sudo tee "$tmp_mount_override_file" >/dev/null <<'EOF'
[Mount]
What=/var/lib/tmp
Type=none
Options=bind,nosuid,nodev
DirectoryMode=1777
EOF
  sudo tee "$tmp_tmpfiles_config" >/dev/null <<'EOF'
# Keep the vendor cleanup policy but remove short-lived build artifacts sooner.
q /tmp 1777 root root 2d
q /var/tmp 1777 root root 30d
EOF
  sudo systemctl daemon-reload

  tmp_mount_source="$(findmnt -no SOURCE -T /tmp 2>/dev/null || true)"
  if [ "$tmp_mount_source" = tmpfs ]; then
    TMP_MOUNT_PENDING=1
    echo "Configured disk-backed /tmp for the next reboot (current tmpfs was not unmounted)."
  fi
  echo "Configured /tmp quota: $TMP_QUOTA_GIB ($tmp_quota_bytes bytes)."
}

if [ "$BUILD_ONLY" -eq 0 ]; then
  configure_disk_backed_tmp
  # Required for GPU acceleration access in Crostini.
  sudo usermod -aG render "$USER"
fi

# cros-im is installed by ChromeOS on supported containers, but install it
# explicitly above so a fresh or older Crostini image gets the GTK/Qt bridge
# that both X11 (through Xwayland) and Wayland applications use.
CROS_IM_GTK3_MODULE="$(dpkg -L cros-im | awk '/\/gtk-3\.0\/.*\/immodules\/im-cros-gtk3\.so$/ { print; exit }')"
CROS_IM_GTK4_MODULE="$(dpkg -L cros-im | awk '/\/gtk-4\.0\/.*\/immodules\/libim-cros-gtk4\.so$/ { print; exit }')"
CROS_IM_QT5_MODULE="$(dpkg -L cros-im | awk '/\/qt5\/plugins\/platforminputcontexts\/libcrosplatforminputcontextplugin\.so$/ { print; exit }')"
export CROS_IM_GTK3_CACHE="$(find /usr/lib -type f -path '*/gtk-3.0/*/immodules.cache' -print -quit 2>/dev/null || true)"
export CROS_IM_GTK4_PATH="$(find /usr/lib -type d -path '*/gtk-4.0' -print -quit 2>/dev/null || true)"

if [ -z "$CROS_IM_GTK3_MODULE" ] || [ -z "$CROS_IM_GTK4_MODULE" ] || [ -z "$CROS_IM_QT5_MODULE" ]; then
    echo "ERROR: cros-im did not install all expected GTK3/GTK4/Qt5 modules." >&2
    echo "Why: GTK and Qt applications need the ChromeOS IME bridge on both display backends." >&2
    echo "Fix: verify that the Crostini apt repository provides cros-im, then rerun this setup." >&2
    exit 1
fi
if [ -z "$CROS_IM_GTK3_CACHE" ] || ! grep -q 'im-cros-gtk3\.so' "$CROS_IM_GTK3_CACHE"; then
    echo "ERROR: GTK3's cros-im module cache is missing or does not list im-cros-gtk3.so." >&2
    echo "Why: GTK3 loads cros-im through GTK_IM_MODULE_FILE, including for X11/Xwayland apps." >&2
    echo "Fix: install/reinstall libgtk-3-0 and cros-im, then rerun this setup." >&2
    exit 1
fi
if [ -z "$CROS_IM_GTK4_PATH" ]; then
    echo "ERROR: could not locate GTK4's module parent directory under /usr/lib." >&2
    echo "Why: GTK4 needs GTK_PATH to discover the system cros-im module from Nix applications." >&2
    echo "Fix: verify that the cros-im GTK4 package is installed, then rerun this setup." >&2
    exit 1
fi
if [ ! -c /dev/wl0 ]; then
    echo "ERROR: Crostini's VirtWL device /dev/wl0 is unavailable." >&2
    echo "Why: sommelier-rs connects directly to VirtWL so ChromeOS IME protocols remain available." >&2
    echo "Fix: start this setup inside a supported Crostini container." >&2
    exit 1
fi

echo "Using ChromeOS IME modules:"
echo "  GTK3: $CROS_IM_GTK3_MODULE"
echo "  GTK3 cache: $CROS_IM_GTK3_CACHE"
echo "  GTK4: $CROS_IM_GTK4_MODULE"
echo "  GTK4 search path: $CROS_IM_GTK4_PATH"
echo "  Qt5: $CROS_IM_QT5_MODULE"

# INSTALL NIX (Only if missing)
if ! command -v nix &> /dev/null; then
    echo "Nix not found. Installing via Determinate Systems..."
    curl --proto "=https" --tlsv1.2 -sSfL https://install.determinate.systems/nix | sh -s -- install --no-confirm --extra-conf "trusted-users = root $USER"
    . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
else
    echo "Nix is already installed."
fi

LOCAL_SERVICES_DIR="$HOME/.config/local-services"
LOCAL_SERVICES_CONFIG="$LOCAL_SERVICES_DIR/process-compose.yaml"
LOCAL_SERVICES_STATE_DIR="$HOME/.local/state/local-services"
if [ "$BUILD_ONLY" -eq 0 ]; then
  mkdir -p "$LOCAL_SERVICES_DIR" "$LOCAL_SERVICES_STATE_DIR"
fi

CONF_DIR="$HOME/.config/home-manager"
BUILD_DIR="$(mktemp -d)"
CONFIG_BACKUP_DIR=""
CONFIG_INSTALLED=0
CODEX_WRAPPER_STAGING=""
CODEX_WRAPPER_PATH="$HOME/.local/bin/codex"
CONFIG_FILES=(flake.nix flake.lock home.nix host.json codex-wrapper)

cleanup_setup() {
  local status=$?
  trap - EXIT
  if [ "$CONFIG_INSTALLED" -eq 1 ]; then
    for config_file in "${CONFIG_FILES[@]}"; do
      rm -f -- "$CONF_DIR/$config_file" "$CONF_DIR/.$config_file.new"
      if [ -e "$CONFIG_BACKUP_DIR/$config_file" ] || [ -L "$CONFIG_BACKUP_DIR/$config_file" ]; then
        cp -a -- "$CONFIG_BACKUP_DIR/$config_file" "$CONF_DIR/"
      fi
    done
    echo "Restored the previous generated configuration from $CONFIG_BACKUP_DIR." >&2
  fi
  if [ -n "$CODEX_WRAPPER_STAGING" ] && [ -e "$CODEX_WRAPPER_STAGING" ]; then
    rm -f -- "$CODEX_WRAPPER_PATH"
    mv -- "$CODEX_WRAPPER_STAGING" "$CODEX_WRAPPER_PATH"
    echo "Restored the previous unmanaged Codex wrapper." >&2
  fi
  rm -rf -- "$BUILD_DIR"
  exit "$status"
}
trap cleanup_setup EXIT

# Home Manager owns the installed ~/.local/bin/codex path.
CODEX_WRAPPER_SOURCE="$BUILD_DIR/codex-wrapper"
cat <<'CODEX_WRAPPER' > "$CODEX_WRAPPER_SOURCE"
#!/usr/bin/env bash
set -euo pipefail

# Keep the wrapper's model policy separate from Codex's native config.
defaults="${CODEX_DEFAULTS_FILE:-$HOME/.config/codex-wrapper/default-model.toml}"
config="${CODEX_HOME:-$HOME/.codex}/config.toml"

# Find the next `codex` on PATH instead of hard-coding a package/store path.
# The wrapper itself is skipped, including when it is reached through a symlink.
self="$(readlink -f "$0")"
real_codex=""
IFS=: read -r -a path_entries <<< "${PATH:-}"
for path_entry in "${path_entries[@]}"; do
    [[ -n "$path_entry" ]] || path_entry=.
    candidate="$path_entry/codex"
    [[ -x "$candidate" ]] || continue
    [[ "$(readlink -f "$candidate")" == "$self" ]] && continue
    real_codex="$candidate"
    break
done
if [[ -z "$real_codex" ]]; then
    printf 'codex-wrapper: real codex was not found on PATH\n' >&2
    exit 127
fi

# Respect Codex's explicit user-config bypass option.
for arg in "$@"; do
    [[ "$arg" == "--" ]] && break
    [[ "$arg" == "--ignore-user-config" ]] && exec "$real_codex" "$@"
done

valid_toml() {
    python3 - "$1" <<'PY'
import sys
import tomllib

try:
    with open(sys.argv[1], "rb") as stream:
        tomllib.load(stream)
except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
    raise SystemExit(1)
PY
}

read_managed_values() {
    python3 - "$1" <<'PY'
import json
import sys
import tomllib

try:
    with open(sys.argv[1], "rb") as stream:
        config = tomllib.load(stream)
except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
    raise SystemExit(1)

for key in ("model", "model_reasoning_effort"):
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(1)
    print(json.dumps(value, ensure_ascii=False))
PY
}

show_uninitialized() {
    printf 'codex-wrapper: default model file is not initialized\n' >&2
    printf '  real Codex: %s\n' "$real_codex" >&2
    printf '  default file: %s\n' "$defaults" >&2
    printf '  create it with model and model_reasoning_effort, then run codex again\n' >&2
}

# Serialize bootstrap and config replacement for concurrent launches.
config_dir="${config%/*}"
mkdir -p "$config_dir"
exec 9>"${config}.default-model.lock"
flock -x 9
unlock() {
    flock -u 9
    exec 9>&-
}

if [[ -e "$defaults" && ! -f "$defaults" ]]; then
    printf 'codex-wrapper: default path is not a regular file: %s\n' "$defaults" >&2
    printf '  real Codex: %s\n' "$real_codex" >&2
    unlock
    exit 78
fi

# A missing default file is a first-run state, not an error. If the native
# config already has both values, use it to initialize the wrapper policy.
if [[ ! -e "$defaults" ]]; then
    native_values=""
    if [[ -f "$config" ]] && valid_toml "$config"; then
        native_values="$(read_managed_values "$config" 2>/dev/null || true)"
    fi
    if [[ -n "$native_values" ]] && [[ "$(printf '%s\n' "$native_values" | wc -l)" -eq 2 ]]; then
        defaults_dir="${defaults%/*}"
        mkdir -p "$defaults_dir"
        bootstrap_tmp="$(mktemp "${defaults}.tmp.XXXXXX")"
        {
            printf 'model = %s\n' "$(printf '%s\n' "$native_values" | sed -n '1p')"
            printf 'model_reasoning_effort = %s\n' "$(printf '%s\n' "$native_values" | sed -n '2p')"
        } >"$bootstrap_tmp"
        chmod 600 "$bootstrap_tmp"
        mv -n "$bootstrap_tmp" "$defaults"
        rm -f "$bootstrap_tmp"
        printf 'codex-wrapper: initialized defaults from %s\n' "$config" >&2
    fi
fi

if [[ ! -f "$defaults" ]]; then
    show_uninitialized
    unlock
    exec "$real_codex" "$@"
fi

# Once the wrapper file exists, it is the canonical policy and must be valid.
if ! default_values="$(read_managed_values "$defaults" 2>/dev/null)"; then
    printf 'codex-wrapper: invalid default file: %s\n' "$defaults" >&2
    printf '  real Codex: %s\n' "$real_codex" >&2
    unlock
    exit 78
fi
model="$(printf '%s\n' "$default_values" | sed -n '1p')"
reasoning="$(printf '%s\n' "$default_values" | sed -n '2p')"

# Never rewrite a malformed native config. Leave it intact for repair.
if [[ -f "$config" ]] && ! valid_toml "$config"; then
    printf 'codex-wrapper: native config is invalid: %s\n' "$config" >&2
    printf '  real Codex: %s\n' "$real_codex" >&2
    unlock
    exit 78
fi

# Rewrite the native config through a same-directory temporary file. Missing
# managed keys are inserted before the first TOML table.
tmp="$(mktemp "${config}.tmp.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
input="$config"
[[ -f "$config" ]] || input=/dev/null
awk -v model="$model" -v reasoning="$reasoning" '
    function add_missing() {
        if (!seen["model"]) {
            print "model = " model
            seen["model"] = 1
        }
        if (!seen["model_reasoning_effort"]) {
            print "model_reasoning_effort = " reasoning
            seen["model_reasoning_effort"] = 1
        }
    }
    /^[[:space:]]*\[/ {
        if (!inserted) {
            add_missing()
            inserted = 1
        }
        in_table = 1
    }
    !in_table && /^[[:space:]]*model[[:space:]]*=/ {
        print "model = " model
        seen["model"] = 1
        next
    }
    !in_table && /^[[:space:]]*model_reasoning_effort[[:space:]]*=/ {
        print "model_reasoning_effort = " reasoning
        seen["model_reasoning_effort"] = 1
        next
    }
    { print }
    END {
        if (!inserted) add_missing()
    }
' "$input" >"$tmp"
if [[ -e "$config" ]]; then
    chmod --reference="$config" "$tmp"
else
    chmod 600 "$tmp"
fi
mv "$tmp" "$config"

unlock
# Preserve every original argument and replace this wrapper with Codex itself.
exec "$real_codex" "$@"
CODEX_WRAPPER
chmod 755 "$CODEX_WRAPPER_SOURCE"

# Build the exact checked-in dependency graph before replacing live config.
cp -- "$SCRIPT_DIR/flake.nix" "$SCRIPT_DIR/flake.lock" "$SCRIPT_DIR/home.nix" "$BUILD_DIR/"
python3 - "$BUILD_DIR/host.json" <<'PY_HOST'
import json
import os
from pathlib import Path
import sys

keys = {
    "username": "USER",
    "homeDirectory": "HOME",
    "gitName": "GIT_USER_NAME",
    "gitEmail": "GIT_USER_EMAIL",
    "setupScript": "CROS_SETUP_SCRIPT_FILE",
    "gtk3Cache": "CROS_IM_GTK3_CACHE",
    "gtk4Path": "CROS_IM_GTK4_PATH",
}
Path(sys.argv[1]).write_text(json.dumps({key: os.environ[value] for key, value in keys.items()}) + "\n")
PY_HOST

if ! ACTIVATION_PACKAGE="$(nix --accept-flake-config build \
    --no-update-lock-file --no-link --print-out-paths --impure \
    "path:$BUILD_DIR#homeConfigurations.\"$USER\".activationPackage")"; then
  echo "ERROR: Home Manager build failed; existing configuration and profile were preserved." >&2
  exit 1
fi
if [ ! -x "$ACTIVATION_PACKAGE/activate" ]; then
  echo "ERROR: Nix did not produce an executable Home Manager activation package." >&2
  exit 1
fi
if [ "$BUILD_ONLY" -eq 1 ]; then
  echo "Build passed: $ACTIVATION_PACKAGE"
  echo "No Home Manager configuration or profile was replaced."
  exit 0
fi

# Preserve the previous generated files, including the old lock. Keep this
# backup after success so a failed later application remains diagnosable.
mkdir -p "$CONF_DIR"
CONFIG_BACKUP_DIR="$(mktemp -d "$CONF_DIR.previous.XXXXXX")"
for config_file in "${CONFIG_FILES[@]}"; do
  if [ -e "$CONF_DIR/$config_file" ] || [ -L "$CONF_DIR/$config_file" ]; then
    cp -a -- "$CONF_DIR/$config_file" "$CONFIG_BACKUP_DIR/"
  fi
done
CONFIG_INSTALLED=1
for config_file in "${CONFIG_FILES[@]}"; do
  cp -- "$BUILD_DIR/$config_file" "$CONF_DIR/.$config_file.new"
  mv -f -- "$CONF_DIR/.$config_file.new" "$CONF_DIR/$config_file"
done

# Stage an unmanaged wrapper before Home Manager claims the same path. This
# makes a failed activation recoverable and lets a successful verification
# retire the old copy explicitly.
mkdir -p "$HOME/.local/bin"

if [ -d "$CODEX_WRAPPER_PATH" ] && [ ! -L "$CODEX_WRAPPER_PATH" ]; then
  echo "ERROR: $CODEX_WRAPPER_PATH is a directory; refusing to replace it." >&2
  echo "Fix: move that directory aside, then rerun cros-setup." >&2
  exit 1
fi

if [ -e "$CODEX_WRAPPER_PATH" ] || [ -L "$CODEX_WRAPPER_PATH" ]; then
  codex_wrapper_target="$(readlink -f "$CODEX_WRAPPER_PATH" 2>/dev/null || true)"
  if [[ "$codex_wrapper_target" != /nix/store/* ]]; then
    CODEX_WRAPPER_STAGING="$(mktemp "$HOME/.local/bin/.codex-wrapper.previous.XXXXXX")"
    rm -f -- "$CODEX_WRAPPER_STAGING"
    mv -- "$CODEX_WRAPPER_PATH" "$CODEX_WRAPPER_STAGING"
    echo "Staged the previous unmanaged Codex wrapper at $CODEX_WRAPPER_STAGING."
  fi
fi

echo "Activating the verified Home Manager build..."
if ! HOME_MANAGER_BACKUP_EXT=backup "$ACTIVATION_PACKAGE/activate"; then
  echo "ERROR: Home Manager activation failed; restoring generated configuration and unmanaged wrapper." >&2
  echo "Activation may have applied partial changes; inspect its output before retrying." >&2
  exit 1
fi
CONFIG_INSTALLED=0

# The shell running the first install has not loaded Home Manager's session
# environment yet. The wrapper also needs the real Codex package on PATH.
export PATH="$HOME/.local/bin:$HOME/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH"

for node_command in node npm npx; do
  if ! PATH="$HOME/.nix-profile/bin:/usr/bin:/bin" \
      "$HOME/.nix-profile/bin/$node_command" --version >/dev/null; then
    echo "ERROR: Home Manager did not install a working $node_command executable." >&2
    echo "Why: Orca's skill updater needs Node.js and npx outside project development shells." >&2
    echo "Fix: inspect the Home Manager activation output and rerun cros-setup." >&2
    exit 1
  fi
done

if ! "$HOME/.nix-profile/bin/earlyoom" -v >/dev/null 2>&1; then
  echo "ERROR: Home Manager did not install a working earlyoom executable." >&2
  echo "Why: the memory emergency shield must be available before the setup is complete." >&2
  echo "Fix: inspect the Home Manager activation output and rerun cros-setup." >&2
  exit 1
fi
if [ ! -x "$HOME/.nix-profile/bin/process-compose" ] ||
   ! "$HOME/.nix-profile/bin/process-compose" version >/dev/null 2>&1; then
  echo "ERROR: Home Manager did not install a working process-compose executable." >&2
  echo "Why: the user local-services supervisor cannot start without it." >&2
  echo "Fix: inspect the Home Manager activation output and rerun cros-setup." >&2
  exit 1
fi
if [ ! -x "$HOME/.nix-profile/bin/cloudflared" ] ||
   ! "$HOME/.nix-profile/bin/cloudflared" version >/dev/null 2>&1; then
  echo "ERROR: Home Manager did not install a working cloudflared executable." >&2
  echo "Why: local Cloudflare tunnels cannot start without it." >&2
  echo "Fix: inspect the Home Manager activation output and rerun cros-setup." >&2
  exit 1
fi
if ! systemctl --user daemon-reload ||
   ! systemctl --user enable --now earlyoom.service ||
   ! systemctl --user is-active --quiet earlyoom.service; then
  echo "ERROR: the Home Manager earlyoom emergency shield is not active." >&2
  echo "Why: the setup must leave a running memory fallback for agent-launched builds." >&2
  echo "Fix: inspect 'systemctl --user status earlyoom.service' and rerun cros-setup." >&2
  exit 1
fi
if [ -e "$LOCAL_SERVICES_CONFIG" ]; then
  if ! systemctl --user enable local-services.service; then
    echo "ERROR: could not enable local-services.service." >&2
    exit 1
  fi
  if ! "$HOME/.nix-profile/bin/process-compose" \
      -f "$LOCAL_SERVICES_CONFIG" \
      up --dry-run --tui=false --disable-dotenv >/dev/null; then
    echo "ERROR: local service catalog validation failed: $LOCAL_SERVICES_CONFIG" >&2
    echo "Fix: repair the YAML, then rerun cros-setup." >&2
    exit 1
  fi
fi

CODEX_WRAPPER_TARGET="$(readlink -f "$CODEX_WRAPPER_PATH" 2>/dev/null || true)"
if [ ! -x "$CODEX_WRAPPER_PATH" ] || [[ "$CODEX_WRAPPER_TARGET" != /nix/store/* ]]; then
  echo "ERROR: Home Manager did not install a managed executable at $CODEX_WRAPPER_PATH." >&2
  echo "Why: the wrapper must be a Nix-store-backed Home Manager file before the old copy is removed." >&2
  echo "Fix: rerun cros-setup after inspecting the Home Manager activation output." >&2
  exit 1
fi
if ! "$CODEX_WRAPPER_PATH" --version >/dev/null ||
   ! "$CODEX_WRAPPER_PATH" exec --help >/dev/null; then
  echo "ERROR: the Home Manager Codex wrapper failed its passthrough smoke tests." >&2
  echo "Why: removing the previous wrapper would risk breaking Codex arguments or startup." >&2
  echo "Fix: inspect the wrapper's real Codex path and rerun cros-setup." >&2
  exit 1
fi

if [ -n "$CODEX_WRAPPER_STAGING" ] && [ -e "$CODEX_WRAPPER_STAGING" ]; then
  rm -f -- "$CODEX_WRAPPER_STAGING"
  echo "Verified the Home Manager Codex wrapper; removed the previous unmanaged copy."
fi
CODEX_WRAPPER_STAGING=""

OPENSPEC_BIN="$HOME/.nix-profile/bin/openspec"
if [ ! -x "$OPENSPEC_BIN" ] || ! "$OPENSPEC_BIN" --version >/dev/null; then
  echo "ERROR: Home Manager did not install a working OpenSpec CLI at $OPENSPEC_BIN." >&2
  echo "Why: the global OpenSpec command is required before its generated skills can be used." >&2
  echo "Fix: inspect the Home Manager activation output and rerun cros-setup." >&2
  exit 1
fi

OPENSPEC_SKILL_COUNT=0
for openspec_skill_file in "$HOME/.agents/skills"/openspec-*/SKILL.md; do
  if [ -e "$openspec_skill_file" ]; then
    OPENSPEC_SKILL_COUNT=$((OPENSPEC_SKILL_COUNT + 1))
  fi
done
if [ "$OPENSPEC_SKILL_COUNT" -eq 0 ]; then
  echo "ERROR: Home Manager did not install any global OpenSpec skills." >&2
  echo "Why: Codex discovers user-scoped skills under $HOME/.agents/skills." >&2
  echo "Fix: inspect the Home Manager activation output and rerun cros-setup." >&2
  exit 1
fi

# Verify installation without changing the state of running desktop services.
if [ ! -x "$HOME/.nix-profile/bin/sommelier-rs" ]; then
  echo "ERROR: sommelier-rs was not installed in the Home Manager profile." >&2
  exit 1
fi
if [ ! -e "$HOME/.config/systemd/user/sommelier-rs.service" ]; then
  echo "ERROR: sommelier-rs.service was not installed." >&2
  exit 1
fi
if grep -q -- '--local-compositor' \
    "$HOME/.config/systemd/user/sommelier-rs.service" ||
   ! grep -q -- '--gpu-accel wayland-2' \
    "$HOME/.config/systemd/user/sommelier-rs.service"; then
  echo "ERROR: sommelier-rs.service is not configured for direct VirtWL access." >&2
  echo "Why: nesting through wayland-0 hides ChromeOS keyboard and text-input extensions." >&2
  echo "Fix: rerun Home Manager activation with this setup script." >&2
  exit 1
fi
if ! grep -q 'GTK_IM_MODULE=wayland:cros:test-cros' \
    "$HOME/.config/systemd/user/cros-garcon.service.d/override.conf"; then
  echo "ERROR: cros-garcon IME environment override was not installed." >&2
  echo "Why: Wayland apps need text-input-v3 while X11 apps need the cros-im fallback." >&2
  echo "Fix: rerun Home Manager activation with this setup script." >&2
  exit 1
fi

# Fix corrupted root shell (common Nix/Crostini issue)
ROOT_SHELL="$(getent passwd root | cut -d: -f7)"
if [ "$ROOT_SHELL" != "/bin/bash" ] && [ "$ROOT_SHELL" != "/bin/sh" ] && [ "$ROOT_SHELL" != "/usr/sbin/nologin" ]; then
  sudo usermod -s /bin/bash root
fi

# Ensure target shell is in /etc/shells (required by chsh PAM)
ZSH_PATH="$HOME/.nix-profile/bin/zsh"
if [ ! -x "$ZSH_PATH" ]; then
  echo "ERROR: Home Manager did not install an executable zsh at $ZSH_PATH." >&2
  exit 1
fi
if ! grep -qxF "$ZSH_PATH" /etc/shells 2>/dev/null; then
  echo "$ZSH_PATH" | sudo tee -a /etc/shells > /dev/null
fi
sudo chsh -s "$ZSH_PATH" "$USER"

# Crostini's OpenSSH build may reject the system GSSAPIAuthentication directive
# and warn on every SSH or Git invocation. Remove only active occurrences.
if grep -qiE '^[[:space:]]*GSSAPIAuthentication[[:space:]]' /etc/ssh/ssh_config; then
  echo "Cleaning up unsupported GSSAPI option from system SSH config..."
  sudo sed -i '/^[[:space:]]*gssapiauthentication[[:space:]]/Id' /etc/ssh/ssh_config
fi

echo "============================================================"
echo "SUCCESS: Home Manager setup is fully activated!"
echo "Your original configs were safely backed up as *.backup"
echo "Generated configuration: $CONF_DIR"
echo "Previous generated configuration: $CONFIG_BACKUP_DIR"
echo "Edit the source files in $SCRIPT_DIR, then rerun cros-setup."
echo "earlyoom is active as the user-session memory emergency shield."
if [ "${TMP_MOUNT_PENDING:-0}" -eq 1 ]; then
  echo "Disk-backed /tmp is configured but not mounted yet; reboot to switch from the current tmpfs mount."
else
  echo "Disk-backed /tmp is active with a quota of $TMP_QUOTA_GIB."
fi
if [ -e "$LOCAL_SERVICES_CONFIG" ]; then
  echo "Local services catalog: $LOCAL_SERVICES_CONFIG"
  echo "Start now without changing the catalog: systemctl --user start local-services.service"
  echo "Inspect logs: process-compose process logs <service-name>"
fi
echo "wayland-2 is configured as the default without restarting running services."
echo "Run cros-reset to start inactive launcher services without interrupting apps."
echo "Run cros-hard-reset only when a full GUI-disrupting reset is required."
echo "============================================================"
