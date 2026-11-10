#!/usr/bin/env python3
"""Keep Crostini native copies and desktop identity on the upstream Orca package.

Fingerprints cover the renderer behavior reviewed for Orca 1.4.255. A future
release with changed assets must be reviewed before it can replace an installed
package. Unchanged assets do not require a hard-coded application version.
Remove the clipboard workaround after stablyai/orca#29377 is released.
"""

import hashlib
import json
from pathlib import Path
import struct
import sys


ORIGINAL_HELPER = (
    "async function Av({terminal:e,writeClipboardText:t,clearSelectionOnSuccess:n=!1})"
    "{let r=Wp(e);return r?(await t(r),n&&e.clearSelection(),!0):!1}"
)
# Native IPC has no delivery acknowledgment. Keep clear-on-success copies on
# their awaited writer so selection cannot disappear before Electron reads it.
PATCHED_HELPER = ORIGINAL_HELPER.replace(
    "await t(r)",
    "await(!n&&window.api.platform.get().platform===`linux`&&"
    "window.api.platform.get().displayServer===`wayland`&&e.textarea&&"
    "e.textarea.ownerDocument.activeElement===e.textarea?"
    "window.api.ui.performNativeSelectionAction(`copy`):t(r))",
)
PATCHES = (
    (
        "terminal-right-click-paste-gesture-",
        "01836e5a811fff8c9dd92599aeb6357e81fe382caed4af7352d4b74cfe06874d",
        ((ORIGINAL_HELPER, PATCHED_HELPER),),
    ),
    (
        "open-standalone-terminal-",
        "784e4ba79dc5bac3671f3f16735ede1cb4da3b34f3a8a6dc2a67ad2ee9905822",
        ((
            "window.api.ui.writeTerminalClipboardText(t).catch(()=>void 0)",
            "j({terminal:n,writeClipboardText:window.api.ui.writeTerminalClipboardText}).catch(()=>void 0)",
        ),),
    ),
)


def patch(archive, desktop, patches=PATCHES):
    archive, desktop = Path(archive), Path(desktop)
    desktop_name = desktop.name
    window_class = desktop.stem
    payload = archive.read_bytes()
    size_payload, header_size = struct.unpack_from("<II", payload)
    assert size_payload == 4, "Unexpected ASAR size header"
    json_size = struct.unpack_from("<I", payload, 12)[0]
    header = json.loads(payload[16:16 + json_size])
    content = payload[8 + header_size:]
    appended = bytearray()
    unpacked_writes = []
    unpacked_root = archive.with_name(archive.name + ".unpacked")

    def read_entry(entry, relative):
        if entry.get("unpacked"):
            original = (unpacked_root / relative).read_bytes()
        else:
            start = int(entry["offset"])
            original = content[start:start + entry["size"]]
        assert len(original) == entry["size"], f"Truncated ASAR entry: {relative}"
        integrity = entry["integrity"]
        assert integrity["algorithm"] == "SHA256", "Unsupported ASAR integrity"
        assert hashlib.sha256(original).hexdigest() == integrity["hash"], f"Invalid ASAR integrity: {relative}"
        return original

    def replace_entry(entry, relative, replacement):
        # Preserve offsets for all original packed entries. Unpacked resources
        # remain unpacked and keep their physical path and file mode.
        if entry.get("unpacked"):
            unpacked_writes.append((unpacked_root / relative, replacement))
        else:
            entry["offset"] = str(len(content) + len(appended))
            appended.extend(replacement)
        entry["size"] = len(replacement)
        integrity = entry["integrity"]
        block_size = integrity["blockSize"]
        integrity["hash"] = hashlib.sha256(replacement).hexdigest()
        integrity["blocks"] = [
            hashlib.sha256(replacement[i:i + block_size]).hexdigest()
            for i in range(0, len(replacement), block_size)
        ]

    package_entry = header["files"]["package.json"]
    metadata = json.loads(read_entry(package_entry, "package.json"))
    assert metadata["name"] == "orca", "Unexpected application metadata"
    previous = metadata.get("desktopName")
    assert previous in (None, desktop_name), f"Unexpected desktopName: {previous!r}"
    if previous is None:
        metadata["desktopName"] = desktop_name
        replacement = (json.dumps(metadata, separators=(",", ":")) + "\n").encode()
        replace_entry(package_entry, "package.json", replacement)

    assets = header["files"]["out"]["files"]["renderer"]["files"]["assets"]["files"]
    for prefix, expected_hash, routes in patches:
        matches = [
            (name, entry) for name, entry in assets.items()
            if name.startswith(prefix) and name.endswith(".js")
            and entry.get("integrity", {}).get("hash") == expected_hash
        ]
        assert len(matches) == 1, (
            f"Re-evaluate Orca {metadata['version']} clipboard compatibility: {prefix}"
        )
        name, entry = matches[0]
        relative = Path("out/renderer/assets") / name
        original = read_entry(entry, relative)
        assert hashlib.sha256(original).hexdigest() == expected_hash, f"Unexpected asset: {name}"
        text = original.decode()
        for old, new in routes:
            assert text.count(old) == 1, f"Expected one native-copy route: {name}"
            text = text.replace(old, new)
        replace_entry(entry, relative, text.encode())

    lines = desktop.read_text().splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith("StartupWMClass=")]
    assert len(matches) == 1, "Expected one desktop window class"
    index = matches[0]
    previous_class = lines[index].split("=", 1)[1].strip()
    assert previous_class in ("orca", window_class), f"Unexpected window class: {previous_class!r}"
    lines[index] = f"StartupWMClass={window_class}\n"

    # Validate the complete patch before writing any output. Nix then publishes
    # the whole package only when every build/fixup check has succeeded.
    for path, replacement in unpacked_writes:
        path.chmod(path.stat().st_mode | 0o200)
        path.write_bytes(replacement)
    encoded = json.dumps(header, separators=(",", ":")).encode()
    string_payload = struct.pack("<I", len(encoded)) + encoded + b"\0" * (-len(encoded) % 4)
    header_pickle = struct.pack("<I", len(string_payload)) + string_payload
    archive.chmod(archive.stat().st_mode | 0o200)
    archive.write_bytes(struct.pack("<II", 4, len(header_pickle)) + header_pickle + content + appended)
    desktop.chmod(desktop.stat().st_mode | 0o200)
    desktop.write_text("".join(lines))
    print(f"Patched Orca {metadata['version']}: desktop identity and focused native clipboard")


if __name__ == "__main__":
    patch(*sys.argv[1:])
