#!/usr/bin/env bash
# Bootstrap for chromeos-dev-setup: prepare what `cros` needs, then run
# `cros apply` from this checkout. Safe to rerun. See DESIGN.md.
set -euo pipefail

SETUP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
NIX_CUSTOM_CONF=/etc/nix/nix.custom.conf

usage() {
  echo "Usage: bash $SETUP_DIR/setup.bash [--maintainer]" >&2
  exit 2
}

log() { printf 'setup: %s\n' "$*" >&2; }
die() {
  log "error: $*"
  exit 1
}

apply_args=()
for argument in "$@"; do
  case "$argument" in
    --maintainer) apply_args+=(--maintainer) ;;
    *) usage ;;
  esac
done

# Fail before changing anything when this is not a supported Crostini user.
[[ "$(id -u)" -ne 0 ]] || die "run as your normal user, not root"
[[ "$(uname -m)" == x86_64 ]] || die "this setup supports x86_64 Crostini only"
[[ -c /dev/wl0 ]] || die "Crostini's VirtWL device /dev/wl0 is missing; run inside Crostini"
REPO_ROOT="$(git -C "$SETUP_DIR" rev-parse --show-toplevel 2>/dev/null)" ||
  die "$SETUP_DIR is not inside a Git checkout"

# Minimal tools to install Nix; the system package declares the rest.
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates xz-utils

if ! command -v nix >/dev/null 2>&1 && [[ -r /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh ]]; then
  # shellcheck source=/dev/null
  . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
fi
if ! command -v nix >/dev/null 2>&1; then
  log "installing Determinate Nix"
  curl --proto '=https' --tlsv1.2 -sSfL https://install.determinate.systems/nix |
    sh -s -- install --no-confirm --extra-conf "trusted-users = root $USER"
  # shellcheck source=/dev/null
  . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
fi

# Binary caches declared by the flake need the user to be trusted. A Nix that
# was installed before this setup lacks the entry the installer adds above.
nix_trusts_user() { nix config show trusted-users 2>/dev/null | tr ' ' '\n' | grep -qxF -e "$USER"; }
if ! nix_trusts_user; then
  log "adding $USER to Nix trusted-users"
  echo "extra-trusted-users = $USER" | sudo tee -a "$NIX_CUSTOM_CONF" >/dev/null
  sudo systemctl restart nix-daemon.service
  nix_trusts_user ||
    die "Nix still does not trust $USER; this Nix does not read $NIX_CUSTOM_CONF (expected Determinate Nix)"
fi

# GPU acceleration reads /dev/dri render nodes; takes effect at next login.
if ! id -nG "$USER" | tr ' ' '\n' | grep -qxF render; then
  sudo usermod -aG render "$USER"
  log "added $USER to the render group (effective after the next login)"
fi

# Git identity is user-owned data in ~/.gitconfig, written once if provided.
for key in name email; do
  variable="GIT_USER_${key^^}"
  if [[ -n "${!variable:-}" ]] && ! git config --file "$HOME/.gitconfig" --get "user.$key" >/dev/null 2>&1; then
    git config --file "$HOME/.gitconfig" "user.$key" "${!variable}"
  fi
done

# `cros apply` owns everything else, including sparse checkout paths.
exec nix run --accept-flake-config "$SETUP_DIR#cros" -- apply --checkout "$REPO_ROOT" "${apply_args[@]}"
