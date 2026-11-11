#!/usr/bin/env python3
"""Disk/EFI validation and firmware transaction tests; never access real devices."""

from contextlib import contextmanager, nullcontext
import hashlib
import os
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import zlib


from cros_kit import install as installer
from tests import SOURCE


HERE = SOURCE
REAL_VALIDATE_INSTALLATION = installer.validate_installation
ESP_UUID = "00000000-0000-4000-8000-000000000012"
OTHER_UUID = "00000000-0000-4000-8000-000000000099"


def packed_key():
    # Structurally valid RSA2048/SHA256 packed key (not a signing fixture).
    key = bytearray(32 + 520)
    struct.pack_into("<QQQQI", key, 0, 32, 520, 4, 1, 64)
    return bytes(key)


def pe_image(key=None, duplicate=False):
    count = 2 if duplicate else 1
    image = bytearray(512 + count * 8192)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 60, 128)
    image[128:132] = b"PE\0\0"
    struct.pack_into("<HH", image, 132, 0x8664, count)
    struct.pack_into("<H", image, 148, 240)
    struct.pack_into("<H", image, 152, 0x20B)
    struct.pack_into("<H", image, 220, 10)
    for index in range(count):
        pos = 392 + index * 40
        image[pos:pos + 8] = b".vbpubk\0"
        struct.pack_into("<IIII", image, pos + 8, 8192, 4096 + index * 8192,
                         8192, 512 + index * 8192)
    if key is not None:
        image[512:512 + len(key)] = key
    return bytes(image)


def gpt_image(edit_header=None, edit_entries=None):
    sectors = 4096
    image = bytearray(sectors * 512)
    entries = bytearray(128 * 128)
    for number, label, kind, first, last in (
        (2, "KERN-A", installer.KERNEL_TYPE, 40, 49),
        (3, "ROOT-A", installer.ROOT_TYPE, 50, 59),
        (4, "KERN-B", installer.KERNEL_TYPE, 80, 89),
        (5, "ROOT-B", installer.ROOT_TYPE, 90, 99),
        (12, "EFI-SYSTEM", installer.ESP_TYPE, 60, 79),
    ):
        pos = (number - 1) * 128
        entries[pos:pos + 16] = uuid.UUID(kind).bytes_le
        entries[pos + 16:pos + 32] = uuid.UUID(
            ESP_UUID if number == 12 else f"00000000-0000-4000-8000-{number:012d}"
        ).bytes_le
        struct.pack_into("<QQ", entries, pos + 32, first, last)
        if number == 2:
            struct.pack_into("<Q", entries, pos + 48, (15 << 48) | (1 << 56))
        name = label.encode("utf-16-le")
        entries[pos + 56:pos + 56 + len(name)] = name
    if edit_entries:
        edit_entries(entries)
    header = bytearray(512)
    header[:8] = b"EFI PART"
    struct.pack_into("<IIIIQQQQ", header, 8, 0x10000, 92, 0, 0,
                     1, sectors - 1, 34, sectors - 34)
    header[56:72] = uuid.UUID("00000000-0000-4000-8000-000000000001").bytes_le
    struct.pack_into("<QIII", header, 72, 2, 128, 128, zlib.crc32(entries))
    if edit_header:
        edit_header(header)
    struct.pack_into("<I", header, 16, zlib.crc32(header[:92]))
    image[512:1024] = header
    image[1024:1024 + len(entries)] = entries
    image[40 * 512:40 * 512 + 8] = b"CHROMEOS"
    image[50 * 512 + 1080:50 * 512 + 1082] = b"\x53\xef"
    image[80 * 512:80 * 512 + 8] = b"CHROMEOS"
    image[90 * 512 + 1080:90 * 512 + 1082] = b"\x53\xef"
    image[60 * 512 + 54:60 * 512 + 62] = b"FAT16   "
    image[60 * 512 + 510:60 * 512 + 512] = b"\x55\xaa"
    return image


class SecureBootTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.efivars = Path(directory.name) / "efivars"
        self.efivars.mkdir()
        self.mounts = Path(directory.name) / "mounts"
        self.mounts.write_text(f"efivarfs {self.efivars} efivarfs rw 0 0\n")
        guid = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
        self.secure = self.efivars / f"SecureBoot-{guid}"
        self.order = self.efivars / f"BootOrder-{guid}"
        self.order.write_bytes(bytes(4) + b"\x01\x00")

    def check(self):
        return installer.check_secure_boot(self.efivars, self.mounts)

    def test_disabled_allows_and_enabled_refuses(self):
        self.secure.write_bytes(bytes(5))
        self.assertEqual(self.check(), "disabled")
        self.secure.write_bytes(bytes(4) + b"\x01")
        with self.assertRaisesRegex(ValueError, "Disable UEFI Secure Boot"):
            self.check()

    def test_unsupported_allows_only_with_working_firmware_variables(self):
        self.assertEqual(self.check(), "unsupported")
        self.order.unlink()
        with self.assertRaises(FileNotFoundError):
            self.check()

    def test_missing_variable_on_unmounted_or_wrong_filesystem_refuses(self):
        for mounts in ("", f"tmpfs {self.efivars} tmpfs rw 0 0\n",
                       "efivarfs /somewhere/else efivarfs rw 0 0\n"):
            with self.subTest(mounts=mounts):
                self.mounts.write_text(mounts)
                with self.assertRaisesRegex(ValueError, "efivarfs is not mounted"):
                    self.check()

    def test_unreadable_variable_is_not_treated_as_unsupported(self):
        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.check()

    def test_malformed_secureboot_and_bootorder_refuse(self):
        for value in (b"", bytes(4), bytes(6), bytes(4) + b"\x02"):
            self.secure.write_bytes(value)
            with self.subTest(secure=value), self.assertRaisesRegex(ValueError, "malformed SecureBoot"):
                self.check()
        self.secure.unlink()
        for value in (b"", bytes(4), bytes(5), bytes(7)):
            self.order.write_bytes(value)
            with self.subTest(order=value), self.assertRaisesRegex(ValueError, "malformed BootOrder"):
                self.check()


class DiskValidationTests(unittest.TestCase):
    def test_supported_layout(self):
        _, parts = installer.read_gpt(io.BytesIO(gpt_image()))
        self.assertEqual(parts[12]["uuid"], ESP_UUID)
        self.assertEqual(parts[2]["attributes"], (15 << 48) | (1 << 56))

    def test_header_and_table_checksums(self):
        for offset in (512 + 40, 1024 + 128 + 56):
            with self.subTest(offset=offset):
                image = gpt_image()
                image[offset] ^= 1
                with self.assertRaisesRegex(ValueError, "checksum"):
                    installer.read_gpt(io.BytesIO(image))

    def test_wrong_partition_type(self):
        image = gpt_image(edit_entries=lambda e: e.__setitem__(slice(128, 144), uuid.UUID(installer.ROOT_TYPE).bytes_le))
        with self.assertRaisesRegex(ValueError, "not a supported Flex disk"):
            installer.read_gpt(io.BytesIO(image))

    def test_overlapping_partitions(self):
        image = gpt_image(edit_entries=lambda e: struct.pack_into("<QQ", e, 2 * 128 + 32, 45, 59))
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            installer.read_gpt(io.BytesIO(image))

    def test_partition_and_filesystem_signatures(self):
        for offset in (60 * 512 + 510,):
            with self.subTest(offset=offset):
                image = gpt_image()
                image[offset] = 0
                with self.assertRaises(ValueError):
                    installer.read_gpt(io.BytesIO(image))

    def test_truncated_table(self):
        with self.assertRaisesRegex(ValueError, "Truncated"):
            installer.read_gpt(io.BytesIO(gpt_image()[:2048]))

    def test_header_cannot_claim_another_location(self):
        image = gpt_image(edit_header=lambda h: struct.pack_into("<Q", h, 24, 2))
        with self.assertRaises(ValueError):
            installer.read_gpt(io.BytesIO(image))

    def test_header_cannot_claim_larger_disk(self):
        image = gpt_image(edit_header=lambda h: struct.pack_into("<Q", h, 32, 999999))
        with self.assertRaises(ValueError):
            installer.read_gpt(io.BytesIO(image))


class CompatibilityTests(unittest.TestCase):
    def test_reference_config_and_exact_version_are_required(self):
        values = installer.compatibility()
        self.assertEqual(installer.require_supported_version(values["FLEX_OS_VERSION"]), values)
        for version in ("unknown", "16836.32.1", values["FLEX_OS_VERSION"] + " "):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "Unsupported Flex version"):
                installer.require_supported_version(version)

    def test_config_is_strict_plain_data_and_never_shell_code(self):
        original = (HERE / "compatibility.conf").read_text()
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "compatibility.conf"
            config.write_text(original)
            self.assertEqual(installer.compatibility(directory), installer.compatibility())
            bad = (original + "FLEX_OS_VERSION=16836.32.0\n",
                   original + " \n", original + " # indented comment\n",
                   original.replace("\n", "\r\n"), original + "\u0085\n",
                   original + "# ignored?\0\n", original.replace("FLEX_OS_VERSION=", "FLEX_OS_VERSION=\0"),
                   original + "UNKNOWN=value\n", original.replace("FLEX_OS_VERSION=", "export FLEX_OS_VERSION="),
                   original.replace("FLEX_OS_VERSION=16836.32.0", "FLEX_OS_VERSION=16836.32"),
                   original.replace("FLEX_OS_VERSION=16836.32.0", 'FLEX_OS_VERSION="16836.32.0"'),
                   original.replace("FLEX_OS_VERSION=16836.32.0", "FLEX_OS_VERSION=$(touch /tmp/unsafe)"),
                   original.replace("FLEX_KERNEL_RELEASE=", "FLEX_KERNEL_RELEASE=../"),
                   original.replace("FLEX_ROOT_SHA256=", "FLEX_ROOT_SHA256=0"),
                   "\n".join(line for line in original.splitlines() if not line.startswith("FLEX_STARTUP_SHA256=")))
            with patch.object(installer.subprocess, "run") as ran:
                for text in bad:
                    config.write_text(text)
                    with self.subTest(config=text), self.assertRaises(ValueError):
                        installer.compatibility(directory)
                ran.assert_not_called()


class BootableSlotTests(unittest.TestCase):
    def roots(self, image):
        stream = io.BytesIO(image)
        _, parts = installer.read_gpt(stream)
        return installer.bootable_roots(stream, parts, 512)

    def test_successful_or_trial_slots_require_positive_priority(self):
        self.assertEqual(self.roots(gpt_image()), [3])
        for attributes, expected in ((1 << 56, [3]), (15 << 48, [3]),
                                     ((2 << 48) | (1 << 52), [3, 5]),
                                     ((1 << 48) | (1 << 56), [3, 5])):
            image = gpt_image(edit_entries=lambda entries: struct.pack_into("<Q", entries, 3 * 128 + 48, attributes))
            with self.subTest(attributes=attributes):
                self.assertEqual(self.roots(image), expected)

    def test_only_b_can_be_bootable_and_disabled_a_content_is_ignored(self):
        def edit(entries):
            struct.pack_into("<Q", entries, 128 + 48, 0)
            struct.pack_into("<Q", entries, 3 * 128 + 48, (2 << 48) | (1 << 52))
        image = gpt_image(edit_entries=edit)
        image[40 * 512] = image[50 * 512 + 1080] = 0
        self.assertEqual(self.roots(image), [5])

    def test_no_eligible_slot_refuses(self):
        image = gpt_image(edit_entries=lambda entries: struct.pack_into("<Q", entries, 128 + 48, 0))
        with self.assertRaisesRegex(ValueError, "No bootable"):
            self.roots(image)

    def test_each_bootable_pair_needs_kernel_and_root_signatures(self):
        for offset in (40 * 512, 50 * 512 + 1080, 80 * 512, 90 * 512 + 1080):
            image = gpt_image(edit_entries=lambda entries: struct.pack_into("<Q", entries, 3 * 128 + 48, (1 << 48) | (1 << 56)))
            image[offset] = 0
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "keyblock|ext filesystem"):
                self.roots(image)

    def test_bootable_b_without_the_expected_root_pair_refuses(self):
        def edit(entries):
            struct.pack_into("<Q", entries, 3 * 128 + 48, (2 << 48) | (1 << 56))
            entries[4 * 128:4 * 128 + 16] = bytes(16)
        with self.assertRaisesRegex(ValueError, "slot layout"):
            self.roots(gpt_image(edit_entries=edit))

    def test_bootable_unrecognized_slot_label_refuses(self):
        def edit(entries):
            struct.pack_into("<Q", entries, 3 * 128 + 48, (2 << 48) | (1 << 56))
            entries[3 * 128 + 56:3 * 128 + 128] = "KERN-C".encode("utf-16-le").ljust(72, b"\0")
        with self.assertRaisesRegex(ValueError, "slot layout"):
            self.roots(gpt_image(edit_entries=edit))


class RootPartitionTests(unittest.TestCase):
    def setUp(self):
        self.disk = Path("/dev/test")
        self.part = dict(first=50, last=59)
        self.tree = dict(name=str(self.disk), type="disk", children=[
            dict(name="/dev/test3", type="part", partn=3)])

    def get(self, tree=None):
        with (patch.object(installer, "run", return_value=json.dumps({"blockdevices": [tree or self.tree]})),
              patch.object(Path, "stat", return_value=SimpleNamespace(st_mode=installer.stat.S_IFBLK, st_rdev=0))):
            # The mapping check is exercised separately with 512/4096 sector units.
            with patch.object(installer, "check_partition_mapping") as mapped:
                result = installer.root_partitions(self.disk, [3], {3: self.part}, 512)
                mapped.assert_called_once_with(Path("/dev/test3"), self.part, 512)
                return result

    def test_lsblk_partition_number_is_used_instead_of_device_name_concatenation(self):
        self.assertEqual(self.get(), [(3, Path("/dev/test3"))])

    def test_missing_duplicate_wrong_type_or_relative_partition_refuses(self):
        for children in ([], [self.tree["children"][0]] * 2,
                         [dict(name="/dev/test3", type="disk", partn=3)],
                         [dict(name="relative", type="part", partn=3)]):
            with self.subTest(children=children), self.assertRaises(ValueError):
                self.get(dict(self.tree, children=children))

    def test_different_disk_tree_refuses(self):
        with self.assertRaises(ValueError):
            self.get(dict(self.tree, name="/dev/another"))

    def test_partition_geometry_is_compared_in_bytes_including_4k_disks(self):
        for sector_size in (512, 4096):
            values = {"start": str(50 * sector_size // 512), "size": str(10 * sector_size // 512)}
            with (self.subTest(sector_size=sector_size),
                  patch.object(Path, "stat", return_value=SimpleNamespace(st_rdev=0)),
                  patch.object(Path, "read_text", new=lambda path: values[path.name])):
                installer.check_partition_mapping(Path("/dev/test3"), self.part, sector_size)
                values["start"] = "0"
                with self.assertRaisesRegex(ValueError, "mapping differs"):
                    installer.check_partition_mapping(Path("/dev/test3"), self.part, sector_size)


class ExecutableValidationTests(unittest.TestCase):
    def test_key_injection_changes_only_key_section(self):
        template = pe_image()
        result = installer.make_loader(template, pe_image(packed_key()))
        self.assertEqual(result[:512], template[:512])
        self.assertEqual(result[512:], packed_key().ljust(8192, b"\0"))
        self.assertEqual(len(result), len(template))

    def test_built_template_accepts_current_key_format(self):
        template = (Path(os.environ["CROS_KIT_PAYLOAD"]) / "crdyboot-debug.efi").read_bytes()
        result = installer.make_loader(template, pe_image(packed_key()))
        start, length = installer.key_section(template)
        self.assertEqual(result[:start], template[:start])
        self.assertEqual(result[start + length:], template[start + length:])

    def test_nonempty_template_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            installer.make_loader(pe_image(packed_key()), pe_image(packed_key()))

    def test_duplicate_or_truncated_sections_rejected(self):
        for image in (pe_image(duplicate=True), pe_image()[:-1]):
            with self.subTest(length=len(image)), self.assertRaises(ValueError):
                installer.key_section(image)

    def test_architecture_and_subsystem_rejected(self):
        for offset, value in ((132, 0xAA64), (220, 3)):
            image = bytearray(pe_image())
            struct.pack_into("<H", image, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                installer.key_section(image)

    def test_invalid_key_offsets_algorithm_and_word_count(self):
        for offset, fmt, value in ((0, "Q", 31), (0, "Q", 8192),
                                   (16, "Q", 18), (32, "I", 1)):
            key = bytearray(packed_key())
            struct.pack_into("<" + fmt, key, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                installer.validate_key(key)


class RwInitValidationTests(unittest.TestCase):
    def test_script_digest_and_manifest_filename_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = b"#!/bin/sh\nexec /sbin/init\n"
            (root / "rw-init.sh").write_bytes(script)
            digest = hashlib.sha256(script).hexdigest()
            manifest = root / "rw-init.sh.sha256"
            manifest.write_text(digest + "  rw-init.sh\n")
            self.assertEqual(installer.verified_rw_init(root), script)
            for text in (digest + "  another-file\n", "0" * 64 + "  rw-init.sh\n",
                         digest + "  rw-init.sh\nextra-entry\n"):
                manifest.write_text(text)
                with self.subTest(manifest=text), self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    installer.verified_rw_init(root)
            manifest.write_text(digest + "  rw-init.sh\n")
            (root / "rw-init.sh").write_bytes(script + b"# changed\n")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                installer.verified_rw_init(root)


class RootdevValidationTests(unittest.TestCase):
    def test_library_digest_and_manifest_filename_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = b"ELF library fixture"
            (root / "rootdev-compat.so").write_bytes(library)
            digest = hashlib.sha256(library).hexdigest()
            manifest = root / "rootdev-compat.so.sha256"
            manifest.write_text(digest + "  rootdev-compat.so\n")
            self.assertEqual(installer.verified_rootdev(root), library)
            for text in (digest + "  another-file\n", "0" * 64 + "  rootdev-compat.so\n",
                         digest + "  rootdev-compat.so\nextra-entry\n"):
                manifest.write_text(text)
                with self.subTest(manifest=text), self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    installer.verified_rootdev(root)
            manifest.write_text(digest + "  rootdev-compat.so\n")
            (root / "rootdev-compat.so").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                installer.verified_rootdev(root)


class FirmwareParsingTests(unittest.TestCase):
    def test_file_and_raw_path_formats_have_same_identity(self):
        for loader in (f"File({installer.LOADER})", installer.LOADER):
            with self.subTest(loader=loader):
                text = (f"BootOrder: 0004\nBoot0004* {installer.LABEL}\t"
                        f"HD(12,GPT,{ESP_UUID},0x49000,0x80000)/{loader}\n"
                        "      dp: 04 01 2a 00\n")
                order, entries = installer.firmware_entries(text)
                self.assertEqual(order, ["0004"])
                self.assertEqual(installer.owned_entries(entries, ESP_UUID), ["0004"])
                self.assertEqual(entries["0004"]["path"], installer.LOADER.lower())

    def test_actual_alpine_stock_entry_is_recognized(self):
        text = ("BootOrder: 0004\nBoot0004* ChromeOS Flex\t"
                r"HD(12,GPT,b339d8f6-e03c-e349-bfe6-ada1b7d2c38b,0x49000,0x80000)/\efi\boot\bootx64.efi"
                "\n")
        order, entries = installer.firmware_entries(text)
        self.assertEqual(order, ["0004"])
        self.assertEqual(entries["0004"], dict(label="ChromeOS Flex", active=True,
                                             uuid="b339d8f6-e03c-e349-bfe6-ada1b7d2c38b",
                                             path=r"\efi\boot\bootx64.efi"))

    def test_raw_paths_keep_strict_identity(self):
        for label, guid, loader in (("Other label", ESP_UUID, installer.LOADER),
                                    (installer.LABEL, OTHER_UUID, installer.LOADER),
                                    (installer.LABEL, ESP_UUID, installer.LOADER + ".backup"),
                                    (installer.LABEL, ESP_UUID, installer.LOADER + " .backup")):
            with self.subTest(label=label, guid=guid, loader=loader):
                text = f"Boot0004* {label}\tHD(12,GPT,{guid},0x1,0x2)/{loader}\n"
                _, entries = installer.firmware_entries(text)
                self.assertEqual(installer.owned_entries(entries, ESP_UUID), [])
        _, entries = installer.firmware_entries(
            f"      dp: Boot0004* {installer.LABEL}\tHD(12,GPT,{ESP_UUID},0x1,0x2)/{installer.LOADER}\n")
        self.assertEqual(entries, {})


class MountLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name) / "mountpoint"
        self.directory.mkdir()
        mocked = patch.object(installer.tempfile, "mkdtemp", return_value=str(self.directory))
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_success_unmounts_then_removes_only_the_empty_mountpoint(self):
        with patch.object(installer, "run") as run:
            with installer.mounted_esp(Path("/dev/fake12"), True) as directory:
                self.assertEqual(directory, self.directory)
                self.assertTrue(directory.is_dir())
        self.assertFalse(self.directory.exists())
        self.assertEqual([call.args for call in run.call_args_list], [
            ("mount", "-t", "vfat", "-o", "rw,nosuid,nodev,noexec",
             "/dev/fake12", str(self.directory)),
            ("umount", str(self.directory)),
        ])

    def test_failed_unmount_retains_stock_efi_files_and_reports_mountpoint(self):
        stock = self.directory / "EFI/BOOT/bootx64.efi"

        def run(*args):
            if args[0] == "mount":
                stock.parent.mkdir(parents=True)
                stock.write_bytes(b"original EFI loader")
            else:
                raise subprocess.CalledProcessError(32, args, stderr="target is busy")

        with patch.object(installer, "run", side_effect=run):
            with self.assertRaisesRegex(ValueError, "Mount and files retained") as raised:
                with installer.mounted_esp(Path("/dev/fake12"), True):
                    pass
        self.assertIn(str(self.directory), str(raised.exception))
        self.assertIn("target is busy", str(raised.exception))
        self.assertEqual(stock.read_bytes(), b"original EFI loader")

    def test_body_error_still_unmounts_and_preserves_the_original_error(self):
        with patch.object(installer, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "operation stopped"):
                with installer.mounted_esp(Path("/dev/fake12"), False):
                    raise RuntimeError("operation stopped")
        self.assertEqual(run.call_args_list[-1].args, ("umount", str(self.directory)))
        self.assertFalse(self.directory.exists())

    def test_mount_failure_removes_empty_directory_without_attempting_unmount(self):
        with patch.object(installer, "run", side_effect=OSError("mount failed")) as run:
            with self.assertRaisesRegex(OSError, "mount failed"):
                with installer.mounted_esp(Path("/dev/fake12"), True):
                    self.fail("Failed mount must not yield")
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.directory.exists())


class Firmware:
    def __init__(self):
        self.order = ["0001", "0008"]
        self.entries = {
            "0001": ("ChromeOS Flex", ESP_UUID, r"\EFI\BOOT\BOOTX64.EFI", True),
            "0008": ("Other OS", OTHER_UUID, r"\EFI\BOOT\BOOTX64.EFI", True),
        }
        self.commands = []
        self.fail_on = None

    def text(self):
        return "BootOrder: " + ",".join(self.order) + "\n" + "\n".join(
            f"Boot{number}{'*' if active else ' '} {label}\tHD(12,GPT,{guid},0x3c,0x14)/File({path})"
            for number, (label, guid, path, active) in self.entries.items()
        ) + "\n"

    def run(self, *args):
        self.commands.append(args)
        if args[0] != "efibootmgr":
            raise AssertionError(f"Unexpected real command: {args}")
        if self.fail_on and self.fail_on in args:
            raise subprocess.CalledProcessError(1, args, stderr="simulated firmware failure")
        if args[1:] == ("--verbose",):
            return self.text()
        if "--create-only" in args:
            number = next(f"{value:04X}" for value in range(1, 65536) if f"{value:04X}" not in self.entries)
            self.entries[number] = (args[args.index("--label") + 1], ESP_UUID,
                                    args[args.index("--loader") + 1], True)
        elif "--bootorder" in args:
            self.order = args[-1].split(",") if args[-1] else []
        elif "--active" in args:
            number = args[args.index("--bootnum") + 1]
            label, guid, path, _ = self.entries[number]
            self.entries[number] = label, guid, path, True
        elif "--delete-bootnum" in args:
            number = args[args.index("--bootnum") + 1]
            self.entries.pop(number)
            self.order = [item for item in self.order if item != number]
        else:
            raise AssertionError(f"Unexpected firmware command: {args}")
        return ""


class FirmwareTransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.esp = self.root / "esp"
        stock = self.esp / "EFI/BOOT/crdybootx64.efi"
        stock.parent.mkdir(parents=True)
        stock.write_bytes(pe_image(packed_key()))
        self.disk = self.root / "disk.bin"
        self.disk.write_bytes(gpt_image())
        self.template = self.root / "template.efi"
        self.template.write_bytes(pe_image())
        self.firmware = Firmware()
        self.args = SimpleNamespace(action="install", disk=self.disk,
                                    template=self.template, dry_run=False)
        self.rw_script = b"#!/bin/sh\nexec /sbin/init\n"
        self.rootdev_library = b"ELF library fixture"
        validation = patch.object(installer, "validate_installation")
        self.validation = validation.start()
        self.addCleanup(validation.stop)
        original_read_text = Path.read_text

        def read_text(path, *args, **kwargs):
            if str(path).startswith("/sys/dev/block/"):
                return {"start": "60", "size": "20"}[path.name]
            return original_read_text(path, *args, **kwargs)

        for item in (
            patch.object(installer, "check_environment", return_value=(self.disk, SimpleNamespace(stat=lambda: SimpleNamespace(st_rdev=0)), 512)),
            patch.object(installer, "mounted_esp", side_effect=lambda *_: nullcontext(self.esp)),
            patch.object(installer, "run", side_effect=self.firmware.run),
            patch.object(installer, "verified_rw_init", return_value=self.rw_script),
            patch.object(installer, "verified_rootdev", return_value=self.rootdev_library),
            patch.object(Path, "read_text", new=read_text),
            patch("builtins.print"),
        ):
            item.start()
            self.addCleanup(item.stop)

    def test_install_is_idempotent_and_retains_other_entries(self):
        installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0003", "0001", "0008", "0002"])
        self.assertEqual(self.firmware.entries["0008"][0], "Other OS")
        self.firmware.commands.clear()
        with patch.object(installer, "atomic_write", wraps=installer.atomic_write) as written:
            installer.manage(self.args)
            written.assert_not_called()
        self.assertTrue(all(command == ("efibootmgr", "--verbose") for command in self.firmware.commands))

    def test_old_installs_gain_configured_mode_without_replacing_stock_files(self):
        installer.manage(self.args)
        directory = self.esp / installer.OWN_DIR
        original_stock = (self.esp / "EFI/BOOT/crdybootx64.efi").read_bytes()
        for version in (1, 2, 3):
            with self.subTest(version=version):
                (directory / "normalx64.efi").unlink()
                del self.firmware.entries["0003"]
                self.firmware.order.remove("0003")
                (directory / "rootdev-compat.so").unlink()
                (directory / "rootdev-compat.so.sha256").unlink()
                if version == 1:
                    (directory / "rw-init.sh").unlink()
                (directory / "install.json").write_text(json.dumps(dict(format=version, esp_uuid=ESP_UUID)))
                installer.manage(self.args)
                self.assertEqual((directory / "rw-init.sh").read_bytes(), self.rw_script)
                self.assertEqual((directory / "rootdev-compat.so").read_bytes(), self.rootdev_library)
                self.assertEqual((directory / "normalx64.efi").read_bytes(), (directory / "bootx64.efi").read_bytes())
                self.assertEqual(json.loads((directory / "install.json").read_text())["format"], 3)
                self.assertEqual((self.esp / "EFI/BOOT/crdybootx64.efi").read_bytes(), original_stock)
                self.assertEqual(self.firmware.order, ["0003", "0001", "0008", "0002"])

    def test_changed_script_is_replaced_atomically_without_firmware_changes(self):
        installer.manage(self.args)
        self.firmware.commands.clear()
        directory = self.esp / installer.OWN_DIR
        changed = self.rw_script + b"# updated\n"
        with (patch.object(installer, "verified_rw_init", return_value=changed),
              patch.object(installer, "atomic_write", wraps=installer.atomic_write) as written):
            installer.manage(self.args)
        written.assert_called_once_with(directory / "rw-init.sh", changed)
        self.assertEqual((directory / "rw-init.sh").read_bytes(), changed)
        self.assertTrue(all(command == ("efibootmgr", "--verbose") for command in self.firmware.commands))

    def test_changed_rootdev_library_and_checksum_are_replaced_without_firmware_changes(self):
        installer.manage(self.args)
        self.firmware.commands.clear()
        directory = self.esp / installer.OWN_DIR
        changed = b"ELF updated library"
        with (patch.object(installer, "verified_rootdev", return_value=changed),
              patch.object(installer, "atomic_write", wraps=installer.atomic_write) as written):
            installer.manage(self.args)
        self.assertEqual(written.call_count, 2)
        self.assertEqual((directory / "rootdev-compat.so").read_bytes(), changed)
        self.assertEqual((directory / "rootdev-compat.so.sha256").read_text(),
                         hashlib.sha256(changed).hexdigest() + "  rootdev-compat.so\n")
        self.assertTrue(all(command == ("efibootmgr", "--verbose") for command in self.firmware.commands))

    def test_rootdev_integrity_failure_changes_no_files_or_firmware(self):
        for dry_run in (False, True):
            self.args.dry_run = dry_run
            self.firmware.commands.clear()
            with (self.subTest(dry_run=dry_run),
                  patch.object(installer, "verified_rootdev", side_effect=ValueError("rootdev-compat.so checksum mismatch"))):
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    installer.manage(self.args)
            self.assertFalse((self.esp / installer.OWN_DIR).exists())
            self.assertTrue(all(command == ("efibootmgr", "--verbose") for command in self.firmware.commands))

    def test_rootdev_copy_failure_keeps_loader_and_boot_order_unchanged(self):
        installer.manage(self.args)
        directory = self.esp / installer.OWN_DIR
        original = (directory / "bootx64.efi").read_bytes()
        order = self.firmware.order[:]
        with (patch.object(installer, "verified_rootdev", return_value=b"new library"),
              patch.object(installer, "atomic_write", side_effect=OSError("library copy failed"))):
            with self.assertRaisesRegex(OSError, "library copy failed"):
                installer.manage(self.args)
        self.assertEqual((directory / "bootx64.efi").read_bytes(), original)
        self.assertEqual(self.firmware.order, order)

    def test_script_copy_failure_keeps_loader_and_boot_order_unchanged(self):
        installer.manage(self.args)
        directory = self.esp / installer.OWN_DIR
        original = (directory / "bootx64.efi").read_bytes()
        order = self.firmware.order[:]
        with (patch.object(installer, "verified_rw_init", return_value=b"new script"),
              patch.object(installer, "atomic_write", side_effect=OSError("copy failed"))):
            with self.assertRaisesRegex(OSError, "copy failed"):
                installer.manage(self.args)
        self.assertEqual((directory / "bootx64.efi").read_bytes(), original)
        self.assertEqual(self.firmware.order, order)

    def test_configured_copy_failure_never_changes_firmware_and_retry_recovers(self):
        real_write = installer.atomic_write

        def fail_normal(path, data):
            if path.name == "normalx64.efi":
                raise OSError("normal copy failed")
            return real_write(path, data)

        with patch.object(installer, "atomic_write", side_effect=fail_normal):
            with self.assertRaisesRegex(OSError, "normal copy failed"):
                installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0001", "0008"])
        self.assertEqual(set(self.firmware.entries), {"0001", "0008"})
        self.assertTrue(all(command == ("efibootmgr", "--verbose") for command in self.firmware.commands))
        installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0003", "0001", "0008", "0002"])

    def test_rw_integrity_failure_changes_no_files_or_firmware(self):
        for dry_run in (False, True):
            self.args.dry_run = dry_run
            self.firmware.commands.clear()
            with (self.subTest(dry_run=dry_run),
                  patch.object(installer, "verified_rw_init", side_effect=ValueError("rw-init.sh checksum mismatch"))):
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    installer.manage(self.args)
            self.assertFalse((self.esp / installer.OWN_DIR).exists())
            self.assertTrue(all(command == ("efibootmgr", "--verbose") for command in self.firmware.commands))

    def test_unknown_owned_directory_file_is_never_overwritten_or_deleted(self):
        installer.manage(self.args)
        other = self.esp / installer.OWN_DIR / "personal-file"
        other.write_bytes(b"preserve")
        for action in ("install", "remove"):
            self.args.action = action
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "Unexpected files"):
                installer.manage(self.args)
            self.assertEqual(other.read_bytes(), b"preserve")

    def test_legacy_debug_default_is_moved_last_on_reinstall(self):
        installer.manage(self.args)
        original_loader = (self.esp / installer.OWN_DIR / "bootx64.efi").read_bytes()
        self.firmware.order = ["0002", "0001", "0008"]
        installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0003", "0001", "0008", "0002"])
        self.assertEqual((self.esp / installer.OWN_DIR / "bootx64.efi").read_bytes(), original_loader)

    def test_unrelated_relative_order_is_preserved_after_configured_default(self):
        self.firmware.order = ["0008", "0001"]
        installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0003", "0008", "0001", "0002"])

    def test_duplicate_owned_entries_are_consolidated_after_stock(self):
        installer.manage(self.args)
        self.firmware.entries["0004"] = installer.LABEL, ESP_UUID, installer.LOADER, True
        self.firmware.order = ["0004", "0008", "0002", "0001", "0003"]
        installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0003", "0008", "0001", "0004"])
        self.assertEqual(set(self.firmware.entries), {"0001", "0003", "0004", "0008"})

    def test_configured_duplicates_consolidate_without_touching_another_disk(self):
        installer.manage(self.args)
        self.firmware.entries["0004"] = installer.NORMAL_LABEL, ESP_UUID, installer.NORMAL_LOADER, True
        self.firmware.entries["0009"] = installer.NORMAL_LABEL, OTHER_UUID, installer.NORMAL_LOADER, True
        self.firmware.order = ["0004", "0009", "0008", "0003", "0001", "0002"]
        installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0004", "0009", "0008", "0001", "0002"])
        self.assertEqual(set(self.firmware.entries), {"0001", "0002", "0004", "0008", "0009"})

    def test_inactive_legacy_default_is_moved_last_before_activation(self):
        installer.manage(self.args)
        self.firmware.entries["0002"] = installer.LABEL, ESP_UUID, installer.LOADER, False
        self.firmware.order = ["0002", "0001", "0008"]
        self.firmware.commands.clear()
        installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0003", "0001", "0008", "0002"])
        self.assertTrue(self.firmware.entries["0002"][-1])
        mutations = [command for command in self.firmware.commands if command != ("efibootmgr", "--verbose")]
        self.assertEqual(mutations, [("efibootmgr", "--bootorder", "0003,0001,0008,0002"),
                                     ("efibootmgr", "--bootnum", "0002", "--active")])

    def test_dry_run_does_not_write_files_or_firmware(self):
        self.args.dry_run = True
        installer.manage(self.args)
        self.assertFalse((self.esp / installer.OWN_DIR).exists())
        self.assertEqual(self.firmware.commands, [("efibootmgr", "--verbose")])

    def test_legacy_migration_dry_run_preserves_files_and_order(self):
        installer.manage(self.args)
        self.firmware.order = ["0002", "0001", "0008"]
        self.firmware.commands.clear()
        directory = self.esp / installer.OWN_DIR
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        self.args.dry_run = True
        installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0002", "0001", "0008"])
        self.assertEqual(self.firmware.commands, [("efibootmgr", "--verbose")])
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, before)

    def test_missing_stock_entry_refuses_install(self):
        self.firmware.order.remove("0001")
        with self.assertRaisesRegex(ValueError, "stock"):
            installer.manage(self.args)
        self.assertFalse((self.esp / installer.OWN_DIR).exists())

    def test_same_label_on_another_disk_is_not_owned(self):
        self.firmware.entries["0009"] = installer.LABEL, OTHER_UUID, installer.LOADER, True
        self.firmware.order.append("0009")
        installer.manage(self.args)
        self.assertIn("0009", self.firmware.order)
        self.assertIn("0009", self.firmware.entries)

    def test_firmware_creation_failure_keeps_original_order(self):
        self.firmware.fail_on = "--create-only"
        with self.assertRaises(subprocess.CalledProcessError):
            installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0001", "0008"])

    def test_bootorder_failure_on_new_install_keeps_debug_out_of_default(self):
        self.firmware.fail_on = "--bootorder"
        with self.assertRaises(subprocess.CalledProcessError):
            installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0001", "0008"])
        self.assertTrue((self.esp / installer.OWN_DIR / "bootx64.efi").exists())
        self.firmware.fail_on = None
        installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0003", "0001", "0008", "0002"])

    def test_ignored_bootorder_write_is_reported_as_failure(self):
        real_run = self.firmware.run

        def ignore_order(*args):
            return "" if "--bootorder" in args else real_run(*args)

        with patch.object(installer, "run", side_effect=ignore_order):
            with self.assertRaisesRegex(ValueError, "did not retain"):
                installer.manage(self.args)

    def test_ignored_legacy_bootorder_does_not_activate_or_delete_entries(self):
        installer.manage(self.args)
        self.firmware.entries["0002"] = installer.LABEL, ESP_UUID, installer.LOADER, False
        self.firmware.entries["0004"] = installer.LABEL, ESP_UUID, installer.LOADER, True
        self.firmware.order = ["0002", "0001", "0004", "0008", "0003"]
        self.firmware.commands.clear()
        real_run = self.firmware.run

        def ignore_order(*args):
            return "" if "--bootorder" in args else real_run(*args)

        with patch.object(installer, "run", side_effect=ignore_order):
            with self.assertRaisesRegex(ValueError, "did not retain"):
                installer.manage(self.args)
        self.assertFalse(self.firmware.entries["0002"][-1])
        self.assertFalse(any("--active" in command or "--delete-bootnum" in command
                             for command in self.firmware.commands))
        self.assertEqual(self.firmware.order, ["0002", "0001", "0004", "0008", "0003"])
        self.assertEqual(set(self.firmware.entries), {"0001", "0002", "0003", "0004", "0008"})

    def test_activation_failure_keeps_stock_boot_first(self):
        installer.manage(self.args)
        self.firmware.entries["0002"] = installer.LABEL, ESP_UUID, installer.LOADER, False
        self.firmware.order = ["0002", "0001", "0008"]
        self.firmware.fail_on = "--active"
        with self.assertRaises(subprocess.CalledProcessError):
            installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0003", "0001", "0008", "0002"])

    def test_failed_migration_does_not_activate_inactive_debug_default(self):
        installer.manage(self.args)
        self.firmware.entries["0002"] = installer.LABEL, ESP_UUID, installer.LOADER, False
        self.firmware.order = ["0002", "0001", "0008"]
        self.firmware.commands.clear()
        self.firmware.fail_on = "--bootorder"
        with self.assertRaises(subprocess.CalledProcessError):
            installer.manage(self.args)
        self.assertFalse(self.firmware.entries["0002"][-1])
        self.assertFalse(any("--active" in command or "--delete-bootnum" in command
                             for command in self.firmware.commands))
        self.assertEqual(self.firmware.order, ["0002", "0001", "0008"])
        self.assertTrue(self.firmware.entries["0001"][-1])

    def test_removal_failure_retains_loader(self):
        installer.manage(self.args)
        self.args.action = "remove"
        self.firmware.fail_on = "--delete-bootnum"
        with self.assertRaises(subprocess.CalledProcessError):
            installer.manage(self.args)
        self.assertTrue((self.esp / installer.OWN_DIR / "bootx64.efi").exists())

    def test_remove_preserves_stock_and_unrelated_entries(self):
        installer.manage(self.args)
        self.firmware.entries["0009"] = installer.NORMAL_LABEL, OTHER_UUID, installer.NORMAL_LOADER, True
        self.firmware.order = ["0003", "0009", "0008", "0001", "0002"]
        self.args.action = "remove"
        installer.manage(self.args)
        self.assertEqual(self.firmware.order, ["0009", "0008", "0001"])
        self.assertEqual(set(self.firmware.entries), {"0001", "0008", "0009"})
        self.assertFalse((self.esp / installer.OWN_DIR).exists())
        self.assertTrue((self.esp / "EFI/BOOT/crdybootx64.efi").exists())


class InstallGuardTests(unittest.TestCase):
    def setUp(self):
        FirmwareTransactionTests.setUp(self)
        self.validation.side_effect = REAL_VALIDATE_INSTALLATION
        self.startup = b"supported startup fixture\n"
        self.supported = dict(installer.compatibility(),
                              FLEX_STARTUP_SHA256=hashlib.sha256(self.startup).hexdigest())
        self.os_roots = {}
        for number in (3, 5):
            root = self.root / f"root{number}"
            (root / "etc/init").mkdir(parents=True)
            (root / "lib/modules" / self.supported["FLEX_KERNEL_RELEASE"]).mkdir(parents=True)
            (root / "etc/lsb-release").write_text(
                "CHROMEOS_RELEASE_BOARD=reven-signed-mp-v11keys\nCHROMEOS_RELEASE_VERSION="
                + self.supported["FLEX_OS_VERSION"] + "\n")
            (root / "etc/init/startup.conf").write_bytes(self.startup)
            self.os_roots[number] = root
        self.root_mounts = []

        def root_partitions(disk, numbers, parts, sector_size):
            return [(number, Path(f"/dev/test{number}")) for number in numbers]

        @contextmanager
        def mounted(partition, filesystem, options, prefix):
            self.assertEqual((filesystem, options, prefix),
                ("ext4", "ro,noload,nosuid,nodev,noexec", "cros-kit-root-"))
            self.root_mounts.append((str(partition), options))
            yield self.os_roots[int(str(partition).removeprefix("/dev/test"))]

        for item in (patch.object(installer, "compatibility", return_value=self.supported),
                     patch.object(installer, "root_partitions", side_effect=root_partitions),
                     patch.object(installer, "mounted_filesystem", side_effect=mounted)):
            item.start()
            self.addCleanup(item.stop)

    def enable_b(self, priority=15, a_priority=15):
        def edit(entries):
            struct.pack_into("<Q", entries, 128 + 48, (a_priority << 48) | (1 << 56))
            struct.pack_into("<Q", entries, 3 * 128 + 48, (priority << 48) | (1 << 52))
        self.disk.write_bytes(gpt_image(edit_entries=edit))

    def test_supported_a_and_trial_b_are_both_read_only_and_update_is_idempotent(self):
        self.enable_b()
        with patch.object(installer, "atomic_write", wraps=installer.atomic_write) as written:
            installer.manage(self.args)
        names = [call.args[0].name for call in written.call_args_list]
        self.assertLess(names.index("compatibility.conf"), names.index("rw-init.sh"))
        self.assertEqual((self.esp / installer.OWN_DIR / "compatibility.conf").read_bytes(),
                         (HERE / "compatibility.conf").read_bytes())
        self.assertEqual([name for name, _ in self.root_mounts], ["/dev/test3", "/dev/test5"])
        with patch.object(installer, "atomic_write", wraps=installer.atomic_write) as written:
            installer.manage(self.args)
            written.assert_not_called()

    def test_unknown_highest_priority_b_never_mounts_esp_or_changes_existing_files(self):
        installer.manage(self.args)
        self.enable_b(priority=15, a_priority=14)
        root = self.os_roots[5]
        (root / "etc/lsb-release").write_text("CHROMEOS_RELEASE_BOARD=reven\nCHROMEOS_RELEASE_VERSION=99999.1.0\n")
        self.assert_rejected_without_writes("unsupported Flex version")

    def test_old_lower_priority_a_does_not_block_supported_updated_b(self):
        self.enable_b(priority=2, a_priority=1)
        (self.os_roots[3] / "etc/lsb-release").write_text(
            "CHROMEOS_RELEASE_BOARD=reven\nCHROMEOS_RELEASE_VERSION=11111.1.0\n")
        installer.manage(self.args)
        self.assertEqual([name for name, _ in self.root_mounts], ["/dev/test5"])
        self.assertTrue((self.esp / installer.OWN_DIR / "bootx64.efi").is_file())

    def test_unknown_version_on_tied_highest_priority_slot_refuses(self):
        self.enable_b()
        (self.os_roots[5] / "etc/lsb-release").write_text(
            "CHROMEOS_RELEASE_BOARD=reven\nCHROMEOS_RELEASE_VERSION=99999.1.0\n")
        self.assert_rejected_without_writes("unsupported Flex version")

    def test_lower_priority_slot_still_requires_valid_kernel_structure(self):
        self.enable_b(priority=2, a_priority=1)
        image = bytearray(self.disk.read_bytes())
        image[40 * 512] = 0
        self.disk.write_bytes(image)
        self.assert_rejected_without_writes("keyblock")

    def assert_rejected_without_writes(self, message):
        before = {str(path.relative_to(self.esp)): path.read_bytes()
                  for path in self.esp.rglob("*") if path.is_file()}
        for dry_run in (False, True):
            self.args.dry_run = dry_run
            self.firmware.commands.clear()
            with (self.subTest(dry_run=dry_run), patch.object(installer, "mounted_esp") as esp_mount,
                  patch.object(installer, "atomic_write") as written):
                with self.assertRaisesRegex(ValueError, message):
                    installer.manage(self.args)
                esp_mount.assert_not_called()
                written.assert_not_called()
                self.assertEqual(self.firmware.commands, [])
                self.assertEqual({str(path.relative_to(self.esp)): path.read_bytes()
                                  for path in self.esp.rglob("*") if path.is_file()}, before)

    def test_wrong_board_startup_or_kernel_modules_fail_before_writable_esp(self):
        for mismatch in ("board", "startup", "modules"):
            with self.subTest(mismatch=mismatch):
                root = self.os_roots[3]
                release, startup = root / "etc/lsb-release", root / "etc/init/startup.conf"
                module = root / "lib/modules" / self.supported["FLEX_KERNEL_RELEASE"]
                if mismatch == "board":
                    release.write_text("CHROMEOS_RELEASE_BOARD=other\nCHROMEOS_RELEASE_VERSION="
                                       + self.supported["FLEX_OS_VERSION"] + "\n")
                elif mismatch == "startup":
                    startup.write_bytes(b"changed")
                else:
                    module.rmdir()
                self.assert_rejected_without_writes("reven|startup.conf|kernel modules")
                release.write_text("CHROMEOS_RELEASE_BOARD=reven\nCHROMEOS_RELEASE_VERSION="
                                   + self.supported["FLEX_OS_VERSION"] + "\n")
                startup.write_bytes(self.startup)
                module.mkdir(exist_ok=True)

    def test_no_bootable_slot_refuses_without_esp_or_firmware_mutation(self):
        self.disk.write_bytes(gpt_image(edit_entries=lambda entries:
            struct.pack_into("<Q", entries, 128 + 48, 0)))
        self.assert_rejected_without_writes("No bootable")

    def test_missing_config_blocks_install_before_esp_and_status_remove_still_work(self):
        installer.manage(self.args)
        with patch.object(installer, "compatibility", side_effect=FileNotFoundError("missing compatibility.conf")):
            with patch.object(installer, "mounted_esp") as esp_mount:
                with self.assertRaises(FileNotFoundError):
                    installer.manage(self.args)
                esp_mount.assert_not_called()
            (self.os_roots[3] / "etc/lsb-release").write_text("CHROMEOS_RELEASE_BOARD=other\n")
            self.validation.reset_mock()
            self.args.action = "status"
            installer.manage(self.args)
            self.args.action = "remove"
            installer.manage(self.args)
            self.validation.assert_not_called()
        self.assertFalse((self.esp / installer.OWN_DIR).exists())
        self.assertTrue((self.esp / "EFI/BOOT/crdybootx64.efi").is_file())


if __name__ == "__main__":
    unittest.main()
