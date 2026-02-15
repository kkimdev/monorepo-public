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
          src = pkgs.lib.cleanSourceWith {
            src = ./.;
            filter = path: type:
              let base = baseNameOf path; in
              !(type == "directory" && (base == ".direnv" || base == "node_modules" || pkgs.lib.hasPrefix "result" base)) &&
              !(pkgs.lib.hasSuffix ".md" base) &&
              !(base == ".envrc" || base == "flake.lock");
          };

          # Allow network access for Playwright/Browser logic
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

            # Install dependencies
            bun install --frozen-lockfile

            # Bundle JS
            bun build ./content.js --outfile=content.js --minify
            bun build ./options.js --outfile=options.js --minify

            # Assets & Screenshots
            (xvfb-run bun tools/capture_screenshot.ts) &
            (sh tools/generate_assets.sh) &
            wait
          '';

          installPhase = ''
            mkdir -p $out
            cp content.js content.css manifest.json icon128.local.png \
               promo_small.png promo_marquee.png screenshot1.png \
               options.html options.js $out/
            (cd $out && zip -r keyboard-navigator.zip .)
          '';
        };

        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            bun
            chromium
            xvfb-run
            resvg
            zip
          ];
          shellHook = ''
            export CHROMIUM_PATH=${pkgs.chromium}/bin/chromium
          '';
        };
      });
}
