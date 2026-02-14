{
  description = "Keyboard Navigator Chrome Extension";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        packages.default = pkgs.stdenv.mkDerivation {
          name = "keyboard-navigator";
          src = ./.;

          nativeBuildInputs = [ pkgs.bun ];

          buildPhase = ''
            export HOME=$TMPDIR
            bun build ./content.js --outfile=content.js --minify
          '';

          installPhase = ''
            mkdir -p $out
            cp content.js $out/
            cp content.css $out/
            cp manifest.json $out/
          '';
        };

        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            bun
          ];
        };
      });
}
