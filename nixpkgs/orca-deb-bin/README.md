# Orca IDE (`orca-deb-bin`)

This package relocates the Linux `.deb` payload published by Orca into a
normal Nix application. It does not install the Debian package, execute its
maintainer scripts, or create a bubblewrap/FHS runtime. The Electron binary and
native modules are patched to Nix libraries, while the process retains the
host's `PATH`, DBus session, MIME database, and desktop portals.

The GUI command is `orca-ide`. The bundled command-line entry point is exposed
as `orca-cli`; the package deliberately does not create a bare `orca` command,
because that name is the GNOME screen reader on many Linux systems.

## Commands

From this directory:

```sh
nix build
nix run
nix flake check
```

Install into a Nix profile:

```sh
nix profile install .#orca-ide
```

Inspect the resulting package:

```sh
nix build --no-link
nix-store --query --requisites ./result
find -L ./result/share -maxdepth 4 -type f
```

Run the GUI and CLI:

```sh
nix run .
./result/bin/orca-cli --help
```

The package is available for `x86_64-linux` and `aarch64-linux`. Upgrades are
performed by updating the Nix expression and its source hash, not by Orca's
in-app Debian/AppImage updater.

## Why the Debian payload

Among Orca's Linux artifacts, the Debian archive is the cleanest binary source
for Nix:

- The `.deb` has a conventional `/opt/Orca` payload that can be extracted with
  `dpkg-deb --extract` without installing into the host or running maintainer
  scripts.
- The RPM contains the same Electron payload but does not provide a useful
  integration advantage for Nix; adding an RPM extraction path would duplicate
  the packaging logic.
- The AppImage bundles an FHS-like userspace and its launcher may add
  bubblewrap/user-namespace isolation. That can hide the host's `xdg-open`,
  MIME handlers, DBus session, portals, and Crostini bridges.
- A source build would make Electron, native Node modules, and the upstream
  release toolchain part of this package's reproducibility boundary. For this
  binary-only IDE, that is substantially more maintenance than patching the
  published ELF closure.

This package therefore uses the `.deb` only as an upstream archive, then lets
Nix own relocation, ELF patching, desktop metadata, dependencies, and
upgrades. The running process stays in the host desktop namespace.

The release decision is therefore:

| Upstream artifact | Nix treatment | Result |
| --- | --- | --- |
| `.deb` | Extract payload with `dpkg-deb`, patch ELF files, generate Nix desktop metadata | **Selected**: ordinary host integration with declarative upgrades |
| `.rpm` | Extract the equivalent Electron payload with an additional RPM toolchain | No host-integration benefit; duplicated packaging logic |
| AppImage | `appimageTools.wrapType2` plus FHS/bubblewrap adjustments | Rejected: nested runtime and fragile host bridges |
| Source tree | Rebuild Electron and native modules from upstream sources | Rejected for this binary release: much larger maintenance and trust boundary |

## Design

- The Debian archive is fetched with a fixed hash and extracted with
  `dpkg-deb --extract`.
- `/opt/Orca` is relocated to `$out/lib/orca-ide`.
- Debian control maintainer scripts are not extracted or executed. The
  similarly named `after-install.sh`/`after-remove.sh` files inside
  `app.asar.unpacked` are ordinary upstream payload files, so they remain
  intact for archive consistency but are never invoked. `resources/package-type`
  and `resources/app-update.yml` are not included.
- Orca initializes `electron-updater` even when the package marker is absent.
  The Nix build therefore replaces that module's `autoUpdater` export with a
  small EventEmitter-compatible no-op. Update status events settle as
  “not available”, while feed checks, downloads, package-manager calls, and
  installs perform no network or filesystem mutation. The wrapper also unsets
  `APPIMAGE` as a defense-in-depth measure and sets
  `NIXPKGS_ORCA_DISABLE_UPDATES=1`. The app-level menu, background, nudge, and
  release-list paths are byte-preserving guarded edits, so the direct GitHub
  preflight and its auxiliary update metadata requests return before any
  network call.
- `autoPatchelfHook` repairs the upstream Electron and native module ELF
  files; `wrapGAppsHook3` supplies the GTK/desktop runtime environment.
- The application closure uses one portable GTK/AT-SPI stack. `at-spi2-core`
  is rebuilt with the Nix `dbus-daemon`, and GTK3 plus its app-indicator
  dependants are overridden to consume that same stack. A post-autoPatchelf
  RPATH normalization keeps the generated Electron RPATH aligned with the
  portable variants, so stock nixpkgs GTK/AT-SPI packages are not retained as
  duplicate runtime references.
- The Linux Computer Use bridge is made reproducible with Nix's Python
  PyGObject/AT-SPI stack plus `xdotool`, `xclip`, `xsel`, `wl-clipboard`, and
  the desktop URL helpers; these are fallback tools in the wrapper while the host `PATH` remains
  ahead of those fallback helpers for desktop-specific integrations. The
  packaged Python interpreter is the one intentional prepended entry.
- The Debian archive also declares `xvfb`, but Orca's production bridge only
  attaches to the user's existing X11/Wayland session and never starts Xvfb;
  Xvfb is therefore a build-time install-check tool, not a runtime dependency.
- The package rebuilds the small `at-spi2-core` daemon package with the Nix
  `dbus-daemon` path and publishes its D-Bus service directory through
  `XDG_DATA_DIRS`. This avoids the NixOS-only `/run/current-system` launcher
  path when the profile is installed on another Linux distribution. The
  generated `org.a11y.Bus.service` also omits the optional `SystemdService=`
  hint, so a systemd-integrated host bus cannot redirect activation to a
  missing NixOS unit. It does not start a background bus, replace the host
  session bus, or inject service files into a bus that is already running. A newly started session bus must
  see the profile's data directory, or the host distribution must already
  provide `org.a11y.Bus`; installing the profile cannot retroactively change
  an existing desktop bus.
- If a launcher starts with an empty `XDG_DATA_DIRS`, the wrapper appends the
  conventional `/usr/local/share:/usr/share` host roots after the Nix roots.
  This keeps host MIME handlers, portals, desktop entries, and ChromeOS
  Crostini integration discoverable while preserving any caller-provided
  `XDG_DATA_DIRS` ahead of the fallback.
- The desktop entry invokes the Nix `orca-ide` command, so it works from
  application launchers and URL arguments without an AppImage FHS. Use
  `orca-cli` for headless CLI calls; do not shadow the system `orca` screen
  reader.
- The wrapper does not inject global Wayland/Ozone flags: the same wrapper also
  serves `orca-cli`, whose `ELECTRON_RUN_AS_NODE` path must receive no GUI-only
  Chromium flags. Electron 43 auto-detects the user's Wayland/X11 backend;
  host `xdg-open`, MIME handlers, portals, DBus, and compositor state remain
  visible to the process.

The build's install check starts a build-time-only Xvfb with an active socket-readiness check,
launches a private `dbus-run-session`, and calls the sidecar's real
`list_apps` operation. This verifies D-Bus service activation and AT-SPI
initialization without requiring the builder to have a graphical login. It is
only a build-time test; a running installation still uses the user's existing
desktop DBus session and display/Wayland compositor. The wrapper deliberately
does not launch a private accessibility bus per process: if the desktop
session does not provide `org.a11y.Bus`, Linux Computer Use reports that the
host accessibility stack is unavailable. On such a host, install/enable the
distro's AT-SPI stack (or start a fresh login session after making the Nix
profile visible to the session bus).

For a normal non-NixOS installation, use the profile path rather than a
mutable `/opt` or `/usr` install:

```sh
nix profile install .#orca-ide
orca-ide
orca-cli --help
```

The package evaluates for `x86_64-linux` and `aarch64-linux`. Building the
aarch64 output requires an aarch64 builder, a configured remote builder, or
emulation; evaluation alone does not claim a cross-architecture binary build.

Electron's Linux sandbox was smoke-tested without `--no-sandbox` under a
private D-Bus session and Nix-provided Xvfb; the process remained alive until
the intentional timeout. The package does not set the Debian `chrome-sandbox`
file setuid bit. Hosts that disable unprivileged user namespaces may still
need an administrator-approved Chromium sandbox policy; that is a host kernel
policy, not a reason to add mutable setuid state to the Nix store.

The upstream ASAR header contains per-file SHA-256 metadata. After applying the
Nix updater patch, the package recalculates the changed `index.js` entry's
SHA-256 digest and ASAR block hashes in the header. The install check verifies
the resulting archive integrity metadata and launches the patched archive.
Repacking is unnecessary: the byte-preserving patch leaves offsets and the
`app.asar.unpacked` manifest unchanged.
