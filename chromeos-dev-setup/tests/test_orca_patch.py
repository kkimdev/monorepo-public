"""Check ASAR integrity and execute the patched clipboard helper in Node.

These fixtures require neither a downloaded Orca package nor a running GUI.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import struct
import subprocess
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
            "TestCopy-", hashlib.sha256(self.original_asset).hexdigest(),
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
            with self.subTest(unpacked=unpacked):
                self.fixture(unpacked)
                before = self.snapshot()
                unknown = ("UnknownCopy-", "0" * 64, (("old", "new"),))
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
        with self.assertRaisesRegex(AssertionError, "Expected one native-copy route"):
            ORCA_PATCH.patch(self.archive, self.desktop, bad_route)
        self.assertEqual(self.snapshot(), before)


NODE_BEHAVIOR = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const patch = JSON.parse(process.argv[1]);

function fixture({platform='linux', displayServer='wayland', focused=true,
                  textarea=true, selection='selected text', writer, native}={}) {
  const calls = {writes: [], native: [], clears: 0};
  const terminal = {
    selection,
    clearSelection() { calls.clears++; this.selection = ''; },
    focus() { throw new Error('The clipboard helper must not steal focus'); }
  };
  if (textarea) {
    terminal.textarea = {ownerDocument: {}};
    terminal.textarea.ownerDocument.activeElement = focused ? terminal.textarea : {};
  }
  const writeClipboardText = text => { calls.writes.push(text); return writer?.(text); };
  const window = {api: {
    platform: {get: () => ({platform, displayServer})},
    ui: {performNativeSelectionAction(action) { calls.native.push(action); return native?.(action); }}
  }};
  const copy = vm.runInNewContext(`(${patch.helper})`, {window, Wp: e => e.selection});
  return {terminal, calls, copy, writeClipboardText};
}

(async () => {
  const selected = fixture();
  assert.equal(await selected.copy(selected), true);
  assert.deepEqual(selected.calls, {writes: [], native: ['copy'], clears: 0});
  assert.equal(selected.terminal.selection, 'selected text');

  for (const options of [
    {platform:'darwin'}, {platform:'win32'}, {displayServer:'x11'},
    {focused:false}, {textarea:false}
  ]) {
    const fallback = fixture(options);
    assert.equal(await fallback.copy(fallback), true);
    assert.deepEqual(fallback.calls, {writes:['selected text'], native:[], clears:0});
  }

  for (const clearSelectionOnSuccess of [false, true]) {
    const empty = fixture({selection:''});
    assert.equal(await empty.copy({...empty, clearSelectionOnSuccess}), false);
    assert.deepEqual(empty.calls, {writes:[], native:[], clears:0});
  }

  let finish;
  const pending = fixture({writer: () => new Promise(resolve => { finish = resolve; })});
  const completion = pending.copy({...pending, clearSelectionOnSuccess:true});
  assert.deepEqual(pending.calls, {writes:['selected text'], native:[], clears:0});
  assert.equal(pending.terminal.selection, 'selected text');
  finish();
  assert.equal(await completion, true);
  assert.equal(pending.calls.clears, 1);
  assert.equal(pending.terminal.selection, '');

  for (const options of [{focused:false}, {clearSelectionOnSuccess:true}]) {
    const failure = new Error('clipboard write failed');
    const rejected = fixture({...options, writer: () => Promise.reject(failure)});
    await assert.rejects(rejected.copy({...rejected, ...options}), error => error === failure);
    assert.deepEqual(rejected.calls, {writes:['selected text'], native:[], clears:0});
    assert.equal(rejected.terminal.selection, 'selected text');
  }

  const nativeFailure = new Error('native IPC failed');
  const rejectedNative = fixture({native: () => Promise.reject(nativeFailure)});
  await assert.rejects(rejectedNative.copy(rejectedNative), error => error === nativeFailure);
  assert.deepEqual(rejectedNative.calls, {writes:[], native:['copy'], clears:0});

  // The standalone handler must pass the original terminal and writer to the
  // shared helper, while retaining its existing rejected-promise handling.
  let routed;
  const terminal = {};
  const writer = () => {};
  await vm.runInNewContext(patch.standalone, {
    n:terminal,
    window:{api:{ui:{writeTerminalClipboardText:writer}}},
    j(options) { routed = options; return Promise.reject(new Error('handled by route')); }
  });
  assert.equal(routed.terminal, terminal);
  assert.equal(routed.writeClipboardText, writer);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "Node is required to execute Orca clipboard helper")
class ClipboardBehaviorTests(unittest.TestCase):
    def test_actual_patched_helper_native_fallback_async_and_selection_behavior(self):
        helper_route = ORCA_PATCH.PATCHES[0][2][0]
        self.assertEqual(helper_route, (ORCA_PATCH.ORIGINAL_HELPER, ORCA_PATCH.PATCHED_HELPER))
        result = subprocess.run(
            ["node", "-e", NODE_BEHAVIOR, json.dumps({
                "helper": ORCA_PATCH.PATCHED_HELPER,
                "standalone": ORCA_PATCH.PATCHES[1][2][0][1],
            })],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
