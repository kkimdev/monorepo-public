#!/usr/bin/env python3
"""USB layout and destructive-write guards, using only memory and temporary files."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import errno
import json
from pathlib import Path
import re
import shlex
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import zlib


from cros_kit import make_usb as builder
from tests import test_install as fixtures


def recovery_image(**kwargs):
    image = fixtures.gpt_image(**kwargs)
    image[446 + 4] = 0xEE
    struct.pack_into("<II", image, 446 + 8, 1, len(image) // 512 - 1)
    image[510:512] = b"\x55\xaa"
    return image


def apply_records(original, size, records):
    output = bytearray(size)
    output[:len(original)] = original
    for offset, data in records:
        output[offset:offset + len(data)] = data
    return output


class ReleaseTests(unittest.TestCase):
    def release(self, version="16836.32.0", **changes):
        item = dict(channel="STABLE", model="ChromeOS Flex", version=version,
                    chrome_version="156.0.8078.93", file="chromeos_reven.bin",
                    url="https://dl.google.com/dl/edgedl/chromeos/recovery/image.bin.zip",
                    sha1="a" * 40, filesize=1000, zipfilesize=500)
        item.update(changes)
        return item

    def test_selects_latest_numeric_stable_flex_only(self):
        chosen = self.release("100.10.0")
        entries = [self.release("99.999.0"), chosen,
                   self.release("999.0.0", channel="DEV"),
                   self.release("999.0.0", model="Another board")]
        self.assertEqual(builder.stable_release(entries), chosen)

    def test_requires_stable_flex(self):
        with self.assertRaisesRegex(ValueError, "no stable Flex"):
            builder.stable_release([self.release(channel="BETA")])

    def test_rejects_unexpected_download_metadata(self):
        bad_fields = (dict(url="http://dl.google.com/image.bin.zip"),
                      dict(url="https://dl.google.com.evil.example/image.bin.zip"),
                      dict(file="../../image.bin"), dict(file="image.zip"),
                      dict(sha1="a" * 39), dict(sha1="g" * 40),
                      dict(filesize=0), dict(zipfilesize=-1))
        for changes in bad_fields:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                builder.stable_release([self.release(**changes)])


class CompatibilityGateTests(unittest.TestCase):
    def dry_run(self, version):
        release = ReleaseTests().release(version, sha256="b" * 64)
        lock = dict(format=1, flex=release, alpine=dict(version="3.24.4", sha256="c" * 64),
                    packages=list(builder.usb_live.PACKAGES))
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            directory = Path(temporary)
            (directory / "media").mkdir()
            (directory / "media/media.json").write_text(json.dumps(lock))
            stack.enter_context(patch.object(builder, "HERE", directory))
            stack.enter_context(patch.object(builder.sys, "argv", [
                "make_usb.py", "--image", str(directory / "new.img"), "--dry-run"]))
            network = stack.enter_context(patch("urllib.request.urlopen"))
            effects = [stack.enter_context(patch.object(builder, name))
                       for name in ("build_image", "publish_image", "flash_image")]
            stack.enter_context(redirect_stdout(io.StringIO()))
            error = stack.enter_context(redirect_stderr(io.StringIO()))
            try:
                builder.main()
                code = 0
            except SystemExit as stopped:
                code = stopped.code
            network.assert_not_called()
            for effect in effects:
                effect.assert_not_called()
            return code, error.getvalue()

    def test_locked_unvalidated_os_stops_without_network_or_image_work(self):
        code, error = self.dry_run("99999.0.0")
        self.assertNotEqual(code, 0)
        self.assertIn("99999.0.0", error)
        self.assertIn(builder.installer.compatibility()["FLEX_OS_VERSION"], error)

    def test_validated_lock_dry_run_does_not_query_latest_metadata(self):
        version = builder.installer.compatibility()["FLEX_OS_VERSION"]
        code, error = self.dry_run(version)
        self.assertEqual(code, 0, error)


class GptTests(unittest.TestCase):
    def assert_valid_backup(self, image):
        sectors = len(image) // 512
        primary = image[512:1024]
        backup = bytearray(image[-512:])
        self.assertEqual(backup[:8], b"EFI PART")
        self.assertEqual(struct.unpack_from("<QQ", backup, 24), (sectors - 1, 1))
        size, crc = struct.unpack_from("<II", backup, 12)
        backup[16:20] = bytes(4)
        self.assertEqual(zlib.crc32(backup[:size]), crc)
        entries_lba, count, stride, table_crc = struct.unpack_from("<QIII", backup, 72)
        table = image[entries_lba * 512:entries_lba * 512 + count * stride]
        self.assertEqual(zlib.crc32(table), table_crc)
        primary_lba = struct.unpack_from("<Q", primary, 72)[0]
        self.assertEqual(table, image[primary_lba * 512:primary_lba * 512 + count * stride])
        self.assertEqual(primary[56:72], backup[56:72])
        self.assertEqual(struct.unpack_from("<Q", primary, 32)[0], sectors - 1)
        self.assertEqual(struct.unpack_from("<I", image, 446 + 12)[0], sectors - 1)

    def test_append_preserves_original_partitions_and_payloads(self):
        original = recovery_image()
        original_guid, original_parts = builder.installer.read_gpt(io.BytesIO(original))
        stream = io.BytesIO(original)
        tools = builder.add_tools_partition(stream, builder.MIB)
        result = stream.getvalue()
        guid, parts = builder.installer.read_gpt(io.BytesIO(result))
        self.assertEqual(guid, original_guid)
        self.assertEqual(set(parts), set(original_parts) | {13})
        for number, part in original_parts.items():
            self.assertEqual(parts[number], part)
            first, end = part["first"] * 512, (part["last"] + 1) * 512
            self.assertEqual(result[first:end], original[first:end])
        self.assertEqual(result[1024:1024 + 12 * 128], original[1024:1024 + 12 * 128])
        self.assertEqual(parts[13]["type"], builder.DATA_TYPE)
        self.assertEqual(parts[13]["label"], builder.TOOLS_LABEL)
        self.assertGreaterEqual(parts[13]["first"] * 512, len(original))
        self.assertEqual(parts[13]["first"] * 512 % builder.MIB, 0)
        self.assertEqual((parts[13]["last"] - parts[13]["first"] + 1) * 512, builder.MIB)
        self.assertEqual(tools["total_size"], len(result))
        self.assert_valid_backup(result)

    def test_occupied_partition_13_is_never_replaced(self):
        def occupied(entries):
            pos = 12 * 128
            entries[pos:pos + 16] = uuid.UUID(builder.DATA_TYPE).bytes_le
            entries[pos + 16:pos + 32] = uuid.uuid4().bytes_le
            struct.pack_into("<QQ", entries, pos + 32, 100, 101)
        original = recovery_image(edit_entries=occupied)
        stream = io.BytesIO(original)
        with self.assertRaisesRegex(ValueError, "already uses partition 13"):
            builder.add_tools_partition(stream, builder.MIB)
        self.assertEqual(stream.getvalue(), original)

    def test_relocate_backup_to_real_larger_device_keeps_all_entries(self):
        stream = io.BytesIO(recovery_image())
        builder.add_tools_partition(stream, builder.MIB)
        original = stream.getvalue()
        old_backup = struct.unpack_from("<Q", original, 512 + 32)[0]
        size = len(original) + 3 * builder.MIB
        records = builder.gpt_records(io.BytesIO(original), size)
        result = apply_records(original, size, records)
        self.assertEqual(builder.installer.read_gpt(io.BytesIO(result))[1],
                         builder.installer.read_gpt(io.BytesIO(original))[1])
        self.assertEqual(result[1024:1024 + 128 * 128], original[1024:1024 + 128 * 128])
        self.assertEqual(result[(old_backup - 32) * 512:(old_backup + 1) * 512], bytes(33 * 512))
        self.assert_valid_backup(result)

    def test_refuses_bad_protective_mbr_and_too_small_output(self):
        for image in (fixtures.gpt_image(), recovery_image()):
            size = len(image) if image[446 + 4] != 0xEE else 50 * 512
            with self.subTest(size=size), self.assertRaises(ValueError):
                builder.gpt_records(io.BytesIO(image), size)


class PatchedChunkTests(unittest.TestCase):
    def test_patches_cross_chunk_boundaries_and_later_records_win(self):
        original = bytes(range(53))
        records = [(14, b"ABCDEFG"), (17, b"xyz"), (50, b"last-record"), (100, b"beyond")]
        expected = bytearray(original)
        expected[14:21] = b"ABCDEFG"
        expected[17:20] = b"xyz"
        expected[50:53] = b"las"
        with patch.object(builder, "CHUNK", 16):
            chunks = list(builder.patched_chunks(io.BytesIO(original), records))
        self.assertEqual([offset for offset, _ in chunks], [0, 16, 32, 48])
        self.assertEqual(b"".join(data for _, data in chunks), expected)
        self.assertEqual(len(b"".join(data for _, data in chunks)), len(original))


class GrubConfigTests(unittest.TestCase):
    def test_each_menu_selects_its_exported_root_before_device_free_boot_paths(self):
        config = builder.grub_config()
        preamble = config.split("menuentry", 1)[0]
        statements = [shlex.split(line) for line in preamble.splitlines() if line.strip()]
        assignments = dict(command[1].split("=", 1) for command in statements
                           if command[0] == "set" and "=" in command[1])
        self.assertEqual(assignments["flex_esp"], "($usb_disk,gpt12)")
        self.assertEqual(assignments["tools"], "($usb_disk,gpt13)")
        exported = {name for command in statements if command[0] == "export" for name in command[1:]}
        menus = re.findall(r"(?ms)^menuentry [^\n]+\{\n(.*?)^\}", config)
        self.assertEqual(len(menus), 2)
        for menu in menus:
            commands = [shlex.split(line) for line in menu.splitlines() if line.strip()]
            boots = [(index, command) for index, command in enumerate(commands)
                     if command[0] in ("chainloader", "linux", "initrd")]
            expected_root = "flex_esp" if any(command[0] == "chainloader" for _, command in boots) else "tools"
            with self.subTest(root=expected_root):
                self.assertIn(expected_root, exported)
                self.assertTrue(boots)
                active_root = None
                for command in commands:
                    if command[0] == "set" and command[1].startswith("root="):
                        active_root = command[1].split("=", 1)[1]
                    elif command[0] in ("chainloader", "linux", "initrd"):
                        self.assertEqual(active_root, f"${{{expected_root}}}")
                        self.assertTrue(command[1].startswith("/"), command)
                        self.assertNotIn("$", command[1])
                        self.assertNotIn("(", command[1])
                        if command[0] == "linux":
                            self.assertIn("modules=loop,squashfs,sd-mod,usb-storage", command)
                            self.assertFalse(any(arg.startswith("alpine_dev=") for arg in command))


class UsbSelectionTests(unittest.TestCase):
    def setUp(self):
        self.nodes = [dict(name="/dev/sdb", type="disk", tran="usb", size=8 * 1024**3,
                           model="Same Model", serial="SERIAL-ONE"),
                      dict(name="/dev/sdc", type="disk", tran="usb", size=16 * 1024**3,
                           model="Same Model", serial="SERIAL-TWO")]
        self.identities = {node["name"]: dict(path=node["name"], device=index + 1,
                           size=node["size"], model=node["model"], serial=node["serial"])
                           for index, node in enumerate(self.nodes)}

    def select(self, answers, blocked=()):
        def check(path):
            if str(path) in blocked:
                raise ValueError("USB is mounted/in use")
            return self.identities[str(path)]
        output = io.StringIO()
        with (patch.object(builder.sys.stdin, "isatty", return_value=True),
              patch.object(builder, "run", return_value=json.dumps({"blockdevices": self.nodes})),
              patch.object(builder, "check_usb", side_effect=check) as checked,
              patch("builtins.input", side_effect=answers) as prompted,
              redirect_stdout(output)):
            selected = builder.select_usb()
        return selected, output.getvalue(), checked, prompted

    def test_two_same_model_devices_are_identified_by_serial_and_explicit_choice(self):
        selected, output, checked, prompted = self.select(["2"])
        self.assertIs(selected, self.identities["/dev/sdc"])
        for node in self.nodes:
            self.assertIn(node["name"], output)
            self.assertIn(node["serial"], output)
        self.assertIn("Same Model", output)
        self.assertIn("GiB", output)
        self.assertEqual(prompted.call_count, 1)
        self.assertEqual({str(call.args[0]) for call in checked.call_args_list}, {"/dev/sdb", "/dev/sdc"})

    def test_internal_disks_excluded_and_mounted_usb_cannot_be_selected(self):
        self.nodes.insert(0, dict(name="/dev/nvme0n1", type="disk", tran="nvme", size=10**12,
                                 model="Internal", serial="HOST"))
        selected, output, checked, prompted = self.select(["2", "1"], blocked=("/dev/sdb",))
        self.assertIs(selected, self.identities["/dev/sdc"])
        self.assertIn("mounted/in use", output)
        self.assertNotIn("/dev/nvme0n1", {str(call.args[0]) for call in checked.call_args_list})
        self.assertEqual(prompted.call_count, 2)

    def test_invalid_input_never_defaults_to_any_device(self):
        selected, _, _, prompted = self.select(["0", "-1", "three", "3", "2"])
        self.assertIs(selected, self.identities["/dev/sdc"])
        self.assertEqual(prompted.call_count, 5)

    def test_no_eligible_usb_fails_without_prompting(self):
        for nodes, blocked in (([], ()), (self.nodes, ("/dev/sdb", "/dev/sdc"))):
            self.nodes = nodes
            with self.subTest(nodes=nodes):
                with self.assertRaises(ValueError):
                    # Any unexpected prompt exhausts this sequence and fails the test.
                    self.select([], blocked=blocked)

    def test_cancel_or_empty_answer_never_selects_even_a_single_usb(self):
        self.nodes = self.nodes[:1]
        for answer in ("q", ""):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                self.select([answer])

    def test_noninteractive_input_refuses_before_listing_devices(self):
        with (patch.object(builder.sys.stdin, "isatty", return_value=False),
              patch.object(builder, "run") as listed,
              patch("builtins.input") as prompted):
            with self.assertRaises(ValueError):
                builder.select_usb()
            listed.assert_not_called()
            prompted.assert_not_called()


class MainSelectionTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(path="/dev/sdc", device=3, size=8 * 1024**3,
                             serial="SELECTED", model="USB")

    def test_default_flow_requires_matching_full_path_before_network(self):
        for answer in ("", "yes", "/dev/sdb"):
            with self.subTest(answer=answer), ExitStack() as stack:
                stack.enter_context(patch.object(builder.sys, "argv", ["make_usb.py"]))
                selected = stack.enter_context(patch.object(builder, "select_usb", return_value=self.identity))
                stack.enter_context(patch("builtins.input", return_value=answer))
                network = stack.enter_context(patch("urllib.request.urlopen"))
                inputs = stack.enter_context(patch.object(builder, "locked_inputs"))
                build = stack.enter_context(patch.object(builder, "build_image"))
                write = stack.enter_context(patch.object(builder, "flash_image"))
                stack.enter_context(redirect_stdout(io.StringIO()))
                stack.enter_context(redirect_stderr(io.StringIO()))
                with self.assertRaises(SystemExit) as stopped:
                    builder.main()
                self.assertNotEqual(stopped.exception.code, 0)
                selected.assert_called_once_with()
                for effect in (network, inputs, build, write):
                    effect.assert_not_called()

    def test_matching_selected_path_allows_preparation(self):
        with (patch.object(builder.sys, "argv", ["make_usb.py"]),
              patch.object(builder, "select_usb", return_value=self.identity) as selected,
              patch("builtins.input", return_value=self.identity["path"]) as confirmed,
              patch.object(builder, "locked_media", side_effect=RuntimeError("preparation reached")) as locked,
              patch("urllib.request.urlopen") as network,
              redirect_stdout(io.StringIO())):
            with self.assertRaisesRegex(RuntimeError, "preparation reached"):
                builder.main()
            selected.assert_called_once_with()
            confirmed.assert_called_once()
            locked.assert_called_once()
            network.assert_not_called()

    def test_cancelled_selection_has_no_network_or_write_effects(self):
        with (patch.object(builder.sys, "argv", ["make_usb.py"]),
              patch.object(builder, "select_usb", side_effect=ValueError("Cancelled")),
              patch("urllib.request.urlopen") as network,
              patch.object(builder, "build_image") as build,
              patch.object(builder, "flash_image") as write,
              redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit):
                builder.main()
            for effect in (network, build, write):
                effect.assert_not_called()

    def test_explicit_disk_headless_still_requires_erase(self):
        with (patch.object(builder.sys, "argv", ["make_usb.py", "--disk", "/dev/sdc"]),
              patch.object(builder.sys.stdin, "isatty", return_value=False),
              patch.object(builder, "select_usb") as selected,
              patch("urllib.request.urlopen") as network,
              patch.object(builder, "flash_image") as write,
              redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit):
                builder.main()
            for effect in (selected, network, write):
                effect.assert_not_called()

    def test_explicit_disk_and_erase_remain_available_without_a_terminal(self):
        with (patch.object(builder.sys, "argv", ["make_usb.py", "--disk", "/dev/sdc", "--erase"]),
              patch.object(builder.sys.stdin, "isatty", return_value=False),
              patch.object(builder, "check_usb", return_value=self.identity) as checked,
              patch.object(builder, "select_usb") as selected,
              patch("builtins.input") as prompted,
              patch.object(builder, "locked_media", side_effect=RuntimeError("preparation reached"))):
            with self.assertRaisesRegex(RuntimeError, "preparation reached"):
                builder.main()
            checked.assert_called_once_with(Path("/dev/sdc"))
            selected.assert_not_called()
            prompted.assert_not_called()

    def test_privileged_write_restores_pinned_tools_after_sudo_resets_path(self):
        original_path = "/pinned/tools:/usr/bin"
        with (patch.object(builder.sys, "argv", ["make_usb.py", "--disk", "/dev/sdc", "--erase",
                   "--write-image", "/prepared.img", "--expected-target", json.dumps(self.identity),
                   "--tool-path", original_path]),
              patch.dict(builder.os.environ, PATH="/sudo/secure/path"),
              patch.object(builder, "flash_image") as flash):
            def inspect(image, expected):
                self.assertEqual(builder.os.environ["PATH"], original_path)
                self.assertEqual(image, Path("/prepared.img"))
                self.assertEqual(expected, self.identity)
            flash.side_effect = inspect
            builder.main()
            flash.assert_called_once()

    def test_prepared_image_uses_module_entrypoint_with_explicit_pythonpath_after_sudo(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            media = dict(flex=ReleaseTests().release(), alpine=dict(version="3.24.4"))
            stack.enter_context(patch.object(builder.sys, "argv", [
                "make_usb.py", "--disk", self.identity["path"], "--erase"]))
            stack.enter_context(patch.dict(builder.os.environ, CROS_KIT_PAYLOAD=str(root / "payload"),
                                           XDG_CACHE_HOME=str(root), PATH="/pinned/tools"))
            stack.enter_context(patch.object(builder, "check_usb", return_value=self.identity))
            stack.enter_context(patch.object(builder, "locked_media", return_value=media))
            stack.enter_context(patch.object(builder.shutil, "which", return_value="/pinned/tool"))
            stack.enter_context(patch.object(builder, "locked_inputs", return_value=(
                root / "flex.zip", root / "alpine.iso", root / "repository")))
            stack.enter_context(patch.object(builder, "extract_flex",
                                            side_effect=lambda archive, release, image: image.touch()))
            stack.enter_context(patch.object(builder.usb_live, "prepare_live",
                                            side_effect=lambda iso, live, payload, repo: live.mkdir()))
            stack.enter_context(patch.object(builder, "build_image"))
            stack.enter_context(patch.object(builder.os, "geteuid", return_value=1000))
            ran = stack.enter_context(patch.object(builder.subprocess, "run"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            builder.main()
            ran.assert_called_once()
            command = ran.call_args.args[0]
            self.assertEqual(command[:7], ["sudo", "env", "PYTHONPATH=" + str(builder.HERE),
                                           builder.sys.executable, "-P", "-m", "cros_kit.make_usb"])
            self.assertEqual(command[command.index("--disk") + 1], self.identity["path"])
            self.assertEqual(json.loads(command[command.index("--expected-target") + 1]), self.identity)
            self.assertEqual(command[command.index("--tool-path") + 1], "/pinned/tools")
            self.assertTrue(ran.call_args.kwargs["check"])


class ImagePublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source.img"
        self.destination = self.root / "output.img"
        self.data = b"image" + bytes(67) + b"tail" + bytes(13)
        self.source.write_bytes(self.data)

    def test_publishes_complete_image_including_sparse_trailing_zeros(self):
        with patch.object(builder, "CHUNK", 8):
            builder.publish_image(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.data)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["output.img", "source.img"])

    def test_delayed_write_failure_leaves_no_partial_output_and_retry_succeeds(self):
        with patch.object(builder.os, "fsync", side_effect=OSError(errno.ENOSPC, "disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                builder.publish_image(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.glob(".*.part")), [])
        builder.publish_image(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.data)

    def test_existing_output_is_never_overwritten_even_at_publication(self):
        self.destination.write_bytes(b"keep user file")
        with self.assertRaises(FileExistsError):
            builder.publish_image(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b"keep user file")
        self.assertEqual(list(self.root.glob(".*.part")), [])


class UsbSafetyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.target = Path(directory.name) / "usb"
        self.target.touch()
        self.node = dict(name=str(self.target), type="disk", tran="usb", serial="ABC",
                         model="Test USB", size=8 * builder.MIB, **{"maj:min": "8:16"},
                         mountpoints=[None], children=[dict(name=str(self.target) + "1",
                         type="part", mountpoints=[None], **{"maj:min": "8:17"})])
        self.swap = False
        self.holder = False
        self.diskseq = "77"

    def check(self):
        def read_text(path, *args, **kwargs):
            if str(path) == "/proc/swaps":
                return "Filename Type Size Used Priority\n" + (str(self.target) + "1 partition 1 0 -1\n" if self.swap else "")
            if str(path) == "/sys/dev/block/8:16/size":
                return str(8 * builder.MIB // 512)
            if str(path) == "/sys/dev/block/8:16/queue/logical_block_size":
                return "512"
            if str(path) == "/sys/dev/block/8:16/diskseq":
                if self.diskseq is None:
                    raise FileNotFoundError(path)
                return self.diskseq
            raise AssertionError(f"Unexpected host read: {path}")
        with ExitStack() as stack:
            stack.enter_context(patch.object(builder.stat, "S_ISBLK", return_value=True))
            stack.enter_context(patch.object(builder, "run", return_value=json.dumps({"blockdevices": [self.node]})))
            stack.enter_context(patch.object(Path, "read_text", read_text))
            stack.enter_context(patch.object(Path, "is_dir", return_value=self.holder))
            stack.enter_context(patch.object(Path, "iterdir", return_value=iter([Path("fake-holder")])))
            return builder.check_usb(self.target)

    def test_offline_whole_usb_has_recheckable_identity(self):
        result = self.check()
        self.assertEqual(result, dict(path=str(self.target), device=self.target.stat().st_rdev,
                                     size=8 * builder.MIB, diskseq=77, serial="ABC", model="Test USB"))

    def test_regular_file_is_not_a_usb(self):
        with self.assertRaisesRegex(ValueError, "whole USB block device"):
            builder.check_usb(self.target)

    def test_refuses_internal_disk_and_partition(self):
        for field, value in (("tran", "sata"), ("type", "part")):
            with self.subTest(field=field):
                original = self.node[field]
                self.node[field] = value
                with self.assertRaisesRegex(ValueError, "whole USB disk"):
                    self.check()
                self.node[field] = original

    def test_refuses_mounted_swapped_or_held_partitions(self):
        self.node["children"][0]["mountpoints"] = ["/"]
        with self.assertRaisesRegex(ValueError, "mounted/in use"):
            self.check()
        self.node["children"][0]["mountpoints"] = [None]
        self.swap = True
        with self.assertRaisesRegex(ValueError, "mounted/in use"):
            self.check()
        self.swap, self.holder = False, True
        with self.assertRaisesRegex(ValueError, "holder"):
            self.check()

    def test_missing_kernel_diskseq_is_rejected(self):
        for value in (None, "invalid", "0"):
            self.diskseq = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "diskseq|kernel disk identity"):
                self.check()

    def test_same_device_number_and_capacity_replacement_refuses_before_writes(self):
        image = self.target.with_name("image")
        image.write_bytes(recovery_image())
        expected = dict(path=str(self.target), device=self.target.stat().st_rdev,
                        size=8 * builder.MIB, diskseq=7, serial="", model="USB")
        with (patch.object(builder, "check_usb", return_value=expected),
              patch.object(builder.fcntl, "ioctl", return_value=struct.pack("Q", 8)),
              patch.object(builder.os, "fdopen") as writable):
            with self.assertRaisesRegex(ValueError, "device instance changed"):
                builder.flash_image(image, expected)
        writable.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"")

    def test_unsupported_diskseq_ioctl_refuses_before_writes(self):
        image = self.target.with_name("image")
        image.write_bytes(recovery_image())
        expected = dict(path=str(self.target), device=self.target.stat().st_rdev,
                        size=8 * builder.MIB, diskseq=7, serial="A", model="USB")
        with (patch.object(builder, "check_usb", return_value=expected),
              patch.object(builder.fcntl, "ioctl", side_effect=OSError(errno.ENOTTY, "unsupported")),
              patch.object(builder.os, "fdopen") as writable):
            with self.assertRaisesRegex(ValueError, "Linux 5.15"):
                builder.flash_image(image, expected)
        writable.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"")

    def test_identity_change_refuses_before_opening_write_target(self):
        expected = dict(path=str(self.target), device=1, size=8 * builder.MIB, serial="old", model="USB")
        current = dict(expected, serial="replacement")
        with patch.object(builder, "check_usb", return_value=current), patch.object(builder.os, "open") as opened:
            with self.assertRaisesRegex(ValueError, "identity/size changed"):
                builder.flash_image(Path("unused.img"), expected)
            opened.assert_not_called()

    def test_open_descriptor_identity_change_refuses_before_writes(self):
        image = self.target.with_name("image")
        image.write_bytes(recovery_image())
        expected = dict(path=str(self.target), device=1, size=8 * builder.MIB, diskseq=7, serial="A", model="USB")
        with (patch.object(builder, "check_usb", return_value=expected),
              patch.object(builder.os, "open", return_value=42),
              patch.object(builder.os, "close") as closed,
              patch.object(builder.os, "fstat", return_value=SimpleNamespace(st_rdev=2)),
              patch.object(builder.os, "fdopen") as writable):
            with self.assertRaisesRegex(ValueError, "device changed before writing"):
                builder.flash_image(image, expected)
            writable.assert_not_called()
            closed.assert_called_once_with(42)

    def test_open_descriptor_size_change_refuses_before_writes(self):
        image = self.target.with_name("image")
        image.write_bytes(recovery_image())
        expected = dict(path=str(self.target), device=1, size=8 * builder.MIB, diskseq=7, serial="A", model="USB")
        with (patch.object(builder, "check_usb", return_value=expected),
              patch.object(builder.os, "open", return_value=42),
              patch.object(builder.os, "close") as closed,
              patch.object(builder.os, "fstat", return_value=SimpleNamespace(st_rdev=1)),
              patch.object(builder.fcntl, "ioctl", side_effect=[struct.pack("Q", 7), struct.pack("Q", 16 * builder.MIB)]),
              patch.object(builder.os, "fdopen") as writable):
            with self.assertRaisesRegex(ValueError, "size changed before writing"):
                builder.flash_image(image, expected)
            writable.assert_not_called()
            closed.assert_called_once_with(42)

    def test_flash_completes_backup_table_that_crosses_source_image_end(self):
        # A real USB can exceed the prepared image by fewer than 32 GPT sectors.
        # Exercise the actual write/readback loop against an ordinary temp file.
        image = self.target.with_name("image")
        original = recovery_image()
        image.write_bytes(original)
        size = len(original) + 9 * 512
        self.target.write_bytes(b"\xa5" * size)
        expected = dict(path=str(self.target), device=self.target.stat().st_rdev,
                        size=size, diskseq=7, serial="A", model="USB")
        records = builder.gpt_records(io.BytesIO(original), size)
        with (patch.object(builder, "check_usb", return_value=expected),
              patch.object(builder.fcntl, "ioctl", side_effect=[struct.pack("Q", 7), struct.pack("Q", size), 0]) as ioctl,
              patch.object(builder, "run", return_value="")):
            builder.flash_image(image, expected)
        self.assertEqual([call.args[1:] for call in ioctl.call_args_list],
                         [(0x80081280, bytes(8)), (0x80081272, bytes(8)), (0x1261,)])
        self.assertEqual({call.args[0] for call in ioctl.call_args_list}, {ioctl.call_args_list[0].args[0]})
        result = self.target.read_bytes()
        GptTests().assert_valid_backup(result)
        self.assertEqual(result, apply_records(original, size, records))


if __name__ == "__main__":
    unittest.main()
