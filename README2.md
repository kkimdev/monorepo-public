```bash
# =============================================================================
# COMPLETE IDEMPOTENT NIX + HOME-MANAGER BOOTSTRAP (FIXED ARGUMENTS)
# =============================================================================

GIT_USER_NAME=""
# Use private email from https://github.com/settings/emails
GIT_USER_EMAIL=""


sudo apt-get purge vi vim command-not-found -y

# 2. INSTALL NIX (Only if missing)
if ! command -v nix &> /dev/null; then
    echo "Nix not found. Installing via Determinate Systems..."
    curl --proto "=https" --tlsv1.2 -sSfL https://install.determinate.systems/nix | sh -s -- install --no-confirm
    . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
else
    echo "Nix is already installed."
fi

# 3. DETECT ARCHITECTURE & SYSTEM
ARCH=$(uname -m)
SYS=$([ "$ARCH" = "x86_64" ] && echo "x86_64-linux" || echo "aarch64-linux")

# 4. DETECT CURRENT NIXPKGS VERSION
echo "Detecting current Nixpkgs release..."
NIX_VER=$(nix eval --raw nixpkgs#lib.version | cut -d. -f1,2)

# 5. PREPARE CONFIG DIRECTORY
CONF_DIR="$HOME/.config/home-manager"
mkdir -p "$CONF_DIR"

# 6. GENERATE FLAKE.NIX
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
      # Use our new native username variable here
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
      git
      micro
      yazi
      zellij
      podman

      # Apps
      gedit
      #chromium      

      # Coding
      vscode
      antigravity

      # Fonts
      nerd-fonts.jetbrains-mono
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
          pushd $CONF_DIR && \\
          nix flake update && \\
          home-manager switch --flake .#$USER --impure && \\
          popd && \\
          nix-collect-garbage -d
        '';

        cros-reset = ''
          systemctl --user daemon-reload && \
          systemctl --user restart sommelier@0.service && \
          systemctl --user restart sommelier-x@0.service
        '';
      };
    };

    git = {
      enable = true;
      settings = {
        core.editor = "code --wait --new-window";
        diff.tool = "diff-code";
        difftool.diff-code.cmd = "code --wait --new-window --diff $LOCAL $REMOTE";
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
      presets = [ "catppuccin-powerline" ];
      settings = {};
    };

    direnv = {
      enable = true;
      nix-direnv.enable = true;
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

# 8. ACTIVATE (With implicit Git bootstrap and Clobber safety)
echo "Activating Home Manager (Version $NIX_VER, System $SYS)..."
cd "$CONF_DIR"

# Pass -b backup cleanly to look after clobbering issues
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
```




































































## Terminal App Nerd Font

References
- https://vcfvct.wordpress.com/2021/11/14/chromeos-dev-setup/
- https://www.reddit.com/r/Crostini/comments/s1dgvk/best_way_to_get_nerd_fonts_on_crostini/

`[Ctrl]+[Shift]+J` in Terminal App then paste the following.

```javascript
await term_.prefs_.set('font-family', '"JetBrainsMono Nerd Font", "JetBrains Mono"');
await term_.prefs_.set('user-css-text', '@font-face {font-family: "JetBrainsMono Nerd Font"; src: url("https://raw.githubusercontent.com/ryanoasis/nerd-fonts/master/patched-fonts/JetBrainsMono/Ligatures/Regular/JetBrainsMonoNerdFont-Regular.ttf"); font-weight: normal; font-style: normal;}')
```

## Github key
```bash

# https://docs.github.com/en/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519

# https://docs.github.com/en/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account
# Paste the output to https://github.com/settings/keys
cat ~/.ssh/id_ed25519.pub
```
