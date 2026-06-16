{
  description = "Sommelier-RS custom packages for monorepo-public";

  inputs = {
    nixpkgs.url = "https://flakehub.com/f/DeterminateSystems/nixpkgs-weekly/0.1";
    sommelier-rs-src = {
      url = "github:google/sommelier-rs/virtwl";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, sommelier-rs-src }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in {
      overlays.default = final: prev: {
        sommelier-rs = prev.callPackage ./package-source.nix {
          src = sommelier-rs-src;
        };
        sommelier-rs-bin = prev.callPackage ./package-bin.nix {};
      };

      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs {
            inherit system;
            config.allowUnfree = true;
            overlays = [ self.overlays.default ];
          };
        in {
          default = pkgs.sommelier-rs;
          sommelier-rs = pkgs.sommelier-rs;
          sommelier-rs-bin = pkgs.sommelier-rs-bin;
        }
      );
    };
}
