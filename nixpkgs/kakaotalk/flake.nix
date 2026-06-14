{
  description = "KakaoTalk custom packages for monorepo-public";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in {
      overlays.default = final: prev: {
        kakaotalk-bin = prev.callPackage ./package.nix {};
      };

      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs {
            inherit system;
            config.allowUnfree = true;
            overlays = [ self.overlays.default ];
          };
        in {
          default = pkgs.kakaotalk-bin;
          kakaotalk-bin = pkgs.kakaotalk-bin;
        }
      );
    };
}
