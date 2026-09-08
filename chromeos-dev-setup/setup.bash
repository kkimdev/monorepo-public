#!/usr/bin/env bash
set -euxo pipefail

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

# TODO: Fill out and execute these before running the script.
# export GIT_USER_NAME=""
# export GIT_USER_EMAIL="" # Use private email from https://github.com/settings/emails

sudo apt-get update -y
sudo apt-get install uidmap cros-im -y
sudo apt-get remove vim vim-tiny command-not-found -y
sudo apt autoremove

# Add user to render group (required for GPU acceleration access in Crostini)
sudo usermod -aG render "$USER"

# cros-im is installed by ChromeOS on supported containers, but install it
# explicitly above so a fresh or older Crostini image gets the GTK/Qt bridge
# that both X11 (through Xwayland) and Wayland applications use.
CROS_IM_GTK3_MODULE="$(dpkg -L cros-im | awk '/\/gtk-3\.0\/.*\/immodules\/im-cros-gtk3\.so$/ { print; exit }')"
CROS_IM_GTK4_MODULE="$(dpkg -L cros-im | awk '/\/gtk-4\.0\/.*\/immodules\/libim-cros-gtk4\.so$/ { print; exit }')"
CROS_IM_QT5_MODULE="$(dpkg -L cros-im | awk '/\/qt5\/plugins\/platforminputcontexts\/libcrosplatforminputcontextplugin\.so$/ { print; exit }')"
CROS_IM_GTK3_CACHE="$(find /usr/lib -type f -path '*/gtk-3.0/*/immodules.cache' -print -quit 2>/dev/null || true)"
CROS_IM_GTK4_PATH="$(find /usr/lib -type d -path '*/gtk-4.0' -print -quit 2>/dev/null || true)"

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

echo "Detecting current Nixpkgs release..."
NIX_VER=$(nix eval --raw nixpkgs#lib.version | cut -d. -f1,2)

CONF_DIR="$HOME/.config/home-manager"
mkdir -p "$CONF_DIR"

# Generate the wrapper from this setup script so a checkout needs no companion
# file. Home Manager copies this source into the Nix store and owns the
# installed ~/.local/bin/codex path.
CODEX_WRAPPER_SOURCE="$CONF_DIR/codex-wrapper"
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

rm -f "$CONF_DIR/flake.nix" "$CONF_DIR/flake.lock"
cat <<'EOF' > "$CONF_DIR/flake.nix"
{
  # Numtide's cache contains the pre-built llm-agents.nix packages.
  nixConfig = {
    extra-substituters = [ "https://cache.numtide.com" ];
    extra-trusted-public-keys = [
      "niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g="
    ];
  };

  inputs = {
    nixpkgs.url = "https://flakehub.com/f/DeterminateSystems/nixpkgs-weekly/0.1";
    openspec = {
      url = "github:Fission-AI/OpenSpec";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    nix-index-database.url = "github:nix-community/nix-index-database";
    nix-index-database.inputs.nixpkgs.follows = "nixpkgs";

    # LLM agent packages are sourced from the upstream Numtide collection.
    # Keep its pinned nixpkgs input so the published binary cache remains usable.
    llm-agents.url = "github:numtide/llm-agents.nix";

    sommelier-rs = {
      url = "github:kkimdev/sommelier-rs/virtwl";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    kakaotalk = {
      url = "github:kkimdev/monorepo-public/main?dir=nixpkgs/kakaotalk";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    codex-desktop-linux = {
      url = "github:ilysenko/codex-desktop-linux";
      inputs.nixpkgs.follows = "nixpkgs";
    };


    # Legacy package sources retained as comments for easy rollback:
    # claude-desktop = {
    #   url = "github:aaddrick/claude-desktop-debian";
    #   inputs.nixpkgs.follows = "nixpkgs";
    # };
    #
    # Keep the existing Antigravity GUI packages: llm-agents.nix currently
    # provides only the CLI, not the IDE/base application.
    antigravity-nix = {
      url = "github:jacopone/antigravity-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    #
    # orca-deb-bin = {
    #   url = "github:kkimdev/monorepo-public/main?dir=nixpkgs/orca-deb-bin";
    #   inputs.nixpkgs.follows = "nixpkgs";
    # };
  };

  # Legacy outputs argument list retained for rollback:
  # outputs = { nixpkgs, home-manager, nix-index-database, sommelier-rs, kakaotalk, codex-desktop-linux, claude-desktop, antigravity-nix, orca-deb-bin, ... }@inputs:
  outputs = { nixpkgs, home-manager, nix-index-database, llm-agents, sommelier-rs, kakaotalk, codex-desktop-linux, antigravity-nix, ... }@inputs:
    let
      system = builtins.currentSystem;

      # Dynamically grab the current user directly from the environment inside Nix
      username = builtins.getEnv "USER";

    in {
      homeConfigurations."${username}" = home-manager.lib.homeManagerConfiguration {
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
          overlays = [
            sommelier-rs.overlays.default
            kakaotalk.overlays.default

            # Legacy LLM-agent overlays retained as comments for rollback.
            # claude-desktop.overlays.default
            # orca-deb-bin.overlays.default
            antigravity-nix.overlays.default
            #
            # Legacy Codex pin (the package now comes from llm-agents.nix):
            # (final: prev: {
            #   # Pin codex CLI to v0.142.5 — the Desktop injects codex_app dynamic tools
            #   # at thread/start, and CLIs before 0.142.0 reject these tools for missing
            #   # inputSchema (rust serde required field check). Upstream fixed this in
            #   # the 0.142.x series (https://github.com/openai/codex/issues/28978).
            #   codex = prev.stdenv.mkDerivation {
            #     pname = "codex";
            #     version = "0.142.5";
            #     src = prev.fetchurl {
            #       url = "https://github.com/openai/codex/releases/download/rust-v0.142.5/codex-x86_64-unknown-linux-musl.tar.gz";
            #       hash = "sha256-y5M+w8thv0tfyI7s9eYUmCn6phclNbbvCvsBVL60qrg=";
            #     };
            #     sourceRoot = ".";
            #     installPhase = ''
            #       mkdir -p $out/bin
            #       mv codex-x86_64-unknown-linux-musl $out/bin/codex
            #     '';
            #   };
            # })
            #
            # Keep the existing OpenCode desktop package and its metadata fix:
            # llm-agents.nix currently provides the CLI (`opencode`) only.
            (final: prev: {
              opencode-desktop = prev.opencode-desktop.overrideAttrs (oldAttrs: {
                desktopItems = final.lib.optional final.stdenvNoCC.hostPlatform.isLinux (
                  final.makeDesktopItem {
                    name = "ai.opencode.desktop";
                    desktopName = "OpenCode";
                    exec = "opencode-desktop %U";
                    icon = "opencode-desktop";
                    startupWMClass = "ai.opencode.desktop";
                    categories = [ "Development" ];
                    mimeTypes = [ "x-scheme-handler/opencode" ];
                  }
                );
              });
            })
          ];
        };

        extraSpecialArgs = { inherit inputs; };

        modules = [
          ./home.nix
          nix-index-database.homeModules.default
          codex-desktop-linux.homeManagerModules.default
          { programs.nix-index-database.comma.enable = true; }
        ];
      };
    };
}
EOF

# 7. GENERATE HOME.NIX
rm -f "$CONF_DIR/home.nix"
cat <<EOF > "$CONF_DIR/home.nix"
{ config, pkgs, lib, inputs, ... }:

let
  crosDesktopShareDir = "\${config.home.homeDirectory}/.local/share";
  nixProfileShareDir  = "\${config.home.homeDirectory}/.nix-profile/share";
  llmAgentsPkgs       = inputs.llm-agents.packages.\${pkgs.stdenv.hostPlatform.system};
  openspecCli         = inputs.openspec.packages.\${pkgs.stdenv.hostPlatform.system}.default;
  # This file is evaluated with --impure on the host. Do not install a
  # Crostini-specific browser default on ordinary Linux systems.
  isCrostini = builtins.pathExists "/opt/google/cros-containers/bin/garcon"
    && builtins.pathExists "/usr/share/applications/garcon_host_browser.desktop";
  # GSettings schema dirs for codex-desktop file dialog (GLib fatal crash fix)
  # See also \`GSETTINGS_SCHEMA_DIR\` in the cros-garcon override below.
  gtk3SchemaDir       = "\${pkgs.gtk3}/share/gsettings-schemas/gtk+3-\${lib.getVersion pkgs.gtk3}/glib-2.0/schemas";
  gsettingsSchemaDir  = "\${pkgs.gsettings-desktop-schemas}/share/gsettings-schemas/gsettings-desktop-schemas-\${lib.getVersion pkgs.gsettings-desktop-schemas}/glib-2.0/schemas";

  myShellAliases = {
    sudo = "sudo env PATH=\\\\\"\$PATH\\\\\"";
    grep = "grep --color=auto";
    fgrep = "fgrep --color=auto";
    egrep = "egrep --color=auto";
    ls = "ls --color=auto";
    dir = "dir --color=auto";
    vdir = "vdir --color=auto";

    cros-update = ''
      sudo apt-get update && sudo apt-get full-upgrade -y && \\
      sudo apt-get autoremove -y && \\
      sudo determinate-nixd upgrade && \\
      pushd "$CONF_DIR" && \\
      nix flake update && \\
      home-manager switch --flake ".#$USER" --impure && \\
      popd
    '';

    cros-clean = ''
      nix-collect-garbage -d && \\
      nix store optimise && \\
      nix store gc
    '';

    cros-reset = ''
      echo "Ensuring deferred user services are running..." && \
      systemctl --user daemon-reload && \
      systemctl --user reset-failed atuin-daemon.service sommelier@0.service sommelier@1.service sommelier-x@0.service sommelier-x@1.service sommelier-rs.service cros-garcon.service && \
      systemctl --user start atuin-daemon.service sommelier@0.service sommelier@1.service sommelier-x@0.service sommelier-x@1.service sommelier-rs.service cros-garcon.service
    '';

    cros-hard-reset = ''
      echo "Hard-resetting Crostini user services; running GUI apps will close..." && \
      systemctl --user stop cros-garcon.service sommelier-rs.service sommelier-x@1.service sommelier-x@0.service sommelier@1.service sommelier@0.service atuin-daemon.service && \
      rm -f /run/user/\$UID/wayland-{1,2}.lock /run/user/\$UID/wayland-{1,2} 2>/dev/null; \
      systemctl --user daemon-reload && \
      systemctl --user reset-failed atuin-daemon.service sommelier@0.service sommelier@1.service sommelier-x@0.service sommelier-x@1.service sommelier-rs.service cros-garcon.service && \
      systemctl --user start atuin-daemon.service sommelier@0.service sommelier@1.service sommelier-x@0.service sommelier-x@1.service sommelier-rs.service cros-garcon.service
    '';

    # Reapply configuration without restarting services, then update this shell
    # so subsequently launched applications use the custom proxy.
    cros-setup = "bash \"\$CROS_SETUP_SCRIPT_FILE\" && export WAYLAND_DISPLAY=wayland-2";
  };
in
{
  nixpkgs.config.allowUnfree = true;

  # Keep cros-setup interruption-free. cros-reset starts only inactive services;
  # updated running units take effect on the next login or after cros-hard-reset.
  systemd.user.startServices = "suggest";

  home = {
    username = "$USER";
    homeDirectory = "$HOME";
    stateVersion = "$NIX_VER";
    sessionPath = [
      "\${config.home.homeDirectory}/.local/bin"
    ];
    packages = with pkgs; [
      # Utils
      bash
      zsh
      zsh-fzf-tab
      git
      difftastic
      delta
      micro
      direnv
      # zplug uses Perl to strip ANSI escape sequences in its global logs.
      # Keep Perl in the Home Manager profile so project Nix shells can
      # filter host PATH entries such as /usr/bin without breaking zplug.
      perl
      bat
      btop
      fzf
      ripgrep
      fd
      eza
      yazi
      zoxide
      zellij
      starship
      wl-clipboard
      killall
      podman-compose
      podman-tui
      xdg-utils
      gh

      ## Already included via other lines.
      # nix-direnv
      # podman

      # Apps
      chromium
      inkscape
      beekeeper-studio
      yt-dlp
      sommelier-rs-bin
      kakaotalk-bin

      # Coding
      vscode
      inputs.openspec.packages.\${pkgs.stdenv.hostPlatform.system}.default
      # Existing GUI packages kept active; llm-agents.nix has no IDE/base-app
      # replacement for these yet.
      google-antigravity-no-fhs
      google-antigravity-ide

      # Packages replaced by llm-agents.nix are retained as comments for rollback:
      # orca-ide
      # google-antigravity-cli
      # claude-code
      # claude-desktop
      # codex
      # opencode

      # Keep all available replacements on the same upstream package set.
      llmAgentsPkgs.antigravity-cli
      llmAgentsPkgs.claude-code
      llmAgentsPkgs.claude-desktop
      llmAgentsPkgs.codex
      llmAgentsPkgs.opencode
      llmAgentsPkgs.orca

      # Existing desktop package kept active; llm-agents.nix currently has no
      # OpenCode desktop replacement.
      opencode-desktop

      # Fonts
      nerd-fonts.jetbrains-mono
      noto-fonts
      google-fonts
    ];
    sessionVariables = {
      NIXPKGS_ALLOW_UNFREE = "1";
      # Legacy orca-deb-bin exposed the CLI as orca-ide; the llm-agents.nix
      # package follows the upstream orca CLI name.
      # ORCA_CLI_COMMAND = "orca-ide";
      ORCA_CLI_COMMAND = "orca";
      EDITOR = "code --wait --new-window";
      VISUAL = "code --wait --new-window";
      GIT_USER_NAME = "$GIT_USER_NAME";
      GIT_USER_EMAIL = "$GIT_USER_EMAIL";
      CROS_SETUP_SCRIPT_FILE = "$CROS_SETUP_SCRIPT_FILE";
      GSETTINGS_SCHEMA_DIR = "\${gtk3SchemaDir}:\${gsettingsSchemaDir}";
      # Native Wayland clients use GTK's text-input-v3 module through
      # sommelier-rs. X11/Xwayland cannot use that module and falls back to the
      # ChromeOS GTK bridge; some GTK4 builds register it as "test-cros".
      GTK_IM_MODULE = "wayland:cros:test-cros";
      GTK_IM_MODULE_FILE = "$CROS_IM_GTK3_CACHE";
      GTK_PATH = "$CROS_IM_GTK4_PATH";
      QT_IM_MODULE = "cros";
      # wayland-0: Crostini default high-density Sommelier, wayland-1: low-density Sommelier, wayland-2: custom sommelier-rs
      WAYLAND_DISPLAY = "wayland-2";
      DISPLAY = ":1";
      CODEX_LINUX_RENDERING_MODE = "wayland-gpu";
      CODEX_LINUX_DISABLE_EXTERNAL_OPEN_PATCH = "1";
      # Web search provider selection. Required for non-opencode providers (model-gateway).
      # See ../scratch-monorepo/model_gateway/WEB_SEARCH.md.
      # OPENCODE_ENABLE_EXA = "true";
      OPENCODE_ENABLE_PARALLEL = "true";
    };
  };

  systemd.user.services.sommelier-rs = {
    Unit = {
      Description = "Sommelier-RS Wayland Compositor";
      After = [ "debian-fixup.service" ];
    };
    Service = {
      # Without --local-compositor, sommelier-rs connects directly to /dev/wl0.
      # The nested wayland-0 path hides ChromeOS keyboard/text-input extensions,
      # preventing host IME switching and GTK text-input-v3 activation.
      ExecStart =
        "\${pkgs.sommelier-rs-bin}/bin/sommelier-rs --gpu-accel wayland-2";
      Restart = "always";
      RestartSec = "5";
      Environment = [
        "RUST_LOG=info"
        "SOMMELIER_ACCELERATORS=Super_L,<Alt>bracketleft,<Alt>bracketright,<Alt>minus,<Alt>equal,<Alt>1,<Alt>2,<Alt>3,<Alt>4,<Alt>5,<Alt>6,<Alt>7,<Alt>8,<Alt>9,print,<Control>space,<Control><Shift>space"
      ];
    };
    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  systemd.user.services.podman-api = {
    Unit = {
      Description = "Podman API Service";
    };

    Service = {
    ExecStartPre = "\${pkgs.coreutils}/bin/mkdir -p %t/podman";
      ExecStart =
        "\${pkgs.podman}/bin/podman system service --time=0 unix://%t/podman/podman.sock";
      Restart = "always";
      RestartSec = "5";
    };

    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  services.ssh-agent.enable = true;

  # ChromeOS / Crostini Integration Fixes
  # This tells Home Manager that it is running on a generic Linux distro (not NixOS)
  # and forces it to export desktop files and icons to standard XDG directories.
  targets.genericLinux.enable = true;
  xdg.enable = true;

  xdg.mimeApps = {
    enable = true;
    # Crostini's host-browser desktop entry is installed below only when the
    # same host integration files are present.
    defaultApplications = lib.optionalAttrs isCrostini {
      "text/html" = [ "garcon_host_browser.desktop" ];
      "x-scheme-handler/http" = [ "garcon_host_browser.desktop" ];
      "x-scheme-handler/https" = [ "garcon_host_browser.desktop" ];
      "x-scheme-handler/about" = [ "garcon_host_browser.desktop" ];
      "x-scheme-handler/unknown" = [ "garcon_host_browser.desktop" ];
    };
  };

  programs = {
    home-manager.enable = true;

    # codex-desktop known issues (for reference):
    #
    # 1. GLib-GIO-ERROR: Settings schema 'org.gtk.Settings.FileChooser' not found
    #    → Fix: GSETTINGS_SCHEMA_DIR is set in cros-garcon.service override
    #      (see xdg.configFile section below).
    #
    # 2. Re-launch hangs when X-closed (Electron single-instance lock)
    #    → Ctrl+Q works to actually quit. No config-side fix yet.
    #
    # The current codex-desktop-linux module discovers the CLI from PATH;
    # keep the Nix-managed codex package in home.packages above.
    codexDesktopLinux = {
      enable = true;
      # Keep Computer Use UI disabled because enabling both package variants causes
      # overlapping browser plugin paths in the Home Manager profile.
      # computerUseUi.enable = true;
      remoteMobileControl.enable = true;
      # Keep the declarative remote-control service disabled because Codex Desktop
      # already owns the app-server control socket in this Crostini environment.
      # remoteControl.enable = true;
    };

    bash = {
      enable = true;
      enableCompletion = true;

      initExtra = ''
        # https://yazi-rs.github.io/docs/quick-start/
        function y() {
          local tmp="\$(mktemp -t "yazi-cwd.XXXXXX")" cwd
          command yazi "\$@" --cwd-file="\$tmp"
          IFS= read -r -d ' ' cwd < "\$tmp"
          [ -n "\$cwd" ] && [ "\$cwd" != "\$PWD" ] && [ -d "\$cwd" ] && builtin cd -- "\$cwd"
          command rm -f -- "\$tmp"
        }
        export PATH="\$HOME/.local/bin:\$PATH"
      '';
      bashrcExtra = ''
        . \$HOME/.bashrc.backup
      '';
      profileExtra = ''
        . \$HOME/.profile.backup
        export PATH="\$HOME/.local/bin:\$HOME/.nix-profile/bin:/nix/var/nix/profiles/default/bin:\$PATH"
      '';

      shellAliases = myShellAliases;
    };

    zsh = {
      enable = true;
      enableCompletion = true;
      autosuggestion.enable = true;
      syntaxHighlighting.enable = true;
      history = {
        append = true;
        extended = true;
        expireDuplicatesFirst = true;
        ignoreDups = true;
        ignoreAllDups = true;
        ignoreSpace = true;
        save = 10000;
        size = 10000;
      };

      shellAliases = myShellAliases;

      plugins = [
        {
          name = "fzf-tab";
          src = "\${pkgs.zsh-fzf-tab}/share/fzf-tab";
        }
      ];

      zplug = {
        enable = true;
        plugins = [
          { name = "plugins/dirhistory"; tags = [ "from:oh-my-zsh" ]; }
        ];
      };

      initContent = ''
        # History Beginning Search (Commented out as Atuin is enabled)
        # autoload -U up-line-or-beginning-search
        # autoload -U down-line-or-beginning-search
        # zle -N up-line-or-beginning-search
        # zle -N down-line-or-beginning-search

        # Arrow up and down keys binding
        # bindkey "^[[A" up-line-or-beginning-search
        # bindkey "^[[B" down-line-or-beginning-search
        # bindkey "^[OA" up-line-or-beginning-search
        # bindkey "^[OB" down-line-or-beginning-search

        # Fix standard navigation keys
        bindkey "\e[1~" beginning-of-line       # Home
        bindkey "^[[H"  beginning-of-line       # Home key
        bindkey "\e[4~" end-of-line             # End
        bindkey "^[[F"  end-of-line             # End key
        bindkey "\e[3~" delete-char             # Delete
        bindkey "^[[3~" delete-char

        # Fix word-by-word movements (Bash style)
        bindkey "\e[1;5D" backward-word        # Ctrl + Left
        bindkey "\e[1;5C" forward-word         # Ctrl + Right
        bindkey "^[^?"    backward-kill-word   # Ctrl + Backspace
        bindkey "^H"      backward-kill-word   # Ctrl + Backspace

        export PATH="\$HOME/.local/bin:\$PATH"
      '';

      # TODO: More options
    };

    git = {
      enable = true;
      settings = {
        core.editor = "code --wait --new-window";
        diff.tool = "vscode";
        difftool.vscode.cmd = "code --wait --new-window --diff \$LOCAL \$REMOTE";
        user = {
          name = "$GIT_USER_NAME";
          email = "$GIT_USER_EMAIL";
        };
      };
      ignores = [
      ];
    };

    ssh = {
      enable = true;
      enableDefaultConfig = false;
      settings = {
        "*" = {
          AddKeysToAgent = "yes";
        };
      };
    };

    starship = {
      enable = true;
      enableBashIntegration = true;
      enableZshIntegration = true;
      presets = [ "catppuccin-powerline" ];
      settings = {};
    };

    direnv = {
      enable = true;
      nix-direnv.enable = true;
      enableBashIntegration = true;
      enableZshIntegration = true;
    };

    difftastic = {
      enable = true;
      git = {
        enable = false;
        mode = "external";
      };
    };

    delta = {
      enable = true;
      enableGitIntegration = true;
      options = {
        "side-by-side" = true;
      };
    };

    fzf = {
      enable = true;
      enableBashIntegration = true;
      enableZshIntegration = true;
      historyWidget.command = "";
    };

    zoxide = {
      enable = true;
      enableBashIntegration = true;
      enableZshIntegration = true;
    };

    # https://github.com/nix-community/home-manager/blob/master/modules/programs/zellij.nix
    zellij = {
      enable = false;
      enableBashIntegration = true;
      attachExistingSession = false;
      exitShellOnExit = false;

      settings = {
        show_startup_tips = false;
        theme = "catppuccin-mocha";
        # default_layout = "compact";
        pane_frames = false;
        ui = {
          pane_frames = {
            # rounded_corners = true;
          };
        };
      };
    };

    atuin = {
      enable = true;
      enableBashIntegration = true;
      enableZshIntegration = true;
      daemon.enable = true;
      settings = {
        enter_accept = true;
        workspaces = true;
        filter_mode = "session-preload";
      };
    };

    nix-index = {
      enableBashIntegration = true;
      enableZshIntegration = true;
    };
  };

  xdg.configFile = {
    # https://nixos.wiki/wiki/Installing_Nix_on_Crostini
    "systemd/user/cros-garcon.service.d/override.conf".text = ''
      [Service]
      Environment="PATH=%h/.nix-profile/bin:/usr/local/sbin:/usr/local/bin:/usr/local/games:/usr/sbin:/usr/bin:/usr/games:/sbin:/bin"
      Environment="XDG_DATA_DIRS=%h/.nix-profile/share:%h/.local/share:%h/.local/share/flatpak/exports/share:/var/lib/flatpak/exports/share:/usr/local/share:/usr/share"
      Environment="WAYLAND_DISPLAY=wayland-2"
      Environment="DISPLAY=:1"
      Environment="GTK_IM_MODULE=wayland:cros:test-cros"
      Environment="GTK_IM_MODULE_FILE=$CROS_IM_GTK3_CACHE"
      Environment="GTK_PATH=$CROS_IM_GTK4_PATH"
      Environment="QT_IM_MODULE=cros"
      Environment="CODEX_LINUX_RENDERING_MODE=wayland-gpu"
      Environment="CODEX_LINUX_DISABLE_EXTERNAL_OPEN_PATCH=1"
      # GLib-GIO-ERROR: Settings schema 'org.gtk.Settings.FileChooser' is not installed
      # codex-desktop crashes (exit 5) when opening a file dialog if these are missing.
      Environment="GSETTINGS_SCHEMA_DIR=\${gtk3SchemaDir}:\${gsettingsSchemaDir}"
    '';

    # Chrome OS shortcuts in Linux apps
    # https://www.reddit.com/r/Crostini/wiki/enable-chrome-shortcuts-in-linux-apps
    # https://issuetracker.google.com/issues/149234835#comment14
    "systemd/user/sommelier@.service.d/cros-sommelier-override.conf".text = ''
      [Service]
      Environment="SOMMELIER_ACCELERATORS=Super_L,<Alt>bracketleft,<Alt>bracketright,<Alt>minus,<Alt>equal,<Alt>1,<Alt>2,<Alt>3,<Alt>4,<Alt>5,<Alt>6,<Alt>7,<Alt>8,<Alt>9,print,<Control>space,<Control><Shift>space"
    '';
    "systemd/user/sommelier-x@.service.d/cros-sommelier-x-override.conf".text = ''
      [Service]
      Environment="SOMMELIER_ACCELERATORS=Super_L,<Alt>bracketleft,<Alt>bracketright,<Alt>minus,<Alt>equal,<Alt>1,<Alt>2,<Alt>3,<Alt>4,<Alt>5,<Alt>6,<Alt>7,<Alt>8,<Alt>9,print,<Control>space,<Control><Shift>space"
    '';

    # Override sommelier-x@1 to run at scale 1.0 (high-density)
    "systemd/user/sommelier-x@1.service.d/override.conf".text = ''
      [Service]
      Environment="SOMMELIER_SCALE=1.0"
    '';

  };

  home.file = {
    # Generate the model-policy wrapper from this setup script while letting
    # Home Manager own its installed path and lifecycle.
    ".local/bin/codex" = {
      source = "$CODEX_WRAPPER_SOURCE";
    };

    # Your existing inputrc configuration
    ".inputrc".text = ''
      # "\e[A": history-search-backward
      # "\e[B": history-search-forward
      set show-all-if-ambiguous on
    '';

    # Share VS Code settings with Antigravity via symlink
    ".config/Antigravity/User/settings.json" = {
      source = config.lib.file.mkOutOfStoreSymlink "$HOME/.config/Code/User/settings.json";
    };
    ".antigravity-ide/User/settings.json" = {
      source = config.lib.file.mkOutOfStoreSymlink "$HOME/.config/Code/User/settings.json";
    };

    # Let the llm-agents.nix Codex CLI satisfy the remote-mobile cold-start
    # hook's managed runtime path without bootstrapping the standalone updater.
    ".codex/packages/standalone/current/codex" = {
      # Legacy source retained for rollback:
      # source = "\${pkgs.codex}/bin/codex";
      source = "\${llmAgentsPkgs.codex}/bin/codex";
    };

    # TODO
    # https://www.reddit.com/r/Nix/comments/zh1803/guide_how_to_have_nix_installed_applications/
  };

  home.activation = {
    # Generate and sync every OpenSpec skill into Codex's user scope. The
    # openspec-* namespace is owned by this activation; other user skills stay
    # untouched while added or removed OpenSpec workflows follow the package.
    installOpenSpecCodexSkills = lib.hm.dag.entryAfter ["installPackages"] ''
      openspecSkillsDir="\$HOME/.agents/skills"
      tempDir="\$(mktemp -d)"
      mkdir -p "\$openspecSkillsDir"

      workDir="\$tempDir/work"
      mkdir -p "\$tempDir/home" "\$workDir"
      (
        cd "\$workDir"
        env \
          CI=1 \
          HOME="\$tempDir/home" \
          "\${openspecCli}/bin/openspec" init --tools codex --profile core --no-animation .
      )

      for existingSkill in "\$openspecSkillsDir"/openspec-*; do
        if [ -e "\$existingSkill" ] || [ -L "\$existingSkill" ]; then
          rm -rf -- "\$existingSkill"
        fi
      done

      for generatedSkill in "\$workDir/.agents/skills"/openspec-*; do
        [ -d "\$generatedSkill" ] || continue
        skillName="\$(basename "\$generatedSkill")"
        cp -a -- "\$generatedSkill" "\$openspecSkillsDir/\$skillName"
      done

      rm -rf -- "\$tempDir"
    '';

    linkDesktopApplications = lib.hm.dag.entryAfter ["writeBoundary"] ''
      rm -rf "\${crosDesktopShareDir}/applications" "\${crosDesktopShareDir}/icons"
      mkdir -p "\${crosDesktopShareDir}/applications" "\${crosDesktopShareDir}/icons"

      cp -rL "\${nixProfileShareDir}/applications/." "\${crosDesktopShareDir}/applications/"
      cp -rL "\${nixProfileShareDir}/icons/."        "\${crosDesktopShareDir}/icons/"
      if [ -r "/usr/share/applications/garcon_host_browser.desktop" ] &&
         [ -x "/opt/google/cros-containers/bin/garcon" ]; then
        cp -L "/usr/share/applications/garcon_host_browser.desktop" \
          "\${crosDesktopShareDir}/applications/garcon_host_browser.desktop"
      else
        rm -f "\${crosDesktopShareDir}/applications/garcon_host_browser.desktop"
      fi
      chmod -R u+w "\${crosDesktopShareDir}/applications" "\${crosDesktopShareDir}/icons" 2>/dev/null || true

      # update-desktop-database "\${crosDesktopShareDir}/applications" 2>/dev/null || true
    '';
  };

  # Podman
  # https://discourse.nixos.org/t/rootless-podman-setup-with-home-manager/57905
  services.podman = {
    enable = true;
  };

  # Declarative Nix Settings / Cachix binary caches
  nix = {
    package = pkgs.nix;
    settings = {
      substituters = [
        "https://cache.nixos.org"
        "https://cache.numtide.com"
        "https://codex-desktop-linux.cachix.org"
      ];
      trusted-public-keys = [
        "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
        "niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g="
        "codex-desktop-linux.cachix.org-1:nX/xy6AdK9hQE24A8ALGjkCKj2ObFmcnemiL5Cid4nk="
      ];
    };
  };
}

EOF

# Stage an unmanaged wrapper before Home Manager claims the same path. This
# makes a failed activation recoverable and lets a successful verification
# retire the old copy explicitly.
CODEX_WRAPPER_PATH="$HOME/.local/bin/codex"
CODEX_WRAPPER_STAGING=""
mkdir -p "$HOME/.local/bin"

restore_codex_wrapper() {
  if [ -n "$CODEX_WRAPPER_STAGING" ] && [ -e "$CODEX_WRAPPER_STAGING" ]; then
    rm -f -- "$CODEX_WRAPPER_PATH"
    mv -- "$CODEX_WRAPPER_STAGING" "$CODEX_WRAPPER_PATH"
    echo "Restored the previous unmanaged Codex wrapper after activation failure." >&2
  fi
}

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

trap restore_codex_wrapper EXIT
echo "Activating Home Manager (Version $NIX_VER)..."
if ! nix shell nixpkgs#git --command \
    nix run github:nix-community/home-manager -- switch --flake "$CONF_DIR#$USER" --impure -b backup; then
  echo "ERROR: Home Manager activation failed; the previous Codex wrapper was preserved." >&2
  exit 1
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
trap - EXIT

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
ZSH_PATH="$(which zsh)"
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
echo "Modify your packages anytime in: $CONF_DIR/home.nix"
echo "wayland-2 is configured as the default without restarting running services."
echo "Run cros-reset to start inactive launcher services without interrupting apps."
echo "Run cros-hard-reset only when a full GUI-disrupting reset is required."
echo "============================================================"
