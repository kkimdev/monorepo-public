{
  description = "CrOS Kit: pinned ChromeOS USB and firmware tools";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    rust-overlay.url = "github:oxalica/rust-overlay";
    rust-overlay.inputs.nixpkgs.follows = "nixpkgs";
    crdyboot = {
      url = "git+https://chromium.googlesource.com/chromiumos/platform/crdyboot?ref=refs/heads/main&shallow=1";
      flake = false;
    };
    vboot = {
      url = "git+https://chromium.googlesource.com/chromiumos/platform/vboot_reference?ref=refs/heads/main&shallow=1";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, rust-overlay, crdyboot, vboot }:
    let
      system = "x86_64-linux";
      locked = (builtins.fromJSON (builtins.readFile ./flake.lock)).nodes;
      pkgs = import nixpkgs { inherit system; config = {}; overlays = [ rust-overlay.overlays.default ]; };
      rustChannel = (builtins.fromTOML (builtins.readFile "${crdyboot}/rust-toolchain.toml")).toolchain.channel;
      toolchain = pkgs.rust-bin.fromRustupToolchain {
        channel = rustChannel;
        targets = [ "x86_64-unknown-uefi" ];
      };
      efi = import ./efi.nix {
        inherit pkgs crdyboot vboot;
        rustToolchain = toolchain;
        provenance = {
          nixpkgs = locked.nixpkgs.locked;
          rust-overlay = locked.rust-overlay.locked;
          crdyboot = locked.crdyboot.locked;
          vboot = locked.vboot.locked;
          rust_toolchain = rustChannel;
        };
      };
      rootdev = import ./rootdev-compat.nix { inherit pkgs; };
      media = import ./media.nix { inherit pkgs; };
      tools = with pkgs; [ python3 grub2_efi mtools dosfstools libarchive apk-tools util-linux ];
      kit = pkgs.writeShellApplication {
        name = "cros-kit";
        runtimeInputs = tools;
        text = ''
          export CROS_KIT_FLEX_ARCHIVE=${media.flex-archive}
          export CROS_KIT_ALPINE_ISO=${media.alpine-iso}
          export CROS_KIT_ALPINE_REPO=${media.alpine-repo}
          exec python3 ${self}/make_usb.py "$@"
        '';
      };
      update = pkgs.writeShellApplication {
        name = "cros-kit-update";
        runtimeInputs = with pkgs; [ python3 apk-tools libarchive gitMinimal ];
        text = ''exec python3 ${self}/update.py "$@"'';
      };
    in {
      packages.${system} = media // { inherit efi rootdev; default = kit; };
      apps.${system} = {
        default = { type = "app"; program = "${kit}/bin/cros-kit"; meta.description = "Create a locked CrOS Kit USB"; };
        update = { type = "app"; program = "${update}/bin/cros-kit-update"; meta.description = "Refresh and validate CrOS Kit dependencies"; };
      };
      checks.${system} = {
        payload = pkgs.runCommand "cros-kit-payload-check" {} ''
          cd ${self}
          ${pkgs.coreutils}/bin/sha256sum --check crdyboot-debug.efi.sha256 rw-init.sh.sha256 rootdev-compat.so.sha256
          cmp crdyboot-debug.efi ${efi}/crdyboot-debug.efi
          cmp build-info.json ${efi}/build-info.json
          cmp THIRD_PARTY_NOTICES.txt ${efi}/THIRD_PARTY_NOTICES.txt
          cmp rootdev-compat.so ${rootdev}/rootdev-compat.so
          touch "$out"
        '';
        unit = pkgs.runCommand "cros-kit-unit-tests" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          cd ${self}
          export PYTHONDONTWRITEBYTECODE=1
          python3 -m unittest test_build test_install test_make_usb test_wizard test_update test_media
          touch "$out"
        '';
        offline = pkgs.runCommand "cros-kit-offline-check" {
          nativeBuildInputs = [ pkgs.python3 pkgs.apk-tools pkgs.libarchive ];
        } ''
          export PYTHONDONTWRITEBYTECODE=1
          cd ${self}
          python3 - <<'PYCODE'
from pathlib import Path
import os
import usb_live
usb_live.prepare_live("${media.alpine-iso}", Path(os.environ["TMPDIR"]) / "live",
                      Path.cwd(), "${media.alpine-repo}")
PYCODE
          touch "$out"
        '';
      };
      devShells.${system}.default = pkgs.mkShell {
        packages = tools ++ [ pkgs.gcc ];
      };
    };
}
