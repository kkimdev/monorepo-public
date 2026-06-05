{
  description = "Linux kernel development environment using latest LLVM";

  inputs = {
    nixpkgs.url = "github:Nixos/nixpkgs/nixos-unstable";
    utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, utils }:
    utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        llvm = pkgs.llvmPackages_latest;
      in
      {
        devShells.default = pkgs.mkShell {
          nativeBuildInputs = with pkgs; [
            gnumake
            flex
            bison
            pkg-config
            elfutils
            git
            openssl
            bc
            ncurses
            llvm.clang
            llvm.lld
            llvm.bintools
          ];

          hardeningDisable = [ "all" ];

          shellHook = ''
            echo "--- Latest LLVM Kernel Build Environment Loaded ---"

            # Standard LLVM Toolchain exports
            export CC=clang
            export HOSTCC=clang
            export LD=ld.lld
            export HOSTLD=ld.lld
            export AR=llvm-ar
            export NM=llvm-nm
            export STRIP=llvm-strip
            export OBJCOPY=llvm-objcopy
            export OBJDUMP=llvm-objdump
            export READELF=llvm-readelf

            # Mandatory Kernel Build Flags
            export LLVM=1
            export LLVM_IAS=1

            # NIX SPECIFIC FIX:
            # We use NIX_CFLAGS_COMPILE because the Nix wrapper prioritizes this.
            # This forces clang to ignore the unused 'nostdlibinc' flag instead of
            # letting -Werror turn it into a fatal crash.
            export NIX_CFLAGS_COMPILE="-Wno-error=unused-command-line-argument -Wno-unused-command-line-argument"

            export PKG_CONFIG_PATH="${pkgs.elfutils.dev}/lib/pkgconfig:${pkgs.ncurses.dev}/lib/pkgconfig"
          '';
        };
      });
}
