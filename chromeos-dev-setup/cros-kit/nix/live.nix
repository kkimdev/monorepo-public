# A live filesystem for GPT13. The existing Flex ESP owns firmware booting.
{ pkgs, nixpkgs, payload }:
let
  launcher = pkgs.writeShellApplication {
    name = "cros-kit";
    runtimeInputs = [ pkgs.python3 pkgs.util-linux pkgs.efibootmgr pkgs.systemd ];
    text = ''
      export PYTHONPATH=${payload}
      exec python3 -P -m cros_kit.wizard "$@"
    '';
  };
  live = nixpkgs.lib.nixosSystem {
    system = pkgs.stdenv.hostPlatform.system;
    modules = [
      ({ config, lib, modulesPath, utils, ... }: {
        imports = [
          (modulesPath + "/profiles/minimal.nix")
          (modulesPath + "/installer/cd-dvd/iso-image.nix")
        ];
        nixpkgs.pkgs = pkgs;
        system.stateVersion = "26.11";
        networking.hostName = "cros-kit";

        # This is an offline appliance, not a NixOS installation medium.
        nix.enable = false;
        systemd.services.register-nix-paths.enable = false;
        networking.useDHCP = false;
        networking.dhcpcd.enable = false;
        networking.firewall.enable = false;
        services.timesyncd.enable = false;
        documentation.enable = false;
        fonts.fontconfig.enable = false;
        # Glibc supplies C.UTF-8 without a separately linked locale archive.
        i18n.defaultLocale = "C.UTF-8";
        i18n.glibcLocales = null;
        programs.nano.enable = false;
        security.sudo.enable = false;
        environment.defaultPackages = [ ];
        # Let OS modules supply essential tools and their service registrations.
        environment.systemPackages = [ launcher ];

        # NixOS keeps /nix/store mounted until it pivots into shutdown ramfs.
        # Keep the overlay's backing mounts for that same phase: stopping them
        # at umount.target leaves the ISO busy while /nix/store still uses it.
        systemd.units = lib.genAttrs
          (map (path: "${utils.escapeSystemdPath path}.mount") [
            "/iso" "/nix/.ro-store" "/nix/.rw-store"
          ])
          (_: {
            overrideStrategy = "asDropin";
            text = ''
              [Unit]
              DefaultDependencies=no
            '';
          });

        # Keep the physical console usable on unknown laptops, including NVMe
        # behind Intel VMD. No network connection or package install is needed.
        hardware.enableAllHardware = true;
        boot.initrd.availableKernelModules = [ "vmd" ];
        boot.supportedFilesystems = [ "ext4" "vfat" ];
        boot.kernelParams = [ "boot.shell_on_fail" "console=tty1" "console=ttyS0,115200" ];
        users.users.root.initialHashedPassword = "";
        services.getty.autologinUser = "root";
        services.getty.helpLine = "Run cros-kit to set up CrOS Kit (offline).";
        environment.etc."motd".text = "";

        # GPT and EFI belong to make_usb.py; emit only an ISO9660 filesystem.
        isoImage = {
          volumeID = "CROSKIT_LIVE";
          makeBiosBootable = false;
          makeEfiBootable = false;
          makeUsbBootable = false;
          showConfiguration = false;
          grubTheme = null;
          contents = [
            {
              source = "${config.boot.kernelPackages.kernel}/${config.system.boot.loader.kernelFile}";
              target = "/boot/vmlinuz";
            }
            {
              source = "${config.system.build.initialRamdisk}/${config.system.boot.loader.initrdFile}";
              target = "/boot/initrd";
            }
          ];
        };
      })
    ];
  };
  config = live.config;
  params = "init=${config.system.build.toplevel}/init ${toString config.boot.kernelParams}";
in
pkgs.runCommand "cros-kit-live" {
  __structuredAttrs = true;
  passthru = { inherit config; };
  # The ISO embeds its complete runtime closure; the init path in kernel-params
  # must not retain a second, uncompressed copy as a Nix output dependency.
  unsafeDiscardReferences.out = true;
} ''
  mkdir -p "$out"
  cp ${config.system.build.isoImage}/iso/*.iso "$out/image.iso"
  printf '%s\n' ${pkgs.lib.escapeShellArg params} > "$out/kernel-params"
''
