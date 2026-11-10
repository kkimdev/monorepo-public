"""Preparation boundaries only; no Chrome launch, host changes, or network."""

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("touchpad_prepare", ROOT / "touchpad/prepare.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class TouchpadPreparation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="touchpad-prepare-test-")
        self.addCleanup(temporary.cleanup)
        self.native = Path(temporary.name) / "shared native folder"
        self.native.mkdir()
        self.manifest = {
            "manifest_version": 3,
            "version": "0.82.9828.27275",
            "background": {"service_worker": "js/nassh_background_main.js", "type": "module"},
            "permissions": ["storage", "terminalPrivate"],
            "update_url": "https://example.test/update",
        }
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("manifest.json", json.dumps(self.manifest))
            archive.writestr("js/nassh_background_main.js", "// upstream background\n")
            archive.writestr("LICENSE", "upstream license fixture\n")
            archive.writestr("unrelated/ssh.js", "// upstream SSH fixture\n")
        self.crx = data.getvalue()
        self.output = io.StringIO()

    def run_prepare(self):
        with redirect_stdout(self.output):
            return prepare.prepare(self.native)

    def prepare_fixture(self):
        with patch.object(prepare, "CRX_SHA256", hashlib.sha256(self.crx).hexdigest()), \
                patch.object(prepare.urllib.request, "urlopen", return_value=io.BytesIO(self.crx)):
            return self.run_prepare()

    def snapshot(self, destination):
        return {p.relative_to(destination): p.read_bytes() for p in destination.rglob("*") if p.is_file()}

    def test_missing_share_explains_files_step_without_downloading_or_writing(self):
        self.native.rmdir()
        with patch.object(prepare.urllib.request, "urlopen") as download:
            with self.assertRaisesRegex(ValueError, "My files/cros-touchpad.*Share with Linux"):
                self.run_prepare()
            download.assert_not_called()
        self.assertFalse(self.native.exists())

    def test_foreign_existing_directory_is_never_replaced(self):
        destination = self.native / "extension"
        destination.mkdir()
        (destination / "personal.txt").write_text("keep this")
        with patch.object(prepare.urllib.request, "urlopen") as download:
            with self.assertRaisesRegex(ValueError, "nothing was replaced"):
                self.run_prepare()
            download.assert_not_called()
        self.assertEqual((destination / "personal.txt").read_text(), "keep this")

    def test_network_failure_leaves_no_partial_extension(self):
        with patch.object(prepare.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "offline"):
                self.run_prepare()
        self.assertEqual(list(self.native.iterdir()), [])

    def test_wrong_download_leaves_no_partial_extension_or_staging(self):
        with patch.object(prepare.urllib.request, "urlopen", return_value=io.BytesIO(b"wrong download")):
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                self.run_prepare()
        self.assertEqual(list(self.native.iterdir()), [])

    def test_prepares_complete_folder_without_adding_permissions(self):
        destination = self.prepare_fixture()
        self.assertEqual(destination, self.native / "extension")
        self.assertEqual(sorted(p.name for p in self.native.iterdir()), ["extension"])
        manifest = json.loads((destination / "manifest.json").read_text())
        self.assertEqual(manifest["permissions"], self.manifest["permissions"])
        self.assertEqual(manifest["key"], prepare.DEVELOPMENT_KEY)
        self.assertNotIn("update_url", manifest)
        self.assertEqual((destination / "LICENSE").read_text(), "upstream license fixture\n")
        self.assertEqual((destination / "unrelated/ssh.js").read_text(), "// upstream SSH fixture\n")
        self.assertIn("import '../touchpad/startup.js'", (destination / "js/nassh_background_main.js").read_text())
        for name in prepare.ASSETS:
            self.assertEqual((destination / "touchpad" / name).read_bytes(), (prepare.SOURCE / name).read_bytes())
        self.assertIn("Chrome installation is still required", self.output.getvalue())

    def test_rerun_copies_updated_checkout_repairs_missing_asset_and_requests_reload(self):
        destination = self.prepare_fixture()
        (destination / "touchpad/page.html").unlink()
        source = self.native.parent / "updated checkout"
        source.mkdir()
        for name in prepare.ASSETS:
            (source / name).write_bytes((prepare.SOURCE / name).read_bytes())
        with (source / "apply.mjs").open("a") as output:
            output.write("\n// Updated checkout source\n")
        with patch.object(prepare, "SOURCE", source):
            self.assertEqual(self.prepare_fixture(), destination)
        self.assertEqual((destination / "touchpad/apply.mjs").read_bytes(), (source / "apply.mjs").read_bytes())
        self.assertEqual((destination / "touchpad/page.html").read_bytes(), (source / "page.html").read_bytes())
        self.assertEqual(sorted(p.name for p in self.native.iterdir()), ["extension"])
        self.assertIn("Touchpad startup → Reload", self.output.getvalue())

    def test_failed_update_download_preserves_loaded_extension(self):
        destination = self.prepare_fixture()
        before = self.snapshot(destination)
        with patch.object(prepare.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "offline"):
                self.run_prepare()
        self.assertEqual(self.snapshot(destination), before)
        self.assertEqual(sorted(p.name for p in self.native.iterdir()), ["extension"])

    def test_failed_update_publish_restores_loaded_extension(self):
        destination = self.prepare_fixture()
        before = self.snapshot(destination)
        original_rename = Path.rename

        def fail_publish(path, target):
            if path.name == "extension" and path.parent.name.startswith(".prepare-"):
                raise OSError("replacement failed")
            return original_rename(path, target)

        with patch.object(Path, "rename", fail_publish):
            with self.assertRaisesRegex(OSError, "replacement failed"):
                self.prepare_fixture()
        self.assertEqual(self.snapshot(destination), before)
        self.assertEqual(sorted(p.name for p in self.native.iterdir()), ["extension"])


if __name__ == "__main__":
    unittest.main()
