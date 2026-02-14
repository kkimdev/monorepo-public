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
        packages.default = pkgs.stdenv.mkDerivation rec {
          name = "keyboard-navigator";
          src = ./.;

          # Allow network access for Playwright browser install
          __noChroot = true;

          nativeBuildInputs = [
            pkgs.bun
            pkgs.zip
            pkgs.resvg
            pkgs.xvfb-run
            pkgs.chromium
          ];

          buildPhase = ''
            export HOME=$TMPDIR
            export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
            export CHROMIUM_PATH=${pkgs.chromium}/bin/chromium

            # Install npm deps
            bun install --frozen-lockfile

            # Bundle extension JS
            bun build ./content.js --outfile=content.js --minify

            # Parallel tasks: Asset generation and Screenshot capture
            (
              # Generate Real Screenshot (1280x800) via Playwright using Nix Chromium
              xvfb-run bun ./capture_screenshot.ts
            ) &

            (
              # Generate Icon (128x128)
              cat <<EOF > icon.svg
              <svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 24 24">
                <rect width="24" height="24" fill="#ffd700" />
                <path fill="#000" d="M19 7H5c-1.1 0-2 .9-2 2v6c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V9c0-1.1-.9-2-2-2zm0 8H5V9h14v6zM7 10h2v2H7v-2zm0 3h2v2H7v-2zm3-3h2v2h-2v-2zm0 3h4v2h-4v-2zm5-3h2v2h-2v-2zm0 3h2v2h-2v-2zm-3-3h2v2h-2v-2z"/>
              </svg>
EOF
              resvg icon.svg icon128.png --width 128 --height 128
            ) &

            (
              # Generate Small Promo Tile (440x280)
              cat <<EOF > promo_small.svg
              <svg xmlns="http://www.w3.org/2000/svg" width="440" height="280" viewBox="0 0 440 280">
                <rect width="440" height="280" fill="#ffd700" />
                <g transform="translate(156, 76)">
                  <path fill="#000" d="M114 42H30c-6.6 0-12 5.4-12 12v36c0 6.6 5.4 12 12 12h84c6.6 0 12-5.4 12-12V54c0-6.6-5.4-12-12-12zm0 48H30V54h84v36zM42 60h12v12H42V60zm0 18h12v12H42v-12zm18-18h12v12H60V60zm0 18h24v12H60v-12zm30-18h12v12H90V60zm0 18h12v12H90v-12zm-18-18h12v12H72V60z" transform="scale(0.8)"/>
                </g>
                <text x="50%" y="220" text-anchor="middle" font-family="monospace" font-weight="bold" font-size="24" fill="#000">KEYBOARD NAVIGATOR</text>
              </svg>
EOF
              resvg promo_small.svg promo_small.png
            ) &

            (
              # Generate Marquee Promo Tile (1400x560)
              cat <<EOF > promo_marquee.svg
              <svg xmlns="http://www.w3.org/2000/svg" width="1400" height="560" viewBox="0 0 1400 560">
                <rect width="1400" height="560" fill="#ffd700" />
                <g transform="translate(560, 140) scale(2)">
                  <path fill="#000" d="M114 42H30c-6.6 0-12 5.4-12 12v36c0 6.6 5.4 12 12 12h84c6.6 0 12-5.4 12-12V54c0-6.6-5.4-12-12-12zm0 48H30V54h84v36zM42 60h12v12H42V60zm0 18h12v12H42v-12zm18-18h12v12H60V60zm0 18h24v12H60v-12zm30-18h12v12H90V60zm0 18h12v12H90v-12zm-18-18h12v12H72V60z" />
                </g>
                <text x="50%" y="460" text-anchor="middle" font-family="monospace" font-weight="bold" font-size="60" fill="#000">KEYBOARD NAVIGATOR</text>
              </svg>
EOF
              resvg promo_marquee.svg promo_marquee.png
            ) &

            wait
          '';

          installPhase = ''
            mkdir -p $out
            cp content.js $out/
            cp content.css $out/
            cp manifest.json $out/
            cp icon128.png $out/
            cp promo_small.png $out/
            cp promo_marquee.png $out/
            cp screenshot1.png $out/
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
