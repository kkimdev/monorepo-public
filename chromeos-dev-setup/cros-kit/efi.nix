# Hermetic unsigned EFI template. All source/tool inputs come from flake.lock.
{ pkgs, rustToolchain, crdyboot, vboot, provenance ? {} }:
let
  llvm = pkgs.llvmPackages_21;
  rustPlatform = pkgs.makeRustPlatform { cargo = rustToolchain; rustc = rustToolchain; };
  target = "x86_64-unknown-uefi";
  cargoHash = "sha256-MCR4TFdlwLO7MsrDTXAwt9v59YOraeDxJi0KZ86//hw=";
  cargoDeps = rustPlatform.fetchCargoVendor {
    pname = "crdyboot";
    version = "locked";
    src = crdyboot;
    hash = cargoHash;
  };
  buildProvenance = pkgs.writeText "cros-kit-efi-provenance.json" (builtins.toJSON {
    inputs = provenance;
    target = target;
    cargo_vendor_hash = cargoHash;
    patch_sha256 = builtins.hashFile "sha256" ./cros-debug.patch;
    recipe_sha256 = builtins.hashFile "sha256" ./efi.nix;
    helper_sha256 = builtins.hashFile "sha256" ./build.py;
  });
in
pkgs.stdenv.mkDerivation {
  pname = "cros-kit-efi";
  version = "1";
  src = crdyboot;
  patches = [ ./cros-debug.patch ];
  patchFlags = [ "-p1" "--fuzz=0" ];
  inherit cargoDeps;
  nativeBuildInputs = [
    rustToolchain
    rustPlatform.cargoSetupHook
    llvm.clang
    llvm.llvm
    pkgs.gnumake
    pkgs.python3
  ];

  # vboot fwlib is freestanding firmware; it has no host stack-protector runtime.
  CC = "${llvm.clang.cc}/bin/clang";
  AR = "${llvm.llvm}/bin/llvm-ar";
  LIBCLANG_PATH = "${llvm.libclang.lib}/lib";
  SOURCE_DATE_EPOCH = "0";
  CARGO_INCREMENTAL = "0";
  CARGO_NET_OFFLINE = "true";
  hardeningDisable = [ "all" ];
  dontConfigure = true;
  dontFixup = true;
  enableParallelBuilding = true;
  strictDeps = true;

  postPatch = ''
    cp -r ${vboot} third_party/vboot_reference
    chmod -R u+w third_party/vboot_reference
  '';

  buildPhase = ''
    runHook preBuild
    # stdenv normalizes unpacked source timestamps; enforce the chosen epoch
    # again for upstream build scripts and the compiler.
    export SOURCE_DATE_EPOCH=0
    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    # Keep absolute build and vendor paths out of the PE image.
    export RUSTFLAGS="--remap-path-prefix=$NIX_BUILD_TOP=/build --remap-path-prefix=$PWD=/build/source -C link-arg=/debug:none -C link-arg=/timestamp:0"
    cargo build --release --frozen --offline --package crdyboot \
      --target ${target} --jobs "$NIX_BUILD_CORES"
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    python3 ${./build.py} --source "$PWD" \
      --artifact target/${target}/release/crdyboot.efi \
      --output "$out" --provenance ${buildProvenance}
    runHook postInstall
  '';
}
