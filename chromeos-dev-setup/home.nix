{ config, pkgs, lib, inputs, ... }:

let
  host = builtins.fromJSON (builtins.readFile ./host.json);
  crosDesktopShareDir = "${config.home.homeDirectory}/.local/share";
  nixProfileShareDir  = "${config.home.homeDirectory}/.nix-profile/share";
  llmAgentsPkgs       = inputs.llm-agents.packages.${pkgs.stdenv.hostPlatform.system};
  openspecCli         = inputs.openspec.packages.${pkgs.stdenv.hostPlatform.system}.default;
  # Temporary package fix while https://github.com/stablyai/orca/pull/29172
  # (https://github.com/stablyai/orca/issues/29171) awaits an upstream release.
  # Match Electron's native Wayland app ID and X11 class to orca-ide.desktop.
  # Keep keyboard and Copy on Select copies on Orca's focused Wayland connection in Crostini.
  # Remove the native-copy patch after https://github.com/stablyai/orca/pull/29377 ships.
  orcaWithDesktopIdentity = llmAgentsPkgs.orca.overrideAttrs (old: {
    postFixup = (old.postFixup or "") + ''
      ${pkgs.python3}/bin/python3 ${./patch-orca.py} \
        "$out/lib/Orca/resources/app.asar" "$out/share/applications/orca-ide.desktop"
    '';
  });
  # This file is evaluated with --impure on the host. Do not install a
  # Crostini-specific browser default on ordinary Linux systems.
  isCrostini = builtins.pathExists "/opt/google/cros-containers/bin/garcon"
    && builtins.pathExists "/usr/share/applications/garcon_host_browser.desktop";
  # GSettings schema dirs for codex-desktop file dialog (GLib fatal crash fix)
  # See also `GSETTINGS_SCHEMA_DIR` in the cros-garcon override below.
  gtk3SchemaDir       = "${pkgs.gtk3}/share/gsettings-schemas/gtk+3-${lib.getVersion pkgs.gtk3}/glib-2.0/schemas";
  gsettingsSchemaDir  = "${pkgs.gsettings-desktop-schemas}/share/gsettings-schemas/gsettings-desktop-schemas-${lib.getVersion pkgs.gsettings-desktop-schemas}/glib-2.0/schemas";

  myShellAliases = {
    sudo = "sudo env PATH=\\\"$PATH\\\"";
    grep = "grep --color=auto";
    fgrep = "fgrep --color=auto";
    egrep = "egrep --color=auto";
    ls = "ls --color=auto";
    dir = "dir --color=auto";
    vdir = "vdir --color=auto";

    cros-update = "bash ${./update.bash} ${lib.escapeShellArg host.setupScript}";

    cros-clean = ''
      nix-collect-garbage -d && \
      nix store optimise && \
      nix store gc
    '';

    cros-reset = ''
      echo "Ensuring deferred user services are running..." && \
      systemctl --user daemon-reload && \
      systemctl --user reset-failed atuin-daemon.service earlyoom.service sommelier@0.service sommelier@1.service sommelier-x@0.service sommelier-x@1.service sommelier-rs.service cros-garcon.service && \
      systemctl --user start atuin-daemon.service earlyoom.service sommelier@0.service sommelier@1.service sommelier-x@0.service sommelier-x@1.service sommelier-rs.service cros-garcon.service
    '';

    cros-hard-reset = ''
      echo "Hard-resetting Crostini user services; running GUI apps will close..." && \
      systemctl --user stop cros-garcon.service sommelier-rs.service sommelier-x@1.service sommelier-x@0.service sommelier@1.service sommelier@0.service atuin-daemon.service && \
      rm -f /run/user/$UID/wayland-{1,2}.lock /run/user/$UID/wayland-{1,2} 2>/dev/null; \
      systemctl --user daemon-reload && \
      systemctl --user reset-failed atuin-daemon.service earlyoom.service sommelier@0.service sommelier@1.service sommelier-x@0.service sommelier-x@1.service sommelier-rs.service cros-garcon.service && \
      systemctl --user start atuin-daemon.service earlyoom.service sommelier@0.service sommelier@1.service sommelier-x@0.service sommelier-x@1.service sommelier-rs.service cros-garcon.service
    '';

    # Reapply configuration without restarting services, then update this shell
    # so subsequently launched applications use the custom proxy.
    cros-setup = "bash ${lib.escapeShellArg host.setupScript} && export WAYLAND_DISPLAY=wayland-2";
  };
in
{
  nixpkgs.config.allowUnfree = true;

  # Keep cros-setup interruption-free. cros-reset starts only inactive services;
  # updated running units take effect on the next login or after cros-hard-reset.
  systemd.user.startServices = "suggest";

  home = {
    username = host.username;
    homeDirectory = host.homeDirectory;
    # Compatibility baseline, independent of package versions.
    stateVersion = "26.11";
    sessionPath = [
      "${config.home.homeDirectory}/.local/bin"
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
      earlyoom
      process-compose
      cloudflared
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
      # # Insecure?
      # beekeeper-studio
      yt-dlp
      sommelier-rs-bin
      kakaotalk-bin

      # Coding
      vscode
      # Orca's skill updater spawns npx outside project development shells.
      nodejs
      inputs.openspec.packages.${pkgs.stdenv.hostPlatform.system}.default
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
      llmAgentsPkgs.opencode2
      orcaWithDesktopIdentity

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
      GIT_USER_NAME = host.gitName;
      GIT_USER_EMAIL = host.gitEmail;
      CROS_SETUP_SCRIPT_FILE = host.setupScript;
      GSETTINGS_SCHEMA_DIR = "${gtk3SchemaDir}:${gsettingsSchemaDir}";
      # Native Wayland clients use GTK's text-input-v3 module through
      # sommelier-rs. X11/Xwayland cannot use that module and falls back to the
      # ChromeOS GTK bridge; some GTK4 builds register it as "test-cros".
      GTK_IM_MODULE = "wayland:cros:test-cros";
      GTK_IM_MODULE_FILE = host.gtk3Cache;
      GTK_PATH = host.gtk4Path;
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
        "${pkgs.sommelier-rs-bin}/bin/sommelier-rs --gpu-accel wayland-2";
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

  # Keep an independent, memory-only emergency shield for the user session.
  # This is intentionally process-level: it does not require every agent to
  # launch compilers through a wrapper. PSI-aware policy remains the job of
  # the Crostini monitor; earlyoom is only the final memory fallback.
  systemd.user.services.earlyoom = {
    Unit = {
      Description = "Early OOM emergency shield";
    };
    Service = {
      ExecStart = "${pkgs.earlyoom}/bin/earlyoom -m 8,4 -s 100 -r 60 --ignore-root-user --avoid=(^|/)(systemd|init|dbus-daemon|garcon|cros-garcon|sommelier|sommelier-rs|earlyoom)$ --prefer=(^|/)(rustc|clang|clang[+][+]|cc1|cc1plus|cargo|bazel|java)([[:space:]]|$)";
      Restart = "always";
      RestartSec = "5s";
      MemoryMax = "50M";
      NoNewPrivileges = true;
      PrivateTmp = true;
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
    ExecStartPre = "${pkgs.coreutils}/bin/mkdir -p %t/podman";
      ExecStart =
        "${pkgs.podman}/bin/podman system service --time=0 unix://%t/podman/podman.sock";
      Restart = "always";
      RestartSec = "5";
    };

    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  # Keep the personal long-running service catalog outside Home Manager's
  # generated home.nix. Home Manager owns only this stable supervisor unit;
  # the user-owned process-compose.yaml can evolve without rerunning setup.
  systemd.user.services.local-services = {
    Unit = {
      Description = "User local services supervisor";
      ConditionPathExists = "%h/.config/local-services/process-compose.yaml";
      StartLimitIntervalSec = "60s";
      StartLimitBurst = 5;
    };
    Service = {
      Type = "simple";
      WorkingDirectory = "%h/.config/local-services";
      ExecStart = "${pkgs.process-compose}/bin/process-compose -f %h/.config/local-services/process-compose.yaml up --tui=false --disable-dotenv";
      Restart = "on-failure";
      RestartSec = "5s";
      KillMode = "control-group";
      TimeoutStopSec = "15s";
      Environment = [
        "HOME=%h"
        "PATH=%h/.nix-profile/bin:/nix/var/nix/profiles/default/bin:/usr/local/bin:/usr/bin:/bin"
        "XDG_CONFIG_HOME=%h/.config"
        "XDG_STATE_HOME=%h/.local/state"
      ];
      StandardOutput = "journal";
      StandardError = "journal";
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
          local tmp="$(mktemp -t "yazi-cwd.XXXXXX")" cwd
          command yazi "$@" --cwd-file="$tmp"
          IFS= read -r -d ' ' cwd < "$tmp"
          [ -n "$cwd" ] && [ "$cwd" != "$PWD" ] && [ -d "$cwd" ] && builtin cd -- "$cwd"
          command rm -f -- "$tmp"
        }
        export PATH="$HOME/.local/bin:$PATH"
      '';
      bashrcExtra = ''
        if [[ -f "$HOME/.bashrc.backup" ]]; then
          . "$HOME/.bashrc.backup"
        fi
        # central secrets (chmod 600) - values live in ~/.config/secrets/.env, never in nix store
        if [[ -f "$HOME/.config/secrets/.env" ]]; then
          set -a
          . "$HOME/.config/secrets/.env"
          set +a
        fi
      '';
      profileExtra = ''
        if [[ -f "$HOME/.profile.backup" ]]; then
          . "$HOME/.profile.backup"
        fi
        export PATH="$HOME/.local/bin:$HOME/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH"
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
          src = "${pkgs.zsh-fzf-tab}/share/fzf-tab";
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

        export PATH="$HOME/.local/bin:$PATH"

        # central secrets (chmod 600) - values live in ~/.config/secrets/.env, never in nix store
        if [[ -f "$HOME/.config/secrets/.env" ]]; then
          set -a
          . "$HOME/.config/secrets/.env"
          set +a
        fi
      '';

      # TODO: More options
    };

    git = {
      enable = true;
      settings = {
        core.editor = "code --wait --new-window";
        diff.tool = "vscode";
        difftool.vscode.cmd = "code --wait --new-window --diff $LOCAL $REMOTE";
        user = {
          name = host.gitName;
          email = host.gitEmail;
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
      Environment="GTK_IM_MODULE_FILE=${host.gtk3Cache}"
      Environment="GTK_PATH=${host.gtk4Path}"
      Environment="QT_IM_MODULE=cros"
      Environment="CODEX_LINUX_RENDERING_MODE=wayland-gpu"
      Environment="CODEX_LINUX_DISABLE_EXTERNAL_OPEN_PATCH=1"
      # GLib-GIO-ERROR: Settings schema 'org.gtk.Settings.FileChooser' is not installed
      # codex-desktop crashes (exit 5) when opening a file dialog if these are missing.
      Environment="GSETTINGS_SCHEMA_DIR=${gtk3SchemaDir}:${gsettingsSchemaDir}"
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
      source = ./codex-wrapper;
    };

    # Your existing inputrc configuration
    ".inputrc".text = ''
      # "\e[A": history-search-backward
      # "\e[B": history-search-forward
      set show-all-if-ambiguous on
    '';

    # Share VS Code settings with Antigravity via symlink
    ".config/Antigravity/User/settings.json" = {
      source = config.lib.file.mkOutOfStoreSymlink "${host.homeDirectory}/.config/Code/User/settings.json";
    };
    ".antigravity-ide/User/settings.json" = {
      source = config.lib.file.mkOutOfStoreSymlink "${host.homeDirectory}/.config/Code/User/settings.json";
    };

    # Let the llm-agents.nix Codex CLI satisfy the remote-mobile cold-start
    # hook's managed runtime path without bootstrapping the standalone updater.
    ".codex/packages/standalone/current/codex" = {
      # Legacy source retained for rollback:
      # source = "${pkgs.codex}/bin/codex";
      source = "${llmAgentsPkgs.codex}/bin/codex";
    };

    # TODO
    # https://www.reddit.com/r/Nix/comments/zh1803/guide_how_to_have_nix_installed_applications/
  };

  home.activation = {
    # Generate and sync every OpenSpec skill into Codex's user scope. The
    # openspec-* namespace is owned by this activation; other user skills stay
    # untouched while added or removed OpenSpec workflows follow the package.
    installOpenSpecCodexSkills = lib.hm.dag.entryAfter ["installPackages"] ''
      openspecSkillsDir="$HOME/.agents/skills"
      tempDir="$(mktemp -d)"
      mkdir -p "$openspecSkillsDir"

      workDir="$tempDir/work"
      mkdir -p "$tempDir/home" "$workDir"
      (
        cd "$workDir"
        env \
          CI=1 \
          HOME="$tempDir/home" \
          "${openspecCli}/bin/openspec" init --tools codex --profile core --no-animation .
      )

      for existingSkill in "$openspecSkillsDir"/openspec-*; do
        if [ -e "$existingSkill" ] || [ -L "$existingSkill" ]; then
          rm -rf -- "$existingSkill"
        fi
      done

      for generatedSkill in "$workDir/.agents/skills"/openspec-*; do
        [ -d "$generatedSkill" ] || continue
        skillName="$(basename "$generatedSkill")"
        cp -a -- "$generatedSkill" "$openspecSkillsDir/$skillName"
      done

      rm -rf -- "$tempDir"
    '';

    linkDesktopApplications = lib.hm.dag.entryAfter ["writeBoundary"] ''
      rm -rf "${crosDesktopShareDir}/applications" "${crosDesktopShareDir}/icons"
      mkdir -p "${crosDesktopShareDir}/applications" "${crosDesktopShareDir}/icons"

      cp -rL "${nixProfileShareDir}/applications/." "${crosDesktopShareDir}/applications/"
      cp -rL "${nixProfileShareDir}/icons/."        "${crosDesktopShareDir}/icons/"
      if [ -r "/usr/share/applications/garcon_host_browser.desktop" ] &&
         [ -x "/opt/google/cros-containers/bin/garcon" ]; then
        cp -L "/usr/share/applications/garcon_host_browser.desktop"           "${crosDesktopShareDir}/applications/garcon_host_browser.desktop"
      else
        rm -f "${crosDesktopShareDir}/applications/garcon_host_browser.desktop"
      fi
      chmod -R u+w "${crosDesktopShareDir}/applications" "${crosDesktopShareDir}/icons" 2>/dev/null || true

      # update-desktop-database "${crosDesktopShareDir}/applications" 2>/dev/null || true
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
