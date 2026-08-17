{
  description = "Relocated, native Nix package for the Orca IDE Debian payload";

  inputs = {
    nixpkgs.url = "https://flakehub.com/f/DeterminateSystems/nixpkgs-weekly/0.1";
  };

  outputs =
    { self, nixpkgs, ... }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      packageSet =
        system:
        import nixpkgs {
          inherit system;
          overlays = [ self.overlays.default ];
        };
    in
    {
      overlays.default = _final: prev: {
        orca-ide = prev.callPackage ./package.nix { };
      };

      packages = forAllSystems (
        system:
        let
          pkgs = packageSet system;
        in
        rec {
          inherit (pkgs) orca-ide;
          default = orca-ide;
        }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = packageSet system;
        in
        {
          inherit (pkgs) orca-ide;
          nix-quality =
            pkgs.runCommand "orca-deb-bin-nix-quality"
              {
                nativeBuildInputs = [
                  pkgs.deadnix
                  pkgs.nixfmt
                  pkgs.statix
                ];
              }
              ''
                nixfmt --check ${./flake.nix} ${./package.nix}
                deadnix --fail ${./flake.nix} ${./package.nix}
                statix check ${./flake.nix}
                statix check ${./package.nix}
                touch "$out"
              '';
        }
      );

      formatter = forAllSystems (system: (packageSet system).nixfmt-tree);
    };
}
