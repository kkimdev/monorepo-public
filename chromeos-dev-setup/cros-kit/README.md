# CrOS Kit

Create a ChromeOS setup USB. Currently supports ChromeOS Flex.
Experimental, x86_64 UEFI; **Secure Boot off**. Debug has passwordless root access.

## Make one USB

On x86_64 Linux 5.15+ with Nix, run from this repository's root with an
**unmounted 16 GB+ USB**. Choose by model, capacity and serial, then confirm its
device path. **The selected USB will be erased.**

```bash
nix run path:./chromeos-dev-setup/cros-kit
```

Builds a minimal offline NixOS environment and downloads the locked official Flex
image. Dependencies are pinned in [flake.lock](flake.lock) and [media.json](media/media.json).

## Install on the laptop

Keep only this setup USB connected while booting.

1. Boot the USB in **UEFI** mode. Choose **Install ChromeOS Flex** and install
   to the laptop's internal drive (**erases it**). Already using Flex? Skip this step.
2. Boot the USB again, choose **Set up CrOS Kit**, then run `cros-kit`.
   Confirm the detected disk and press Enter to shut down. Remove the USB.
   If no disk is found, check the reported reason. A fresh install may need one
   original Flex boot before retrying.
3. Boot normally into **ChromeOS Flex Configured** and complete ChromeOS setup.
   For Linux tools, follow [Crostini setup](../README.md#setup-chrome-os-crostini).

## Use

| Boot entry | Purpose |
| --- | --- |
| **ChromeOS Flex Configured** (default) | Everyday use, with saved settings and developer mode off |
| **ChromeOS Flex Debug** | Host root shell for system configuration |
| **ChromeOS Flex** (original) | Original boot, without saved system overrides |

To edit settings, boot **Debug**, press `Ctrl+Alt+F2`, log in as `chronos`, and use
`sudo`. After sign-in, Crosh (`Ctrl+Alt+T`) also supports `shell --isolated`.
Reboot into **Configured** when done.

## How it works

The verified OS stays unchanged underneath one writable root overlay. Edits are
saved on the stateful partition and shared by Debug and Configured. Existing data
and runtime mounts stay direct; verified kernel modules and firmware stay read-only.

The OS, kernel, verified root and startup script must match the validated build.
After an unvalidated OS update, saved edits stay untouched and the overlay stays
off until updated tools validate that build. Debug still enables host root.

On a validated OS, saved edits are imported into a fresh layer when needed.
Edited files and deletions override the OS; untouched files follow it. The previous
layer is kept for recovery. Original boot ignores all overrides.

Physical hardware and real A/B OS updates remain untested. Keep the original boot
entry available; firmware or signing-key changes may require reinstalling the tools.

To remove, boot the USB tools and run `cros-kit --remove`. Saved edits remain.

## Update dependencies

From the repository root:

```bash
nix run path:./chromeos-dev-setup/cros-kit#update
```

Refreshes all flake inputs (including firmware sources) and media locks, then
builds the live image and runs all checks before publishing the locks. Rust and Cargo
dependencies follow the locked firmware source.
Unvalidated Flex releases stop the update; the OS compatibility contract stays explicit.

[Builds and validation](validation.md) ·
[Source](nix/cros-debug.patch)
