#!/usr/bin/env bash
set -euxo pipefail

export SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CROS_SETUP_SCRIPT_FILE="$(readlink -f "${BASH_SOURCE[0]}")"

# TODO
# - [ ] https://github.com/sigoden/aichat setup

# TODO: Fill out and execute these before running the script.
# export GIT_USER_NAME=""
# export GIT_USER_EMAIL="" # Use private email from https://github.com/settings/emails

sudo apt-get update -y
sudo apt-get install uidmap -y
sudo apt-get remove vim command-not-found -y

# Add user to render group (required for GPU acceleration access in Crostini)
sudo usermod -aG render "$USER"

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

rm -f "$CONF_DIR/flake.nix" "$CONF_DIR/flake.lock"
cat <<'EOF' > "$CONF_DIR/flake.nix"
{
  inputs = {
    nixpkgs.url = "https://flakehub.com/f/DeterminateSystems/nixpkgs-weekly/0.1";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    nix-index-database.url = "github:nix-community/nix-index-database";
    nix-index-database.inputs.nixpkgs.follows = "nixpkgs";

    sommelier-rs = {
      url = "github:kkimdev/monorepo-public/main?dir=nixpkgs/sommelier-rs";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    kakaotalk = {
      url = "github:kkimdev/monorepo-public/main?dir=nixpkgs/kakaotalk";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # TODO: Re-enable when upstream flake fixes stale Codex.dmg hash.
    #   error: hash mismatch in fixed-output derivation '.../Codex.dmg.drv':
    #     specified: sha256-c9hj/+uCB+9WbmmWAz/bFgcnLdHRdgrm3A7I7JxiOuo=
    #     got:       sha256-feTM5exuOUeLnwYw4rkleq3R0C3WoP3ADC7N8PU2Ai0=
    #   Track: https://github.com/ilysenko/codex-desktop-linux
    # codex-desktop-linux = {
    #   url = "github:ilysenko/codex-desktop-linux";
    #   inputs.nixpkgs.follows = "nixpkgs";
    # };


    claude-desktop = {
      url = "github:aaddrick/claude-desktop-debian";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    antigravity-nix = {
      url = "github:jacopone/antigravity-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, home-manager, nix-index-database, sommelier-rs, kakaotalk, claude-desktop, antigravity-nix, ... }@inputs:
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
            claude-desktop.overlays.default
            antigravity-nix.overlays.default
          ];
        };

        extraSpecialArgs = { inherit inputs; };

        modules = [
          ./home.nix
          nix-index-database.homeModules.default
          # TODO: Re-enable when codex-desktop-linux upstream fixes Codex.dmg hash mismatch.
          # codex-desktop-linux.homeManagerModules.default
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
  # GSettings schema dirs for codex-desktop file dialog (GLib fatal crash fix)
  # See also `GSETTINGS_SCHEMA_DIR` in the cros-garcon override below.
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
      # Clean up stale sockets before restarting (keep wayland-0, it's the host compositor)
      rm -f /run/user/\$UID/wayland-{1,2}.lock /run/user/\$UID/wayland-{1,2} 2>/dev/null; \
      systemctl --user daemon-reload && \
      systemctl --user reset-failed sommelier@1.service 2>/dev/null; \
      systemctl --user restart sommelier@0.service && \
      systemctl --user restart sommelier-x@0.service && \
      systemctl --user restart sommelier-x@1.service && \
      systemctl --user restart sommelier-rs.service && \
      systemctl --user restart cros-garcon.service
    '';

    # Re-run the full ChromeOS dev setup and reload the current shell so all
    # new paths, aliases, and env vars take effect immediately.
    # The path to setup.bash is baked in at generation time so this alias
    # works from any directory.
    cros-setup = "bash \"\$CROS_SETUP_SCRIPT_FILE\" && exec \"\$(readlink -f /proc/\$\$/exe)\"";
  };
in
{
  nixpkgs.config.allowUnfree = true;

  home = {
    username = "$USER";
    homeDirectory = "$HOME";
    stateVersion = "$NIX_VER";
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

      ## Already included via other lines.
      # nix-direnv
      # podman

      # Apps
      chromium
      inkscape
      beekeeper-studio
      yt-dlp
      sommelier-rs
      kakaotalk-bin

      # Coding
      vscode
      google-antigravity-no-fhs
      google-antigravity-ide
      google-antigravity-cli
      # TODO: Re-enable when codex-desktop-linux upstream fixes Codex.dmg hash mismatch.
      # codex
      claude-code
      claude-desktop
      opencode
      opencode-desktop

      # Fonts
      nerd-fonts.jetbrains-mono
      noto-fonts
      google-fonts
    ];
    sessionVariables = {
      NIXPKGS_ALLOW_UNFREE = "1";
      EDITOR = "code --wait --new-window";
      GIT_USER_NAME = "$GIT_USER_NAME";
      GIT_USER_EMAIL = "$GIT_USER_EMAIL";
      CROS_SETUP_SCRIPT_FILE = "$CROS_SETUP_SCRIPT_FILE";
      GSETTINGS_SCHEMA_DIR = "\${gtk3SchemaDir}:\${gsettingsSchemaDir}";
      # wayland-0: host compositor (sommelier), wayland-1: Crostini default sommelier@1, wayland-2: custom sommelier-rs
      WAYLAND_DISPLAY = "wayland-2";
      DISPLAY = ":1";
      CODEX_LINUX_RENDERING_MODE = "wayland-gpu";
      # Web search provider selection. Required for non-opencode providers (model-gateway).
      # See ../scratch-monorepo/model_gateway/WEB_SEARCH.md.
      # OPENCODE_ENABLE_EXA = "true";
      OPENCODE_ENABLE_PARALLEL = "true";
    };
  };

  systemd.user.services.sommelier-rs = {
    Unit = {
      Description = "Sommelier-RS Wayland Compositor";
      # Ensure it starts after the basic user session and wayland-0 host compositor are ready
      After = [ "debian-fixup.service" "sommelier@0.service" ];
      Requires = [ "sommelier@0.service" ];
    };
    Service = {
      # Use the absolute path from the derivation defined above
      ExecStart =
        "\${pkgs.sommelier-rs}/bin/sommelier-rs wayland-2";
      Restart = "always";
      RestartSec = "5";
      Environment = [
        "RUST_LOG=info"
        "SOMMELIER_ACCELERATORS=Super_L,<Alt>bracketleft,<Alt>bracketright,<Alt>minus,<Alt>equal,<Alt>1,<Alt>2,<Alt>3,<Alt>4,<Alt>5,<Alt>6,<Alt>7,<Alt>8,<Alt>9,print,<Control>space"
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
    defaultApplications = {
      "text/html" = [ "garcon_host_browser.desktop" ];
      "x-scheme-handler/http" = [ "garcon_host_browser.desktop" ];
      "x-scheme-handler/https" = [ "garcon_host_browser.desktop" ];
      "x-scheme-handler/about" = [ "garcon_host_browser.desktop" ];
      "x-scheme-handler/unknown" = [ "garcon_host_browser.desktop" ];
    };
  };

  programs = {
    home-manager.enable = true;

    # TODO: Re-enable when codex-desktop-linux upstream fixes stale Codex.dmg hash.
    #   The computerUseUi and remoteMobileControl sub-packages fetch Codex.dmg
    #   from OpenAI servers as a fixed-output derivation, and the hash is stale.
    #   Track: https://github.com/ilysenko/codex-desktop-linux
    #
    # codex-desktop known issues (for reference when re-enabling):
    #
    # 1. GLib-GIO-ERROR: Settings schema 'org.gtk.Settings.FileChooser' not found
    #    → Fix: GSETTINGS_SCHEMA_DIR is set in cros-garcon.service override
    #      (see xdg.configFile section below).
    #
    # 2. Re-launch hangs when X-closed (Electron single-instance lock)
    #    → Ctrl+Q works to actually quit. No config-side fix yet.
    #
    # codexDesktopLinux = {
    #   enable = true;
    #   cliPackage = pkgs.codex;
    #   computerUseUi.enable = true;
    #   remoteMobileControl.enable = true;
    #   remoteControl = {
    #     enable = true;
    #     package = pkgs.codex;
    #   };
    # };

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
      '';
      bashrcExtra = ''
        . \$HOME/.bashrc.backup
      '';
      profileExtra = ''
        . \$HOME/.profile.backup
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
      Environment="CODEX_LINUX_RENDERING_MODE=wayland-gpu"
      # GLib-GIO-ERROR: Settings schema 'org.gtk.Settings.FileChooser' is not installed
      # codex-desktop crashes (exit 5) when opening a file dialog if these are missing.
      Environment="GSETTINGS_SCHEMA_DIR=\${gtk3SchemaDir}:\${gsettingsSchemaDir}"
    '';

    # Chrome OS shortcuts in Linux apps
    # https://www.reddit.com/r/Crostini/wiki/enable-chrome-shortcuts-in-linux-apps
    # https://issuetracker.google.com/issues/149234835#comment14
    "systemd/user/sommelier@.service.d/cros-sommelier-override.conf".text = ''
      [Service]
      Environment="SOMMELIER_ACCELERATORS=Super_L,<Alt>bracketleft,<Alt>bracketright,<Alt>minus,<Alt>equal,<Alt>1,<Alt>2,<Alt>3,<Alt>4,<Alt>5,<Alt>6,<Alt>7,<Alt>8,<Alt>9,print,<Control>space"
    '';
    "systemd/user/sommelier-x@.service.d/cros-sommelier-x-override.conf".text = ''
      [Service]
      Environment="SOMMELIER_ACCELERATORS=Super_L,<Alt>bracketleft,<Alt>bracketright,<Alt>minus,<Alt>equal,<Alt>1,<Alt>2,<Alt>3,<Alt>4,<Alt>5,<Alt>6,<Alt>7,<Alt>8,<Alt>9,print,<Control>space"
    '';

    # Override sommelier-x@1 to run at scale 1.0 (high-density)
    "systemd/user/sommelier-x@1.service.d/override.conf".text = ''
      [Service]
      Environment="SOMMELIER_SCALE=1.0"
    '';

  };

  home.file = {
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

    # TODO
    # https://www.reddit.com/r/Nix/comments/zh1803/guide_how_to_have_nix_installed_applications/
  };

  home.activation = {
    linkDesktopApplications = lib.hm.dag.entryAfter ["writeBoundary"] ''
      rm -rf "\${crosDesktopShareDir}/applications" "\${crosDesktopShareDir}/icons"
      mkdir -p "\${crosDesktopShareDir}/applications" "\${crosDesktopShareDir}/icons"

      cp -rL "\${nixProfileShareDir}/applications/." "\${crosDesktopShareDir}/applications/"
      cp -rL "\${nixProfileShareDir}/icons/."        "\${crosDesktopShareDir}/icons/"
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
        "https://codex-desktop-linux.cachix.org"
      ];
      trusted-public-keys = [
        "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
        "codex-desktop-linux.cachix.org-1:nX/xy6AdK9hQE24A8ALGjkCKj2ObFmcnemiL5Cid4nk="
      ];
    };
  };
}

EOF

echo "Activating Home Manager (Version $NIX_VER)..."
nix shell nixpkgs#git --command \
  nix run --refresh github:nix-community/home-manager -- switch --flake "$CONF_DIR#$USER" --impure -b backup --refresh

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

# Clean up stale Wayland sockets that may conflict from previous runs
# Keep wayland-0 (host compositor), clean up wayland-1 and wayland-2 which we manage
echo "Cleaning up stale Wayland sockets..."
rm -f /run/user/"$(id -u)"/wayland-{1,2}.lock /run/user/"$(id -u)"/wayland-{1,2} 2>/dev/null || true

echo "============================================================"
echo "SUCCESS: Home Manager setup is fully activated!"
echo "Your original configs were safely backed up as *.backup"
echo "Modify your packages anytime in: $CONF_DIR/home.nix"
echo "============================================================"

# exec "$(readlink -f /proc/$PPID/exe)"
