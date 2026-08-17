# Orca Nix package verification

The package was verified from this directory with:

```sh
nix fmt .
nix build --print-build-logs
nix flake check --no-build --all-systems
nix flake check --print-build-logs
```

The build completed with `auto-patchelf: 0 dependencies could not be
satisfied`. The install check and additional smoke checks passed:

- `result/bin/orca-cli --help` prints the CLI usage without shadowing the
  system GNOME `orca` screen reader.
- Electron reports Node `v24.18.0` through `ELECTRON_RUN_AS_NODE`.
- The public wrapper removes an inherited `APPIMAGE` value and resolves
  `python3` to the packaged PyGObject environment.
- With `XDG_DATA_DIRS` unset, the wrapper still retains `/usr/share` for host
  desktop/MIME/portal discovery.
- PyGObject/AT-SPI imports successfully during `installCheckPhase`.
- The package uses stock binary-cache GTK3 and `at-spi2-core` outputs. A small
  source-free service wrapper redirects the launcher's NixOS-only
  `/run/current-system/sw/bin/dbus-daemon` lookup without rebuilding either
  package.
- `libredirect` is scoped to `at-spi-bus-launcher`. Inspection of the live
  accessibility `dbus-daemon` environment confirms that `LD_PRELOAD` and
  `NIX_REDIRECTS` are removed before the daemon starts.
- The packaged `runtime.py` sidecar returns its Linux handshake JSON and a
  real `list_apps` response from dynamically allocated Xvfb and a private
  `dbus-run-session`.
- The install-check log records successful activation of `org.a11y.Bus` and
  `org.a11y.atspi.Registry`.
- A live host-session smoke test showed that an already-running D-Bus daemon
  does not rescan an application's `XDG_DATA_DIRS`; this is why the wrapper
  does not pretend to install a per-process accessibility bus. The target
  desktop must provide its normal AT-SPI stack, or start a fresh session bus
  with the Nix profile visible.
- `app.asar` still lists both Linux packaging script paths.
- `package-type`, `app-update.yml`, and `apparmor-profile` are absent.
- Both public wrappers contain `unset APPIMAGE`.
- The bundled `electron-updater` module exports `NixDisabledUpdater`; its
  compatibility check emits `update-not-available` without contacting a feed.
  The install check also calls `downloadUpdate()` and `quitAndInstall()` and
  verifies that both are rejected with the Nix-managed-update error before
  they can download an artifact, invoke a package manager, or change the
  filesystem.
- The wrapper sets `NIXPKGS_ORCA_DISABLE_UPDATES=1`; the app-level setup/menu,
  background, nudge, and release-list entrypoints return before Orca's direct
  GitHub release preflight or auxiliary update requests.
- The direct closure contains no AppImage launcher/runtime environment,
  bubblewrap, or bwrap dependency; format names retained in upstream updater
  source are unreachable from the Nix updater export.
- The x86_64 closure is approximately 1.1 GiB. Its largest paths are the
  upstream Orca/Electron payload (about 532 MiB), Python (about 135 MiB), and
  stock GTK3 (about 42 MiB); no dependency was source-rebuilt just to reduce
  this size.
- `nix fmt .` uses `nixfmt-tree`, and the flake quality check runs formatting,
  `deadnix`, and `statix`.
- x86_64 and aarch64 package expressions evaluate successfully; aarch64
  builds are omitted by `nix flake check --no-build` on this x86_64 host.

The host-namespace regression check also passed:

```text
$ cat /proc/self/uid_map
0 0 4294967295
$ stat -Lc '%U:%G %a' ~/.ssh/config
root:root 444
$ ssh -G github.com >/dev/null && echo SSH_CONFIG_OK
SSH_CONFIG_OK
```

Together with the absence of a namespace wrapper, this confirms that the
package launcher does not enter the portable-Nix/AppImage user namespace that
previously exposed Nix store ownership as `nobody:nobody`. Electron may still
use its own Chromium sandbox namespaces.

The profile installation path was also smoke-tested with a fresh temporary
profile:

```sh
profile_root="$(mktemp -d)"
nix profile install --profile "$profile_root/profile" .#orca-ide
"$profile_root/profile/bin/orca-cli" --help
```

That command completed successfully. The GUI wrapper keeps the host's
`xdg-open`/portal helpers ahead of its packaged fallback helpers when the
profile is added to `PATH`.

A native GUI startup smoke test was run with the Nix-provided Xvfb rather than
the host's X server. From this directory, the exact copy-pasteable command is:

```sh
nix shell nixpkgs#xvfb nixpkgs#dbus --command bash -c '
  set -eu
  test -x ./result/bin/orca-ide
  test -x "$(command -v Xvfb)"
  test -x "$(command -v dbus-run-session)"
  root="$(mktemp -d)"
  trap "kill \"\$xvfb_pid\" 2>/dev/null || true; wait \"\$xvfb_pid\" 2>/dev/null || true; rm -rf \"\$root\"" EXIT
  mkdir -m 700 "\$root/runtime" "\$root/home" "\$root/config" "\$root/cache"
  Xvfb -displayfd 3 -screen 0 1280x720x24 -nolisten tcp -noreset \
    >"\$root/xvfb.log" 2>&1 3>"\$root/display" &
  xvfb_pid="\$!"
  for _ in \$(seq 1 100); do
    test -s "\$root/display" && break
    kill -0 "\$xvfb_pid" 2>/dev/null || { cat "\$root/xvfb.log" >&2; exit 1; }
    sleep 0.05
  done
  test -s "\$root/display"
  display=":\$(cat "\$root/display")"
  test -S "/tmp/.X11-unix/X\${display#:}"
  set +e
  XDG_RUNTIME_DIR="\$root/runtime" DISPLAY="\$display" XDG_SESSION_TYPE=x11 \
    HOME="\$root/home" XDG_CONFIG_HOME="\$root/config" XDG_CACHE_HOME="\$root/cache" \
    dbus-run-session \
      --config-file="$(dirname "$(command -v dbus-run-session)")/../share/dbus-1/session.conf" \
      -- env \
      XDG_RUNTIME_DIR="\$root/runtime" DISPLAY="\$display" XDG_SESSION_TYPE=x11 \
      HOME="\$root/home" XDG_CONFIG_HOME="\$root/config" XDG_CACHE_HOME="\$root/cache" \
      timeout --signal=TERM --kill-after=5s 15s \
      ./result/bin/orca-ide --disable-gpu --user-data-dir="\$root/profile"
  rc="\$?"
  test "\$rc" -eq 124
'
```

The wrapper created the Electron process and kept the GUI alive until the
intentional timeout (`status=124`) without `--no-sandbox`; `--disable-gpu` is
limited to this Xvfb smoke test because the virtual display has no GPU. There
was no missing ELF dependency, AppImage launcher, or bubblewrap process. The
isolated check also confirmed that a private D-Bus session can activate
`org.a11y.Bus` and
`org.a11y.atspi.Registry`. A host-session launch may still print
`org.a11y.Bus was not provided by any .service files` when the existing desktop
D-Bus daemon has no AT-SPI service; that daemon does not rescan an application's
`XDG_DATA_DIRS`, so this is a session setup limitation rather than a Nix
payload failure.

The updater patch also makes the direct `downloadUpdate`, `quitAndInstall`,
and `performQuitAndInstall` entrypoints return immediately under
`NIXPKGS_ORCA_DISABLE_UPDATES=1`. The package recalculates the changed
`index.js` SHA-256 entry and ASAR block hashes after this byte-preserving edit;
the install check verifies those refreshed values, and the GUI launch above
exercises the patched archive.

The wrapper intentionally does not add global Wayland/Ozone flags: it is also
the `orca-cli` wrapper, and Electron's `ELECTRON_RUN_AS_NODE` path rejects GUI
flags. Electron 43 auto-detects X11 versus Wayland for the GUI process.

The aarch64 output is intentionally evaluation-only on this x86_64 builder.
An actual aarch64 build needs an aarch64 machine, a configured remote builder,
or binary emulation; no such cross-build executor is assumed by this flake.

The repository-wide `nix develop -c bun run verify` command was also run from
the monorepo root after the final package rebuild. It remains non-green with
`2473 pass / 10 fail` because of pre-existing root-level problems outside this
Nix package: nested Biome roots, stale/generated root hygiene findings, missing
directories referenced by the Slidev check, and existing executable-bit/shebang
violations. No TypeScript or JavaScript source was changed for this package.
