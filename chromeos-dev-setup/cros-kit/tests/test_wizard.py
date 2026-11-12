#!/usr/bin/env python3
"""Offline installer safety and confirmation tests; never mount or write devices."""

from contextlib import ExitStack, redirect_stdout
import copy
from dataclasses import replace
import io
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch


from cros_kit import wizard


ESP = "00000000-0000-4000-8000-000000000012"
REAL_RECHECK = wizard.recheck


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.disk = self.directory / "disk"
        self.disk.touch()
        (self.directory / "etc").mkdir()
        (self.directory / "etc/lsb-release").write_text(
            'CHROMEOS_RELEASE_BOARD="reven-signed-mp-v11keys"\nCHROMEOS_RELEASE_VERSION=16836.32.0\n')
        self.tree = dict(name=str(self.disk), type="disk", model="Test disk", serial="SERIAL",
                         size=64 * 1024**3, wwn="WWN", **{"maj:min": "8:0"},
                         mountpoints=[None], children=[dict(name="/dev/test3", partn=3,
                         type="part", label="ROOT-A", mountpoints=[None])])
        self.info = wizard.installer.DiskInfo(self.disk, "disk-guid", ESP, "16836.32.0")

    def test_full_read_only_preflight_preserves_selected_identity(self):
        with patch.object(wizard.installer, "configure", return_value=self.info) as configure:
            target = wizard.inspect(self.tree)
        configure.assert_called_once_with(self.disk, remove=False, dry_run=True)
        self.assertEqual(target.disk, self.disk)
        self.assertEqual(target.hardware, wizard.identity(self.tree))
        self.assertEqual(target.esp, ESP)
        self.assertEqual(target.version, "16836.32.0")
        self.assertIn("SERIAL", target.describe())
        for board in ("reven", "reven-signed-mp-v11keys"):
            self.assertEqual(wizard.installer.flex_release(f"CHROMEOS_RELEASE_BOARD={board}\nCHROMEOS_RELEASE_VERSION=123"), "123")
        for board in ("", "glados", "reven-other", "reven-signed-", "revenevil"):
            with self.subTest(board=board), self.assertRaises(ValueError):
                wizard.installer.flex_release(f"CHROMEOS_RELEASE_BOARD={board}")

    def test_mounted_disks_and_tools_usb_are_excluded_before_opening_them(self):
        mounted = copy.deepcopy(self.tree)
        mounted["children"][0]["mountpoints"] = ["/"]
        tools = copy.deepcopy(self.tree)
        tools["children"].append(dict(name="/dev/test13", partn=13,
                                     label="CROSKIT", mountpoints=[None]))
        legacy_tools = copy.deepcopy(tools)
        legacy_tools["children"][-1]["label"] = "FLEXTOOLS"
        nixos_tools = copy.deepcopy(tools)
        nixos_tools["children"][-1]["label"] = "CROSKIT_LIVE"
        partition = dict(self.tree, type="part")
        with patch.object(wizard.installer, "configure") as configure:
            for tree in (mounted, tools, legacy_tools, nixos_tools, partition):
                with self.subTest(tree=tree):
                    self.assertIsNone(wizard.inspect(tree))
            configure.assert_not_called()

    def test_remove_discovery_does_not_depend_on_supported_os_or_config(self):
        with patch.object(wizard.installer, "configure", return_value=replace(self.info, version="unknown")) as configure:
            target = wizard.inspect(self.tree, remove=True)
            self.assertEqual(target.version, "unknown")
            configure.assert_called_once_with(self.disk, remove=True, dry_run=True)

    def test_failed_preflight_never_offers_the_disk(self):
        with (patch.object(wizard, "disks", return_value=[self.tree]),
              patch.object(wizard.installer, "configure", side_effect=ValueError("Invalid stock loader")),
              redirect_stdout(io.StringIO()) as output):
            self.assertEqual(wizard.discover(), [])
        self.assertIn("Invalid stock loader", output.getvalue())

    def test_root_identification_never_replays_journal_and_unmounts_after_error(self):
        with patch.object(wizard.installer, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "probe stopped"):
                with wizard.installer.root_files("/dev/fake3"):
                    raise RuntimeError("probe stopped")
        mount, unmount = run.call_args_list
        self.assertEqual(mount.args[:5], ("mount", "-t", "ext4", "-o", "ro,noload,nosuid,nodev,noexec"))
        self.assertEqual(unmount.args, ("umount", mount.args[-1]))

    def test_failed_root_unmount_retains_mountpoint_and_all_identification_files(self):
        mounted = self.directory / "root-mountpoint"
        mounted.mkdir()
        release = mounted / "etc/lsb-release"

        def run(*args):
            if args[0] == "mount":
                release.parent.mkdir()
                release.write_text("CHROMEOS_RELEASE_BOARD=reven\n")
            else:
                raise subprocess.CalledProcessError(32, args, stderr="target is busy")

        with (patch.object(wizard.installer.tempfile, "mkdtemp", return_value=str(mounted)),
              patch.object(wizard.installer, "run", side_effect=run)):
            with self.assertRaisesRegex(ValueError, "Mount and files retained") as raised:
                with wizard.installer.root_files("/dev/fake3"):
                    self.assertTrue(release.is_file())
        self.assertIn(str(mounted), str(raised.exception))
        self.assertEqual(release.read_text(), "CHROMEOS_RELEASE_BOARD=reven\n")


class WizardFlowTests(unittest.TestCase):
    def setUp(self):
        self.target = wizard.Target(Path("/dev/flex"), ("hardware",), "disk-guid", ESP,
                                    "16836.32.0", "Disk", 64 * 1024**3, "SERIAL")

    def setup_mocks(self, stack, answers, targets=None):
        events = []
        responses = iter(answers)

        def answer(prompt):
            events.append(("prompt", prompt))
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            return value

        def configure(disk, *, remove=False, expected=None):
            events.append(("configure", remove, expected, disk))

        def discover(remove=False):
            events.append(("discover", remove))
            return targets if targets is not None else [self.target]

        stack.enter_context(patch.object(wizard.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(Path, "is_dir", return_value=True))
        stack.enter_context(patch.object(wizard.installer, "check_secure_boot"))
        stack.enter_context(patch.object(wizard, "discover", side_effect=discover))
        managed = stack.enter_context(patch.object(wizard.installer, "configure", side_effect=configure))
        rechecked = stack.enter_context(patch.object(wizard, "recheck", side_effect=lambda target: events.append(("recheck", target))))
        ran = stack.enter_context(patch.object(wizard.installer, "run"))
        prompted = stack.enter_context(patch("builtins.input", side_effect=answer))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return events, managed, rechecked, ran, prompted

    def test_single_target_is_shown_and_multiple_targets_require_explicit_choice(self):
        other = replace(self.target, disk=Path("/dev/other"), serial="OTHER")
        with patch("builtins.input") as prompted, redirect_stdout(io.StringIO()):
            self.assertIs(wizard.choose([self.target]), self.target)
            prompted.assert_not_called()
        with patch("builtins.input", side_effect=["0", "bad", "3", "2"]), redirect_stdout(io.StringIO()):
            self.assertIs(wizard.choose([self.target, other]), other)
        with patch("builtins.input", return_value=""), redirect_stdout(io.StringIO()):
            self.assertIsNone(wizard.choose([self.target, other]))

    def test_default_confirmation_cancels_after_read_only_preflight(self):
        with ExitStack() as stack:
            events, configured, rechecked, ran, _ = self.setup_mocks(stack, [""])
            self.assertFalse(wizard.setup())
            self.assertEqual(events[0], ("discover", False))
            configured.assert_not_called()
            rechecked.assert_not_called()
            ran.assert_not_called()

    def test_eof_before_confirmation_is_handled_without_writes(self):
        with ExitStack() as stack:
            _, configured, rechecked, ran, _ = self.setup_mocks(stack, [EOFError()])
            stack.enter_context(patch.object(sys, "argv", ["wizard.py"]))
            self.assertEqual(wizard.main(), 1)
            configured.assert_not_called()
            rechecked.assert_not_called()
            ran.assert_not_called()

    def test_discovery_failure_never_asks_for_confirmation(self):
        with ExitStack() as stack:
            _, configured, _, ran, prompted = self.setup_mocks(stack, [])
            wizard.discover.side_effect = OSError("Cannot list disks")
            with self.assertRaisesRegex(OSError, "Cannot list disks"):
                wizard.setup()
            configured.assert_not_called()
            prompted.assert_not_called()
            ran.assert_not_called()

    def test_confirmed_setup_rechecks_before_write_and_shutdown_is_optional(self):
        for shutdown_answer in ("", "s"):
            with self.subTest(shutdown=shutdown_answer), ExitStack() as stack:
                events, _, _, ran, _ = self.setup_mocks(stack, ["yes", shutdown_answer])
                output = io.StringIO()
                stack.enter_context(redirect_stdout(output))
                self.assertTrue(wizard.setup())
                self.assertEqual(events[0], ("discover", False))
                self.assertTrue(events[1][1].startswith("Continue?"))
                self.assertEqual(events[2], ("recheck", self.target))
                self.assertEqual(events[3], ("configure", False, (self.target.gpt, self.target.esp), self.target.disk))
                self.assertIn("Configured and Debug share saved OS changes", output.getvalue())
                self.assertIn("ChromeOS Flex Configured (developer mode disabled)", output.getvalue())
                self.assertIn("Debug enables passwordless host root", output.getvalue())
                if shutdown_answer == "":
                    ran.assert_called_once_with("poweroff")
                else:
                    ran.assert_not_called()

    def test_hardware_change_disappearance_or_duplicate_identity_blocks_the_write(self):
        tree = dict(name="/dev/flex", size=self.target.size, model="Disk", serial="SERIAL",
                    wwn=None, **{"maj:min": "8:0"})
        target = replace(self.target, hardware=wizard.identity(tree))
        for trees in ([dict(tree, serial="REPLACEMENT")], [], [tree, tree]):
            with self.subTest(trees=trees), ExitStack() as stack:
                _, configured, mocked_recheck, ran, _ = self.setup_mocks(stack, ["yes"], targets=[target])
                # Exercise the real recheck rather than simulating its failure.
                mocked_recheck.side_effect = REAL_RECHECK
                stack.enter_context(patch.object(wizard, "disks", return_value=trees))
                with self.assertRaisesRegex(ValueError, "changed or became unavailable"):
                    wizard.setup()
                configured.assert_not_called()
                self.assertNotIn("poweroff", [call.args[0] for call in ran.call_args_list])

    def test_remove_reuses_same_preflight_confirmation_and_backend(self):
        with ExitStack() as stack:
            events, _, _, _, _ = self.setup_mocks(stack, ["y", "s"])
            self.assertTrue(wizard.setup(remove=True))
            self.assertEqual(events[0], ("discover", True))
            self.assertEqual([event[1:3] for event in events if event[0] == "configure"],
                             [(True, (self.target.gpt, self.target.esp))])

    def test_backend_identity_rejection_prevents_shutdown(self):
        with ExitStack() as stack:
            _, configured, rechecked, ran, _ = self.setup_mocks(stack, ["y"])
            configured.side_effect = ValueError("Selected disk changed or became unavailable")
            with self.assertRaisesRegex(ValueError, "changed or became unavailable"):
                wizard.setup()
            rechecked.assert_called_once_with(self.target)
            configured.assert_called_once_with(self.target.disk, remove=False,
                                              expected=(self.target.gpt, self.target.esp))
            ran.assert_not_called()

    def test_no_targets_never_prompts_or_calls_backend(self):
        with ExitStack() as stack:
            _, managed, _, ran, prompted = self.setup_mocks(stack, [], targets=[])
            self.assertFalse(wizard.setup())
            managed.assert_not_called()
            prompted.assert_not_called()
            ran.assert_not_called()


if __name__ == "__main__":
    unittest.main()
