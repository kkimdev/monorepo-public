# Validation

## Current design

The signed kernel and verified OS remain unchanged. After native ChromeOS startup
mounts stateful storage, `rw-init.sh` overlays `/` and moves existing data/runtime
mounts onto it. `/var`, `/home`, stateful storage and temporary files keep their
original filesystems and mount restrictions. Original boot skips the overlay;
writes to shared data are still shared.

Saved OS changes live in `/mnt/stateful_partition/flex-debug/root/upper`.
There is no list of supported settings paths.
The Flex backend retains its installed `flex-debug` paths across the CrOS Kit rename
so existing saved changes, boot entries and removal still work.

[compatibility.conf](compatibility.conf) is the single reference for the validated
OS version, kernel release, verified-root digest and original startup-script hash.
Both readers treat it as data; malformed, duplicate or missing values are rejected.
The updater refuses an unvalidated stable release before downloading images.
The USB maker uses only the locked, validated release. Installation checks eligible A/B layout and keyblock/filesystem
headers, then inspects the highest-priority slots (including ties) read-only for version, board,
startup hash and kernel modules. An older lower-priority fallback does not block
tool updates. Runtime checks the actual kernel/root digest independently.

The boot checks run before wrapping startup and again after removing the temporary
`/etc/init` bind, before importing or changing saved layers. Rejection leaves the
saved upper/previous generation untouched and continues on the stock root/startup.
The selected mode remains unchanged: Debug still enables host root. Use the
original firmware entry for the original boot policy.

Overlay compatibility is handled separately:

- `xino=on` gives root directories and policy files consistent device identities,
  as required by Minijail. Indexing, metacopy and directory redirects stay disabled.
- A fingerprint covers the verified root digest, original root device, runtime,
  shim and compatibility contract. When it changes on a validated OS, saved files
  are imported into a fresh upper/work tree.
  Sparse files, hardlinks, modes, whiteouts and opaque directories survive; stale
  lower identity xattrs are excluded. Activation uses renames and keeps `root.previous`.
- A tool-owned compatibility lower contains the [rootdev shim](rootdev-compat.c),
  this OS's original rootdev library and original root device number. The shim
  resolves paths on the root overlay, including `/usr` and `/.`, to the verified
  device and delegates other queries to stock code.
- `/lib/modules` and `/lib/firmware` remain direct read-only binds from the verified
  root for LoadPin. Minijail policies that bind `/` or select
  `minimalistic-mountns` also receive those two binds. Other policies stay unchanged;
  the entire root bind is never made recursive. Custom policies and namespace
  builders outside these stock configuration files may need equivalent binds.

The compatibility lower is cached until the fingerprint changes. Native startup
still handles stateful/encryption/wipe sequencing before the overlay. The bootstrap
uses a private executable tmpfs, opens standard descriptors, then detaches its ESP
mount; surrounding `/tmp` retains its normal `noexec` restrictions.

## Evidence

Reference: official Flex stable **156.0.8078.93 / 16836.32.0**, downloaded
2026-11-11; recovery ZIP SHA1 `c9aee70432d3915064a412cfa0086404969be7c4`
matched Google's metadata. Guest kernel: `6.12.100-01491-g094d98275718`.
Local proof files below are under `~/.cache/flex-usb-lab/`.

VM and image evidence below predates the CrOS Kit flake conversion. Runtime,
shim and compatibility contract are unchanged. EFI was rebuilt from the same
upstream sources using the locked, sandboxed recipe; its new artifact has not
been boot-tested. Flake checks validate the current offline media tree and payloads.

The guarded production payload booted without diagnostic hooks. Live runtime,
shim and contract hashes matched source; native chronos login and sudo, SELinux,
saved files/job/backlight, rootdev aliases, zram and six expected services passed
(`guard-final-native-proof.json`, `guard-final-native.log`).

A negative VM changed only its bundled OS contract to an unvalidated version.
It reached the stock GUI/services without an overlay; Debug flags remained 1/1.
Saved current/previous upper contents, inodes, metadata and version were identical
before and after (`guard-rejected-proof.json`, `guard-saved-before.txt`,
`guard-saved-after.txt`). This diagnostic VM predates the final NUL check; unit
conformance tests and the final native boot cover that addition. It is not an
actual A/B update. The guest lacked `cmp`; exact snapshot comparison ran on host.

The offline installer fixture uses a preinstalled Flex target and a stock boot
entry seeded in Alpine. Supported installation passed; root probes were read-only
with journal replay disabled, payloads matched source, and all four stock EFI
files stayed unchanged. Unsupported-contract install and dry run both failed
before any ESP mount; whole-ESP hashes and firmware listings were unchanged
(`guardusb2-proof.json`, `guardusb2-install.log`, `guardusb2-negative.log`).
This does not prove a fresh-laptop OS installation.

Earlier review evidence remains a baseline for unchanged overlay/import/recovery
code: `review-final-roundtrip-proof.json` covers Debug to Configured, actual
isolated Crosh sudo, saved startup job and stable lower-cache inodes.
`review-fatal-proof.json` verifies service gating after forced failure before
mount changes; partial mount reversal is covered by unit tests. The older
`root-rollback-immediate.log`/`root-rollback-observed-ui.png` establish successful
post-pivot rollback and graphical startup.

The guest-kernel lower-replacement experiment used two distinct filesystems with
`xino=on`. New/untouched OS files followed the replacement; edits, deletion,
whiteouts/opaque directories, symlinks, modes and hardlinks survived, stale origins
were stripped and the previous generation remained recoverable. This is not an
OS update. Raw ACL xattrs are imported and host-tested; the guest tar lacks libacl,
so production deliberately avoids `--acls`. Sparse-file import is host-tested.

| Check | Status |
| --- | --- |
| Current complete suite with pinned Python/compiler | **PASS: 207 tests, no skips (root fixtures)** |
| Flake unit, payload and signed offline repository checks | **PASS: sandboxed checks** |
| Actual one-command dependency update | **PASS: staged rebuild/check/publication; one capability skip without root** |
| EFI offline rebuild | **PASS: identical complete Nix output** |
| Locked full USB image, 10 payloads, 64 APKs, signed index, both GPTs | **PASS: offline creation and exact readback** |
| Pre-flake installer, bootstrap, runtime, shim and USB tests | **PASS: 188 tests, no skips** |
| Python/shell contract agreement, malformed/injected inputs | **PASS: conformance tests** |
| A/B highest-priority ties, old fallback, refusal before EFI writes | **PASS: temporary-disk integration tests** |
| Final uninstrumented guarded boot and saved settings | **PASS: native Debug VM** |
| Unknown OS leaves saved layers untouched, stock GUI starts | **PASS: diagnostic VM** |
| Debug to Configured and isolated Crosh | **PASS: pre-guard VM baseline** |
| Recovery failure withholds services | **PASS: pre-guard VM baseline + current unit tests** |
| Pre-rename offline wizard, guarded install/refusal and stock EFI | **PASS: isolated offline VM** |
| Combined image payload and primary/backup GPT | **PASS: all 10 files read back exactly** |
| Actual A/B OS update and postinstall persistence | **UNRUN** |
| Existing signed-in/encrypted user account | **UNRUN** |
| Physical USB write, full byte comparison and payload/GPT readback | **PASS: SanDisk 3.2Gen1** |
| Physical laptop boot with this full-root runtime | **UNRUN** |

`guard-final-tests.log` records the suite. `combined-guard-final.img` was repackaged
from the validated combined image, replacing all ten installer files and
regenerating GRUB (`combined-guard-final-payload.json`). This is not a clean full
builder run: an earlier rebuild hit the host disk quota.

The SanDisk USB (serial `00015028051925093504`) now contains the locked CrOS Kit
image, flashed on 2026-11-12. Full byte comparison, both GPTs, ten installer files,
the signed index and all 64 APKs passed physical readback
(`cros-kit-physical-flash-final.json`, `cros-kit-physical-flash.log`). Laptop boot
remains untested. Old preview storage is not imported into the new layer format.

Current flake evidence (2026-11-12): `flake-root-tests-final.log`,
`flake-check-final.log`, `flake-update-final.log`, `flake-image-final.log` and
`flake-image-readback-final.json`. The full image uses the current published
payload bytes. Large image staging uses the output folder or user cache to avoid
small `/tmp` quotas. Physical write/readback evidence is recorded separately above.

## Updates and recovery

Boot original Flex for OS updates until the actual update path is tested. Debug
sets `debug_build=1`, affecting automatic update checks and signature policy;
Configured keeps developer mode off, but a running updater is not update proof.

An unvalidated OS update disables the saved overlay. User data and saved edits
remain; reinstall updated tools after that OS is validated. Approving a new build
requires testing it before updating the four reference values. File preservation
does not establish semantic compatibility of old edits with a new OS.

The installer keeps stock EFI files and a separate firmware entry. Overlay failure
attempts to restore stock mounts. If restoration fails, startup waits and logs a
recovery message instead of starting services with incomplete mounts. Reboot and
select original Flex. Retain the original entry for recovery; it ignores
OS overrides. Import requires room for another copy of saved changes. Edited files
and deletions continue to override the new OS, so an incompatible customization may
need manual correction. Firmware changes, signing-key rotation or loader revocation
can require reinstalling/rebuilding these tools. Future update compatibility is not
guaranteed by the current source review or VM tests.

## Manual installation

Boot a UEFI Linux live USB with Bash, Python 3, `curl`, `sudo`, `sha256sum`,
`efibootmgr` and `util-linux`.
Use `lsblk` to identify the unmounted, already installed Flex disk; replace the path:

```bash
curl -fsSL https://raw.githubusercontent.com/kkimdev/monorepo-public/main/chromeos-dev-setup/cros-kit/install.bash \
  | bash -s -- install --disk /dev/nvme0n1
```

Add `--dry-run` to validate without writing. Use `status` or `remove` in place of
`install`; removal retains saved changes and user data.

## Locked builds and updates

From this directory:

```bash
nix build path:.#efi path:.#rootdev
nix flake check path:.
nix run path:.#update
```

| Input | Lock |
| --- | --- |
| Nix tools, firmware sources, Rust component hashes | [flake.lock](flake.lock), including rust-overlay |
| Rust version, firmware revisions, Cargo dependency hash | [flake.nix](flake.nix), [efi.nix](efi.nix); upstream Cargo.lock |
| Flex ZIP, Alpine ISO and every APK | [media.json](media.json), SHA256 per file |
| Official signed repository snapshot | [alpine-index.tar.gz](alpine-index.tar.gz), hashed in media.json |
| Built EFI and exact build inputs | [build-info.json](build-info.json), adjacent artifact manifests |

Normal creation never queries latest releases. Nix fetches the exact locked bytes;
APK signature and dependency checks run offline using keys from the locked ISO.
Upstream may remove old downloads: hashes prevent silent substitutions, but do not
guarantee permanent availability. Keep a Nix cache for long-term rebuilds.

The update command refreshes nixpkgs, rust-overlay and official media in a temporary
source copy, rebuilds EFI/shim, refreshes manifests and runs flake checks plus the
full local suite. It replaces generated files only after success and refuses
concurrent source edits; replacement errors and Ctrl+C roll back completed writes.
Abrupt power loss during multi-file replacement is not transactional.

Firmware revisions, the Rust release and the OS compatibility contract remain
explicit source changes; the command never approves a new OS automatically. After
reviewing such a change, use the same command to rebuild and validate. Unit tests
and artifact checks do not replace OS boot/update testing.

EFI compilation is sandboxed with vendored Cargo dependencies and `--frozen
--offline`; Rust never downloads components during compilation. A complete offline
Nix rebuild reproduced the EFI, notices and provenance byte-for-byte. The shim also
reproduces its previous SHA256. USB images contain fresh partition UUIDs and FAT
timestamps; they are not byte-identical disk images.

The manual installer resolves `main` once and downloads all payloads from that
commit. Set `CROS_KIT_REV` to a full commit SHA to select the same payload again.

Current SHA256:

- EFI: `09a1e9127ce5de3fa1740eabc05f67379addca65e99b8c3ad336dca8810c9b8b`
- Runtime: `590f0f70786d893bcbb97876b592ee1670d65c9b11c16d84da898f5b480cc4e6`
- Shim: `e205e7c36f53d3f0d8b7bd12097a61ba7adad4241e1039cbe74480ecd5814689`
- Contract: `75d14677bd8103cba2866702622b21669f4c6905a0a2216eb63fc219cbcc8266`

Relevant upstream sources:

- [Minijail root identity check](https://chromium.googlesource.com/chromiumos/platform/minijail/+/f4882a440f6d6eab7f35190e06bc16257ef3dcae/minijail0_cli.c#1272)
- [Overlay identity](https://chromium.googlesource.com/chromiumos/third_party/kernel/+/094d98275718/fs/overlayfs/inode.c#97) and [lower replacement constraints](https://chromium.googlesource.com/chromiumos/third_party/kernel/+/094d98275718/Documentation/filesystems/overlayfs.rst#621)
- [LoadPin](https://chromium.googlesource.com/chromiumos/third_party/kernel/+/094d98275718/security/loadpin/loadpin.c#128)
- [Pinned crdyboot](https://chromium.googlesource.com/chromiumos/platform/crdyboot/+/0484fa5b0c994a0077ef89703a833df9be52f8ad/)
- [EFI updater](https://chromium.googlesource.com/chromiumos/platform2/+/138cd27358b0c7aaeadc790457c3497a8ffbd04c/installer/chromeos_legacy.cc)
- [Updater build classification](https://chromium.googlesource.com/chromiumos/platform2/+/be45ef7d19e64b678388a4bf3a7e546194bba647/update_engine/cros/hardware_chromeos.cc#151), [automatic checks](https://chromium.googlesource.com/chromiumos/platform2/+/be45ef7d19e64b678388a4bf3a7e546194bba647/update_engine/update_manager/official_build_check_policy_impl.cc) and [signature policy](https://chromium.googlesource.com/chromiumos/platform2/+/be45ef7d19e64b678388a4bf3a7e546194bba647/update_engine/cros/omaha_response_handler_action.cc#363)
