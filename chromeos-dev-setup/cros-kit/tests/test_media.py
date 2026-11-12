"""Offline admission for the locked Flex archive and Nix-built live image."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cros_kit import make_usb as builder

PARAMS = "init=/nix/store/abc-nixos-system-cros-kit/init boot.shell_on_fail"


def live_image():
    image = bytearray(20 * 2048)
    image[16 * 2048:16 * 2048 + 7] = b"\x01CD001\x01"
    image[16 * 2048 + 40:16 * 2048 + 72] = b"CROSKIT_LIVE".ljust(32, b" ")
    return image


class LockedInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / "flex.zip"
        self.archive.write_bytes(b"locked flex")
        self.live = self.root / "live"
        self.live.mkdir()
        self.iso = self.live / "image.iso"
        self.iso.write_bytes(live_image())
        (self.live / "kernel-params").write_text(PARAMS + "\n")
        self.media = dict(format=2, flex=dict(
            channel="STABLE", model="ChromeOS Flex", version=builder.installer.compatibility()["FLEX_OS_VERSION"],
            chrome_version="156.0.8078.93", file="recovery.bin", filesize=100,
            url="https://dl.google.com/recovery.bin.zip", sha1="a" * 40,
            zipfilesize=self.archive.stat().st_size,
            sha256=hashlib.sha256(self.archive.read_bytes()).hexdigest()))
        self.environment = {"CROS_KIT_FLEX_ARCHIVE": str(self.archive), "CROS_KIT_LIVE": str(self.live)}

    def test_exact_inputs_work_with_network_unavailable(self):
        with patch.dict(os.environ, self.environment), patch(
                "urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
            self.assertEqual(builder.locked_inputs(self.media), (self.archive, self.live))

    def test_changed_archive_is_rejected_even_at_same_size(self):
        self.archive.write_bytes(bytes(self.archive.stat().st_size))
        with patch.dict(os.environ, self.environment):
            with self.assertRaisesRegex(ValueError, "checksum"):
                builder.locked_inputs(self.media)

    def test_wrong_filesystem_signature_volume_label_and_truncation_refuse(self):
        original = self.iso.read_bytes()
        for offset, data in ((16 * 2048, b"badiso!"), (16 * 2048 + 40, b"OTHER_LABEL")):
            changed = bytearray(original)
            changed[offset:offset + len(data)] = data
            self.iso.write_bytes(changed)
            with self.subTest(offset=offset), patch.dict(os.environ, self.environment):
                with self.assertRaisesRegex(ValueError, "live filesystem"):
                    builder.locked_inputs(self.media)
        self.iso.write_bytes(original[:-1])
        with patch.dict(os.environ, self.environment):
            with self.assertRaisesRegex(ValueError, "live filesystem"):
                builder.locked_inputs(self.media)

    def test_missing_nix_inputs_never_falls_back_to_network(self):
        with patch.dict(os.environ, {}, clear=True), patch("urllib.request.urlopen") as network:
            with self.assertRaisesRegex(ValueError, "nix run"):
                builder.locked_inputs(self.media)
        network.assert_not_called()

    def test_old_media_lock_requires_explicit_update(self):
        self.media["format"] = 1
        (self.root / "media").mkdir()
        (self.root / "media/media.json").write_text(json.dumps(self.media))
        with patch.object(builder, "HERE", self.root):
            with self.assertRaisesRegex(ValueError, "lock format"):
                builder.locked_media()

    def test_kernel_parameters_cannot_inject_grub_commands_or_omit_system_init(self):
        for params in (PARAMS + "\nhalt", PARAMS + "; halt", PARAMS + " $(halt)", "quiet"):
            with self.subTest(params=params), self.assertRaisesRegex(ValueError, "kernel parameters"):
                builder.grub_config(params)


if __name__ == "__main__":
    unittest.main()
