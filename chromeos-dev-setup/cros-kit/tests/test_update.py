"""Exercise update admission, media locks and publication boundaries."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from cros_kit import update


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
            self.assertEqual(list(path.parent.iterdir()), [path])

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
                self.assertEqual(list(path.parent.iterdir()), [path])


    def test_overlapping_downloads_publish_independently_without_partial_files(self):
        contents = b"same verified download"
        barrier = threading.Barrier(2)

        class Response(io.BytesIO):
            def read(self, count=-1):
                data = super().read(count)
                if not data:
                    barrier.wait(timeout=5)
                return data

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "locked.zip"
            with patch("urllib.request.urlopen", side_effect=lambda *a, **k: Response(contents)):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    downloads = [pool.submit(update.download, "https://example.test/locked.zip", path,
                                             hashlib.sha256(contents).hexdigest(), size=len(contents))
                                 for _ in range(2)]
                    for future in downloads:
                        self.assertEqual(future.result(timeout=10), path)
            self.assertEqual(path.read_bytes(), contents)
            self.assertEqual(list(path.parent.iterdir()), [path])


class MediaResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        (self.source / "media").mkdir(parents=True)
        self.flex = {"channel": "STABLE", "model": "ChromeOS Flex", "version": "1.2.3",
                     "chrome_version": "1.0", "file": "recovery.bin", "filesize": 100,
                     "url": "https://dl.google.com/recovery.bin.zip", "sha1": "a" * 40,
                     "zipfilesize": 5}

    def test_unsupported_flex_preserves_lock_and_stops_before_downloads(self):
        lock = self.source / "media/media.json"
        lock.write_bytes(b"old lock")
        with patch.object(update.urllib.request, "urlopen", return_value=io.BytesIO(b"[]")), \
             patch.object(update.make_usb, "stable_release", return_value=self.flex), \
             patch.object(update.make_usb.installer, "require_supported_version",
                          side_effect=ValueError("unsupported")), \
             patch.object(update, "download") as download:
            with self.assertRaisesRegex(ValueError, "unsupported"):
                update.resolve_media(self.source, self.root / "cache")
        download.assert_not_called()
        self.assertEqual(lock.read_bytes(), b"old lock")

    def test_resolution_locks_flex_bytes_and_populates_nix_cache(self):
        archive = self.root / "flex.zip"
        archive.write_bytes(b"flex archive")
        with patch.object(update.urllib.request, "urlopen", return_value=io.BytesIO(b"[]")), \
             patch.object(update.make_usb, "stable_release", return_value=self.flex), \
             patch.object(update.make_usb.installer, "require_supported_version"), \
             patch.object(update, "download", return_value=archive) as download, \
             patch.object(update, "_cache_in_nix") as cache:
            lock = update.resolve_media(self.source, self.root / "cache")
        self.assertEqual(download.call_args.args[2:], ("a" * 40, "sha1", 5))
        self.assertEqual(lock["flex"]["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(set(lock), {"format", "flex"})
        self.assertEqual(lock["format"], 2)
        cache.assert_called_once_with(archive, "recovery.bin.zip")
        self.assertEqual(json.loads((self.source / "media/media.json").read_text()), lock)


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
        self.assertEqual(set(update.PUBLISHED), {"media/media.json", "flake.lock"})
        self.assertEqual(set(update._snapshot(self.source)), set(self.original))
        for name in ("flake.nix", "nix/efi.nix", "cros_kit/compatibility.conf", "cros_kit/rw-init.sh"):
            self.assertEqual((self.source / name).read_bytes(), self.original[Path(name)])
        args = [item[0] for item in self.commands]
        self.assertEqual(args[0], ["nix", "flake", "update"])
        self.assertEqual([arg for arg in args[1] if arg.startswith(".#")],
                         [".#live", ".#flex-archive"])
        self.assertIn("--no-link", args[1])
        self.assertEqual(args[2], ["nix", "flake", "check", "--print-build-logs"])
        self.assertEqual(len(args), 3)

    def test_generated_images_and_archives_are_neither_read_nor_copied(self):
        # Small stand-ins for multi-gigabyte exports, including artifacts nested
        # inside source directories. Never read their bytes.
        artifacts = [self.source / name for name in (
            "preview.img", "media/preview.iso", "media/recovery.bin.zip",
            "nix/generated.img", "cache/nested/guest.qcow2", "result-local/large-data",
        )]
        for path in artifacts:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"local generated data")
        read_bytes = Path.read_bytes

        def read_source(path):
            if path in artifacts:
                raise AssertionError(f"Generated artifact entered snapshot: {path}")
            return read_bytes(path)

        def resolve(directory, cache):
            staged_files = {path.relative_to(directory) for path in directory.rglob("*") if path.is_file()}
            self.assertEqual(staged_files, set(self.original))
            self.resolve(directory, cache)

        def command(args, directory):
            # An unrelated export can change while the updater validates source.
            artifacts[0].write_bytes(b"new local export")
            self.command(args, directory)

        with (patch.object(Path, "read_bytes", new=read_source),
              patch.object(update, "resolve_media", side_effect=resolve),
              patch.object(update, "_command", side_effect=command)):
            update.update(self.source, self.root / "cache")
        self.assertEqual(artifacts[0].read_bytes(), b"new local export")
        for path in artifacts[1:]:
            self.assertEqual(path.read_bytes(), b"local generated data")
        self.assertEqual((self.source / "flake.lock").read_bytes(), b"new flake lock")

    def test_validation_failure_preserves_entire_original_tree(self):
        for failure in ("update", "build", "check"):
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
            if "check" in args:
                (self.source / "cros_kit/compatibility.conf").write_bytes(b"concurrent edit")
            return self.command(args, directory)
        with patch.object(update, "resolve_media", side_effect=self.resolve), \
             patch.object(update, "_command", side_effect=edit):
            with self.assertRaisesRegex(ValueError, "source changed"):
                update.update(self.source, self.root / "cache")
        expected = dict(self.original)
        expected[Path("cros_kit/compatibility.conf")] = b"concurrent edit"
        self.assertEqual(update._snapshot(self.source), expected)

    def test_source_added_during_validation_is_preserved_and_blocks_publication(self):
        added = self.source / "new-directory/new-source.futuretype"

        def add(args, directory):
            if "check" in args:
                added.parent.mkdir(parents=True)
                added.write_bytes(b"new source")
            return self.command(args, directory)

        with (patch.object(update, "resolve_media", side_effect=self.resolve),
              patch.object(update, "_command", side_effect=add)):
            with self.assertRaisesRegex(ValueError, "source changed"):
                update.update(self.source, self.root / "cache")
        self.assertEqual(added.read_bytes(), b"new source")
        for name in update.PUBLISHED:
            self.assertEqual((self.source / name).read_bytes(), self.original[Path(name)])

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
                    if calls == 2:
                        raise exception
                    replace(source, target)

                with patch.object(update.os, "replace", side_effect=fail):
                    with self.assertRaises(type(exception)):
                        update._publish(self.source, staged, self.original)
                self.assertEqual(update._snapshot(self.source), self.original)


if __name__ == "__main__":
    unittest.main()
