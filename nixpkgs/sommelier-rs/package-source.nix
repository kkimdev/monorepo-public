{ lib
, rustPlatform
, fetchFromGitHub
, pkg-config
, libgbm
, libxkbcommon
, libdrm
}:

rustPlatform.buildRustPackage rec {
  pname = "sommelier-rs";
  version = "0.1.1";

  src = fetchFromGitHub {
    owner = "google";
    repo = "sommelier-rs";
    rev = "virtwl-v0.1.1";
    hash = "sha256-UgwN0DiurH8to28pVT23i7Jw9fbpu7ddFGmAu14Gk+0=";
  };

  cargoLock = {
    lockFile = "${src}/Cargo.lock";
  };

  nativeBuildInputs = [ pkg-config ];

  buildInputs = [
    libgbm
    libxkbcommon
    libdrm
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
