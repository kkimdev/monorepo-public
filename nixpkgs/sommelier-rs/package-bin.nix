{ lib
, fetchurl
, libgbm
, libxkbcommon
, makeWrapper
, stdenv
}:

let
  targetCpu = {
    "x86_64-linux"  = "x86_64";
    "aarch64-linux" = "aarch64";
  }.${stdenv.hostPlatform.system} or (throw "Unsupported system architecture: ${stdenv.hostPlatform.system}");

  shaMap = {
    "x86_64"  = "sha256-zztkv98J8hpVXCXfUnjSaPKaPua44e56yyO/jUwtFzY";
    "aarch64" = "sha256-PLACE_ARM64_SHA256_HERE_IF_AVAILABLE";
  };
in
stdenv.mkDerivation rec {
  pname = "sommelier-rs";
  version = "0.1.1";

  src = fetchurl {
    url = "https://github.com/google/sommelier-rs/releases/download/virtwl-v0.1.1/sommelier_rs_virtwl-v0.1.1-${targetCpu}";
    sha256 = shaMap.${targetCpu};
  };

  dontUnpack = true;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    mkdir -p $out/bin
    cp $src $out/bin/sommelier-rs
    chmod +x $out/bin/sommelier-rs
  '';

  postFixup = ''
    patchelf --set-interpreter $(cat ${stdenv.cc}/nix-support/dynamic-linker) \
      --set-rpath ${lib.makeLibraryPath [ libxkbcommon libgbm ]} \
      $out/bin/sommelier-rs
  '';

  meta = with lib; {
    description = "Sommelier-RS Wayland Compositor (prebuilt binary)";
    homepage = "https://github.com/google/sommelier-rs";
    license = licenses.asl20;
    platforms = [ "x86_64-linux" "aarch64-linux" ];
    mainProgram = "sommelier-rs";
  };
}
