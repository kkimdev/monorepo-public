{
  description = "Sommelier-RS custom packages for monorepo-public";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in {
      overlays.default = final: prev: {
        sommelier-rs = prev.callPackage ./package-source.nix {};
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
