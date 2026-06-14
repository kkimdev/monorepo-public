{ lib
, libgbm
, libxkbcommon
, pkg-config
, rustPlatform
, src
}:

rustPlatform.buildRustPackage rec {
  pname = "sommelier-rs";
  version = "0.1.1";

  inherit src;

  cargoLock = {
    lockFile = "${src}/Cargo.lock";
  };

  nativeBuildInputs = [ pkg-config ];

  buildInputs = [
    libgbm
    libxkbcommon
  ];

  postInstall = ''
    ln -s sommelier $out/bin/sommelier-rs
  '';

  meta = with lib; {
    description = "A Wayland compositor for running Wayland applications inside Wine/virtwl";
    homepage = "https://github.com/google/sommelier-rs";
    license = licenses.asl20;
    platforms = platforms.linux;
    mainProgram = "sommelier";
  };
}
