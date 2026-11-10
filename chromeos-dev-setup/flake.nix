{
  # Enable package caches during the first build, before Home Manager writes
  # the user's Nix configuration.
  nixConfig = {
    extra-substituters = [
      "https://cache.numtide.com"
      "https://codex-desktop-linux.cachix.org"
    ];
    extra-trusted-public-keys = [
      "niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g="
      "codex-desktop-linux.cachix.org-1:nX/xy6AdK9hQE24A8ALGjkCKj2ObFmcnemiL5Cid4nk="
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

    # The local native-copy workaround is verified against Orca 1.4.226.
    # Keep this input separate so other agent packages can update independently.
    orca-pinned.url = "github:numtide/llm-agents.nix/d05b753d92887cede67da8dde612c9b15549d8dc";

    sommelier-rs = {
      # virtwl releases >= 0.2.6 provide Wine's compositor v4 compatibility.
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

      # The installer serializes host-specific values without Nix interpolation.
      username = (builtins.fromJSON (builtins.readFile ./host.json)).username;

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
