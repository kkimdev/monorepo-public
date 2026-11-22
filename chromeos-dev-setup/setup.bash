#!/usr/bin/env bash
# Bootstrap for chromeos-dev-setup: prepare what `cros` needs, run `cros apply`
# from this checkout, then bring Debian up to date. Safe to rerun. See DESIGN.md.
#
# Run it from a checkout, or pipe it for a first install; it then clones the
# repository and reruns itself from there:
#   curl -fsSL https://raw.githubusercontent.com/kkimdev/monorepo-public/main/chromeos-dev-setup/setup.bash | bash
set -euo pipefail

# Empty when the script is piped into bash.
SCRIPT="${BASH_SOURCE[0]:-}"
REPOSITORY="${CROS_SETUP_REPOSITORY:-https://github.com/kkimdev/monorepo-public.git}"
CHECKOUT="${CROS_SETUP_CHECKOUT:-$HOME/workspaces/monorepo-public}"
NIX_CUSTOM_CONF=/etc/nix/nix.custom.conf

usage() {
  echo "Usage: bash <checkout>/chromeos-dev-setup/setup.bash [--maintainer]" >&2
  exit 2
}

log() { printf 'setup: %s\n' "$*" >&2; }
die() {
  log "error: $*"
  exit 1
}

# Piped first install: clone (shallow and sparse) and rerun from the checkout.
clone_and_rerun() {
  if ! command -v git >/dev/null 2>&1; then
    sudo apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y git ca-certificates
  fi
  if [[ ! -e "$CHECKOUT" ]]; then
    mkdir -p "$(dirname -- "$CHECKOUT")"
    git clone --filter=blob:none --sparse --depth=1 --branch main "$REPOSITORY" "$CHECKOUT"
  fi
  if [[ "$(git -C "$CHECKOUT" config --bool core.sparseCheckout 2>/dev/null || true)" == true ]]; then
    git -C "$CHECKOUT" sparse-checkout add chromeos-dev-setup
  fi
  git -C "$CHECKOUT" ls-files --error-unmatch chromeos-dev-setup/setup.bash >/dev/null 2>&1 ||
    die "$CHECKOUT exists but is not a monorepo-public checkout"
  log "continuing from $CHECKOUT"
  exec bash "$CHECKOUT/chromeos-dev-setup/setup.bash" "$@"
}

nix_trusts_user() { nix config show trusted-users 2>/dev/null | tr ' ' '\n' | grep -qxF -e "$USER"; }

main() {
  local argument setup_dir repo_root apply_args=()
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

  [[ -f "$SCRIPT" ]] || clone_and_rerun "$@"
  setup_dir="$(cd "$(dirname "$(readlink -f "$SCRIPT")")" && pwd)"
  repo_root="$(git -C "$setup_dir" rev-parse --show-toplevel 2>/dev/null)" ||
    die "$setup_dir is not inside a Git checkout"

  # Tools to install Nix, and cros-im, whose GTK paths `cros apply` records
  # before it installs the system package (which also depends on it).
  sudo apt-get update
  sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates xz-utils cros-im

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

  # `cros apply` owns everything else, including sparse checkout paths.
  nix run --accept-flake-config "$setup_dir#cros" -- apply --checkout "$repo_root" "${apply_args[@]}"

  # Upgrade Debian now that the system package has added the security and
  # updates suites; afterwards `cros sync` does this.
  sudo apt-get update
  sudo env DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y
  sudo env DEBIAN_FRONTEND=noninteractive apt-get autoremove -y
  log "done; open a new terminal"
}

# Last line: bash has read the whole script before anything can read stdin.
main "$@"
