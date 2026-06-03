```bash
# =============================================================================
# COMPLETE IDEMPOTENT NIX + HOME-MANAGER BOOTSTRAP (FIXED ARGUMENTS)
# =============================================================================

GIT_USER_NAME=""
# Use private email from https://github.com/settings/emails
GIT_USER_EMAIL=""

sudo apt-get purge vim command-not-found -y

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
cat <<EOF > "$CONF_DIR/flake.nix"
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

  outputs = { nixpkgs, home-manager, nix-index-database, ... }: {
    homeConfigurations."$USER" = home-manager.lib.homeManagerConfiguration {
      pkgs = nixpkgs.legacyPackages.$SYS;
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
cat <<EOF > "$CONF_DIR/home.nix"
{ config, pkgs, ... }: {
  nixpkgs.config.allowUnfree = true;

  home = {
    username = "$USER";
    homeDirectory = "$HOME";
    stateVersion = "$NIX_VER";
    packages = with pkgs; [
      git
      vscode
      antigravity
      podman
      micro
      yazi
      zellij
      # Fonts
      nerd-fonts.jetbrains-mono
      google-fonts
    ];
    sessionVariables = {
      NIXPKGS_ALLOW_UNFREE = "1";
      EDITOR = "code --wait --new-window";
    };
  };

  services.ssh-agent.enable = true;

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
          sudo apt-get update && sudo apt-get full-upgrade -y && \
          sudo apt-get autoremove -y && \
          pushd $CONF_DIR && \
          nix flake update && \
          home-manager switch --flake .#$USER --impure && \
          popd && \
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
      enable = true;
      enableBashIntegration = true;
      attachExistingSession = false;
      exitShellOnExit = true;

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

  # https://nixos.wiki/wiki/Installing_Nix_on_Crostini
  xdg.configFile."systemd/user/cros-garcon.service.d/override.conf".text = ''
    [Service]
    Environment="PATH=%h/.nix-profile/bin:/usr/local/sbin:/usr/local/bin:/usr/local/games:/usr/sbin:/usr/bin:/usr/games:/sbin:/bin"
    Environment="XDG_DATA_DIRS=%h/.nix-profile/share:%h/.local/share:%h/.local/share/flatpak/exports/share:/var/lib/flatpak/exports/share:/usr/local/share:/usr/share"
  '';

  # Chrome OS shortcuts in Linux apps
  # https://www.reddit.com/r/Crostini/wiki/enable-chrome-shortcuts-in-linux-apps
  # https://issuetracker.google.com/issues/149234835#comment14
  xdg.configFile."systemd/user/sommelier@.service.d/cros-sommelier-override.conf".text = ''
    [Service]
    Environment="SOMMELIER_ACCELERATORS=Super_L,<Alt>bracketleft,<Alt>bracketright,<Alt>minus,<Alt>equal,<Alt>1,<Alt>2,<Alt>3,<Alt>4,<Alt>5,<Alt>6,<Alt>7,<Alt>8,<Alt>9,print,<Control>space"
  '';
  xdg.configFile."systemd/user/sommelier-x@.service.d/cros-sommelier-x-override.conf".text = ''
    [Service]
    Environment="SOMMELIER_ACCELERATORS=Super_L,<Alt>bracketleft,<Alt>bracketright,<Alt>minus,<Alt>equal,<Alt>1,<Alt>2,<Alt>3,<Alt>4,<Alt>5,<Alt>6,<Alt>7,<Alt>8,<Alt>9,print,<Control>space"
  '';

  home.file.".inputrc".text = ''
    "\e[A": history-search-backward
    "\e[B": history-search-forward
    set show-all-if-ambiguous on
  '';
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
