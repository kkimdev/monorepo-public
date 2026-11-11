"""Exercise update admission, signed media snapshots and publication boundaries."""

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cros_kit import update


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
        release = update.latest_release(metadata)
        self.assertEqual(release["filename"], "alpine-standard-3.24.4-x86_64.iso")
        self.assertEqual(release["url"], update.ALPINE + "/v3.24/releases/x86_64/" + release["filename"])
        self.assertEqual(release["sha256"], "a" * 64)

    def test_rejects_missing_or_duplicate_standard(self):
        for metadata in (release_block("virt"), release_block() * 2):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                update.latest_release(metadata)

    def test_rejects_invalid_digest_filename_branch_and_size(self):
        original = release_block()
        for before, after in (("a" * 64, "not-a-digest"),
                              ("file: alpine-standard", "file: ../alpine-standard"),
                              ("branch: v3.24", "branch: v3.23"),
                              ("size: 370147328", "size: -1")):
            with self.subTest(field=before), self.assertRaises(ValueError):
                update.latest_release(original.replace(before, after))


class DownloadTests(unittest.TestCase):
    def test_verified_cache_never_contacts_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "locked.zip"
            path.write_bytes(b"verified cache")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch("urllib.request.urlopen") as network:
                self.assertEqual(update.download("https://example.test/locked.zip", path, digest,
                                                 size=path.stat().st_size), path)
            network.assert_not_called()

    def test_changed_cache_is_replaced_only_after_download_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "locked.zip"
            path.write_bytes(b"changed cache")
            contents = b"verified download"
            with patch("urllib.request.urlopen", return_value=io.BytesIO(contents)) as network:
                update.download("https://example.test/locked.zip", path,
                                hashlib.sha256(contents).hexdigest(), size=len(contents))
            network.assert_called_once()
            self.assertEqual(path.read_bytes(), contents)
            self.assertFalse(path.with_name(path.name + ".part").exists())

    def test_invalid_download_preserves_existing_cache_and_removes_partial(self):
        contents = b"download"
        for digest, size in (("0" * 64, len(contents)),
                             (hashlib.sha256(contents).hexdigest(), len(contents) + 1)):
            with self.subTest(digest=digest, size=size), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "locked.zip"
                path.write_bytes(b"old cache")
                with patch("urllib.request.urlopen", return_value=io.BytesIO(contents)):
                    with self.assertRaisesRegex(ValueError, "checksum/size mismatch"):
                        update.download("https://example.test/locked.zip", path, digest, size=size)
                self.assertEqual(path.read_bytes(), b"old cache")
                self.assertFalse(path.with_name(path.name + ".part").exists())


class MediaResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "media").mkdir()
        self.flex = {"channel": "STABLE", "model": "ChromeOS Flex", "version": "1.2.3",
                     "chrome_version": "1.0", "file": "recovery.bin", "filesize": 100,
                     "url": "https://dl.google.com/recovery.bin.zip", "sha1": "a" * 40,
                     "zipfilesize": 5}
        self.alpine = {"version": "3.24.4", "branch": "v3.24", "filename": "alpine.iso",
                       "url": "https://dl-cdn.alpinelinux.org/alpine/v3.24/alpine.iso",
                       "sha256": "b" * 64, "size": 3}

    def test_unsupported_flex_preserves_lock_and_stops_before_downloads(self):
        lock = self.source / "media/media.json"
        lock.write_bytes(b"old lock")
        with patch.object(update.urllib.request, "urlopen", return_value=io.BytesIO(b"[]")), \
             patch.object(update.make_usb, "stable_release", return_value=self.flex), \
             patch.object(update.make_usb.installer, "require_supported_version",
                          side_effect=ValueError("unsupported")), \
             patch.object(update, "download") as download, \
             patch.object(update, "latest_release") as alpine:
            with self.assertRaisesRegex(ValueError, "unsupported"):
                update.resolve_media(self.source, self.root / "cache")
        download.assert_not_called()
        alpine.assert_not_called()
        self.assertEqual(lock.read_bytes(), b"old lock")
        self.assertFalse((self.source / "media/alpine-index.tar.gz").exists())

    def test_resolution_snapshots_verified_closure_and_exact_signed_index(self):
        archive, iso = self.root / "flex.zip", self.root / "alpine.iso"
        archive.write_bytes(b"flex archive")
        iso.write_bytes(b"iso")
        commands = []

        def apk(arguments):
            args = list(map(str, arguments))
            commands.append(args)
            if args[0] == "bsdtar":
                destination = Path(args[args.index("-C") + 1])
                if Path(args[2]) == iso:
                    files = {".alpine-release": b"alpine-standard-3.24.4",
                             "boot/vmlinuz-lts": b"kernel", "boot/initramfs-lts": b"init",
                             "boot/modloop-lts": b"modules", "apks/.boot_repository": b"",
                             "apks/x86_64/alpine-keys-1.apk": b"keys"}
                else:
                    files = {"etc/apk/keys/official.pub": b"official public key"}
                for name, contents in files.items():
                    path = destination / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(contents)
            elif "update" in args:
                cache = Path(args[args.index("--cache-dir") + 1])
                (cache / "APKINDEX.snapshot.tar.gz").write_bytes(b"signed index bytes")
            elif "fetch" in args:
                destination = Path(args[args.index("--output") + 1])
                for name, contents in (("a-1.apk", b"package a"), ("b-2.apk", b"package b")):
                    (destination / name).write_bytes(contents)
            return ""

        with patch.object(update.urllib.request, "urlopen", return_value=io.BytesIO(b"[]")), \
             patch.object(update.make_usb, "stable_release", return_value=self.flex), \
             patch.object(update.make_usb.installer, "require_supported_version"), \
             patch.object(update, "latest_release", return_value=self.alpine), \
             patch.object(update, "download", side_effect=[archive, iso]) as download, \
             patch.object(update.usb_live, "run", side_effect=apk):
            lock = update.resolve_media(self.source, self.root / "cache")
        self.assertEqual(download.call_args_list[0].args[2:], ("a" * 40, "sha1", 5))
        self.assertEqual(lock["flex"]["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual((self.source / "media/alpine-index.tar.gz").read_bytes(), b"signed index bytes")
        self.assertEqual(lock["alpine"]["index_sha256"], hashlib.sha256(b"signed index bytes").hexdigest())
        self.assertEqual([item["filename"] for item in lock["alpine"]["packages"]],
                         ["a-1.apk", "b-2.apk"])
        for package, contents in zip(lock["alpine"]["packages"], (b"package a", b"package b")):
            self.assertEqual(package["sha256"], hashlib.sha256(contents).hexdigest())
            self.assertEqual(package["size"], len(contents))
            self.assertTrue(package["url"].endswith("/x86_64/" + package["filename"]))
        self.assertEqual(json.loads((self.source / "media/media.json").read_text()), lock)
        self.assertTrue(any("--usermode" in args and "--initdb" in args for args in commands))
        offline = [args for args in commands if "--no-network" in args]
        self.assertTrue(any("--simulate" in args for args in offline))
        self.assertTrue(any("fetch" in args and "--link" in args for args in offline))
        self.assertFalse(any("--allow-untrusted" in args for args in commands))


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in (*update.PUBLISHED, "flake.nix", "nix/efi.nix",
                     "cros_kit/compatibility.conf", "cros_kit/rw-init.sh"):
            (self.source / name).parent.mkdir(parents=True, exist_ok=True)
            (self.source / name).write_bytes(("old " + name).encode())
        self.original = update._snapshot(self.source)
        self.commands = []

    def resolve(self, directory, cache):
        self.assertNotEqual(directory, self.source)
        (directory / "media/media.json").write_bytes(b"new media")
        (directory / "media/alpine-index.tar.gz").write_bytes(b"new index")

    def command(self, args, directory):
        self.commands.append((args, directory))
        self.assertNotEqual(directory, self.source)
        if args[1:3] == ["flake", "update"]:
            (directory / "flake.lock").write_bytes(b"new flake lock")

    def test_success_validates_staging_before_publishing_only_allowlist(self):
        with patch.object(update, "resolve_media", side_effect=self.resolve), \
             patch.object(update, "_command", side_effect=self.command):
            update.update(self.source, self.root / "cache")
        self.assertEqual((self.source / "media/media.json").read_bytes(), b"new media")
        self.assertEqual((self.source / "flake.lock").read_bytes(), b"new flake lock")
        self.assertEqual(set(update.PUBLISHED), {"media/media.json", "media/alpine-index.tar.gz", "flake.lock"})
        self.assertEqual(set(update._snapshot(self.source)), set(self.original))
        for name in ("flake.nix", "nix/efi.nix", "cros_kit/compatibility.conf", "cros_kit/rw-init.sh"):
            self.assertEqual((self.source / name).read_bytes(), self.original[Path(name)])
        args = [item[0] for item in self.commands]
        self.assertEqual(args[0], ["nix", "flake", "update"])
        self.assertEqual([arg for arg in args[1] if arg.startswith(".#")],
                         [".#payload", ".#alpine-repo", ".#flex-archive", ".#alpine-iso"])
        self.assertIn("--no-link", args[1])
        self.assertEqual(args[2], ["nix", "flake", "check"])
        self.assertEqual(args[3][:2], ["nix", "develop"])
        self.assertIn("unittest", args[3])
        self.assertEqual(Path(args[3][args[3].index("-s") + 1]), self.commands[3][1] / "tests")
        self.assertEqual(Path(args[3][args[3].index("-t") + 1]), self.commands[3][1])
        self.assertIn("-v", args[3])

    def test_validation_failure_preserves_entire_original_tree(self):
        for failure in ("update", "build", "check", "develop"):
            with self.subTest(failure=failure):
                def fail(args, directory):
                    if failure in args[1:3]:
                        raise subprocess.CalledProcessError(1, args)
                    return self.command(args, directory)
                with patch.object(update, "resolve_media", side_effect=self.resolve), \
                     patch.object(update, "_command", side_effect=fail):
                    with self.assertRaises(subprocess.CalledProcessError):
                        update.update(self.source, self.root / "cache")
                self.assertEqual(update._snapshot(self.source), self.original)

    def test_concurrent_source_edit_is_preserved_and_blocks_publication(self):
        def edit(args, directory):
            if args[1] == "develop":
                (self.source / "cros_kit/compatibility.conf").write_bytes(b"concurrent edit")
            return self.command(args, directory)
        with patch.object(update, "resolve_media", side_effect=self.resolve), \
             patch.object(update, "_command", side_effect=edit):
            with self.assertRaisesRegex(ValueError, "source changed"):
                update.update(self.source, self.root / "cache")
        expected = dict(self.original)
        expected[Path("cros_kit/compatibility.conf")] = b"concurrent edit"
        self.assertEqual(update._snapshot(self.source), expected)

    def test_publication_errors_and_interrupts_restore_originals(self):
        staged = self.root / "staged"
        shutil.copytree(self.source, staged)
        for name in update.PUBLISHED:
            (staged / name).write_bytes(b"new")
        replace = update.os.replace
        for exception in (OSError("fixture replace failure"), KeyboardInterrupt()):
            with self.subTest(exception=type(exception).__name__):
                calls = 0

                def fail(source, target):
                    nonlocal calls
                    calls += 1
                    if calls == 3:
                        raise exception
                    replace(source, target)

                with patch.object(update.os, "replace", side_effect=fail):
                    with self.assertRaises(type(exception)):
                        update._publish(self.source, staged, self.original)
                self.assertEqual(update._snapshot(self.source), self.original)


if __name__ == "__main__":
    unittest.main()
