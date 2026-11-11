# Runtime files for the offline installer. Generated binaries stay in the store.
{ pkgs, efi, rootdev }:
pkgs.runCommand "cros-kit-payload" {} ''
  mkdir -p "$out/cros_kit"
  cp ${../cros_kit/__init__.py} "$out/cros_kit/__init__.py"
  cp ${../cros_kit/install.py} "$out/cros_kit/install.py"
  cp ${../cros_kit/wizard.py} "$out/cros_kit/wizard.py"
  cp ${../cros_kit/compatibility.conf} "$out/cros_kit/compatibility.conf"
  cp ${../cros_kit/rw-init.sh} "$out/cros_kit/rw-init.sh"
  cp ${efi}/crdyboot-debug.efi \
     ${efi}/crdyboot-debug.efi.sha256 \
     ${efi}/THIRD_PARTY_NOTICES.txt \
     ${efi}/build-info.json \
     "$out/cros_kit/"
  cp ${rootdev}/rootdev-compat.so "$out/cros_kit/"
  cd "$out/cros_kit"
  sha256sum rootdev-compat.so > rootdev-compat.so.sha256
  sha256sum rw-init.sh > rw-init.sh.sha256
''
