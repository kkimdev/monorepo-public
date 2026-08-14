{ lib
, fetchurl
, libdrm
, libgbm
, libxkbcommon
, patchelf
, stdenv
}:

let
  # Keep the prebuilt package on the last verified upstream release until the
  # personal virtwl-v0.2.1 release exists and its SRI hashes are recorded.
  targetCpu = {
    "x86_64-linux"  = "x86_64";
    "aarch64-linux" = "aarch64";
  }.${stdenv.hostPlatform.system} or (throw "Unsupported system architecture: ${stdenv.hostPlatform.system}");

  shaMap = {
    "x86_64"  = "sha256-TkZjFjdUXdJx0BBjv+aEYJ0jFD9dNrha7aash/HQ70A";
    "aarch64" = "sha256-uo2JJGGPuQonh3i3jwlktSPX3JPUHw9NQG0xpQ/VlOE";
  };
in
stdenv.mkDerivation rec {
  pname = "sommelier-rs";
  version = "0.2.0";

  src = fetchurl {
    url = "https://github.com/google/sommelier-rs/releases/download/virtwl-v0.2.0/sommelier_rs_virtwl-v0.2.0-${targetCpu}";
    sha256 = shaMap.${targetCpu};
  };

  dontUnpack = true;

  nativeBuildInputs = [ patchelf ];

  installPhase = ''
    mkdir -p $out/bin
    cp $src $out/bin/sommelier-rs
    chmod +x $out/bin/sommelier-rs
  '';

  postFixup = ''
    patchelf --set-interpreter $(cat ${stdenv.cc}/nix-support/dynamic-linker) \
      --set-rpath ${lib.makeLibraryPath [ libdrm libgbm libxkbcommon ]} \
      $out/bin/sommelier-rs
  '';

  meta = with lib; {
    description = "Sommelier-RS Wayland Compositor (prebuilt binary)";
    homepage = "https://github.com/kkimdev/sommelier-rs";
    license = licenses.asl20;
    platforms = [ "x86_64-linux" "aarch64-linux" ];
    mainProgram = "sommelier-rs";
  };
}
