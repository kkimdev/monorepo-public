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

          nativeBuildInputs = [ pkgs.bun pkgs.zip pkgs.resvg ];

          buildPhase = ''
            export HOME=$TMPDIR
            bun build ./content.js --outfile=content.js --minify

            # Generate 128x128 PNG icon from SVG
            cat <<EOF > icon.svg
            <svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 24 24">
              <path fill="#ffd700" d="M19 7H5c-1.1 0-2 .9-2 2v6c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V9c0-1.1-.9-2-2-2zm0 8H5V9h14v6zM7 10h2v2H7v-2zm0 3h2v2H7v-2zm3-3h2v2h-2v-2zm0 3h4v2h-4v-2zm5-3h2v2h-2v-2zm0 3h2v2h-2v-2zm-3-3h2v2h-2v-2z"/>
              <rect x="3" y="7" width="18" height="10" rx="2" fill="none" stroke="black" stroke-width="0.5"/>
            </svg>
            EOF
            resvg icon.svg icon128.png --width 128 --height 128
          '';

          installPhase = ''
            mkdir -p $out
            cp content.js $out/
            cp content.css $out/
            cp manifest.json $out/
            cp icon128.png $out/
            (cd $out && zip -r keyboard-navigator.zip .)
          '';
        };

        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            bun
          ];
        };
      });
}
