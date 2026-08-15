{ lib
, appimageTools
, buildPackages
, fetchurl
, procps
, stdenv
}:

let
  version = "1.4.179";
  sources = {
    "x86_64-linux" = {
      url = "https://github.com/stablyai/orca/releases/download/v1.4.179/orca-linux.AppImage";
      hash = "sha256-B4CEhW22bSmya1dguIAo3pWv/NdxQxNZ7uNpJCl7EN8=";
    };
    "aarch64-linux" = {
      url = "https://github.com/stablyai/orca/releases/download/v1.4.179/orca-linux-arm64.AppImage";
      hash = "sha256-Toa2/8DymRlMDfuGsRDnhRdNk6XTGo4nFthrfFa+WRQ=";
    };
  };
  source = sources.${stdenv.hostPlatform.system} or (
    throw "orca-ide: unsupported system ${stdenv.hostPlatform.system}"
  );
  src = fetchurl source;
  extracted = appimageTools.extract {
    pname = "orca-ide";
    inherit version src;
    # Electron derives the native Wayland app ID from its desktop name. Upstream
    # defaults that value to `orca.desktop`, while its launcher is named
    # `orca-ide.desktop`; Crostini therefore cannot associate the running window
    # with the exported icon. Patch the desktop name before the first window is
    # created, preserving the ASAR offsets and updating its integrity metadata.
    postExtract = ''
      ${buildPackages.python3}/bin/python3 - "$out/resources/app.asar" <<'PY'
      import hashlib
      import json
      import struct
      import sys


      archive_path = sys.argv[1]
      old = b"electron.app.setName(devInstanceIdentity.appName);"
      new = b'electron.app.setDesktopName("orca-ide.desktop");'

      with open(archive_path, "r+b") as archive:
          payload = bytearray(archive.read())

          header_size = struct.unpack("<I", payload[12:16])[0]
          header = json.loads(payload[16 : 16 + header_size])
          entry = header["files"]["out"]["files"]["main"]["files"]["index.js"]
          data_start = 16 + ((header_size + 3) & ~3) + int(entry["offset"])
          source = bytearray(payload[data_start : data_start + int(entry["size"])])

          assert source.count(old) == 1, "expected one Electron app-name assignment"
          assert len(new) <= len(old), (len(old), len(new))
          replacement = new + b" " * (len(old) - len(new))
          offset = source.index(old)
          source[offset : offset + len(old)] = replacement

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
          assert all(
              len(digest) == len(previous) == 64
              for digest, previous in zip(block_digests, integrity["blocks"])
          )
          integrity["hash"] = source_digest
          integrity["blocks"] = block_digests

          replacement_header = json.dumps(
              header,
              separators=(",", ":"),
          ).encode("utf-8")
          assert len(replacement_header) == header_size, (
              len(replacement_header),
              header_size,
          )

          payload[data_start : data_start + len(source)] = source
          payload[16 : 16 + header_size] = replacement_header
          archive.seek(0)
          archive.write(payload)
      PY
    '';
  };
in
appimageTools.wrapAppImage {
  pname = "orca-ide";
  inherit version;
  src = extracted;

  # Orca enumerates processes with `ps`; the default AppImage FHS does not
  # include procps.
  extraPkgs = _: [ procps ];

  # The wrapped AppImage does not export desktop metadata on its own, so copy
  # the upstream launcher and icons into the Nix profile for desktop discovery.
  # Keep every upstream size: Garcon's fallback icon search does not include a
  # 512px-only directory when the package-specific hicolor root has no index.
  extraInstallCommands = ''
    install -Dm444 ${extracted}/orca-ide.desktop \
      $out/share/applications/orca-ide.desktop
    mkdir -p $out/share/icons/hicolor
    cp -a ${extracted}/usr/share/icons/hicolor/. \
      $out/share/icons/hicolor/
    substituteInPlace $out/share/applications/orca-ide.desktop \
      --replace-fail "Exec=AppRun --no-sandbox %U" "Exec=orca-ide --no-sandbox %U"
  '';

  meta = with lib; {
    description = "AI orchestrator for parallel agentic development";
    homepage = "https://onorca.dev";
    license = licenses.mit;
    mainProgram = "orca-ide";
    platforms = [ "x86_64-linux" "aarch64-linux" ];
  };
}
