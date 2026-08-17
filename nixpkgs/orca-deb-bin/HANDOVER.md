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

The stock nixpkgs `at-spi2-core` launcher is configured with a NixOS-only
`/run/current-system/sw/bin/dbus-daemon` path. The expression keeps the cached
AT-SPI and GTK packages and adds a source-free `orca-at-spi2-service` shim.
The shim scopes `libredirect` to `at-spi-bus-launcher`, translating only that
lookup to the Nix D-Bus executable. Its D-Bus wrapper unsets `LD_PRELOAD` and
`NIX_REDIRECTS` before starting the accessibility daemon, preventing either
variable from reaching the daemon or services it activates.

The shim publishes its `org.a11y.Bus.service` directory through
`XDG_DATA_DIRS` for newly started session buses and omits the optional
`SystemdService=` hint. It cannot alter service directories of an
already-running host bus. The application wrapper also appends
`/usr/local/share:/usr/share` when the caller has no `XDG_DATA_DIRS`,
preserving normal host desktop/MIME/portal discovery in minimal launchers.
No GTK, AT-SPI, app-indicator, or D-Bus source package is overridden, so this
expression does not force source rebuilds of that stack.

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
nix fmt .
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
- the final closure uses the stock binary-cache `at-spi2-core` and GTK3 paths;
- `org.a11y.Bus` activates through the service shim, while the final
  accessibility `dbus-daemon` process contains neither `LD_PRELOAD` nor
  `NIX_REDIRECTS`;
- `nix flake check` runs `nixfmt`, `deadnix`, and `statix` checks over the
  package expressions;
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

An already-running host session bus does not rescan `XDG_DATA_DIRS`. Adding
the package's service directory to the application environment therefore
cannot repair a bus that started without `org.a11y.Bus`, and the wrapper
intentionally does not create a per-process private bus. Live accessibility
actions require the target desktop's normal AT-SPI stack or a fresh
login/session bus that starts with the Nix profile visible. Portal URL opening
and Wayland input also need one final check in the target Linux desktop
session. Python imports, typelibs, real X11 AT-SPI initialization on an
isolated private bus, CLI behavior, and the ELF closure are verified during
the Nix build.

The x86_64 runtime closure is approximately 1.1 GiB. About 532 MiB is the
upstream Orca/Electron payload, 135 MiB is Python, and 42 MiB is stock GTK3.
The remaining clipboard, URL-opening, desktop, and accessibility dependencies
back declared functionality. There is no currently identified reduction that
both preserves those features and retains binary-cache reuse.

## Next steps

- Exercise portal URL opening and Wayland Computer Use in the target desktop
  session.
- After the next upstream Orca release, update `version`, both fixed source
  hashes, and the updater-patch/install-check markers together.
- Recheck the closure breakdown when nixpkgs changes the Python, GTK, or
  desktop-helper dependency graphs.
