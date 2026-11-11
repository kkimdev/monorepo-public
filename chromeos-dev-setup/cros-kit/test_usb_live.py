#!/usr/bin/env python3
"""Offline media selection/preparation tests; no downloads or devices."""

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cros_kit_usb_live", HERE / "usb_live.py")
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)


def release_block(flavor="standard", arch="x86_64"):
    return f"""-
  title: "Standard"
  desc: |
    Network connection is required.
  branch: v3.24
  arch: {arch}
  version: 3.24.4
  flavor: alpine-{flavor}
  file: alpine-{flavor}-3.24.4-{arch}.iso
  size: 370147328
  sha256: {'a' * 64}
"""


class ReleaseTests(unittest.TestCase):
    def test_standard_not_virtual_or_other_arch(self):
        metadata = release_block("virt") + release_block(arch="aarch64") + release_block()
        release = live.latest_release(metadata)
        self.assertEqual(release["filename"], "alpine-standard-3.24.4-x86_64.iso")
        self.assertEqual(release["url"], live.ALPINE + "/v3.24/releases/x86_64/" + release["filename"])
        self.assertEqual(release["sha256"], "a" * 64)

    def test_rejects_missing_or_duplicate_standard(self):
        for metadata in (release_block("virt"), release_block() * 2):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                live.latest_release(metadata)

    def test_rejects_invalid_digest_filename_branch_and_size(self):
        original = release_block()
        for before, after in (("a" * 64, "not-a-digest"),
                              ("file: alpine-standard", "file: ../alpine-standard"),
                              ("branch: v3.24", "branch: v3.23"),
                              ("size: 370147328", "size: -1")):
            with self.subTest(field=before), self.assertRaises(ValueError):
                live.latest_release(original.replace(before, after))


class OverlayTests(unittest.TestCase):
    def test_boot_services_autologin_and_wizard_entrypoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tools.apkovl.tar.gz"
            live._overlay(path)
            first = path.read_bytes()
            with tarfile.open(path) as archive:
                names = set(archive.getnames())
                self.assertIn("etc/.default_boot_services", names)
                self.assertNotIn("etc/apk/repositories", names)
                world = archive.extractfile("etc/apk/world").read().decode().splitlines()
                for package in ("python3", "efibootmgr", "lsblk", "mount", "umount", "util-linux-misc"):
                    self.assertIn(package, world)
                inittab = archive.extractfile("etc/inittab").read().decode()
                self.assertIn("agetty --autologin root", inittab)
                profile = archive.extractfile("etc/profile.d/cros-kit.sh").read().decode()
                self.assertIn("/media/*/cros-kit/wizard.py", profile)
                self.assertNotIn("/media/CROSKIT", profile)
                self.assertEqual(archive.extractfile("etc/motd").read(), b"")
                subprocess.run(["sh", "-n"], input=profile, text=True, check=True)
                # The profile only offers an alias; launching is explicit.
                self.assertNotIn("install.py", profile)
                self.assertTrue(all(member.isfile() and member.uid == 0
                                    for member in archive.getmembers()))
            live._overlay(path)
            self.assertEqual(path.read_bytes(), first)


class WizardProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        archive_path = self.root / "tools.apkovl.tar.gz"
        live._overlay(archive_path)
        with tarfile.open(archive_path) as archive:
            profile = archive.extractfile("etc/profile.d/cros-kit.sh").read().decode()
        self.profile = self.root / "profile.sh"
        self.wizard = self.root / "media/disk with spaces/cros-kit/wizard.py"
        self.wizard.parent.mkdir(parents=True)
        self.wizard.write_text("# test wizard\n")
        self.profile.write_text(profile.replace("/media/*/cros-kit/wizard.py",
                                               str(self.root / "media/*/cros-kit/wizard.py")))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        executable = self.bin / "python3"
        executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FLEX_TEST_LOG"\n')
        executable.chmod(0o755)
        self.log = self.root / "calls"
        self.environment = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                                FLEX_TEST_LOG=str(self.log), FLEX_TEST_PROFILE=str(self.profile))

    def shell(self, script, interactive=True):
        return subprocess.run(["sh", *(["-i"] if interactive else []), "-c", script],
                              env=self.environment, capture_output=True, text=True, check=True)

    def test_interactive_profile_only_prints_command_without_launching(self):
        result = self.shell('. "$FLEX_TEST_PROFILE"\n. "$FLEX_TEST_PROFILE"\n')
        self.assertFalse(self.log.exists())
        self.assertIn("Run cros-kit to set up CrOS Kit (disk detected automatically).", result.stdout)

    def test_alias_launches_dynamic_path_and_forwards_arguments(self):
        self.shell('. "$FLEX_TEST_PROFILE"\ncros-kit\ncros-kit --remove\n')
        self.assertEqual(self.log.read_text().splitlines(),
                         [str(self.wizard), str(self.wizard) + " --remove"])

    def test_noninteractive_shell_does_not_launch(self):
        self.shell('. "$FLEX_TEST_PROFILE"', interactive=False)
        self.assertFalse(self.log.exists())


class PrepareTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.installer = self.root / "installer"
        self.installer.mkdir()
        for name in live.INSTALLER_FILES:
            (self.installer / name).write_bytes(name.encode())
        (self.installer / "compatibility.conf").write_bytes((HERE / "compatibility.conf").read_bytes())
        for name in ("crdyboot-debug.efi", "rw-init.sh", "rootdev-compat.so"):
            digest = hashlib.sha256((self.installer / name).read_bytes()).hexdigest()
            (self.installer / (name + ".sha256")).write_text(digest + "  " + name + "\n")
        self.destination = self.root / "staging"
        self.repository = self.root / "repository"
        packages = self.repository / "x86_64"
        packages.mkdir(parents=True)
        (packages / "APKINDEX.tar.gz").write_bytes(b"signed snapshot")
        for name in live.PACKAGES:
            (packages / f"{name}-1.0-r0.apk").write_text("signed package")
        self.calls = []
        self.release = "alpine-standard-3.24.4 261105\n"
        self.fail_offline = False

    def fake_run(self, arguments):
        args = [str(argument) for argument in arguments]
        self.calls.append(args)
        if args[0] == "bsdtar":
            destination = Path(args[args.index("-C") + 1])
            source = Path(args[args.index("-xf") + 1])
            if source.suffix == ".iso":
                files = {".alpine-release": self.release,
                         "boot/vmlinuz-lts": "kernel", "boot/initramfs-lts": "initramfs",
                         "boot/modloop-lts": "modloop", "apks/.boot_repository": "",
                         "apks/x86_64/alpine-keys-2.6-r0.apk": "keys",
                         "apks/x86_64/obsolete-1.0-r0.apk": "old package",
                         "apks/x86_64/APKINDEX.tar.gz": "old index"}
                for name, contents in files.items():
                    path = destination / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(contents)
                    path.chmod(0o444)
                (destination / "apks/x86_64").chmod(0o555)
            else:
                path = destination / "etc/apk/keys/official.rsa.pub"
                path.parent.mkdir(parents=True)
                path.write_text("key")
        elif "update" in args:
            raise AssertionError("Normal preparation must not refresh a repository")
        elif "--simulate" in args and self.fail_offline:
            raise ValueError("offline package missing")
        elif "fetch" in args:
            output = Path(args[args.index("--output") + 1])
            if "--no-network" in args:
                for package in (self.destination / "apks/x86_64").glob("*.apk"):
                    shutil.copyfile(package, output / package.name)
            else:
                raise AssertionError("Normal preparation must not fetch from the network")
        return ""

    def prepare(self):
        with patch.object(live, "_run", side_effect=self.fake_run):
            return live.prepare_live(self.root / "alpine.iso", self.destination, self.installer, self.repository)

    def test_signed_index_snapshot_complete_offline_checks_and_private_apk_root(self):
        metadata = self.prepare()
        self.assertEqual(metadata["packages"], len(live.PACKAGES))
        self.assertEqual((self.destination / "apks/x86_64/APKINDEX.tar.gz").read_bytes(), b"signed snapshot")
        self.assertFalse((self.destination / "apks/x86_64/obsolete-1.0-r0.apk").exists())
        self.assertEqual((self.destination / "boot/modloop-lts").read_bytes(), b"modloop")
        for name in live.INSTALLER_FILES:
            self.assertEqual((self.destination / "cros-kit" / name).read_bytes(),
                             (self.installer / name).read_bytes())
        self.assertTrue((self.destination / "cros-kit.apkovl.tar.gz").is_file())
        apk_calls = [args for args in self.calls if args[0] == "apk"]
        self.assertTrue(any("--usermode" in args for args in apk_calls))
        self.assertTrue(all("--no-network" in args for args in apk_calls))
        self.assertTrue(any("--no-network" in args and "--simulate" in args for args in apk_calls))
        self.assertTrue(any("--no-network" in args and "fetch" in args for args in apk_calls))
        for args in apk_calls:
            self.assertNotEqual(args[args.index("--root") + 1], "/")
            self.assertNotIn("--allow-untrusted", args)
            self.assertNotIn("--force-no-chroot", args)

    def test_rejects_overwriting_existing_directory(self):
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_text("unchanged")
        with self.assertRaisesRegex(ValueError, "must be empty"):
            self.prepare()
        self.assertEqual(marker.read_text(), "unchanged")
        self.assertFalse(self.calls)

    def test_malformed_compatibility_is_rejected_before_iso_extraction(self):
        (self.installer / "compatibility.conf").write_text("FLEX_OS_VERSION=unknown\n")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.calls)

    def test_rejects_bad_efi_digest_before_extracting(self):
        (self.installer / "crdyboot-debug.efi").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.prepare()
        self.assertFalse(self.calls)

    def test_rejects_bad_rw_init_digest_before_extracting(self):
        (self.installer / "rw-init.sh").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "rw-init.sh checksum mismatch"):
            self.prepare()
        self.assertFalse(self.calls)

    def test_rejects_bad_rootdev_digest_before_extracting(self):
        (self.installer / "rootdev-compat.so").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "rootdev-compat.so checksum mismatch"):
            self.prepare()
        self.assertFalse(self.calls)

    def test_rejects_missing_wizard_before_extracting(self):
        (self.installer / "wizard.py").unlink()
        with self.assertRaisesRegex(ValueError, "Missing CrOS Kit payload: wizard.py"):
            self.prepare()
        self.assertFalse(self.calls)

    def test_rejects_virtual_media(self):
        self.release = "alpine-virt-3.24.4 261105\n"
        with self.assertRaisesRegex(ValueError, "STANDARD"):
            self.prepare()
        self.assertFalse(any(args[0] == "apk" for args in self.calls))

    def test_offline_failure_prevents_final_payload(self):
        self.fail_offline = True
        with self.assertRaisesRegex(ValueError, "offline package missing"):
            self.prepare()
        self.assertFalse((self.destination / "cros-kit.apkovl.tar.gz").exists())


class NativeCommandTests(unittest.TestCase):
    def test_apk_solver_error_is_fatal_even_with_zero_exit_status(self):
        result = subprocess.CompletedProcess(["apk"], 0, "", "ERROR: unable to select packages\n")
        with patch.object(live.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "unable to select packages"):
                live._run(["apk", "fetch", "missing-package"])


if __name__ == "__main__":
    unittest.main()
