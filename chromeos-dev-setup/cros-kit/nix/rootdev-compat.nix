{ pkgs }:
pkgs.stdenv.mkDerivation {
  pname = "flex-rootdev-compat";
  version = "1";
  src = ./rootdev-compat.c;
  dontUnpack = true;
  dontFixup = true;
  buildPhase = ''
    # No host libc, interpreter, RPATH or symbol-version requirement is linked
    # in. The target's original library supplies libc and all unchanged APIs.
    export NIX_LDFLAGS=""
    # This absolute SONAME names the target's root tree, not a build input.
    export NIX_ENFORCE_PURITY=0
    flags="-shared -nostdlib -fPIC -Os -fno-ident -fno-stack-protector"
    flags="$flags -Wl,--build-id=none,-z,relro,-z,now,-z,noexecstack"
    printf 'void flex_stock_link_anchor(void) {}\n' > stock.c
    $CC $flags -Wl,-soname,/usr/lib64/flex-debug/librootdev-stock.so.1 \
      stock.c -o stock.so
    $CC $flags -Wall -Wextra -Werror -fvisibility=hidden \
      -Wl,-soname,librootdev.so.1 -Wl,--no-as-needed \
      "$src" ./stock.so -o rootdev-compat.so
    $STRIP --strip-all rootdev-compat.so
  '';
  installPhase = ''
    mkdir -p "$out"
    cp rootdev-compat.so "$out/"
  '';
}
