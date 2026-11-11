#!/usr/bin/env python3
"""Exercise persistent data and failure recovery; never mount or pivot host paths."""

import errno
import hashlib
import os
import shutil
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("rw-init.sh")
SOURCE = SCRIPT.read_text().rsplit('\nrw_main "$@"', 1)[0]


class RuntimeFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.store = self.root / "store"
        self.version = "a" * 64
        (self.runtime / "version").write_text(self.version + "\n")
        self.source = SOURCE.replace("/sys/fs/selinux/enforce", str(self.root / "selinux-enforce"))

    def shell(self, body, *, source=None):
        setup = '''
RW_RUNTIME="$FLEX_TEST_ROOT/runtime"
RW_STORE="$FLEX_TEST_ROOT/store"
# The backing fixtures belong to the test runner. Simulate only the ownership
# requirement; stat modes and every data operation use the actual filesystem.
stat() { if [ "$1" = -c ] && [ "$2" = %u ]; then printf '0\\n'; else command stat "$@"; fi; }
'''
        return subprocess.run(["/bin/sh", "-c", (source or self.source) + setup + body],
                              env=dict(os.environ, FLEX_TEST_ROOT=str(self.root)),
                              capture_output=True, text=True, timeout=15)

    def create_layer(self):
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.store / "root" / "upper"

    def change_version(self, version="b" * 64):
        (self.runtime / "version").write_text(version + "\n")


class MountListTests(RuntimeFixture):
    def snapshot(self, paths):
        mountinfo = self.root / "mountinfo"
        mountinfo.write_text("".join(
            f"{number} 1 0:1 / {path} rw - tmpfs tmpfs rw\n"
            for number, path in enumerate(paths, 10)))
        result = self.shell("rw_mount_list", source=self.source.replace(
            "/proc/self/mountinfo", str(mountinfo)))
        output = self.runtime / "mounts"
        return result, output.read_text().splitlines() if output.exists() else []

    def test_nested_trees_move_as_one_and_file_mounts_are_retained(self):
        paths = ["/", "/var", "/var/log", "/var/log/private", "/various", "/tmp",
                 "/tmp/flex-debug", "/tmp/flex-debug/runtime", "/etc/localtime",
                 "/mnt/stateful_partition", "/mnt/stateful_partition/encrypted",
                 "/dev", "/dev/pts", "/proc", "/sys", "/var"]
        result, targets = self.snapshot(paths)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(targets), {"/dev", "/etc/localtime", "/mnt/stateful_partition",
                                        "/proc", "/sys", "/var", "/various", "/tmp"})
        self.assertEqual(targets[0], "/proc")
        self.assertEqual(targets[-1], "/tmp")
        self.assertEqual(len(targets), len(set(targets)))

    def test_encoded_names_are_rejected_before_any_mount_list_can_be_used(self):
        for path in (r"/mnt/a\040b", r"/mnt/a\011b", r"/mnt/a\012b", r"/mnt/a\134b"):
            with self.subTest(path=path):
                result, targets = self.snapshot(["/", "/tmp", path])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(targets, [])


class LayerTests(RuntimeFixture):
    def saved_tree(self):
        upper = self.create_layer()
        (upper / "etc").mkdir(mode=0o750)
        edited = upper / "etc" / "edited"
        edited.write_bytes(b"my edited setting\n")
        edited.chmod(0o640)
        os.link(edited, upper / "hardlink")
        (upper / "symlink").symlink_to("etc/edited")
        (upper / "added").write_text("new file\n")
        os.mkfifo(upper / "named-pipe", mode=0o600)
        return upper

    def test_version_transition_preserves_real_data_hardlinks_modes_and_symlinks(self):
        original = self.saved_tree()
        self.change_version()
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        upper = self.store / "root" / "upper"
        self.assertEqual((upper / "etc/edited").read_bytes(), b"my edited setting\n")
        self.assertEqual((upper / "added").read_text(), "new file\n")
        self.assertEqual(stat.S_IMODE((upper / "etc/edited").stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE((upper / "etc").stat().st_mode), 0o750)
        self.assertEqual((upper / "etc/edited").stat().st_ino, (upper / "hardlink").stat().st_ino)
        self.assertEqual(os.readlink(upper / "symlink"), "etc/edited")
        self.assertTrue(stat.S_ISFIFO((upper / "named-pipe").stat().st_mode))
        self.assertEqual((upper.parent / "version").read_text(), "b" * 64 + "\n")
        backup = self.store / "root.previous" / "upper"
        self.assertEqual((backup / "etc/edited").read_bytes(), b"my edited setting\n")
        self.assertNotEqual((backup / "etc/edited").stat().st_ino, (upper / "etc/edited").stat().st_ino)
        self.assertFalse((self.store / "root.next").exists())
        self.assertFalse((self.runtime / "import").exists())

    def test_missing_version_on_used_upper_forces_fresh_import(self):
        upper = self.saved_tree()
        inode = (upper / "etc/edited").stat().st_ino
        (upper.parent / "version").unlink()
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((upper / "etc/edited").read_bytes(), b"my edited setting\n")
        self.assertNotEqual((upper / "etc/edited").stat().st_ino, inode)
        self.assertEqual((upper.parent / "version").read_text(), self.version + "\n")
        self.assertFalse((self.store / "root.previous/version").exists())

    def test_version_transition_keeps_sparse_files_sparse_and_preserves_contents(self):
        upper = self.create_layer()
        sparse = upper / "sparse-image"
        size = 8 * 1024 * 1024
        with sparse.open("wb") as stream:
            stream.write(b"image-start")
            stream.seek(size - len(b"image-end"))
            stream.write(b"image-end")
        if sparse.stat().st_blocks * 512 >= size // 8:
            self.skipTest("Fixture filesystem does not retain sparse holes")

        self.change_version()
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sparse.stat().st_size, size)
        self.assertLess(sparse.stat().st_blocks * 512, size // 8)
        with sparse.open("rb") as stream:
            self.assertEqual(stream.read(len(b"image-start")), b"image-start")
            stream.seek(size // 2)
            self.assertEqual(stream.read(4096), bytes(4096))
            stream.seek(-len(b"image-end"), os.SEEK_END)
            self.assertEqual(stream.read(), b"image-end")

    def test_existing_version_marker_is_replaced_atomically(self):
        self.create_layer()
        marker = self.store / "root/version"
        before = marker.stat().st_ino
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(marker.stat().st_ino, before)
        self.assertEqual(marker.read_text(), self.version + "\n")
        self.assertFalse((self.store / "root/version.next").exists())

    def test_same_version_boot_does_not_copy_saved_files(self):
        upper = self.saved_tree()
        inode = (upper / "etc/edited").stat().st_ino
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((upper / "etc/edited").stat().st_ino, inode)
        self.assertFalse((self.store / "root.previous").exists())

    def test_user_xattrs_are_preserved_and_lower_identity_attrs_are_excluded(self):
        upper = self.saved_tree()
        edited = upper / "etc/edited"
        try:
            os.setxattr(edited, "user.flex-test", b"saved attribute")
        except OSError as error:
            self.skipTest(f"Fixture filesystem has no user xattr support: {error}")
        self.change_version()
        result = self.shell(r'''
tar() {
  printf '%s\n' "$@" >> "$FLEX_TEST_ROOT/tar-arguments"
  command tar "$@"
}
rw_layer
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.getxattr(upper / "etc/edited", "user.flex-test"), b"saved attribute")
        arguments = (self.root / "tar-arguments").read_text().splitlines()
        # These origin/index references belong to the replaced lower OS tree.
        for name in ("origin", "impure", "nlink", "upper", "uuid"):
            self.assertIn("--xattrs-exclude=trusted.overlay." + name, arguments)
        self.assertNotIn("--xattrs-exclude=trusted.overlay.opaque", arguments)

    def test_access_and_default_posix_acls_survive_import_as_raw_xattrs(self):
        upper = self.saved_tree()
        edited = upper / "etc/edited"
        directory = upper / "etc"

        def acl(owner, named_user, group, mask, other):
            # Linux POSIX ACL xattr v2: header, then tag/permission/identifier.
            entries = ((0x01, owner, 0xFFFFFFFF), (0x02, named_user, 12345),
                       (0x04, group, 0xFFFFFFFF), (0x10, mask, 0xFFFFFFFF),
                       (0x20, other, 0xFFFFFFFF))
            return struct.pack("<I", 2) + b"".join(
                struct.pack("<HHI", *entry) for entry in entries)

        access = acl(6, 4, 4, 4, 0)
        default = acl(7, 5, 5, 5, 0)
        try:
            os.setxattr(edited, "system.posix_acl_access", access)
            os.setxattr(directory, "system.posix_acl_default", default)
        except OSError as error:
            if error.errno in (errno.EOPNOTSUPP, errno.EPERM, errno.EACCES):
                self.skipTest(f"Fixture filesystem does not permit POSIX ACLs: {error}")
            raise
        self.assertEqual(os.getxattr(edited, "system.posix_acl_access"), access)
        self.assertEqual(os.getxattr(directory, "system.posix_acl_default"), default)
        self.assertEqual(stat.S_IMODE(edited.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o750)

        self.change_version()
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.getxattr(edited, "system.posix_acl_access"), access)
        self.assertEqual(os.getxattr(directory, "system.posix_acl_default"), default)
        self.assertEqual(stat.S_IMODE(edited.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o750)
        self.assertEqual(edited.read_bytes(), b"my edited setting\n")
        self.assertEqual(edited.stat().st_ino, (upper / "hardlink").stat().st_ino)

    def test_whiteout_character_devices_survive_import_when_permitted(self):
        upper = self.create_layer()
        whiteout = upper / "deleted-stock-file"
        try:
            os.mknod(whiteout, stat.S_IFCHR | 0o600, os.makedev(0, 0))
        except PermissionError:
            self.skipTest("Creating overlay whiteouts requires CAP_MKNOD")
        self.change_version()
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        info = whiteout.stat()
        self.assertTrue(stat.S_ISCHR(info.st_mode))
        self.assertEqual(info.st_rdev, os.makedev(0, 0))

    def test_opaque_directory_is_preserved_but_origin_is_dropped_when_permitted(self):
        upper = self.create_layer()
        opaque = upper / "opaque"
        opaque.mkdir()
        try:
            os.setxattr(opaque, "trusted.overlay.opaque", b"y")
            os.setxattr(opaque, "trusted.overlay.origin", b"old lower identity")
        except PermissionError:
            self.skipTest("Trusted overlay xattrs require CAP_SYS_ADMIN")
        self.change_version()
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.getxattr(opaque, "trusted.overlay.opaque"), b"y")
        self.assertNotIn("trusted.overlay.origin", os.listxattr(opaque))

    def test_incomplete_staging_tree_is_rebuilt_without_losing_active_data(self):
        self.saved_tree()
        next_layer = self.store / "root.next"
        next_layer.mkdir()
        (next_layer / "partial").write_text("incomplete import")
        self.change_version()
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.store / "root/upper/etc/edited").read_bytes(), b"my edited setting\n")
        self.assertFalse((self.store / "root/partial").exists())

    def test_interrupted_activation_recovers_previous_tree_and_retries_import(self):
        self.saved_tree()
        (self.store / "root").rename(self.store / "root.previous")
        next_layer = self.store / "root.next"
        next_layer.mkdir()
        (next_layer / "partial").write_text("uncommitted staging data")
        self.change_version()
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.store / "root/upper/etc/edited").read_bytes(), b"my edited setting\n")
        self.assertEqual((self.store / "root.previous/upper/etc/edited").read_bytes(), b"my edited setting\n")
        self.assertFalse((self.store / "root.next").exists())

    def test_failed_archive_producer_or_extractor_never_replaces_active_tree(self):
        upper = self.saved_tree()
        inode = (upper / "etc/edited").stat().st_ino
        self.change_version()
        for operation in ("--create", "--extract"):
            with self.subTest(operation=operation):
                result = self.shell('''
tar() {
  if [ "$1" = 'OPERATION' ]; then
    if [ "$1" = --extract ]; then cat >/dev/null; fi
    return 7
  fi
  command tar "$@"
}
rw_layer
'''.replace("OPERATION", operation))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((upper / "etc/edited").stat().st_ino, inode)
                self.assertEqual((upper / "etc/edited").read_bytes(), b"my edited setting\n")
                self.assertEqual((upper.parent / "version").read_text(), self.version + "\n")
                self.assertFalse((self.store / "root.previous").exists())
                self.assertFalse((self.runtime / "import").exists())
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((upper / "etc/edited").read_bytes(), b"my edited setting\n")

    def test_symlinked_store_or_internal_directories_never_modify_link_target(self):
        other = self.root / "outside"
        other.mkdir()
        sentinel = other / "keep"
        sentinel.write_text("preserve\n")
        for name in ("store", "root", "upper", "work", "root.next", "root.previous"):
            with self.subTest(name=name):
                # Each case gets an isolated backing directory but shares the
                # outside sentinel, which must retain its contents and modes.
                base = self.root / ("case-" + name.replace(".", "-"))
                if name == "store":
                    base.symlink_to(other, target_is_directory=True)
                else:
                    base.mkdir()
                    if name in ("upper", "work"):
                        (base / "root").mkdir()
                        (base / "root" / name).symlink_to(other, target_is_directory=True)
                    else:
                        (base / name).symlink_to(other, target_is_directory=True)
                    if name == "root.next":
                        (base / "root/upper").mkdir(parents=True)
                        (base / "root/work").mkdir()
                        (base / "root/version").write_text("b" * 64 + "\n")
                result = self.shell(f'RW_STORE="{base}"\nrw_layer')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(sentinel.read_text(), "preserve\n")
                self.assertEqual(sorted(p.name for p in other.iterdir()), ["keep"])

    def test_invalid_release_key_is_rejected_before_creating_storage(self):
        for version in ("../old-release", "a" * 63, "g" * 64, ""):
            with self.subTest(version=version):
                self.change_version(version)
                result = self.shell("rw_layer")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.store.exists())


class FailureSafetyTests(RuntimeFixture):
    def test_rollback_restores_mounts_before_reverting_root_and_unmounting_overlay(self):
        result = self.shell(r'''
RW_STOCK=/run/flex-debug/stock
RW_PIVOTED=1
RW_MOVED='/tmp /var /mnt/stateful_partition'
mountpoint() { return 0; }
mkdir() { :; }
mount() { printf 'mount'; printf ' %s' "$@"; printf '\n'; }
umount() { printf 'umount'; printf ' %s' "$@"; printf '\n'; }
pivot_root() { printf 'pivot'; printf ' %s' "$@"; printf '\n'; }
rw_rollback || exit 1
printf 'pivoted=%s\n' "$RW_PIVOTED"
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[:4], [
            "mount --move /run/flex-debug/stock /.flex-debug-stock",
            "mount --move /tmp /.flex-debug-stock/tmp",
            "mount --move /var /.flex-debug-stock/var",
            "mount --move /mnt/stateful_partition /.flex-debug-stock/mnt/stateful_partition"])
        pivot = next(i for i, line in enumerate(lines) if line.startswith("pivot "))
        overlay_unmount = lines.index(f"umount {self.runtime}/newroot")
        self.assertLess(pivot, overlay_unmount)
        self.assertEqual(lines[-1], "pivoted=0")

    def test_failed_overlay_with_successful_rollback_reloads_stock_jobs(self):
        result = self.shell('''
rw_apply() { return 1; }
rw_rollback() { printf 'rollback\\n'; return 0; }
initctl() { printf 'initctl'; printf ' %s' "$@"; printf '\\n'; }
rw_apply_or_continue
''')
        self.assertEqual(result.returncode, 0)
        self.assertIn("restoring stock mounts", result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["rollback", "initctl reload-configuration"])

    def test_failed_rollback_enters_recovery_without_reloading_or_reporting_success(self):
        result = self.shell(r'''
rw_apply() {
  RW_PIVOTED=1
  RW_MOVED='/tmp /var /mnt/stateful_partition /run /proc'
  return 1
}
mountpoint() { [ "$2" = "$RW_STOCK" ]; }
mkdir() { :; }
mount() {
  printf 'mount'; printf ' %s' "$@"; printf '\n'
  [ "$2" != /var ]
}
umount() { printf 'unexpected-unmount\n'; return 1; }
pivot_root() { printf 'unexpected-pivot\n'; return 1; }
rw_recovery() { printf 'recovery pivoted=%s\n' "$RW_PIVOTED"; return 77; }
initctl() { printf 'unsafe-reload\n'; }
rw_apply_or_continue
''')
        self.assertEqual(result.returncode, 77)
        self.assertEqual(result.stdout.splitlines(), [
            "mount --move /run/flex-debug/stock /.flex-debug-stock",
            "mount --move /tmp /.flex-debug-stock/tmp",
            "mount --move /var /.flex-debug-stock/var",
            "recovery pivoted=1"])

    def test_recovery_reports_stock_boot_and_waits_without_creating_missing_console(self):
        console = self.root / "missing-console"
        result = self.shell(r'''
sleep() { printf 'wait %s\n' "$1"; exit 77; }
rw_recovery
''', source=self.source.replace("/dev/console", str(console)))
        self.assertEqual(result.returncode, 77)
        self.assertEqual(result.stdout.splitlines(), ["wait 60"])
        self.assertIn("FATAL", result.stderr)
        self.assertIn("original ChromeOS Flex boot entry", result.stderr)
        self.assertFalse(console.exists())

    def test_shell_syntax(self):
        subprocess.run(["/bin/sh", "-n", SCRIPT], check=True)


class SupportGuardTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        upper = self.create_layer()
        (upper / "keep").write_text("saved settings must survive rejection\n")
        self.saved_inode = (upper / "keep").stat().st_ino
        self.original = self.root / "stock/init"
        self.original.mkdir(parents=True)
        self.startup = self.original / "startup.conf"
        self.startup.write_text("exec /bin/true\n")
        self.release = self.root / "lsb-release"
        self.release.write_text("CHROMEOS_RELEASE_VERSION=16836.32.0\n")
        self.cmdline = self.root / "cmdline"
        self.cmdline.write_text("cros_debug root_hexdigest=" + "a" * 64 + "\n")
        self.contract = (
            "# validated fixture\nFLEX_OS_VERSION=16836.32.0\n"
            "FLEX_ROOT_SHA256=" + "a" * 64 + "\n"
            "FLEX_KERNEL_RELEASE=6.12.100-test\nFLEX_STARTUP_SHA256="
            + hashlib.sha256(self.startup.read_bytes()).hexdigest() + "\n")
        self.write_contract(self.contract)
        (self.root / "bootstrap.sh").write_text("# trusted bootstrap fixture\n")
        (self.root / "rootdev-compat.so").write_bytes(b"trusted shim fixture")
        self.source = self.source.replace("/etc/lsb-release", str(self.release))
        self.source = self.source.replace("/proc/cmdline", str(self.cmdline))
        self.source = self.source.replace("/etc/init", str(self.original))
        self.guard_harness = '''
uname() { printf '6.12.100-test\\n'; }
umount() { printf 'stock-startup-restored\\n'; }
mountpoint() { [ "$2" = /mnt/stateful_partition ]; }
rw_layer() { printf 'layer-called\\n'; return 1; }
rw_mount_list() { printf 'unsafe-mount-list\\n'; return 1; }
rw_compat() { printf 'unsafe-compat\\n'; return 1; }
pivot_root() { printf 'unsafe-pivot\\n'; return 1; }
initctl() { printf 'stock-jobs-reloaded\\n'; }
'''

    def write_contract(self, text):
        (self.root / "compatibility.conf").write_text(text)
        (self.runtime / "compatibility.conf").write_text(text)

    def assert_saved_untouched(self):
        layer = self.store / "root"
        self.assertEqual((layer / "upper/keep").read_text(), "saved settings must survive rejection\n")
        self.assertEqual((layer / "upper/keep").stat().st_ino, self.saved_inode)
        self.assertEqual((layer / "version").read_text(), self.version + "\n")
        self.assertEqual(sorted(p.name for p in self.store.iterdir()), ["root"])
        self.assertFalse((self.runtime / "import").exists())

    def assert_rejected(self):
        result = self.shell(self.guard_harness + "rw_apply_or_continue\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "stock-startup-restored", "stock-jobs-reloaded"])
        self.assertIn("saved OS changes will not be applied", result.stderr)
        self.assert_saved_untouched()

    def test_supported_original_os_reaches_layer_preparation(self):
        result = self.shell(self.guard_harness + "rw_apply\n")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.splitlines(), ["stock-startup-restored", "layer-called"])
        self.assertNotIn("compatibility contract", result.stderr)
        self.assert_saved_untouched()

    def test_unsupported_os_root_kernel_and_startup_preserve_saved_changes(self):
        for key, value in (("FLEX_OS_VERSION", "16837.0.0"),
                           ("FLEX_ROOT_SHA256", "b" * 64),
                           ("FLEX_KERNEL_RELEASE", "6.12.101-other"),
                           ("FLEX_STARTUP_SHA256", "c" * 64)):
            with self.subTest(key=key):
                text = "\n".join(key + "=" + value if line.startswith(key + "=") else line
                                 for line in self.contract.splitlines()) + "\n"
                self.write_contract(text)
                self.assert_rejected()

    def test_missing_malformed_duplicate_and_executable_contracts_are_rejected(self):
        invalid = ["", self.contract.replace("FLEX_OS_VERSION=16836.32.0\n", ""),
                   self.contract + "UNKNOWN=1\n", self.contract + "FLEX_OS_VERSION=16836.32.0\n",
                   self.contract.replace("a" * 64, "a" * 63),
                   self.contract.replace("16836.32.0", "16836.32.0 # comment"),
                   self.contract + " \n", self.contract + " # indented comment\n",
                   self.contract.replace("6.12.100-test", ".6.12.100-test"),
                   self.contract.replace("6.12.100-test", "-6.12.100-test"),
                   self.contract.replace("16836.32.0", "16836.\0" + "32.0"),
                   self.contract.replace("FLEX_OS_VERSION", "FLEX_\0OS_VERSION"),
                   self.contract.replace("# validated", "# validated\0"),
                   self.contract + 'touch "$FLEX_TEST_ROOT/executed"\n',
                   self.contract.replace("6.12.100-test", '$(touch "$FLEX_TEST_ROOT/executed")')]
        for text in invalid:
            with self.subTest(text=text):
                self.write_contract(text)
                self.assert_rejected()
                self.assertFalse((self.root / "executed").exists())
        (self.runtime / "compatibility.conf").unlink()
        self.assert_rejected()

    def test_missing_or_duplicate_actual_root_and_changed_original_job_are_rejected(self):
        for cmdline in ("cros_debug\n", "root_hexdigest=" + "a" * 64
                        + " root_hexdigest=" + "a" * 64 + "\n"):
            with self.subTest(cmdline=cmdline):
                self.cmdline.write_text(cmdline)
                self.assert_rejected()
        self.cmdline.write_text("root_hexdigest=" + "a" * 64 + "\n")
        self.startup.write_text("exec /bin/false\n")
        self.assert_rejected()

    def test_apply_checks_original_startup_after_unmount(self):
        original = self.startup.read_text()
        self.startup.write_text("# temporary wrapped job\n")
        (self.root / "original.conf").write_text(original)
        result = self.shell(self.guard_harness + '''
umount() { command cp "$FLEX_TEST_ROOT/original.conf" "''' + str(self.startup) + '''"; }
rw_apply
''')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.splitlines(), ["layer-called"])
        self.assert_saved_untouched()

    def test_prepare_rejects_before_wrapping_startup_or_writing_saved_layers(self):
        self.write_contract(self.contract.replace("16836.32.0", "16837.0.0"))
        shutil.rmtree(self.runtime)
        result = self.shell('''
FLEX_DEBUG_INIT_SOURCE="$FLEX_TEST_ROOT/bootstrap.sh"
uname() { printf '6.12.100-test\\n'; }
mountpoint() { return 0; }
mount() { if [ "$1" = --bind ]; then printf 'unsafe-bind\\n'; fi; }
rw_prepare
''')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.runtime / "init").exists())
        self.assertFalse((self.runtime / "version").exists())
        self.assert_saved_untouched()

    def test_supported_prepare_copies_contract_and_wraps_original_job(self):
        shutil.rmtree(self.runtime)
        result = self.shell('''
FLEX_DEBUG_INIT_SOURCE="$FLEX_TEST_ROOT/bootstrap.sh"
uname() { printf '6.12.100-test\\n'; }
mountpoint() { return 0; }
mount() { :; }
rw_prepare
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.runtime / "compatibility.conf").read_text(), self.contract)
        self.assertIn("--apply", (self.runtime / "init/startup.conf").read_text())
        self.assertIn("compatibility.conf", (self.runtime / "inputs").read_text())
        self.assert_saved_untouched()


class CompatibilityTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        self.upper = self.create_layer()
        self.stock = self.root / "stock"
        libraries = self.stock / "usr/lib64"
        policies = self.stock / "usr/share/minijail"
        libraries.mkdir(parents=True)
        policies.mkdir(parents=True)
        (libraries / "librootdev.so.1.0").write_bytes(b"stock rootdev library")
        (libraries / "librootdev.so.1").symlink_to("librootdev.so.1.0")
        (libraries / "librootdev.so.1.0").chmod(0o755)
        (self.runtime / "rootdev-compat.so").write_bytes(b"compatibility shim")
        (self.runtime / "rootdev-compat.so").chmod(0o755)
        self.policies = {
            "whole-root.conf": "mountns\nbind-mount = /,,0\n",
            "minimal.conf": "  profile = minimalistic-mountns\n",
            "ordinary.conf": "mountns\nbind-mount = /usr/share/example\n",
            "without-ns.conf": "user = nobody\n",
        }
        for name, contents in self.policies.items():
            path = policies / name
            path.write_text(contents)
            path.chmod(0o644)
        self.compat_source = self.source.replace(
            'for policy in /usr/share/minijail/*.conf; do',
            'for input in "$FLEX_TEST_ROOT/stock"/usr/share/minijail/*.conf; do\n'
            '    policy=${input#"$FLEX_TEST_ROOT/stock"}')
        self.compat_source = self.compat_source.replace('[ -f "$policy" ]',
                                                        '[ -f "$FLEX_TEST_ROOT/stock$policy" ]')
        self.compat_body = r'''
layer="$RW_STORE/root"
readlink() {
  if [ "$2" = /usr/lib64/librootdev.so.1 ]; then printf '/usr/lib64/librootdev.so.1.0\n';
  else command readlink "$@"; fi
}
# Redirect only reads of original stock files. Actual copies, labels/modes and
# cache data stay under this fixture; tool-owned output paths remain canonical.
cp() {
  option=$1
  shift
  case "$1" in /usr/*) input="$FLEX_TEST_ROOT/stock$1" ;; *) input=$1 ;; esac
  command cp "$option" "$input" "$2"
}
awk() {
  case "$2" in /usr/*) command awk "$1" "$FLEX_TEST_ROOT/stock$2" ;; *) command awk "$@" ;; esac
}
stat() {
  if [ "$2" = %u ]; then printf '0\n'; return; fi
  case "$3" in /usr|/usr/*) command stat "$1" "$2" "$FLEX_TEST_ROOT/stock$3" ;;
    *) command stat "$@" ;; esac
}
chcon() { printf '%s\n' "$*" >> "$FLEX_TEST_ROOT/labels"; }
rw_compat
'''

    def compat(self):
        return self.shell(self.compat_body, source=self.compat_source)

    def test_generic_root_namespace_policies_only_receive_verified_file_binds(self):
        (self.root / "selinux-enforce").write_text("1\n")
        result = self.compat()
        self.assertEqual(result.returncode, 0, result.stderr)
        compat = self.upper.parent / "compat"
        self.assertEqual((compat / "usr/lib64/librootdev.so.1.0").read_bytes(), b"compatibility shim")
        self.assertEqual((compat / "usr/lib64/flex-debug/librootdev-stock.so.1").read_bytes(), b"stock rootdev library")
        self.assertEqual((compat / "usr/lib64/flex-debug/root-device").read_text(), str(Path("/").stat().st_dev) + "\n")
        self.assertEqual(stat.S_IMODE((compat / "usr/lib64/flex-debug/root-device").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((compat / "usr/lib64/librootdev.so.1.0").stat().st_mode), 0o755)
        for name in ("whole-root.conf", "minimal.conf"):
            text = (compat / "usr/share/minijail" / name).read_text()
            self.assertTrue(text.startswith(self.policies[name]))
            self.assertIn("bind-mount = /lib/modules\n", text)
            self.assertIn("bind-mount = /lib/firmware\n", text)
            self.assertNotIn("recursive", text)
            self.assertEqual((self.stock / "usr/share/minijail" / name).read_text(), self.policies[name])
        for name in ("ordinary.conf", "without-ns.conf"):
            self.assertFalse((compat / "usr/share/minijail" / name).exists())
        self.assertEqual((self.root / "selinux-enforce").read_text(), "1\n")
        labels = (self.root / "labels").read_text()
        self.assertIn("--reference=/usr/lib64", labels)
        self.assertIn("--reference=/usr/lib64/librootdev.so.1.0", labels)
        self.assertTrue((self.upper.parent / "compat.ready").is_file())

    def test_completed_lower_cache_keeps_inodes_until_fingerprint_changes(self):
        result = self.compat()
        self.assertEqual(result.returncode, 0, result.stderr)
        library = self.upper.parent / "compat/usr/lib64/librootdev.so.1.0"
        inode = library.stat().st_ino
        (self.runtime / "rootdev-compat.so").write_bytes(b"updated shim")
        result = self.compat()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(library.stat().st_ino, inode)
        self.assertEqual(library.read_bytes(), b"compatibility shim")
        # rw_prepare changes the fingerprint when the shim changes. rw_layer
        # imports only user data, then compatibility is built for the new lower.
        self.change_version()
        result = self.shell("rw_layer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.upper.parent / "compat.ready").exists())
        result = self.compat()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(library.read_bytes(), b"updated shim")
        self.assertNotEqual(library.stat().st_ino, inode)

    def test_incomplete_lower_cache_is_rebuilt(self):
        compat = self.upper.parent / "compat"
        compat.mkdir()
        (compat / "incomplete").write_text("stale")
        result = self.compat()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((compat / "incomplete").exists())
        self.assertEqual((compat / "usr/lib64/librootdev.so.1.0").read_bytes(), b"compatibility shim")


class FingerprintTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        self.original = self.root / "init-source"
        self.original.mkdir()
        (self.original / "startup.conf").write_text("exec /bin/true\n")
        (self.root / "bootstrap.sh").write_text("# bootstrap v1\n")
        (self.root / "rootdev-compat.so").write_bytes(b"shim v1")
        (self.root / "compatibility.conf").write_text("# fingerprint fixture v1\n")
        self.prepare_source = self.source.replace("/proc/cmdline", str(self.root / "cmdline"))
        self.prepare_source = self.prepare_source.replace("/etc/init", str(self.original))

    def prepare(self, *, flags="cros_debug", digest="a" * 64, device=42):
        shutil.rmtree(self.runtime)
        (self.root / "cmdline").write_text(flags + " root_hexdigest=" + digest + " other=value\n")
        result = self.shell('''
FLEX_DEBUG_INIT_SOURCE="$FLEX_TEST_ROOT/bootstrap.sh"
mountpoint() { return 0; }
mount() { return 0; }
rw_supported() { return 0; }
stat() { if [ "$2" = %d ]; then printf 'DEVICE\\n'; else command stat "$@"; fi; }
rw_prepare
'''.replace("DEVICE", str(device)), source=self.prepare_source)
        version = self.runtime / "version"
        return result, version.read_text() if version.exists() else None

    def test_fingerprint_is_stable_across_boot_mode_and_sensitive_to_each_lower_input(self):
        result, baseline = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(baseline, r"^[0-9a-f]{64}\n$")
        result, configured = self.prepare(flags="cros_nodebug")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(configured, baseline)
        for arguments in ({"digest": "b" * 64}, {"device": 43}):
            with self.subTest(arguments=arguments):
                result, changed = self.prepare(**arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotEqual(changed, baseline)
        (self.root / "bootstrap.sh").write_text("# bootstrap v2\n")
        result, changed_script = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(changed_script, baseline)
        (self.root / "rootdev-compat.so").write_bytes(b"shim v2")
        result, changed_shim = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(changed_shim, changed_script)
        (self.root / "compatibility.conf").write_text("# fingerprint fixture v2\n")
        result, changed_contract = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(changed_contract, changed_shim)

    def test_missing_or_invalid_verified_root_hash_prevents_overlay_bootstrap(self):
        for digest in ("", "x" * 64, "a" * 63):
            with self.subTest(digest=digest):
                result, version = self.prepare(digest=digest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(version)
                self.assertFalse((self.runtime / "init/startup.conf").exists())


class StartupWrapperTests(unittest.TestCase):
    def test_prepare_initializes_closed_standard_descriptors_and_appends_boot_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "init-source"
            original.mkdir()
            (original / "startup.conf").write_text("exec /bin/true\n")
            (root / "cmdline").write_text("cros_debug root_hexdigest=" + "a" * 64 + "\n")
            (root / "bootstrap.sh").write_text("# trusted bootstrap\n")
            (root / "rootdev-compat.so").write_bytes(b"trusted shim fixture")
            (root / "compatibility.conf").write_text("# trusted contract fixture\n")
            source = SOURCE.replace("/proc/cmdline", str(root / "cmdline"))
            source = source.replace("/etc/init", str(original))
            # Only mounts are simulated. Script copying, release hashing,
            # startup wrapping and stdio setup execute under the real dash.
            harness = r'''
RW_RUNTIME="$FLEX_TEST_ROOT/runtime"
FLEX_DEBUG_INIT_SOURCE="$FLEX_TEST_ROOT/bootstrap.sh"
mountpoint() { return 0; }
rw_supported() { return 0; }
mount() {
  if [ "$1" = -t ]; then
    printf 'earlier-log\n' > "$RW_RUNTIME/boot.log"
  else
    printf 'bound\n' > "$FLEX_TEST_ROOT/bind-status"
  fi
}
exec 0<&- 1>&- 2>&-
rw_prepare || exit 1
printf 'stdout-restored\n'
printf 'stderr-restored\n' >&2
if read -r value; then exit 1; fi
printf 'stdin-at-eof\n'
'''
            subprocess.run(["/bin/sh", "-c", source + harness],
                           env=dict(os.environ, FLEX_TEST_ROOT=str(root)), check=True)
            runtime = root / "runtime"
            self.assertEqual((runtime / "boot.log").read_text().splitlines(), [
                "earlier-log", "stdout-restored", "stderr-restored", "stdin-at-eof"])
            self.assertEqual((runtime / "rw-init.sh").read_text(), "# trusted bootstrap\n")
            self.assertEqual((runtime / "rootdev-compat.so").read_bytes(), b"trusted shim fixture")
            self.assertRegex((runtime / "version").read_text(), r"^[0-9a-f]{64}\n$")
            self.assertIn("--apply", (runtime / "init/startup.conf").read_text())
            self.assertEqual((root / "bind-status").read_text(), "bound\n")

    def test_esp_is_detached_while_shell_still_has_source_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "sourced.sh"
            # Replace only the final init handoff; preparation and mount
            # commands are fakes, so this test cannot mount or unmount paths.
            source.write_text(SOURCE.replace("  exec /sbin/init", "  printf 'stock-init\\n'") + r'''
rw_prepare() { return 0; }
mountpoint() { [ "$2" = /tmp/flex-debug-esp ]; }
umount() {
  for fd in /proc/$$/fd/*; do
    if [ "$(readlink "$fd")" = "$FLEX_TEST_SOURCE" ]; then
      printf 'source-fd-open\n'
      break
    fi
  done
  printf 'umount'; printf ' %s' "$@"; printf '\n'
}
rw_main
''')
            result = subprocess.run(["/bin/sh", "-c", '. "$FLEX_TEST_SOURCE"'],
                                    env=dict(os.environ, FLEX_TEST_SOURCE=str(source)),
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.splitlines(), [
                "source-fd-open", "umount -l /tmp/flex-debug-esp", "stock-init"])

    def test_prepare_mounts_use_stock_permissions_and_security_flags(self):
        for filesystem, destination, options in (
            ("proc", "/proc", "nosuid,nodev,noexec"),
            ("sysfs", "/sys", "nosuid,nodev,noexec"),
            ("devtmpfs", "/dev", "nosuid,noexec"),
            ("tmpfs", "/tmp", "nosuid,nodev,noexec,nosymfollow,mode=1777"),
            ("tmpfs", "/tmp/flex-debug", "nosuid,nodev,exec,mode=0700"),
        ):
            harness = r'''
mountpoint() { [ "$2" != 'DESTINATION' ]; }
# Fail after recording arguments, before preparation can write any paths.
mount() { printf '%s\n' "$@"; return 1; }
mkdir() { :; }
rw_prepare
'''.replace("DESTINATION", destination)
            with self.subTest(destination=destination):
                result = subprocess.run(["/bin/sh", "-c", SOURCE + harness],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout.splitlines(), [
                    "-t", filesystem, "-o", options, filesystem, destination])

    def wrap(self, text):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source, target = root / "input", root / "output"
        source.write_text(text)
        result = subprocess.run(["/bin/sh", "-c", SOURCE + '\nrw_wrap_startup "$1" "$2"',
                                 "test", str(source), str(target)], capture_output=True, text=True)
        return result, target.read_text()

    def test_preserves_startup_exit_status_and_only_applies_after_success(self):
        # Upstart runs script stanzas under /bin/sh -e. Explicit conditional
        # execution must preserve the original status with or without errexit.
        for flags in ([], ["-e"]):
            for status in (0, 7):
                with self.subTest(flags=flags, status=status):
                    result, wrapped = self.wrap(f'description "startup"\nexec sh -c "exit {status}"\n')
                    self.assertEqual(result.returncode, 0)
                    body = wrapped.split("script\n", 1)[1].rsplit("end script", 1)[0]
                    body = body.replace('/bin/sh /tmp/flex-debug/rw-init.sh --apply >> /tmp/flex-debug/boot.log 2>&1',
                                        "printf overlay-applied")
                    run = subprocess.run(["/bin/sh", *flags, "-c", body], capture_output=True, text=True)
                    self.assertEqual(run.returncode, status)
                    self.assertEqual(run.stdout, "overlay-applied" if status == 0 else "")

    def test_refuses_unsupported_missing_or_multiple_exec_stanzas(self):
        for text in ("script\n true\nend script\n", "exec true\nexec false\n"):
            result, _ = self.wrap(text)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
