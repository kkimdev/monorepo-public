# Local Debian package for the root-owned configuration that must survive
# without Nix: the adb udev rule, the disk-backed /tmp policy, and Debian's
# security and stable-updates sources.
#
# The package version embeds a hash of this whole directory: the tree
# (including DEBIAN/ scripts and the control template) and this recipe, which
# sets file modes and compression. Any change makes `cros apply` reinstall it.
{
  lib,
  runCommand,
  dpkg,
}:

let
  # builtins.path gives this directory its own store path even when this file
  # is evaluated as part of a flake, so the hash covers exactly system/.
  src = builtins.path {
    path = ./.;
    name = "cros-dev-system-src";
  };
  tree = "${src}/tree";

  version = "1+" + builtins.substring 0 12 (builtins.baseNameOf "${src}");
in
runCommand "cros-dev-system.deb"
  {
    nativeBuildInputs = [ dpkg ];
  }
  ''
    set -euo pipefail

    package="$TMPDIR/cros-dev-system"
    cp -a ${tree} "$package"
    chmod -R u+w "$package"

    sed -i 's/@VERSION@/${version}/' "$package/DEBIAN/control"

    # Vendor files are data, never executables; keep the package reproducible.
    find "$package" -type d -exec chmod 0755 {} +
    find "$package" -type f -exec chmod 0644 {} +
    chmod 0755 "$package/DEBIAN/postinst" "$package/DEBIAN/postrm"

    mkdir -p "$out"
    dpkg-deb --root-owner-group -Zxz --build "$package" "$out/cros-dev-system.deb"
  ''
