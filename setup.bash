#!/usr/bin/env bash
set -euxo pipefail

# TODO
# - [ ] podman setup
# - [ ] zsh setup
# - [ ] fzf setup

# TODO: Fill out and execute these before running the script.
# export GIT_USER_NAME=""
# export GIT_USER_EMAIL="" # Use private email from https://github.com/settings/emails

sudo apt-get purge vim command-not-found -y

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

rm -f "$CONF_DIR/flake.nix"
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
  };

  outputs = { nixpkgs, home-manager, nix-index-database, ... }:
    let
      system = builtins.currentSystem;

      # Dynamically grab the current user directly from the environment inside Nix
      username = builtins.getEnv "USER";

      targetCpu = {
        "x86_64-linux"  = "x86_64";
        "aarch64-linux" = "aarch64";
      }.${system} or (throw "Unsupported system architecture: ${system}");

      shaMap = {
        "x86_64"  = "sha256-zztkv98J8hpVXCXfUnjSaPKaPua44e56yyO/jUwtFzY";
        "aarch64" = "sha256-PLACE_ARM64_SHA256_HERE_IF_AVAILABLE";
      };

      sommelierOverlay = final: prev: {
        sommelier-rs = prev.stdenv.mkDerivation rec {
          pname = "sommelier-rs";
          version = "0.1.1";
          src = prev.fetchurl {
            url = "https://github.com/google/sommelier-rs/releases/download/virtwl-v0.1.1/sommelier_rs_virtwl-v0.1.1-${targetCpu}";
            sha256 = shaMap.${targetCpu};
          };
          dontUnpack = true;
          nativeBuildInputs = [ prev.makeWrapper ];
          installPhase = "mkdir -p $out/bin && cp $src $out/bin/sommelier-rs && chmod +x $out/bin/sommelier-rs";
          postFixup = "patchelf --set-interpreter \$(cat ${prev.stdenv.cc}/nix-support/dynamic-linker) --set-rpath ${prev.lib.makeLibraryPath [ prev.libxkbcommon prev.libgbm ]} $out/bin/sommelier-rs";
        };
      };
    in {
      homeConfigurations."${username}" = home-manager.lib.homeManagerConfiguration {
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
          overlays = [ sommelierOverlay ];
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
  # https://github.com/google/sommelier-rs/issues/14
  useSommelierRS = false;
  crosDesktopShareDir = "\${config.home.homeDirectory}/.local/share";
  nixProfileShareDir  = "\${config.home.homeDirectory}/.nix-profile/share";
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
      micro
      direnv
      nix-direnv
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
      podman

      # Apps
      #chromium
      inkscape
      beekeeper-studio
      yt-dlp

      # Coding
      vscode
      antigravity
      codex
      claude-code
      opencode

      # Fonts
      nerd-fonts.jetbrains-mono
      noto-fonts
      google-fonts
    ] ++ lib.optional useSommelierRS pkgs.sommelier-rs;
    sessionVariables = {
      NIXPKGS_ALLOW_UNFREE = "1";
      EDITOR = "code --wait --new-window";
    };
  };

  systemd.user.services.sommelier-rs = lib.mkIf useSommelierRS {
    Unit = {
      Description = "Sommelier-RS Wayland Compositor";
      # Ensure it starts after the basic user session is ready
      After = [ "debian-fixup.service" ];
    };
    Service = {
      # Use the absolute path from the derivation defined above
      ExecStart = "\${pkgs.sommelier-rs}/bin/sommelier-rs --virtio-wl /dev/wl0 wayland-0";
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

      shellAliases = {
        sudo = "sudo env PATH=\\\\\"\$PATH\\\\\"";
        grep = "grep --color=auto";
        fgrep = "fgrep --color=auto";
        egrep = "egrep --color=auto";

        upgrade-all = ''
          sudo apt-get update && sudo apt-get full-upgrade -y && \\
          sudo apt-get autoremove -y && \\
          sudo determinate-nixd upgrade && \\
          pushd $CONF_DIR && \\
          nix flake update && \\
          home-manager switch --flake .#$USER --impure && \\
          popd && \\
          nix store gc
        '';

        cros-reset = ''
          systemctl --user daemon-reload && \
          systemctl --user restart sommelier@0.service && \
          systemctl --user restart sommelier-x@0.service
        '';
      };
    };

    zsh = {
      enable = true;
      enableCompletion = true;
      autosuggestion.enable = true;
      syntaxHighlighting.enable = true;

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
      # TODO: More options
    };

    git = {
      enable = true;
      settings = {
        core.editor = "code --wait --new-window";
        difftool.diff-code.cmd = "code --wait --new-window --diff \$LOCAL \$REMOTE";
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
        enable = true;
        diffToolMode = true;
      };
    };

    fzf = {
      enable = true;
      enableBashIntegration = true;
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
  } // lib.optionalAttrs useSommelierRS {
    # These entire file definitions are only added if useSommelierRS is true
    "systemd/user/sommelier@.service".source = config.lib.file.mkOutOfStoreSymlink "/dev/null";
    "systemd/user/sommelier-x@.service".source = config.lib.file.mkOutOfStoreSymlink "/dev/null";
  };

  home.file = {
    # Your existing inputrc configuration
    ".inputrc".text = ''
      "\e[A": history-search-backward
      "\e[B": history-search-forward
      set show-all-if-ambiguous on
    '';

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
}
EOF

echo "Activating Home Manager (Version $NIX_VER)..."
cd "$CONF_DIR"
nix shell nixpkgs#git --command \
  nix run github:nix-community/home-manager -- switch --flake .#$USER --impure -b backup

echo "============================================================"
echo "SUCCESS: Home Manager setup is fully activated!"
echo "Your original configs were safely backed up as *.backup"
echo "Modify your packages anytime in: $CONF_DIR/home.nix"
echo "============================================================"

# . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
# export PATH="/nix/var/nix/profiles/default/bin:$HOME/.nix-profile/bin:$PATH"
# exec bash

. "$HOME/.bashrc"
