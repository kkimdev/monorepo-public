#!/usr/bin/env python3
"""Offline packaging checks; no compiler, network or real devices required."""

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("cros_kit_build", Path(__file__).with_name("build.py"))
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def template():
    image = bytearray(512 + 8192)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 60, 128)
    image[128:132] = b"PE\0\0"
    struct.pack_into("<HH", image, 132, 0x8664, 1)
    struct.pack_into("<H", image, 148, 240)
    struct.pack_into("<H", image, 152, 0x20B)
    image[392:400] = b".vbpubk\0"
    struct.pack_into("<IIII", image, 400, 8192, 4096, 8192, 512)
    return image


class PackagingTests(unittest.TestCase):
    def test_template_requires_zero_key_and_reproducible_timestamp(self):
        image = template()
        build.validate_template(image)
        image[512] = 1
        with self.assertRaisesRegex(ValueError, "zero-filled"):
            build.validate_template(image)
        image[512] = 0
        struct.pack_into("<I", image, 136, 1)
        with self.assertRaisesRegex(ValueError, "timestamp"):
            build.validate_template(image)

    def test_truncated_section_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Truncated"):
            build.validate_template(template()[:-1])
        with self.assertRaisesRegex(ValueError, "Truncated"):
            build.validate_template(template()[:410])

    def test_package_records_current_inputs_and_actual_bytes(self):
        with TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "Cargo.lock").write_text("locked dependencies\n")
            artifact = source / "crdyboot.efi"
            artifact.write_bytes(template())
            output = source / "out"
            provenance = {"inputs": {"crdyboot": {"rev": "locked-revision"}},
                          "recipe_sha256": "recipe-hash"}
            with patch.object(build, "notices") as notices, patch.object(
                    build, "run", return_value=SimpleNamespace(stdout="pinned tool 1.0\n")):
                build.package(source, artifact, output, provenance)
            notices.assert_called_once_with(source, output / "THIRD_PARTY_NOTICES.txt")
            info = json.loads((output / "build-info.json").read_text())
            self.assertEqual(info["inputs"], provenance["inputs"])
            self.assertEqual(info["recipe_sha256"], "recipe-hash")
            self.assertNotIn("cargo_vendor_hash", info)
            self.assertEqual(info["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
            self.assertEqual(info["cargo_lock_sha256"],
                             hashlib.sha256(b"locked dependencies\n").hexdigest())
            self.assertEqual(info["bytes"], artifact.stat().st_size)
            self.assertNotIn("pins_sha256", info)
            self.assertNotIn("environment_sha256", info)
            self.assertEqual((output / "crdyboot-debug.efi").read_bytes(), artifact.read_bytes())
            self.assertEqual((output / "crdyboot-debug.efi.sha256").read_text(),
                             f"{info['sha256']}  crdyboot-debug.efi\n")


if __name__ == "__main__":
    unittest.main()
