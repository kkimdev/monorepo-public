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

# INSTALL NIX (Only if missing)
if ! command -v nix &> /dev/null; then
    echo "Nix not found. Installing via Determinate Systems..."
    curl --proto "=https" --tlsv1.2 -sSfL https://install.determinate.systems/nix | sh -s -- install --no-confirm
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
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
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
  };

  outputs = { nixpkgs, home-manager, nix-index-database, sommelier-rs, kakaotalk, ... }:
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
          ];
        };

        modules = [
          ./home.nix
          nix-index-database.homeModules.default
          { programs.nix-index-database.comma.enable = true; }
        ];
      };
    };
}
EOF

# 7. GENERATE HOME.NIX
rm -f "$CONF_DIR/home.nix"
cat <<EOF > "$CONF_DIR/home.nix"
{ config, pkgs, lib, ... }:

let
  crosDesktopShareDir = "\${config.home.homeDirectory}/.local/share";
  nixProfileShareDir  = "\${config.home.homeDirectory}/.nix-profile/share";

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
      systemctl --user daemon-reload && \
      systemctl --user restart sommelier@0.service && \
      systemctl --user restart sommelier-x@0.service && \
      systemctl --user restart sommelier-x@1.service && \
      systemctl --user restart sommelier-rs.service
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
      antigravity
      antigravity-cli
      codex
      claude-code
      opencode

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
      # wayland-0: host compositor (sommelier), wayland-1: custom sommelier-rs
      WAYLAND_DISPLAY = "wayland-1";
      DISPLAY = ":1";
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
        "\${pkgs.sommelier-rs}/bin/sommelier-rs wayland-1";
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
      # TODO: Confirm if this is correct.
      ignores = [
        "*_personal/"
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
        diffToolMode = false;
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
      enableBashIntegration = false;
      enableZshIntegration = false;
    };
  };

  xdg.configFile = {
    # https://nixos.wiki/wiki/Installing_Nix_on_Crostini
    "systemd/user/cros-garcon.service.d/override.conf".text = ''
      [Service]
      Environment="PATH=%h/.nix-profile/bin:/usr/local/sbin:/usr/local/bin:/usr/local/games:/usr/sbin:/usr/bin:/usr/games:/sbin:/bin"
      Environment="XDG_DATA_DIRS=%h/.nix-profile/share:%h/.local/share:%h/.local/share/flatpak/exports/share:/var/lib/flatpak/exports/share:/usr/local/share:/usr/share"
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

    # Disable system sommelier@1 by overriding it to run /bin/true
    "systemd/user/sommelier@1.service.d/override.conf".text = ''
      [Service]
      ExecStart=
      ExecStart=\${pkgs.coreutils}/bin/true
      Restart=no
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
}

EOF

echo "Activating Home Manager (Version $NIX_VER)..."
nix shell nixpkgs#git --command \
  nix run --refresh github:nix-community/home-manager -- switch --flake "$CONF_DIR#$USER" --impure -b backup --refresh

sudo chsh -s "$(which zsh)" "$USER"

echo "============================================================"
echo "SUCCESS: Home Manager setup is fully activated!"
echo "Your original configs were safely backed up as *.backup"
echo "Modify your packages anytime in: $CONF_DIR/home.nix"
echo "============================================================"

# exec "$(readlink -f /proc/$PPID/exe)"
