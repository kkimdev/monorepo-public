{ config, pkgs, lib, inputs, hostData, hostDir, ... }:

let
  # Written by `cros apply` to ~/.config/cros/host/host.json.
  host = hostData;
  system = pkgs.stdenv.hostPlatform.system;
  llmAgentsPkgs       = inputs.llm-agents.packages.${system};
  # From llm-agents like the other agent tools: its binary cache has it, so
  # installs never build it (the upstream flake's build fetches npm packages).
  openspecCli         = llmAgentsPkgs.openspec;
  crosCli             = inputs.self.packages.${system}.cros;
  zshPath             = "${config.home.profileDirectory}/bin/zsh";
  # Claude Code plugin whose skills directory is the shared ~/.agents/skills.
  sharedSkillsPluginDir = ".claude/local-plugins/agents";
  # Claude resets PATH to system directories before opening login links.
  # Provide xdg-open inside its FHS environment, where /usr/bin is visible.
  claudeDesktopWithOpener = llmAgentsPkgs.claude-desktop.override (old: {
    buildFHSEnv = args: old.buildFHSEnv (args // {
      targetPkgs = p: args.targetPkgs p ++ [ p.xdg-utils ];
    });
  });
  # Temporary package fix while https://github.com/stablyai/orca/pull/29172
  # (https://github.com/stablyai/orca/issues/29171) awaits an upstream release.
  # Match Electron's native Wayland app ID and X11 class to orca-ide.desktop.
  # Copies need no patch: sommelier-rs offers ext-data-control-v1 (0.4.1+),
  # which Orca's clipboard helper uses to reach the ChromeOS clipboard.
  orcaWithDesktopIdentity = llmAgentsPkgs.orca.overrideAttrs (old: {
    postFixup = (old.postFixup or "") + ''
      ${pkgs.python3}/bin/python3 ${./patch-orca.py} \
        "$out/lib/Orca/resources/app.asar" "$out/share/applications/orca-ide.desktop"
    '';
  });
  # wayland-0: Crostini's high-density Sommelier, wayland-1: its low-density
  # one, wayland-2: sommelier-rs (direct VirtWL, ChromeOS IME protocols).
  sommelierRsDisplay = "wayland-2";

  # Adds makeWrapper arguments to an app's binary. Desktop entries run the
  # binary by name, so they pick up the wrapper from the profile.
  wrapApp = pkg: bin: arguments: pkgs.symlinkJoin {
    name = "${pkg.pname or pkg.name}-wrapped";
    paths = [ pkg ];
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postBuild = "wrapProgram $out/bin/${bin} ${arguments}";
    inherit (pkg) meta;
  };
  # Antigravity's browser agent looks for Chrome through CHROME_BIN; use the
  # Chromium installed below.
  antigravityChrome = "--set-default CHROME_BIN ${pkgs.chromium}/bin/chromium";
  antigravityHub = wrapApp llmAgentsPkgs.antigravity-hub "antigravity" antigravityChrome;
  # Keep the IDE's data where earlier packages put it (~/.antigravity-ide holds
  # settings, sign-in, and extensions), instead of ~/.config/Antigravity IDE.
  antigravityIde = wrapApp pkgs.antigravity-ide "antigravity-ide"
    "${antigravityChrome} --add-flags --user-data-dir=\\$HOME/.antigravity-ide";
  # The launcher picks Wayland only with NIXOS_OZONE_WL; under X11 the
  # wayland-first GTK_IM_MODULE below crashes it on the first text input.
  chatgptWayland = wrapApp llmAgentsPkgs.chatgpt "chatgpt" "--set NIXOS_OZONE_WL 1";

  # Display and input environment for login shells and for apps the ChromeOS
  # launcher starts through cros-garcon.
  desktopEnvironment = {
    # Native Wayland clients use GTK's text-input-v3 module through
    # sommelier-rs. X11/Xwayland cannot use that module and falls back to the
    # ChromeOS GTK bridge; some GTK4 builds register it as "test-cros".
    GTK_IM_MODULE = "wayland:cros:test-cros";
    GTK_IM_MODULE_FILE = host.gtk3Cache;
    GTK_PATH = host.gtk4Path;
    QT_IM_MODULE = "cros";
    WAYLAND_DISPLAY = sommelierRsDisplay;
    DISPLAY = ":1";
  };

  # PATH prefix for login shells (home.sessionPath), apps the ChromeOS
  # launcher starts through cros-garcon, and local services; each unit appends
  # its own system directories. systemd does not expand variables, so
  # sessionPath entries must be absolute paths.
  sessionPathPrefix = lib.concatStringsSep ":"
    (config.home.sessionPath ++ [ "${config.home.profileDirectory}/bin" ]);

  systemdEnvironment = env:
    lib.concatStrings (lib.mapAttrsToList (name: value: "Environment=\"${name}=${value}\"\n") env);

  # ChromeOS shortcuts reach the host instead of Linux apps.
  # https://www.reddit.com/r/Crostini/wiki/enable-chrome-shortcuts-in-linux-apps
  # https://issuetracker.google.com/issues/149234835#comment14
  sommelierAccelerators = "Super_L,<Alt>bracketleft,<Alt>bracketright,<Alt>minus,<Alt>equal,<Alt>1,<Alt>2,<Alt>3,<Alt>4,<Alt>5,<Alt>6,<Alt>7,<Alt>8,<Alt>9,print,<Control>space,<Control><Shift>space";

  # Central secrets (chmod 600): values live in ~/.config/secrets/.env, never
  # in the Nix store.
  loadSecrets = ''
    if [[ -f "$HOME/.config/secrets/.env" ]]; then
      set -a
      . "$HOME/.config/secrets/.env"
      set +a
    fi
  '';

  myShellAliases = {
    sudo = "sudo env PATH=\\\"$PATH\\\"";
    grep = "grep --color=auto";
    fgrep = "fgrep --color=auto";
    egrep = "egrep --color=auto";
    ls = "ls --color=auto";
    dir = "dir --color=auto";
    vdir = "vdir --color=auto";
  };
in
{
  # Optional per-user additions: ~/.config/cros/host/local.nix.
  imports = lib.optional (builtins.pathExists "${hostDir}/local.nix") "${hostDir}/local.nix";

  nixpkgs.config.allowUnfree = true;

  # Keep `cros apply` interruption-free. `cros reset` starts only inactive
  # services; updated running units take effect on the next login or after
  # `cros reset --hard`.
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
      openspecCli
      crosCli
      # The Antigravity app comes from llm-agents, the IDE from nixpkgs.
      antigravityHub
      antigravityIde

      # Agent tools come from one upstream package set.
      llmAgentsPkgs.antigravity-cli
      llmAgentsPkgs.claude-code
      claudeDesktopWithOpener
      llmAgentsPkgs.codex
      # OpenAI's official Linux ChatGPT (Codex) desktop app.
      chatgptWayland
      # OpenCode V2 supersedes the V1 `opencode` CLI.
      llmAgentsPkgs.opencode2
      orcaWithDesktopIdentity

      # nixpkgs' build ships ai.opencode.desktop.desktop, matching its window
      # class; llm-agents' opencode2-desktop names its entry differently.
      opencode-desktop

      # Fonts
      nerd-fonts.jetbrains-mono
      noto-fonts
      google-fonts
    ];
    sessionVariables = desktopEnvironment // {
      NIXPKGS_ALLOW_UNFREE = "1";
      ORCA_CLI_COMMAND = "orca";
      EDITOR = "code --wait --new-window";
      VISUAL = "code --wait --new-window";
      # Agent guidance refers to the bootstrap script through this variable.
      CROS_SETUP_SCRIPT_FILE = "${host.checkout}/chromeos-dev-setup/setup.bash";
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
        "${pkgs.sommelier-rs-bin}/bin/sommelier-rs --gpu-accel ${sommelierRsDisplay}";
      Restart = "always";
      RestartSec = "5";
      Environment = [
        "RUST_LOG=info"
        "SOMMELIER_ACCELERATORS=${sommelierAccelerators}"
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
        "PATH=${sessionPathPrefix}:/nix/var/nix/profiles/default/bin:/usr/local/bin:/usr/bin:/bin"
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
    # Crostini ships garcon_host_browser.desktop in /usr/share/applications.
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

      initExtra = lib.mkMerge [
        # Debian bash stays the login shell, so a broken Nix profile still
        # opens a working terminal. Only shells started as the login shell
        # (SHELL is /bin/bash or unset) switch to the Nix zsh; `bash` typed in
        # zsh, `nix develop` (which sets SHELL to its own bash), commands
        # (`bash -ic`), and CROS_NO_ZSH=1 stay in bash. A failed exec leaves
        # interactive bash running.
        (lib.mkBefore ''
          if [[ -z "''${CROS_NO_ZSH:-}" && -z "''${BASH_EXECUTION_STRING:-}" &&
                "''${SHELL:-/bin/bash}" == /bin/bash && -x "${zshPath}" ]]; then
            export SHELL="${zshPath}"
            if shopt -q login_shell; then
              exec "$SHELL" -l
            else
              exec "$SHELL"
            fi
          fi
        '')
        ''
        # https://yazi-rs.github.io/docs/quick-start/
        function y() {
          local tmp="$(mktemp -t "yazi-cwd.XXXXXX")" cwd
          command yazi "$@" --cwd-file="$tmp"
          IFS= read -r -d ' ' cwd < "$tmp"
          [ -n "$cwd" ] && [ "$cwd" != "$PWD" ] && [ -d "$cwd" ] && builtin cd -- "$cwd"
          command rm -f -- "$tmp"
        }
        export PATH="$HOME/.local/bin:$PATH"
      ''
      ];
      bashrcExtra = ''
        if [[ -f "$HOME/.bashrc.backup" ]]; then
          . "$HOME/.bashrc.backup"
        fi
        ${loadSecrets}
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

        ${loadSecrets}
      '';

      # TODO: More options
    };

    git = {
      enable = true;
      settings = {
        core.editor = "code --wait --new-window";
        diff.tool = "vscode";
        difftool.vscode.cmd = "code --wait --new-window --diff $LOCAL $REMOTE";
        # Identity (user.name/user.email) lives in the user-owned ~/.gitconfig.
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
      Environment="PATH=${sessionPathPrefix}:/usr/local/sbin:/usr/local/bin:/usr/local/games:/usr/sbin:/usr/bin:/usr/games:/sbin:/bin"
      Environment="XDG_DATA_DIRS=%h/.nix-profile/share:%h/.local/share:%h/.local/share/flatpak/exports/share:/var/lib/flatpak/exports/share:/usr/local/share:/usr/share"
    '' + systemdEnvironment desktopEnvironment;

    "systemd/user/sommelier@.service.d/cros-sommelier-override.conf".text =
      "[Service]\n" + systemdEnvironment { SOMMELIER_ACCELERATORS = sommelierAccelerators; };
    "systemd/user/sommelier-x@.service.d/cros-sommelier-x-override.conf".text =
      "[Service]\n" + systemdEnvironment { SOMMELIER_ACCELERATORS = sommelierAccelerators; };

    # Debian's low-density drop-in sets sommelier-x@1 to 0.5; run it at 1.0.
    "systemd/user/sommelier-x@1.service.d/override.conf".text = ''
      [Service]
      Environment="SOMMELIER_SCALE=1.0"
    '';

  };

  # ChromeOS's launcher (garcon) watches ~/.local/share/applications but not
  # the Nix profile, so link each profile entry there; replaced links update
  # the launcher immediately. Icons resolve through XDG_DATA_DIRS.
  xdg.dataFile."applications" = {
    source = "${config.home.path}/share/applications";
    recursive = true;
  };

  home.file = {
    # Applies the default model policy before every Codex launch.
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
      source = config.lib.file.mkOutOfStoreSymlink "${config.home.homeDirectory}/.config/Code/User/settings.json";
    };
    ".antigravity-ide/User/settings.json" = {
      source = config.lib.file.mkOutOfStoreSymlink "${config.home.homeDirectory}/.config/Code/User/settings.json";
    };

    # ~/.agents/skills is the single home of user-scoped skills: Codex and
    # OpenCode read it directly, and OpenSpec and Orca install there. Claude
    # Code reads only top-level ~/.claude/skills/<name> entries, so expose the
    # whole directory as this plugin's skills instead of linking each skill;
    # added or removed skills follow without rerunning this setup. Claude
    # lists them as agents:<name>. registerSharedSkillsPlugin loads the plugin.
    "${sharedSkillsPluginDir}/.claude-plugin/plugin.json".text = builtins.toJSON {
      name = "agents";
      description = "User-scoped skills shared with Codex and OpenCode from ~/.agents/skills";
    };
    "${sharedSkillsPluginDir}/skills" = {
      source = config.lib.file.mkOutOfStoreSymlink "${config.home.homeDirectory}/.agents/skills";
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
        # Activation PATH contains only Home Manager's own tools, so expose Git
        # explicitly; OpenSpec 2.x refuses to scaffold skills without it.
        env \
          CI=1 \
          HOME="$tempDir/home" \
          PATH="${pkgs.git}/bin:$PATH" \
          "${openspecCli}/bin/openspec" init --tools codex --profile core --no-animation .
      )

      for existingSkill in "$openspecSkillsDir"/openspec-*; do
        if [ -e "$existingSkill" ] || [ -L "$existingSkill" ]; then
          rm -rf -- "$existingSkill"
        fi
      done

      # OpenSpec 2.x installs user-scoped skills under $HOME/.agents/skills.
      for generatedSkill in "$tempDir/home/.agents/skills"/openspec-*; do
        [ -d "$generatedSkill" ] || continue
        skillName="$(basename "$generatedSkill")"
        cp -a -- "$generatedSkill" "$openspecSkillsDir/$skillName"
      done

      rm -rf -- "$tempDir"
    '';

    # ~/.agents/AGENTS.md is the single user-owned global guidance shared by
    # Codex, Claude Code, and OpenCode. Only the links are managed here: the
    # content stays out of this public repository and out of the read-only Nix
    # store so agents can keep editing it. Existing files or foreign links are
    # left in place with a warning instead of being replaced.
    linkSharedAgentGuidance = lib.hm.dag.entryAfter ["writeBoundary"] ''
      sharedGuidance="$HOME/.agents/AGENTS.md"
      if [ ! -f "$sharedGuidance" ]; then
        echo "warning: $sharedGuidance is missing; not linking agent guidance." >&2
      else
        link_shared_guidance() {
          local guidanceLink="$1" linkTarget="$2"
          if [ -L "$guidanceLink" ] && [ "$(readlink -- "$guidanceLink")" = "$linkTarget" ]; then
            return 0
          fi
          if [ -e "$guidanceLink" ] || [ -L "$guidanceLink" ]; then
            echo "warning: $guidanceLink exists and is not a link to $sharedGuidance; leaving it unchanged." >&2
            return 0
          fi
          run mkdir -p -- "$(dirname -- "$guidanceLink")"
          run ln -s -- "$linkTarget" "$guidanceLink"
        }
        link_shared_guidance "$HOME/.codex/AGENTS.md" "../.agents/AGENTS.md"
        link_shared_guidance "$HOME/.claude/CLAUDE.md" "../.agents/AGENTS.md"
        # OpenCode V2 reads only AGENTS.md (no CLAUDE.md fallback).
        # ~/.config/opencode is two levels below $HOME.
        link_shared_guidance "$HOME/.config/opencode/AGENTS.md" "../../.agents/AGENTS.md"
      fi
    '';

    # Claude Code loads plugin directories named in CLAUDE_CODE_PLUGIN_DIRS
    # in place, so the env entry in its user settings is the only change made
    # to ~/.claude/settings.json; every other setting stays as the user left
    # it. A different existing value is left in place with a warning.
    registerSharedSkillsPlugin = lib.hm.dag.entryAfter ["writeBoundary"] ''
      claudeSettings="$HOME/.claude/settings.json"
      pluginDir="$HOME/${sharedSkillsPluginDir}"
      current=""
      if [ -e "$claudeSettings" ]; then
        if ! current="$(${pkgs.jq}/bin/jq -r '.env.CLAUDE_CODE_PLUGIN_DIRS // ""' "$claudeSettings")"; then
          echo "warning: $claudeSettings is not valid JSON; not registering the shared skills plugin." >&2
          current="invalid"
        fi
      fi
      if [ "$current" = "$pluginDir" ] || [ "$current" = "invalid" ]; then
        :
      elif [ -n "$current" ]; then
        echo "warning: $claudeSettings already sets CLAUDE_CODE_PLUGIN_DIRS=$current; leaving it unchanged." >&2
      else
        run mkdir -p -- "$(dirname -- "$claudeSettings")"
        settingsTemp="$(mktemp "$claudeSettings.XXXXXX")"
        if [ -e "$claudeSettings" ]; then
          ${pkgs.jq}/bin/jq --arg dir "$pluginDir" '.env.CLAUDE_CODE_PLUGIN_DIRS = $dir' "$claudeSettings" > "$settingsTemp"
          chmod --reference="$claudeSettings" -- "$settingsTemp"
        else
          ${pkgs.jq}/bin/jq -n --arg dir "$pluginDir" '{env: {CLAUDE_CODE_PLUGIN_DIRS: $dir}}' > "$settingsTemp"
          chmod 600 -- "$settingsTemp"
        fi
        run mv -- "$settingsTemp" "$claudeSettings"
      fi
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
    # Optional user-owned GitHub token (README: "GitHub API rate limit");
    # `!include` ignores a missing file.
    extraOptions = ''
      !include github-token.conf
    '';
    settings = {
      substituters = [
        "https://cache.nixos.org"
        "https://cache.numtide.com"
      ];
      trusted-public-keys = [
        "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
        "niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g="
      ];
    };
  };
}
