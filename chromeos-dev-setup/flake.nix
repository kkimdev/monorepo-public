{
  # Enable package caches during the first build, before Home Manager writes
  # the user's Nix configuration.
  nixConfig = {
    extra-substituters = [
      "https://cache.numtide.com"
    ];
    extra-trusted-public-keys = [
      "niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g="
    ];
  };

  inputs = {
    # One nixpkgs for everything: the official NixOS/nixpkgs nixpkgs-unstable
    # that llm-agents pins. Its binary cache then covers the agent packages, and
    # `cros bump llm-agents` (or a full `cros bump`) advances nixpkgs too.
    nixpkgs.follows = "llm-agents/nixpkgs";
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

    # LLM agent packages are sourced from the upstream Numtide collection. It
    # keeps its own nixpkgs input (see `nixpkgs` above) so its cache stays usable.
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

    # Per-user data. `cros` overrides it with ~/.config/cros/host; the example
    # keeps evaluation pure and gives CI a user named "ci".
    host = {
      url = "path:./host-example";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, home-manager, nix-index-database, sommelier-rs, kakaotalk, host, ... }@inputs:
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
          { programs.nix-index-database.comma.enable = true; }
        ];
      };
    };
}
