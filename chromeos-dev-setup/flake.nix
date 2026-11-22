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

    sommelier-rs = {
      # virtwl releases >= 0.2.6 provide Wine's compositor v4 compatibility.
      url = "github:kkimdev/sommelier-rs/virtwl";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Same repository: a package change ships with its commit, no lock bump.
    kakaotalk = {
      url = "path:../nixpkgs/kakaotalk";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    codex-desktop-linux = {
      url = "github:ilysenko/codex-desktop-linux";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # llm-agents.nix has the Antigravity CLI and hub but no IDE.
    antigravity-nix = {
      url = "github:jacopone/antigravity-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Per-user data. `cros` overrides it with ~/.config/cros/host; the example
    # keeps evaluation pure and gives CI a user named "ci".
    host = {
      url = "path:./host-example";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, home-manager, nix-index-database, sommelier-rs, kakaotalk, codex-desktop-linux, antigravity-nix, host, ... }@inputs:
    let
      system = "x86_64-linux";
      hostData = builtins.fromJSON (builtins.readFile "${host}/host.json");

      # Tools that must build before any host data or overlay input exists.
      basePkgs = nixpkgs.legacyPackages.${system};

      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
        overlays = [
          sommelier-rs.overlays.default
          kakaotalk.overlays.default
          antigravity-nix.overlays.default
        ];
      };
    in {
      packages.${system} = {
        cros = basePkgs.callPackage ./cros { };
        system-deb = basePkgs.callPackage ./system { };
      };

      homeConfigurations.${hostData.username} = home-manager.lib.homeManagerConfiguration {
        inherit pkgs;
        extraSpecialArgs = {
          inherit inputs hostData;
          hostDir = host;
        };
        modules = [
          ./home.nix
          nix-index-database.homeModules.default
          codex-desktop-linux.homeManagerModules.default
          { programs.nix-index-database.comma.enable = true; }
        ];
      };
    };
}
