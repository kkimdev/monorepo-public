"""Check the ASAR rewrite: integrity, offsets, desktop identity and preflight.

These fixtures require neither a downloaded Orca package nor a running GUI.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest


PATCH_FILE = Path(__file__).resolve().parents[1] / "patch-orca.py"
SPEC = importlib.util.spec_from_file_location("orca_patch", PATCH_FILE)
ORCA_PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORCA_PATCH)


def integrity(payload, block_size=16):
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(payload).hexdigest(),
        "blockSize": block_size,
        "blocks": [
            hashlib.sha256(payload[i:i + block_size]).hexdigest()
            for i in range(0, len(payload), block_size)
        ],
    }


def encode_archive(header, content):
    encoded = json.dumps(header, separators=(",", ":")).encode()
    string_payload = struct.pack("<I", len(encoded)) + encoded + b"\0" * (-len(encoded) % 4)
    header_pickle = struct.pack("<I", len(string_payload)) + string_payload
    return struct.pack("<II", 4, len(header_pickle)) + header_pickle + content


def decode_archive(path):
    payload = path.read_bytes()
    _, header_size = struct.unpack_from("<II", payload)
    json_size = struct.unpack_from("<I", payload, 12)[0]
    return json.loads(payload[16:16 + json_size]), payload[8 + header_size:]


def lookup(header, relative):
    current = header
    for part in Path(relative).parts:
        current = current["files"][part]
    return current


class AsarPatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="orca-patch-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / "app.asar"
        self.desktop = self.root / "orca-ide.desktop"
        self.desktop.write_text("[Desktop Entry]\nName=Orca\nStartupWMClass=orca\nExec=orca-ide %U\n")
        self.unpacked = self.root / "app.asar.unpacked"

    def fixture(self, unpacked=False):
        self.original_asset = b"/* Unicode selection: \xe2\x98\x83 */\ncopyOriginal(selection);\n"
        self.asset_path = "out/renderer/assets/TestCopy-reviewed.js"
        entries = [
            ("untouched-before.bin", b"before\x00\xff", False),
            ("package.json", b'{"name":"orca","version":"1.4.255"}', False),
            (self.asset_path, self.original_asset, unpacked),
            ("untouched-after.bin", b"after\x00\xfe", False),
        ]
        header = {"files": {}}
        content = bytearray()
        for relative, payload, is_unpacked in entries:
            parts = Path(relative).parts
            parent = header
            for part in parts[:-1]:
                parent = parent["files"].setdefault(part, {"files": {}})
            entry = {"size": len(payload), "integrity": integrity(payload)}
            if is_unpacked:
                entry["unpacked"] = True
                target = self.unpacked / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                target.chmod(0o751)
            else:
                entry["offset"] = str(len(content))
                content.extend(payload)
            parent["files"][parts[-1]] = entry
        self.archive.write_bytes(encode_archive(header, content))
        self.patches = ((
            "out/renderer/assets/TestCopy-", hashlib.sha256(self.original_asset).hexdigest(),
            (("copyOriginal(selection)", "await nativeCopySelection(selection)"),),
        ),)
        return header, bytes(content)

    def entry_payload(self, header, content, relative):
        entry = lookup(header, relative)
        if entry.get("unpacked"):
            return (self.unpacked / relative).read_bytes()
        start = int(entry["offset"])
        return content[start:start + entry["size"]]

    def assert_integrity(self, entry, payload):
        self.assertEqual(entry["size"], len(payload))
        self.assertEqual(entry["integrity"], integrity(payload, entry["integrity"]["blockSize"]))

    def test_packed_and_unpacked_patches_keep_other_offsets_and_refresh_integrity(self):
        for unpacked in (False, True):
            with self.subTest(unpacked=unpacked):
                before, original_content = self.fixture(unpacked)
                ORCA_PATCH.patch(self.archive, self.desktop, self.patches)
                after, content = decode_archive(self.archive)
                # Appending replacement records must preserve every original byte.
                self.assertTrue(content.startswith(original_content))
                for relative in ("untouched-before.bin", "untouched-after.bin"):
                    self.assertEqual(lookup(after, relative), lookup(before, relative))
                    self.assertEqual(
                        self.entry_payload(after, content, relative),
                        self.entry_payload(before, original_content, relative),
                    )
                package = self.entry_payload(after, content, "package.json")
                self.assertEqual(json.loads(package)["desktopName"], self.desktop.name)
                self.assert_integrity(lookup(after, "package.json"), package)
                patched_asset = self.entry_payload(after, content, self.asset_path)
                self.assertEqual(
                    patched_asset,
                    self.original_asset.replace(b"copyOriginal(selection)", b"await nativeCopySelection(selection)"),
                )
                asset_entry = lookup(after, self.asset_path)
                self.assert_integrity(asset_entry, patched_asset)
                if unpacked:
                    self.assertIs(asset_entry["unpacked"], True)
                    self.assertNotIn("offset", asset_entry)
                    self.assertEqual(stat.S_IMODE((self.unpacked / self.asset_path).stat().st_mode), 0o751)
                else:
                    self.assertGreaterEqual(int(asset_entry["offset"]), len(original_content))
                self.assertEqual(
                    self.desktop.read_text(),
                    "[Desktop Entry]\nName=Orca\nStartupWMClass=orca-ide\nExec=orca-ide %U\n",
                )

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            for path in self.root.rglob("*") if path.is_file()
        }

    def test_unknown_second_asset_fails_before_any_packed_or_unpacked_write(self):
        for unpacked in (False, True):
            # Cover both an unknown name and a directory absent from the archive.
            for prefix in ("out/renderer/assets/UnknownCopy-", "out/preload/index"):
                with self.subTest(unpacked=unpacked, prefix=prefix):
                    self.fixture(unpacked)
                    before = self.snapshot()
                    unknown = (prefix, "0" * 64, (("old", "new"),))
                    with self.assertRaisesRegex(AssertionError, "Re-evaluate Orca 1.4.255"):
                        ORCA_PATCH.patch(self.archive, self.desktop, self.patches + (unknown,))
                    self.assertEqual(self.snapshot(), before)

    def test_invalid_desktop_fails_after_asset_preflight_without_writing(self):
        self.fixture(unpacked=True)
        self.desktop.write_text("[Desktop Entry]\nStartupWMClass=unrelated-app\n")
        before = self.snapshot()
        with self.assertRaisesRegex(AssertionError, "Unexpected window class"):
            ORCA_PATCH.patch(self.archive, self.desktop, self.patches)
        self.assertEqual(self.snapshot(), before)

    def test_corrupt_unpacked_asset_is_rejected_before_writing(self):
        self.fixture(unpacked=True)
        asset = self.unpacked / self.asset_path
        asset.write_bytes(self.original_asset.replace(b"copyOriginal", b"copyTampered"))
        before = self.snapshot()
        with self.assertRaisesRegex(AssertionError, "Invalid ASAR integrity"):
            ORCA_PATCH.patch(self.archive, self.desktop, self.patches)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_replacement_route_does_not_write_package_metadata(self):
        self.fixture()
        prefix, expected_hash, _ = self.patches[0]
        bad_route = ((prefix, expected_hash, (("routeWhichDoesNotExist()", "newRoute()"),)),)
        before = self.snapshot()
        with self.assertRaisesRegex(AssertionError, "Expected one replacement route"):
            ORCA_PATCH.patch(self.archive, self.desktop, bad_route)
        self.assertEqual(self.snapshot(), before)

    def test_the_shipped_patch_only_sets_the_desktop_identity(self):
        before, original_content = self.fixture()
        ORCA_PATCH.patch(self.archive, self.desktop)
        after, content = decode_archive(self.archive)
        self.assertTrue(content.startswith(original_content))
        self.assertEqual(lookup(after, self.asset_path), lookup(before, self.asset_path))
        package = self.entry_payload(after, content, "package.json")
        self.assertEqual(json.loads(package)["desktopName"], self.desktop.name)
        self.assertIn("StartupWMClass=orca-ide\n", self.desktop.read_text())


if __name__ == "__main__":
    unittest.main()
