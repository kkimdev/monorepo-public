{
  description = "KakaoTalk messaging application, wrapped in Wine for Linux";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "i686-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      pkgsFor = system: import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
    in {
      packages = forAllSystems (system: {
        default = (pkgsFor system).callPackage ./package.nix {};
      });

      overlays.default = final: prev: {
        kakaotalk = prev.callPackage ./package.nix {};
      };
    };
}
