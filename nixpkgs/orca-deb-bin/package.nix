{
  lib,
  stdenv,
  fetchurl,
  makeDesktopItem,
  buildPackages,
  wrapGAppsHook3,
  alsa-lib,
  at-spi2-core,
  cairo,
  cups,
  dbus,
  expat,
  fontconfig,
  freetype,
  gdk-pixbuf,
  gobject-introspection,
  glib,
  gtk3,
  libappindicator,
  libdbusmenu-gtk3,
  libdrm,
  libgbm,
  libGL,
  libsecret,
  libindicator-gtk3,
  libuuid,
  libX11,
  libXcomposite,
  libXdamage,
  libXext,
  libXfixes,
  libXi,
  libXrandr,
  libXrender,
  libXScrnSaver,
  libXtst,
  libxcb,
  libxkbcommon,
  libxshmfence,
  nspr,
  nss,
  pango,
  procps,
  python3,
  systemdLibs,
  udev,
  wayland,
  wl-clipboard,
  xclip,
  xdg-utils,
  xdotool,
  xsel,
  zlib,
}:

let
  version = "1.4.182";
  sources = {
    x86_64-linux = {
      url = "https://github.com/stablyai/orca/releases/download/v${version}/orca-ide_${version}_amd64.deb";
      hash = "sha256-ttRsxS8GtAJbjianKD24+xYI2+ym5ub8Uq+7a+A6aNU=";
    };
    aarch64-linux = {
      url = "https://github.com/stablyai/orca/releases/download/v${version}/orca-ide_${version}_arm64.deb";
      hash = "sha256-kBLtx2Tq1PFv1htz1XzDXoBSlY2burhU7zdd+0QZlZY=";
    };
  };
  source =
    sources.${stdenv.hostPlatform.system}
      or (throw "orca-deb-bin: unsupported system ${stdenv.hostPlatform.system}");

  # Nixpkgs builds at-spi2-core for NixOS and embeds
  # /run/current-system/sw/bin/dbus-daemon in its launcher. A profile
  # installation on a non-NixOS host has no such path, so rebuild this small
  # daemon package with the already-pinned Nix dbus executable instead. This
  # does not start a private bus or replace the host session bus; it only makes
  # D-Bus activation of org.a11y.Bus portable.
  atSpi2Core = at-spi2-core.overrideAttrs (oldAttrs: {
    mesonFlags =
      let
        oldFlags = oldAttrs.mesonFlags or [ ];
      in
      lib.filter (
        flag:
        !(lib.hasPrefix "-Ddbus_daemon=" flag)
        && !(lib.hasPrefix "-Ddbus_broker=" flag)
        && flag != "-Duse_systemd=false"
      ) oldFlags
      ++ [
        "-Ddbus_daemon=${dbus}/bin/dbus-daemon"
        "-Duse_systemd=false"
      ];
    # The upstream D-Bus service file can still carry a SystemdService hint
    # even when the build disables systemd support. On a non-NixOS host whose
    # session bus is systemd-integrated, that hint makes activation prefer a
    # unit that is not present in a Nix profile and can prevent Exec= from
    # running. Keep activation self-contained and portable by removing only
    # the optional systemd hint; the service's Exec= launcher remains.
    postInstall =
      (oldAttrs.postInstall or "")
      + ''
        if test -f "$out/share/dbus-1/services/org.a11y.Bus.service"; then
          sed -i '/^SystemdService=/d' \
            "$out/share/dbus-1/services/org.a11y.Bus.service"
        fi
      '';
  });

  # GTK3 propagates its AT-SPI implementation. Override the GTK stack that is
  # placed in this application's closure as well, otherwise the normal
  # nixpkgs GTK build pulls a second stock at-spi2-core with the NixOS-only
  # `/run/current-system` launcher path.
  gtk3Portable = gtk3.override {
    at-spi2-atk = atSpi2Core;
    atk = atSpi2Core;
  };
  libdbusmenuGtk3Portable = libdbusmenu-gtk3.override {
    gtk3 = gtk3Portable;
  };
  libindicatorGtk3Portable = libindicator-gtk3.override {
    gtk3 = gtk3Portable;
  };
  libappindicatorPortable = libappindicator.override {
    gtk3 = gtk3Portable;
    libdbusmenu-gtk3 = libdbusmenuGtk3Portable;
    libindicator-gtk3 = libindicatorGtk3Portable;
  };

  # Electron bundles a large amount of native code. Keep the runtime library
  # closure explicit so autoPatchelfHook can repair every ELF payload without
  # creating an FHS or bubblewrap environment.
  runtimeLibraries = [
    alsa-lib
    atSpi2Core
    cairo
    cups
    dbus
    expat
    fontconfig
    freetype
    gdk-pixbuf
    glib
    gtk3Portable
    libappindicatorPortable
    libdrm
    libgbm
    libGL
    libsecret
    libuuid
    libX11
    libXcomposite
    libXdamage
    libXext
    libXfixes
    libXi
    libXrandr
    libXrender
    libXScrnSaver
    libXtst
    libxcb
    libxkbcommon
    libxshmfence
    nspr
    nss
    pango
    stdenv.cc.cc
    systemdLibs
    udev
    wayland
    zlib
  ];
  # Orca's Linux Computer Use bridge is shipped as Python and imports
  # PyGObject/AT-SPI. Keep that bridge reproducible instead of depending on a
  # distro's python3-gi package, while still letting the desktop session and
  # its DBus/portal services come from the host.
  pythonWithAtspi = python3.withPackages (ps: [ ps.pygobject3 ]);
  desktopTools = [
    procps
    wl-clipboard
    xclip
    xdotool
    xsel
    xdg-utils
  ];
  giTypelibPath = lib.makeSearchPath "lib/girepository-1.0" [
    atSpi2Core
    gdk-pixbuf
    gobject-introspection
    glib
    gtk3Portable
  ];
in
stdenv.mkDerivation (finalAttrs: {
  pname = "orca-ide";
  inherit version;
  src = fetchurl source;

  nativeBuildInputs = [
    buildPackages.dpkg
    buildPackages.autoPatchelfHook
    buildPackages.copyDesktopItems
    buildPackages.makeWrapper
    buildPackages.python3
    # Xvfb is used only by the isolated AT-SPI install check below. Orca's
    # production Computer Use bridge attaches to the user's existing display;
    # it never starts a virtual X server, so xvfb is not a runtime dependency.
    buildPackages.xvfb
    # Use the build-platform wrapper hook, as recommended for cross builds.
    (buildPackages.wrapGAppsHook3.override {
      makeWrapper = buildPackages.makeShellWrapper;
    })
  ];

  buildInputs = runtimeLibraries;

  dontConfigure = true;
  dontBuild = true;
  dontStrip = true;

  unpackPhase = "true";

  installPhase = ''
        runHook preInstall

        mkdir -p "$out/lib/orca-ide" "$out/bin"
        dpkg-deb --extract "$src" "$out/.deb-root"

        # Only copy the application payload. Debian control maintainer scripts are
        # intentionally not extracted or run: they chmod a setuid sandbox and
        # install a mutable /usr/bin symlink, neither of which is valid in the Nix
        # store.
        cp -a "$out/.deb-root/opt/Orca/." "$out/lib/orca-ide/"

        # electron-updater defaults to an AppImage updater when package-type is
        # absent, and Orca initializes it even for a relocated Nix payload. Keep
        # the upstream UI/status protocol but replace the updater export with a
        # deterministic no-op that cannot download, install, or mutate the store.
        install -Dm644 "${./disabled-updater.js}" \
          "$out/lib/orca-ide/resources/node_modules/electron-updater/out/NixDisabledUpdater.js"
        substituteInPlace \
          "$out/lib/orca-ide/resources/node_modules/electron-updater/out/main.js" \
          --replace-fail \
          'return _autoUpdater || doLoadAutoUpdater();' \
          'return _autoUpdater || require("./NixDisabledUpdater").disabledAutoUpdater;'

        # Orca performs a GitHub release-tag preflight before it calls
        # electron-updater. Patch the app-level entry points in the ASAR so
        # automatic and menu-triggered checks cannot perform that direct request.
        # The replacement is deliberately byte-length preserving: ASAR offsets and
        # the rest of the archive remain unchanged, so no repack/FHS step is
        # needed.
        ${buildPackages.python3}/bin/python3 - \
          "$out/lib/orca-ide/resources/app.asar" <<'PY'
    import hashlib
    import json
    import struct
    import sys

    archive_path = sys.argv[1]
    with open(archive_path, "r+b") as archive:
        payload = bytearray(archive.read())

        header_size = struct.unpack("<I", payload[12:16])[0]
        header = json.loads(payload[16 : 16 + header_size])
        entry = header["files"]["out"]["files"]["main"]["files"]["index.js"]
        # ASAR pads the JSON header to a 4-byte boundary before file data.
        # Keep this aligned with Electron's reader; the two-byte pad is
        # present in the current upstream archive.
        data_start = 16 + ((header_size + 3) & ~3) + int(entry["offset"])
        source = bytearray(payload[data_start : data_start + int(entry["size"])])


        def replace_after(marker, old, new):
            marker_offset = source.find(marker)
            assert marker_offset >= 0, marker
            offset = source.find(old, marker_offset + len(marker))
            assert offset >= 0, old
            function_boundaries = [
                boundary
                for boundary in (
                    source.find(b"\nfunction ", marker_offset + len(marker)),
                    source.find(b"\nasync function ", marker_offset + len(marker)),
                )
                if boundary >= 0
            ]
            assert function_boundaries and offset < min(function_boundaries), (marker, old)
            assert len(new) <= len(old), (len(old), len(new))
            replacement = new + b" " * (len(old) - len(new))
            source[offset : offset + len(old)] = replacement


        replace_after(
            b"function setupAutoUpdater(mainWindow$1, opts) {",
            b"if (!electron.app.isPackaged && !__electron_toolkit_utils.is.dev) return;",
            b'if (process.env.NIXPKGS_ORCA_DISABLE_UPDATES === "1") return;',
        )
        replace_after(
            b"function checkForUpdatesFromMenu(options) {",
            b"if (!electron.app.isPackaged || __electron_toolkit_utils.is.dev) {",
            b'if (process.env.NIXPKGS_ORCA_DISABLE_UPDATES === "1") {',
        )
        replace_after(
            b"function runBackgroundUpdateCheck(nudgeId = getPersistedPendingUpdateNudgeId()) {",
            b'if (activeUpdateSource !== "release" || isPinnedBuildActive || localBuildSelectionInProgress || pinnedBuildSelectionInProgress) return false;',
            b'if (process.env.NIXPKGS_ORCA_DISABLE_UPDATES === "1") return false;',
        )
        replace_after(
            b"async function checkForUpdateNudge() {",
            b"if (!electron.app.isPackaged || __electron_toolkit_utils.is.dev) return;",
            b'if (process.env.NIXPKGS_ORCA_DISABLE_UPDATES === "1") return;',
        )
        replace_after(
            b"async function listAvailableReleaseBuilds(channel) {",
            b"return listReleaseBuilds(channel);",
            b"return [];",
        )
        # Keep the IPC/API escape hatches fail-closed as well. The normal menu
        # and background paths are guarded above, but these functions can also
        # be reached by remote-server update messages. Returning before their
        # timers/state changes avoids even a rejected updater call in a Nix
        # installation.
        replace_after(
            b"function quitAndInstall() {",
            b"if (localBuildSelectionInProgress || pinnedBuildSelectionInProgress || pendingQuitAndInstallTimer || quitAndInstallInProgress || linuxPackageRevalidationInFlight) return;",
            b'if (process.env.NIXPKGS_ORCA_DISABLE_UPDATES === "1") return;',
        )
        replace_after(
            b"function downloadUpdate() {",
            b"if (localBuildSelectionInProgress || pinnedBuildSelectionInProgress || downloadInFlight) return;",
            b'if (process.env.NIXPKGS_ORCA_DISABLE_UPDATES === "1") return;',
        )
        replace_after(
            b"async function performQuitAndInstall() {",
            b"if (quitAndInstallInProgress || linuxPackageRevalidationInFlight) {",
            b'if (process.env.NIXPKGS_ORCA_DISABLE_UPDATES === "1") {',
        )

        # Electron-builder records a SHA-256 digest and block digests for each
        # file in the ASAR header. The updater guards above keep the original
        # byte offsets, but the metadata must still describe the new bytes so
        # archive validators and future Electron versions do not observe a
        # self-contradictory payload.
        integrity = entry.get("integrity")
        assert integrity and integrity.get("algorithm") == "SHA256", integrity
        block_size = int(integrity["blockSize"])
        block_digests = [
            hashlib.sha256(source[offset : offset + block_size]).hexdigest()
            for offset in range(0, len(source), block_size)
        ]
        source_digest = hashlib.sha256(source).hexdigest()
        assert len(source_digest) == len(integrity["hash"]) == 64
        assert len(block_digests) == len(integrity["blocks"])
        assert all(len(digest) == len(old) == 64 for digest, old in zip(block_digests, integrity["blocks"]))
        integrity["hash"] = source_digest
        integrity["blocks"] = block_digests
        replacement_header = json.dumps(header, separators=(",", ":")).encode("utf-8")
        assert len(replacement_header) == header_size, (
            len(replacement_header),
            header_size,
        )

        payload[data_start : data_start + len(source)] = source
        payload[16 : 16 + header_size] = replacement_header
        archive.seek(0)
        archive.write(payload)
    PY

        # The Debian payload's marker makes Orca treat the Nix install as a root
        # Debian install and try to invoke apt/dpkg during self-update. The
        # immutable Nix package owns upgrades, so do not ship that marker or the
        # updater feed metadata.
        rm -f "$out/lib/orca-ide/resources/package-type"
        rm -f "$out/lib/orca-ide/resources/app-update.yml"
        rm -f "$out/lib/orca-ide/resources/apparmor-profile"
        # Keep the upstream unpacked packaging-hook paths intact. These are regular
        # files inside Electron's `app.asar.unpacked` payload, not Debian control
        # maintainer scripts. Deleting them makes archive extraction/integrity
        # tooling report missing files; they remain inert because this package
        # never invokes them and `resources/package-type` is absent.

        # The GUI binary is the public Nix command. The upstream CLI launcher is
        # exposed as `orca-cli`: a bare `orca` command would collide with the
        # GNOME Orca screen reader on Linux. The launcher resolves the Electron
        # binary next to the resources directory and therefore continues to work
        # after relocation.
        ln -s "$out/lib/orca-ide/orca-ide" "$out/bin/orca-ide"
        # Use an absolute target so the upstream launcher resolves its real
        # resources directory after it is invoked through the public Nix command.
        makeWrapper "$out/lib/orca-ide/resources/bin/orca-ide" "$out/bin/orca-cli"

        # Keep all upstream icon sizes for desktop environments that do not scale
        # a 512px-only icon correctly.
        mkdir -p "$out/share/icons/hicolor"
        cp -a "$out/.deb-root/usr/share/icons/hicolor/." "$out/share/icons/hicolor/"

        rm -rf "$out/.deb-root"
        runHook postInstall
  '';

  desktopItems = [
    (makeDesktopItem {
      name = "orca-ide";
      desktopName = "Orca";
      comment = "Next-gen IDE for parallel agentic development";
      categories = [ "Utility" ];
      exec = "orca-ide %U";
      icon = "orca-ide";
      startupWMClass = "orca";
    })
  ];

  preFixup = ''
    # Keep host desktop helper precedence: this lets Crostini and other
    # environments provide their own xdg-open/portal bridge. Only the
    # packaged Python/AT-SPI bridge is prepended because a host `python3` may
    # not have gi; ordinary helpers remain suffix fallbacks and do not replace
    # host URL integration.
    gappsWrapperArgs+=(
      --prefix PATH : ${lib.makeBinPath [ pythonWithAtspi ]}
      --suffix PATH : ${lib.makeBinPath desktopTools}
      --prefix XDG_DATA_DIRS : ${atSpi2Core}/share
      # Keep the standard host desktop data roots even when a minimal launcher
      # did not export XDG_DATA_DIRS. This preserves host MIME handlers,
      # desktop entries, portals, and Crostini integration instead of making
      # the Nix wrapper's data roots the entire desktop search path.
      --suffix XDG_DATA_DIRS : /usr/local/share:/usr/share
      --prefix GI_TYPELIB_PATH : ${giTypelibPath}
      # Chromium's bundled libEGL.so loads the system libEGL.so.1 through
      # dlopen. RPATH cannot cover that lookup, so expose Nix's libglvnd
      # implementation in the standard Electron wrapper environment.
      --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ libGL ]}
      # electron-updater defaults to AppImageUpdater on Linux when the
      # electron-builder package marker is absent. Never let an inherited
      # APPIMAGE variable turn this Nix build into an AppImage updater.
      --unset APPIMAGE
      --set NIXPKGS_ORCA_DISABLE_UPDATES 1
    )
    # The inherited GTK setup hook can add the stock GTK gsettings directory
    # before our portable override is appended. Replace that generated path
    # in-place so the wrapper and closure agree on one GTK/AT-SPI stack.
    for i in "''${!gappsWrapperArgs[@]}"; do
      gappsWrapperArgs[$i]="$(printf '%s' "''${gappsWrapperArgs[$i]}" \
        | sed "s#${gtk3}#${gtk3Portable}#g")"
    done

    # autoPatchelfHook runs from postFixupHooks. Append the RPATH rewrite
    # there so it runs after autoPatchelf has selected its libraries.
    rewritePortableRpath() {
      while IFS= read -r -d "" elf; do
        if ! patchelf --print-rpath "$elf" >/dev/null 2>&1; then
          continue
        fi
        rpath="$(patchelf --print-rpath "$elf")"
        portableRpath="$(printf "%s" "$rpath" \
          | sed \
            -e "s#${at-spi2-core}#${atSpi2Core}#g" \
            -e "s#${gtk3}#${gtk3Portable}#g" \
            -e "s#${libdbusmenu-gtk3}#${libdbusmenuGtk3Portable}#g" \
            -e "s#${libindicator-gtk3}#${libindicatorGtk3Portable}#g" \
            -e "s#${libappindicator}#${libappindicatorPortable}#g")"
        if test "$portableRpath" != "$rpath"; then
          patchelf --set-rpath "$portableRpath" "$elf"
        fi
      done < <(find "$out" -type f -print0)
    }
    postFixupHooks+=(rewritePortableRpath)
  '';

  # Only run the install check when the selected builder can execute the target
  # payload. This keeps native builds fully exercised while allowing a remote
  # builder or configured emulator to make the same check available for a
  # foreign architecture.
  doInstallCheck = stdenv.buildPlatform.canExecute stdenv.hostPlatform;
  installCheckPhase = ''
    runHook preInstallCheck

    test -x "$out/bin/orca-ide"
    test -x "$out/bin/orca-cli"
    # Do not shadow the GNOME Orca screen reader command on Linux.
    test ! -e "$out/bin/orca"
    test -x "$out/lib/orca-ide/orca-ide"
    test -x "$out/lib/orca-ide/resources/bin/orca-ide"
    test -f "$out/share/applications/orca-ide.desktop"
    grep -Fq "Exec=orca-ide %U" "$out/share/applications/orca-ide.desktop"
    ! grep -Fq "/opt/Orca" "$out/share/applications/orca-ide.desktop"
    ! grep -Fq "AppRun" "$out/share/applications/orca-ide.desktop"
    test ! -e "$out/lib/orca-ide/resources/package-type"
    test ! -e "$out/lib/orca-ide/resources/app-update.yml"
    test ! -e "$out/lib/orca-ide/resources/apparmor-profile"
    test -f "$out/lib/orca-ide/resources/node_modules/electron-updater/out/NixDisabledUpdater.js"
    grep -Fq 'NixDisabledUpdater' \
      "$out/lib/orca-ide/resources/node_modules/electron-updater/out/NixDisabledUpdater.js"
    grep -Fq 'NixDisabledUpdater' \
      "$out/lib/orca-ide/resources/node_modules/electron-updater/out/main.js"
    # The app-level preflight guards are byte-preserving edits inside app.asar;
    # assert both exact markers so a future upstream payload cannot silently
    # lose the Nix updater policy.
    grep -a -Fq \
      'if (process.env.NIXPKGS_ORCA_DISABLE_UPDATES === "1") return;' \
      "$out/lib/orca-ide/resources/app.asar"
    grep -a -Fq \
      'if (process.env.NIXPKGS_ORCA_DISABLE_UPDATES === "1") {' \
      "$out/lib/orca-ide/resources/app.asar"
    grep -a -Fq \
      'if (process.env.NIXPKGS_ORCA_DISABLE_UPDATES === "1") return false;' \
      "$out/lib/orca-ide/resources/app.asar"
    grep -a -Fq \
      'async function listAvailableReleaseBuilds(channel)' \
      "$out/lib/orca-ide/resources/app.asar"
    grep -a -Fq 'return [];' "$out/lib/orca-ide/resources/app.asar"
    grep -a -Fq \
      'function quitAndInstall()' \
      "$out/lib/orca-ide/resources/app.asar"
    grep -a -Fq \
      'function downloadUpdate()' \
      "$out/lib/orca-ide/resources/app.asar"
    grep -a -Fq \
      'async function performQuitAndInstall()' \
      "$out/lib/orca-ide/resources/app.asar"
    # The byte-preserving patch also refreshes the modified entry's ASAR
    # integrity metadata. Verify every packed entry in the installed archive,
    # rather than relying only on Electron's launch behavior or checking the
    # one entry that Nix edits. Entries marked `unpacked` live beside the ASAR
    # and are checked separately below.
    ${buildPackages.python3}/bin/python3 - \
      "$out/lib/orca-ide/resources/app.asar" <<'PY'
import hashlib
import json
import struct
import sys

archive_path = sys.argv[1]
with open(archive_path, "rb") as archive:
    payload = archive.read()
header_size = struct.unpack("<I", payload[12:16])[0]
header = json.loads(payload[16 : 16 + header_size])

checked = 0


def verify_files(node, path=""):
    global checked
    for name, entry in node.get("files", {}).items():
        entry_path = f"{path}/{name}" if path else name
        if "files" in entry:
            verify_files(entry, entry_path)
            continue
        if entry.get("unpacked"):
            continue
        integrity = entry.get("integrity")
        assert integrity and integrity["algorithm"] == "SHA256", entry_path
        start = 16 + ((header_size + 3) & ~3) + int(entry["offset"])
        size = int(entry["size"])
        source = payload[start : start + size]
        assert len(source) == size, entry_path
        assert integrity["hash"] == hashlib.sha256(source).hexdigest(), entry_path
        block_size = int(integrity["blockSize"])
        expected_blocks = [
            hashlib.sha256(source[offset : offset + block_size]).hexdigest()
            for offset in range(0, max(len(source), 1), block_size)
        ]
        assert integrity["blocks"] == expected_blocks, entry_path
        checked += 1


verify_files(header)
assert checked > 100, checked
PY
    test -f "${atSpi2Core}/share/dbus-1/services/org.a11y.Bus.service"
    ! grep -Fq "SystemdService=" \
      "${atSpi2Core}/share/dbus-1/services/org.a11y.Bus.service"
    ! grep -R -a -Fq "/run/current-system" "${atSpi2Core}"
    ! grep -R -a -Fq "dbus-broker-launch" "${atSpi2Core}"
    grep -Fq "unset APPIMAGE" "$out/bin/orca-ide"
    grep -Fq "unset APPIMAGE" "$out/bin/orca-cli"
    # These upstream payload files are referenced by the asar-unpacked
    # manifest. They must remain present even though Nix never executes Debian
    # control maintainer scripts or these packaging hooks.
    test -x "$out/lib/orca-ide/resources/app.asar.unpacked/resources/linux/packaging/after-install.sh"
    test -x "$out/lib/orca-ide/resources/app.asar.unpacked/resources/linux/packaging/after-remove.sh"

    # This exercises the relocated upstream CLI launcher and Electron's
    # ELECTRON_RUN_AS_NODE path without requiring a graphical display.
    "$out/bin/orca-cli" --help > "$TMPDIR/orca-help.txt"
    grep -Fq "Usage:" "$TMPDIR/orca-help.txt"

    # Exercise the public wrapper itself. This proves that an inherited
    # APPIMAGE value is removed and that `python3` resolves to the packaged
    # PyGObject environment rather than an arbitrary host interpreter.
    env -u XDG_DATA_DIRS \
      APPIMAGE="/tmp/inherited-appimage-marker" \
      ELECTRON_RUN_AS_NODE=1 \
      "$out/bin/orca-ide" -e '
        const { execFileSync } = require("node:child_process");
        if (process.env.APPIMAGE !== undefined) {
          throw new Error("APPIMAGE leaked through the Nix wrapper");
        }
        if (process.env.NIXPKGS_ORCA_DISABLE_UPDATES !== "1") {
          throw new Error("Nix updater guard leaked through the wrapper");
        }
        if (!process.env.XDG_DATA_DIRS.split(":").includes("/usr/share")) {
          throw new Error(`host desktop data root missing: \''${process.env.XDG_DATA_DIRS}`);
        }
        const executable = execFileSync("python3", [
          "-c",
          "import ctypes,gi,sys; ctypes.CDLL(\"libEGL.so.1\"); print(sys.executable)",
        ], { encoding: "utf8" }).trim();
        if (executable !== "${pythonWithAtspi}/bin/python3") {
          throw new Error(`unexpected python3: \''${executable}`);
        }
        process.stdout.write(JSON.stringify({
          appimage: process.env.APPIMAGE ?? null,
          updates: process.env.NIXPKGS_ORCA_DISABLE_UPDATES ?? null,
          python3: executable,
        }));
      ' > "$TMPDIR/orca-wrapper.json"
    grep -Fq '"appimage":null' "$TMPDIR/orca-wrapper.json"
    grep -Fq '"updates":"1"' "$TMPDIR/orca-wrapper.json"
    grep -Fq "\"python3\":\"${pythonWithAtspi}/bin/python3\"" "$TMPDIR/orca-wrapper.json"

    # The packaged updater export must never perform a network request or
    # return a downloadable artifact. Its compatibility events still let the
    # upstream update UI settle cleanly at "not available".
    ELECTRON_RUN_AS_NODE=1 \
      "$out/bin/orca-ide" -e '
        (async () => {
        const updater = require(
          "'$out'/lib/orca-ide/resources/node_modules/electron-updater/out/main.js",
        ).autoUpdater;
        if (updater.constructor.name !== "NixDisabledUpdater") {
          throw new Error(`unexpected updater: \''${updater.constructor.name}`);
        }
        let settled = false;
        updater.on("update-not-available", () => {
          settled = true;
        });
        updater.setFeedURL({ provider: "generic", url: "https://invalid.example/" });
        const result = await updater.checkForUpdates();
        if (result.isUpdateAvailable || result.cancellationToken !== null) {
          throw new Error("disabled updater returned an update");
        }
        await updater.downloadUpdate().then(
          () => {
            throw new Error("disabled updater returned a downloadable artifact");
          },
          (error) => {
            if (!String(error).includes("managed by Nix")) {
              throw new Error(`unexpected download rejection: \''${error}`);
            }
          },
        );
        let installRejected = false;
        try {
          updater.quitAndInstall();
        } catch (error) {
          installRejected = String(error).includes("managed by Nix");
        }
        if (!installRejected) {
          throw new Error("disabled updater allowed installation");
        }
        updater.addAuthHeader("Authorization", "secret-must-not-persist");
        setImmediate(() => {
          if (!settled) throw new Error("disabled updater did not settle");
        });
        })();
      '

    # Verify the packaged Linux Computer Use interpreter can load the
    # PyGObject/AT-SPI modules without requiring a live graphical session.
    GI_TYPELIB_PATH="${giTypelibPath}" \
      "${pythonWithAtspi}/bin/python3" -c '
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        assert Atspi is not None
      '

    # Run the sidecar's protocol handshake and one real AT-SPI operation
    # through a private session bus and Xvfb. The X server is started with an
    # active readiness check rather than a fixed sleep. This catches missing
    # DBus service discovery, AT-SPI activation, typelibs, and X11 wiring
    # without depending on the build host's graphical login.
    handshake_input="$TMPDIR/orca-handshake.json"
    handshake_output="$TMPDIR/orca-handshake-output.json"
    list_apps_input="$TMPDIR/orca-list-apps.json"
    list_apps_output="$TMPDIR/orca-list-apps-output.json"
    runtime_dir="$TMPDIR/orca-xdg-runtime"
    mkdir -m 700 "$runtime_dir"
    printf "%s\n" '{"tool":"handshake"}' > "$handshake_input"
    printf "%s\n" '{"tool":"list_apps"}' > "$list_apps_input"
    xvfb_display_file="$TMPDIR/orca-xvfb-display"
    xvfb_log="$TMPDIR/orca-xvfb.log"
    ${buildPackages.xvfb}/bin/Xvfb -displayfd 3 \
      -screen 0 1280x720x24 -nolisten tcp -noreset \
      >"$xvfb_log" 2>&1 3>"$xvfb_display_file" &
    xvfb_pid="$!"
    cleanup_xvfb() {
      kill "$xvfb_pid" 2>/dev/null || true
      wait "$xvfb_pid" 2>/dev/null || true
    }
    trap cleanup_xvfb EXIT
    for _ in $(seq 1 100); do
      if test -s "$xvfb_display_file"; then
        break
      fi
      if ! kill -0 "$xvfb_pid" 2>/dev/null; then
        cat "$xvfb_log" >&2
        exit 1
      fi
      sleep 0.05
    done
    test -s "$xvfb_display_file"
    xvfb_display=":$(cat "$xvfb_display_file")"
    test -S "/tmp/.X11-unix/X''${xvfb_display#:}"
    atspi_data_dirs="${
      lib.makeSearchPath "share" [
        atSpi2Core
        dbus
      ]
    }"
    atspi_env=(
      "DISPLAY=$xvfb_display"
      "XDG_SESSION_TYPE=x11"
      "XDG_RUNTIME_DIR=$runtime_dir"
      "XDG_DATA_DIRS=$atspi_data_dirs"
      "GI_TYPELIB_PATH=${giTypelibPath}"
      "PATH=${lib.makeBinPath desktopTools}"
    )
    XDG_RUNTIME_DIR="$runtime_dir" \
      DISPLAY="$xvfb_display" \
      XDG_SESSION_TYPE=x11 \
      XDG_DATA_DIRS="$atspi_data_dirs" \
      ${buildPackages.dbus}/bin/dbus-run-session \
      --config-file="${buildPackages.dbus}/share/dbus-1/session.conf" -- \
        env GI_TYPELIB_PATH="${giTypelibPath}" \
          PATH="${lib.makeBinPath desktopTools}" \
        "${pythonWithAtspi}/bin/python3" \
        "$out/lib/orca-ide/resources/computer-use-linux/runtime.py" \
        "$handshake_input" > "$handshake_output"
    grep -Fq '"ok":true' "$handshake_output"
    grep -Fq '"provider":"orca-computer-use-linux"' "$handshake_output"
    XDG_RUNTIME_DIR="$runtime_dir" \
      DISPLAY="$xvfb_display" \
      XDG_SESSION_TYPE=x11 \
      XDG_DATA_DIRS="$atspi_data_dirs" \
      ${buildPackages.dbus}/bin/dbus-run-session \
      --config-file="${buildPackages.dbus}/share/dbus-1/session.conf" -- \
      env "''${atspi_env[@]}" \
        "${pythonWithAtspi}/bin/python3" \
        "$out/lib/orca-ide/resources/computer-use-linux/runtime.py" \
        "$list_apps_input" > "$list_apps_output"
    grep -Fq '"ok":true' "$list_apps_output"
    grep -Fq '"apps":[]' "$list_apps_output"
    trap - EXIT
    cleanup_xvfb

    runHook postInstallCheck
  '';

  meta = {
    description = "AI orchestrator for parallel agentic development";
    homepage = "https://onorca.dev";
    changelog = "https://github.com/stablyai/orca/releases/tag/v${version}";
    license = lib.licenses.mit;
    mainProgram = "orca-ide";
    platforms = builtins.attrNames sources;
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
})
