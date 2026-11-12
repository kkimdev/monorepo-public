#!/usr/bin/env python3
"""Refresh and validate CrOS Kit's locked inputs in a disposable source copy."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from . import make_usb


PUBLISHED = ("media/media.json", "flake.lock")
# Keep generated disk images and downloaded archives out of both source-change
# detection and the temporary build tree. New source types need no allowlist.
GENERATED = (".git", "__pycache__", "result*", "*.img", "*.iso", "*.qcow2", "*.zip")


FLEX_METADATA = "https://dl.google.com/dl/edgedl/chromeos/recovery/cloudready_recovery2.json"


def download(url, path, digest, algorithm="sha256", size=None):
    if path.exists() and (size is None or path.stat().st_size == size) and make_usb.file_hash(path, algorithm) == digest:
        print(f"Using verified cache: {path.name}", flush=True)
        return path
    temporary = None
    received = 0
    checksum = hashlib.new(algorithm)
    started = last = time.monotonic()
    print(f"Downloading {path.name}", flush=True)
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".part",
                                         dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            with urllib.request.urlopen(url, timeout=60) as response:
                while data := response.read(make_usb.CHUNK):
                    received += len(data)
                    if size is not None and received > size:
                        raise ValueError("Download exceeds its published size")
                    checksum.update(data)
                    output.write(data)
                    now = time.monotonic()
                    if now - last >= 5:
                        print(f"  {received / make_usb.MIB:.0f} MiB, {now - started:.1f}s", flush=True)
                        last = now
        if (size is not None and received != size) or checksum.hexdigest() != digest:
            raise ValueError(f"Download checksum/size mismatch: {path.name}")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def _cache_in_nix(path, name):
    # fetchurl uses the same flat SHA256 store identity: avoid downloading twice.
    subprocess.run(["nix", "store", "add-file", "--hash-algo", "sha256",
                    "--name", name, str(path)], check=True, stdout=subprocess.DEVNULL)


def resolve_media(directory, cache):
    """Lock the official Flex archive after admitting its OS version."""
    directory, cache = Path(directory), Path(cache)
    with urllib.request.urlopen(FLEX_METADATA, timeout=60) as response:
        flex = make_usb.stable_release(json.load(response))
    make_usb.installer.require_supported_version(flex["version"], directory / "cros_kit")
    cache.mkdir(parents=True, exist_ok=True)
    archive = download(flex["url"], cache / (flex["file"] + ".zip"),
                       flex["sha1"], "sha1", flex["zipfilesize"])
    flex = {key: flex[key] for key in ("channel", "model", "version", "chrome_version",
                                     "file", "filesize", "url", "sha1", "zipfilesize")}
    flex["sha256"] = make_usb.file_hash(archive, "sha256")
    _cache_in_nix(archive, flex["file"] + ".zip")
    lock = {"format": 2, "flex": flex}
    (directory / "media/media.json").write_text(json.dumps(lock, indent=2) + "\n")
    return lock


def _snapshot(directory):
    snapshot = {}
    ignore = shutil.ignore_patterns(*GENERATED)
    for current, directories, files in os.walk(directory):
        excluded = ignore(current, directories + files)
        directories[:] = [name for name in directories if name not in excluded]
        for name in files:
            path = Path(current) / name
            if name not in excluded and path.is_file():
                snapshot[path.relative_to(directory)] = path.read_bytes()
    return snapshot


def _command(arguments, directory):
    print("+ " + " ".join(map(str, arguments)), flush=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([str(arg) for arg in arguments], cwd=directory, env=env, check=True)


def _publish(directory, staged, original):
    if _snapshot(directory) != original:
        raise ValueError("CrOS Kit source changed during update; refusing to publish staged results")
    # Prepare every replacement before changing any original file. Restore the
    # old bytes if a filesystem error interrupts the short replacement phase.
    with tempfile.TemporaryDirectory(prefix=".cros-kit-publish-", dir=directory) as temporary:
        pending = Path(temporary)
        for name in PUBLISHED:
            (pending / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staged / name, pending / name)
        changed = []
        try:
            for name in PUBLISHED:
                os.replace(pending / name, directory / name)
                changed.append(name)
        except BaseException:
            for name in reversed(changed):
                if Path(name) in original:
                    (directory / name).write_bytes(original[Path(name)])
                else:
                    (directory / name).unlink(missing_ok=True)
            raise


def update(directory, cache):
    directory = Path(directory).resolve()
    original = _snapshot(directory)
    with tempfile.TemporaryDirectory(prefix="cros-kit-update-") as temporary:
        staged = Path(temporary) / "source"
        shutil.copytree(directory, staged, ignore=shutil.ignore_patterns(*GENERATED))
        if _snapshot(staged) != original:
            raise ValueError("CrOS Kit source changed while copying; retry the update")
        resolve_media(staged, cache)
        print("Updating all flake inputs, including firmware sources and toolchain definitions.", flush=True)
        _command(["nix", "flake", "update"], staged)
        _command(["nix", "build", "--no-link", ".#live", ".#flex-archive"], staged)
        _command(["nix", "flake", "check", "--print-build-logs"], staged)
        _publish(directory, staged, original)
    print("Updated media and flake locks after building and validating the installer payload.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, help="CrOS Kit source directory")
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache/cros-kit")
    args = parser.parse_args()
    directory = args.directory or Path.cwd()
    if args.directory is None and not (directory / "flake.nix").is_file():
        directory = directory / "chromeos-dev-setup/cros-kit"
    if not (directory / "flake.nix").is_file():
        parser.error("Run from the repository root or CrOS Kit directory, or pass --directory")
    try:
        update(directory, args.cache_dir)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Update failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
