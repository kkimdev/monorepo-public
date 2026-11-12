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
      efi = import ./nix/efi.nix {
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
      rootdev = import ./nix/rootdev-compat.nix { inherit pkgs; };
      payload = import ./nix/payload.nix { inherit pkgs efi rootdev; };
      media = import ./nix/media.nix { inherit pkgs; };
      live = import ./nix/live.nix { inherit pkgs nixpkgs payload; };
      tools = with pkgs; [ python3 grub2_efi mtools util-linux ];
      kit = pkgs.writeShellApplication {
        name = "cros-kit";
        runtimeInputs = tools;
        text = ''
          export PYTHONPATH=${self}
          export CROS_KIT_FLEX_ARCHIVE=${media.flex-archive}
          export CROS_KIT_LIVE=${live}
          exec python3 -P -m cros_kit.make_usb "$@"
        '';
      };
      update = pkgs.writeShellApplication {
        name = "cros-kit-update";
        runtimeInputs = with pkgs; [ python3 gitMinimal ];
        text = ''
          export PYTHONPATH=${self}
          exec python3 -P -m cros_kit.update "$@"
        '';
      };
    in {
      packages.${system} = media // { inherit efi rootdev payload live; default = kit; };
      apps.${system} = {
        default = { type = "app"; program = "${kit}/bin/cros-kit"; meta.description = "Create a locked CrOS Kit USB"; };
        update = { type = "app"; program = "${update}/bin/cros-kit-update"; meta.description = "Refresh and validate CrOS Kit dependencies"; };
      };
      checks.${system} = {
        efi-policy = efi.policyCheck;
        payload = pkgs.runCommand "cros-kit-payload-check" {} ''
          cd ${payload}/cros_kit
          ${pkgs.coreutils}/bin/sha256sum --check crdyboot-debug.efi.sha256 rw-init.sh.sha256 rootdev-compat.so.sha256
          touch "$out"
        '';
        unit = pkgs.runCommand "cros-kit-unit-tests" {
          nativeBuildInputs = with pkgs; [ python3 gcc bash gnutar coreutils findutils gnugrep gnused gawk ];
        } ''
          cd ${self}
          export PYTHONDONTWRITEBYTECODE=1
          export PYTHONPATH=${self}
          export CROS_KIT_PAYLOAD=${payload}/cros_kit
          python3 -m unittest discover -s tests -v
          touch "$out"
        '';
        offline = pkgs.runCommand "cros-kit-offline-check" {
          nativeBuildInputs = [ pkgs.libarchive pkgs.util-linux ];
        } ''
          test -s ${live}/image.iso
          test -s ${live}/kernel-params
          test "$(blkid -p -s TYPE -o value ${live}/image.iso)" = iso9660
          test "$(blkid -p -s LABEL -o value ${live}/image.iso)" = CROSKIT_LIVE
          bsdtar -tf ${live}/image.iso > files
          grep -Fx boot/vmlinuz files
          grep -Fx boot/initrd files
          grep -Fx nix-store.squashfs files
          ${live.config.system.path}/bin/cros-kit --help > /dev/null
          test -x ${live.config.system.path}/bin/poweroff
          test -x ${live.config.system.path}/bin/reboot
          test -f ${live.config.system.path}/share/dbus-1/system-services/org.freedesktop.login1.service
          touch "$out"
        '';
      };
      devShells.${system}.default = pkgs.mkShell {
        packages = tools ++ [ pkgs.gcc ];
        CROS_KIT_PAYLOAD = "${payload}/cros_kit";
      };
    };
}
