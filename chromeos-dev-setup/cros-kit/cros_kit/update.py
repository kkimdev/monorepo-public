#!/usr/bin/env python3
"""Refresh and validate CrOS Kit's locked inputs in a disposable source copy."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from . import make_usb
from . import usb_live


PUBLISHED = ("media/media.json", "media/alpine-index.tar.gz", "flake.lock")


FLEX_METADATA = "https://dl.google.com/dl/edgedl/chromeos/recovery/cloudready_recovery2.json"
ALPINE = "https://dl-cdn.alpinelinux.org/alpine"
RELEASES = f"{ALPINE}/latest-stable/releases/x86_64/latest-releases.yaml"


def download(url, path, digest, algorithm="sha256", size=None):
    if path.exists() and (size is None or path.stat().st_size == size) and make_usb.file_hash(path, algorithm) == digest:
        print(f"Using verified cache: {path.name}", flush=True)
        return path
    temporary = path.with_name(path.name + ".part")
    received = 0
    checksum = hashlib.new(algorithm)
    started = last = time.monotonic()
    print(f"Downloading {path.name}", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
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
        temporary.unlink(missing_ok=True)
    return path


def latest_release(metadata=None):
    """Select official stable STANDARD x86_64 media, with its published digest."""
    if metadata is None:
        with urllib.request.urlopen(RELEASES, timeout=60) as response:
            metadata = response.read().decode("utf-8")
    # Only flat release fields are needed; descriptions are indented YAML text.
    matches = []
    for block in re.split(r"(?m)^-\s*$", metadata):
        fields = dict(re.findall(r"(?m)^  ([a-z0-9_]+): ([^\n]+)$", block))
        fields = {key: value.strip().strip('"') for key, value in fields.items()}
        if fields.get("flavor") == "alpine-standard" and fields.get("arch") == "x86_64":
            matches.append(fields)
    if len(matches) != 1:
        raise ValueError("Official Alpine metadata must contain one STANDARD x86_64 release")
    fields = matches[0]
    version = fields.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid Alpine stable version")
    branch = "v" + ".".join(version.split(".")[:2])
    filename = f"alpine-standard-{version}-x86_64.iso"
    digest = fields.get("sha256", "")
    if (fields.get("branch") != branch or fields.get("file") != filename
            or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("Invalid Alpine release filename, branch or SHA256")
    try:
        size = int(fields["size"])
    except (KeyError, ValueError) as error:
        raise ValueError("Invalid Alpine ISO size") from error
    if not 0 < size < 4 * 1024**3:
        raise ValueError("Invalid Alpine ISO size")
    return {"version": version, "branch": branch, "filename": filename,
            "url": f"{ALPINE}/{branch}/releases/x86_64/{filename}",
            "sha256": digest, "size": size}


def _cache_in_nix(path, name):
    # fetchurl uses the same flat SHA256 store identity: avoid downloading twice.
    usb_live.run(["nix", "store", "add-file", "--hash-algo", "sha256", "--name", name, path])


def resolve_media(directory, cache):
    """Snapshot official media and the signed Alpine dependency closure.

    Only the small signed index and content lock enter the source tree. Media
    downloads stay in cache; package archives stay in the disposable resolver.
    The existing OS compatibility contract is checked before large downloads.
    """
    directory, cache = Path(directory), Path(cache)
    with urllib.request.urlopen(FLEX_METADATA, timeout=60) as response:
        flex = make_usb.stable_release(json.load(response))
    make_usb.installer.require_supported_version(flex["version"], directory / "cros_kit")
    alpine = latest_release()
    cache.mkdir(parents=True, exist_ok=True)
    archive = download(flex["url"], cache / (flex["file"] + ".zip"),
                       flex["sha1"], "sha1", flex["zipfilesize"])
    flex = {key: flex[key] for key in ("channel", "model", "version", "chrome_version",
                                     "file", "filesize", "url", "sha1", "zipfilesize")}
    flex["sha256"] = make_usb.file_hash(archive, "sha256")
    _cache_in_nix(archive, flex["file"] + ".zip")
    iso = download(alpine["url"], cache / alpine["filename"],
                   alpine["sha256"], size=alpine["size"])
    _cache_in_nix(iso, alpine["filename"])
    with tempfile.TemporaryDirectory(prefix="cros-kit-resolve-") as temporary:
        work = Path(temporary)
        media = work / "media"
        _, packages, apk, repositories = usb_live.prepare_media(
            iso, media, work, expected_version=alpine["version"])
        apk_cache = work / "cache"
        repository = f"{ALPINE}/{alpine['branch']}/main"
        repositories.write_text(repository + "\n")
        usb_live.run([*apk, "update"])
        indexes = list(apk_cache.glob("APKINDEX.*.tar.gz"))
        if len(indexes) != 1:
            raise ValueError("Expected one signed Alpine APKINDEX snapshot")
        for package in packages.glob("*.apk"):
            package.unlink()
        usb_live.run([*apk, "fetch", "--recursive", "--output", packages, *usb_live.PACKAGES])
        shutil.copyfile(indexes[0], packages / "APKINDEX.tar.gz")
        repositories.write_text(str(media / "apks") + "\n")
        usb_live.run([*apk, "--no-network", "add", "--simulate", *usb_live.PACKAGES])
        verified = work / "verified"
        verified.mkdir()
        usb_live.run([*apk, "--no-network", "fetch", "--recursive", "--link",
                  "--output", verified, *usb_live.PACKAGES])
        if not list(verified.glob("*.apk")):
            raise ValueError("Offline Alpine tools package set is empty")
        if {p.name for p in packages.glob("*.apk")} != {p.name for p in verified.glob("*.apk")}:
            raise ValueError("Offline Alpine dependency closure differs from downloaded packages")
        alpine = dict(alpine, repository=repository,
                      index_sha256=make_usb.file_hash(indexes[0], "sha256"),
                      packages=[{"filename": path.name,
                                 "url": f"{repository}/x86_64/{path.name}",
                                 "sha256": make_usb.file_hash(path, "sha256"),
                                 "size": path.stat().st_size}
                                for path in sorted(packages.glob("*.apk"))])
        for package in sorted(packages.glob("*.apk")):
            _cache_in_nix(package, package.name)
        shutil.copyfile(indexes[0], directory / "media/alpine-index.tar.gz")
    lock = {"format": 1, "flex": flex, "alpine": alpine, "packages": list(usb_live.PACKAGES)}
    (directory / "media/media.json").write_text(json.dumps(lock, indent=2) + "\n")
    return lock


def _snapshot(directory):
    return {path.relative_to(directory): path.read_bytes()
            for path in directory.rglob("*")
            if not any(part in (".git", "__pycache__") or part.startswith("result")
                       for part in path.relative_to(directory).parts)
            and path.is_file()}


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
        shutil.copytree(directory, staged,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "result*"))
        if _snapshot(staged) != original:
            raise ValueError("CrOS Kit source changed while copying; retry the update")
        resolve_media(staged, cache)
        print("Updating all flake inputs, including firmware sources and toolchain definitions.", flush=True)
        _command(["nix", "flake", "update"], staged)
        _command(["nix", "build", "--no-link", ".#payload", ".#alpine-repo",
                  ".#flex-archive", ".#alpine-iso"], staged)
        _command(["nix", "flake", "check"], staged)
        print("Running the complete local suite; verbose output reports any capability-dependent skips. "
              "This command does not acquire root privileges.", flush=True)
        _command(["nix", "develop", "path:" + str(staged), "--command", "python3",
                  "-m", "unittest", "discover", "-s", str(staged / "tests"),
                  "-t", str(staged), "-p", "test_*.py", "-v"], staged)
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
