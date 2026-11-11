"""Locked input admission: exact bytes and package sets, without the network."""

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cros_kit import make_usb as builder


def sha(data):
    return hashlib.sha256(data).hexdigest()


class LockedInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive, self.iso = self.root / "flex.zip", self.root / "alpine.iso"
        self.archive.write_bytes(b"locked flex")
        self.iso.write_bytes(b"locked alpine")
        self.repository = self.root / "repository"
        packages = self.repository / "x86_64"
        packages.mkdir(parents=True)
        (packages / "APKINDEX.tar.gz").write_bytes(b"signed index")
        (packages / "python3-1.apk").write_bytes(b"locked package")
        self.media = dict(format=1, packages=list(builder.usb_live.PACKAGES), flex=dict(
            channel="STABLE", model="ChromeOS Flex", version=builder.installer.compatibility()["FLEX_OS_VERSION"],
            chrome_version="156.0.8078.93", file="recovery.bin", filesize=100,
            url="https://dl.google.com/recovery.bin.zip", sha1="a" * 40,
            zipfilesize=self.archive.stat().st_size, sha256=sha(self.archive.read_bytes())), alpine=dict(
            version="3.24.4", sha256=sha(self.iso.read_bytes()), size=self.iso.stat().st_size,
            index_sha256=sha(b"signed index"), packages=[dict(filename="python3-1.apk",
                size=len(b"locked package"), sha256=sha(b"locked package"))]))
        self.environment = {"CROS_KIT_FLEX_ARCHIVE": str(self.archive),
                            "CROS_KIT_ALPINE_ISO": str(self.iso),
                            "CROS_KIT_ALPINE_REPO": str(self.repository)}

    def test_exact_inputs_work_with_network_unavailable(self):
        with patch.dict(os.environ, self.environment), patch(
                "urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
            self.assertEqual(builder.locked_inputs(self.media), (self.archive, self.iso, self.repository))

    def test_changed_archive_iso_index_and_packages_are_rejected(self):
        for path in (self.archive, self.iso, self.repository / "x86_64/APKINDEX.tar.gz",
                     self.repository / "x86_64/python3-1.apk"):
            original = path.read_bytes()
            path.write_bytes(bytes(len(original)))  # same size, different hash
            with self.subTest(path=path), patch.dict(os.environ, self.environment):
                with self.assertRaisesRegex(ValueError, "checksum"):
                    builder.locked_inputs(self.media)
            path.write_bytes(original)

    def test_extra_or_missing_package_prevents_preparation(self):
        package = self.repository / "x86_64/extra.apk"
        package.write_bytes(b"unlocked")
        with patch.dict(os.environ, self.environment):
            with self.assertRaisesRegex(ValueError, "package set"):
                builder.locked_inputs(self.media)
        package.unlink()
        (self.repository / "x86_64/python3-1.apk").unlink()
        with patch.dict(os.environ, self.environment):
            with self.assertRaisesRegex(ValueError, "package set"):
                builder.locked_inputs(self.media)

    def test_missing_nix_inputs_never_falls_back_to_latest_downloads(self):
        with patch.dict(os.environ, {}, clear=True), patch("urllib.request.urlopen") as network:
            with self.assertRaisesRegex(ValueError, "nix run"):
                builder.locked_inputs(self.media)
        network.assert_not_called()

    def test_requested_package_change_requires_explicit_lock_update(self):
        media = copy.deepcopy(self.media)
        media["packages"].pop()
        (self.root / "media").mkdir()
        (self.root / "media/media.json").write_text(json.dumps(media))
        with patch.object(builder, "HERE", self.root):
            with self.assertRaisesRegex(ValueError, "package list changed"):
                builder.locked_media()


if __name__ == "__main__":
    unittest.main()
