# Orca IDE package handover

## Current status

`package.nix` packages Orca `1.4.182` from the upstream amd64 and arm64
Debian archives. It extracts only the payload, relocates `/opt/Orca` into the
Nix store, repairs native ELF dependencies, installs desktop metadata and
exposes the GUI launcher as `orca-ide` and the CLI launcher as `orca-cli`.
It intentionally does not create a bare `orca` command because that name is
the GNOME screen reader on many Linux systems.

The package also carries the Linux Computer Use runtime dependencies that the
upstream Debian metadata expects (`python3-gi`/AT-SPI, clipboard helpers,
`xdotool`, and desktop URL helpers). Nix provides these through the wrapper without
replacing the host's DBus session or URL opener.
The Debian metadata also lists `xvfb`; the production sidecar never starts a
virtual display, so Xvfb is kept only as a build-time install-check input.

Because the stock nixpkgs `at-spi2-core` launcher is configured for NixOS, the
expression overrides its Meson configuration to use the package's own
`dbus-daemon` and disables the systemd broker path. The wrapper adds the
resulting `share/dbus-1/services` directory to `XDG_DATA_DIRS`, which makes
activation portable for a newly started session bus. It cannot alter service
directories of an already-running host bus. The post-install service-file
normalization also removes `SystemdService=at-spi-dbus-bus.service`; this keeps
a systemd-integrated non-NixOS bus on the portable `Exec=` activation path.
The wrapper also appends `/usr/local/share:/usr/share` when the caller has no
`XDG_DATA_DIRS`, preserving normal host desktop/MIME/portal discovery in
minimal launchers.

GTK3 and the GTK app-indicator dependencies are overridden to consume that
portable AT-SPI package as well. Since `autoPatchelfHook` searches propagated
inputs and can otherwise select the stock variants for Electron's generated
RPATH, a post-hook normalization rewrites only those RPATH entries to the
portable paths. The final package closure therefore contains one GTK/AT-SPI
implementation rather than a mixed stock/portable pair.

## Architecture

The package deliberately avoids `appimageTools.wrapType2`. AppImage FHS and
bubblewrap isolation hide host `xdg-open`, MIME associations, DBus portals and
Crostini integration. A relocated Debian payload gives Electron the ordinary
host namespace while Nix supplies its dynamic libraries reproducibly.

The RPM release is not materially better for this use case: it carries the
same Electron application but would require a second archive extraction path
without improving host integration. A source build would instead make the
upstream Electron/native-module toolchain part of the Nix maintenance burden.
The `.deb` is used as a fixed upstream archive, not as a system package.

## Verification

The intended gates are:

```sh
nix flake check
nix build
```

After a successful build, inspect that the package has:

- `bin/orca-ide` and `bin/orca-cli`;
- `share/applications/orca-ide.desktop`;
- all upstream hicolor icons;
- no Debian control maintainer scripts are executed;
- no `resources/package-type`;
- no `resources/app-update.yml`;
- no `extraBwrapArgs` or AppImage runtime;
- `electron-updater`'s exported `autoUpdater` is replaced by
  `NixDisabledUpdater`, an EventEmitter-compatible no-op; this is what
  prevents feed checks, downloads, package-manager calls, and installs from
  mutating a Nix installation. The wrapper also unsets `APPIMAGE`;
  it sets `NIXPKGS_ORCA_DISABLE_UPDATES=1`. The app-level menu, background,
  nudge, and release-list entrypoints are patched to return before Orca's
  direct GitHub release-tag preflight or auxiliary update requests.
- the Python AT-SPI bridge imports successfully in `installCheckPhase`.
- a dynamically allocated Xvfb plus private `dbus-run-session` successfully
  runs the sidecar's real `list_apps` operation; the build log shows
  activation of both `org.a11y.Bus` and `org.a11y.atspi.Registry`.
- the final closure contains only the portable `at-spi2-core` and GTK3
  variants; its Electron RPATH points to those same paths.
- the Electron GUI starts under Nix-provided Xvfb without `--no-sandbox`
  (the smoke invocation uses `--disable-gpu` because Xvfb has no GPU); the
  package does not set the Debian `chrome-sandbox` setuid bit. A host that
  disables user namespaces may require a host policy change.
- The wrapper does not inject global Wayland/Ozone flags because the same
  wrapper serves `orca-cli` via `ELECTRON_RUN_AS_NODE`; Electron 43 performs
  backend auto-detection for GUI launches and the CLI remains flag-free.
- With `XDG_DATA_DIRS` unset, the wrapper retains the conventional host roots
  after the Nix roots; the install check explicitly exercises this path.
- The updater patch recalculates the changed `index.js` SHA-256 entry and the
  ASAR block hashes in the archive header. The install check verifies those
  refreshed integrity values and launches the patched archive. The package
  deliberately keeps the byte-preserving patch and avoids a repack that would
  disturb offsets and `app.asar.unpacked`.

## Known limitation

Electron's Chromium sandbox still depends on the host's user-namespace policy.
The package does not grant setuid permissions in the immutable store. On hosts
that disable unprivileged user namespaces, the upstream Electron fallback may
require `--no-sandbox`, which is a host security-policy issue rather than a
Debian/AppImage packaging issue.

The current host session bus does not advertise `org.a11y.Bus`. Adding the
package's service directory to the application's `XDG_DATA_DIRS` cannot change
an already-running D-Bus daemon, and the wrapper intentionally does not create
a per-process private bus. Therefore live accessibility actions still require
the target desktop's normal AT-SPI stack (or a fresh login/session bus that
starts with the Nix profile visible). Portal URL opening and Wayland input also
need one final check in the target Linux desktop session. The package's Python
imports, typelibs, real X11 AT-SPI initialization on an isolated private bus,
CLI, and ELF closure are verified during the Nix build.
