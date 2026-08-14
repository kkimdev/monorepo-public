{
  description = "Relocated, native Nix package for the Orca IDE Debian payload";

  inputs = {
    nixpkgs.url = "https://flakehub.com/f/DeterminateSystems/nixpkgs-weekly/0.1";
  };

  outputs =
    { self, nixpkgs }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in
    {
      overlays.default = final: prev: {
        orca-ide = prev.callPackage ./package.nix { };
      };

      packages = forAllSystems (system: {
        default = self.packages.${system}.orca-ide;
        orca-ide = (import nixpkgs {
          inherit system;
          overlays = [ self.overlays.default ];
        }).orca-ide;
      });
    };
}
