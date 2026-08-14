{ lib
, libdrm
, libgbm
, libxkbcommon
, pkg-config
, rustPlatform
, src
}:

rustPlatform.buildRustPackage rec {
  pname = "sommelier-rs";
  version = "0.2.1";

  inherit src;

  cargoLock = {
    lockFile = "${src}/Cargo.lock";
  };

  # The FD ownership regression test intentionally reserves a high descriptor.
  # Serialize workspace test binaries so Nix's cargoCheckHook cannot race it
  # against another binary that reuses the same descriptor number.
  dontUseCargoParallelTests = true;
  # This derivation ships the compositor binary; the GitHub CI matrix runs the
  # sample GUI and code generator tests separately.
  cargoTestFlags = [ "-p" "sommelier" "--bin" "sommelier" ];

  nativeBuildInputs = [ pkg-config ];

  buildInputs = [
    libdrm
    libgbm
    libxkbcommon
  ];

  postInstall = ''
    ln -s sommelier $out/bin/sommelier-rs
  '';

  meta = with lib; {
    description = "A Wayland compositor for running Wayland applications inside Wine/virtwl";
    homepage = "https://github.com/kkimdev/sommelier-rs";
    license = licenses.asl20;
    platforms = platforms.linux;
    mainProgram = "sommelier";
  };
}
