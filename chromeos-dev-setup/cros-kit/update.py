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
import urllib.request

import make_usb
import usb_live


PUBLISHED = ("media.json", "alpine-index.tar.gz", "flake.lock",
             "crdyboot-debug.efi", "crdyboot-debug.efi.sha256",
             "THIRD_PARTY_NOTICES.txt", "build-info.json",
             "rootdev-compat.so", "rootdev-compat.so.sha256", "rw-init.sh.sha256")


_apk_run = usb_live._run


def _cache_in_nix(path, name):
    # fetchurl uses the same flat SHA256 store identity: avoid downloading twice.
    _apk_run(["nix", "store", "add-file", "--hash-algo", "sha256", "--name", name, path])


def resolve_media(directory, cache):
    """Snapshot official media and the signed Alpine dependency closure.

    Only the small signed index and content lock enter the source tree. Media
    downloads stay in cache; package archives stay in the disposable resolver.
    The existing OS compatibility contract is checked before large downloads.
    """
    directory, cache = Path(directory), Path(cache)
    with urllib.request.urlopen(make_usb.FLEX_METADATA, timeout=60) as response:
        flex = make_usb.stable_release(json.load(response))
    make_usb.installer.require_supported_version(flex["version"], directory)
    alpine = usb_live.latest_release()
    cache.mkdir(parents=True, exist_ok=True)
    archive = make_usb.download(flex["url"], cache / (flex["file"] + ".zip"),
                                flex["sha1"], "sha1", flex["zipfilesize"])
    flex = {key: flex[key] for key in ("channel", "model", "version", "chrome_version",
                                     "file", "filesize", "url", "sha1", "zipfilesize")}
    flex["sha256"] = make_usb.file_hash(archive, "sha256")
    _cache_in_nix(archive, flex["file"] + ".zip")
    iso = make_usb.download(alpine["url"], cache / alpine["filename"],
                            alpine["sha256"], size=alpine["size"])
    _cache_in_nix(iso, alpine["filename"])
    with tempfile.TemporaryDirectory(prefix="cros-kit-resolve-") as temporary:
        work = Path(temporary)
        media = work / "media"
        media.mkdir()
        _apk_run(["bsdtar", "-xf", iso, "-C", media])
        media.chmod(0o755)
        for item in media.rglob("*"):
            if item.is_symlink() or not (item.is_file() or item.is_dir()):
                raise ValueError("Unexpected nonregular file in Alpine ISO")
            item.chmod(0o755 if item.is_dir() else 0o644)
        release = (media / ".alpine-release").read_text().strip()
        if not re.fullmatch(r"alpine-standard-" + re.escape(alpine["version"]) + r"(?: \d+)?", release):
            raise ValueError("Alpine ISO version differs from official release metadata")
        for name in ("boot/vmlinuz-lts", "boot/initramfs-lts", "boot/modloop-lts",
                     "apks/.boot_repository"):
            if not (media / name).is_file():
                raise ValueError(f"Missing Alpine boot file: {name}")
        packages = media / "apks/x86_64"
        keys_apk = list(packages.glob("alpine-keys-*.apk"))
        if len(keys_apk) != 1:
            raise ValueError("Expected one trusted alpine-keys package in the ISO")
        keys = work / "keys"
        keys.mkdir()
        _apk_run(["bsdtar", "-xf", keys_apk[0], "-C", keys])
        keys_dir = keys / "etc/apk/keys"
        if not list(keys_dir.glob("*.pub")):
            raise ValueError("Official Alpine ISO contains no package signing keys")
        root, apk_cache = work / "root", work / "cache"
        root.mkdir()
        apk_cache.mkdir()
        repositories = work / "repositories"
        repositories.write_text("")
        apk = ["apk", "--root", root, "--arch", "x86_64", "--keys-dir", keys_dir,
               "--cache-dir", apk_cache, "--repositories-file", repositories]
        _apk_run([*apk, "add", "--initdb", "--usermode", "--no-network"])
        repository = f"{usb_live.ALPINE}/{alpine['branch']}/main"
        repositories.write_text(repository + "\n")
        _apk_run([*apk, "update"])
        indexes = list(apk_cache.glob("APKINDEX.*.tar.gz"))
        if len(indexes) != 1:
            raise ValueError("Expected one signed Alpine APKINDEX snapshot")
        for package in packages.glob("*.apk"):
            package.unlink()
        _apk_run([*apk, "fetch", "--recursive", "--output", packages, *usb_live.PACKAGES])
        shutil.copyfile(indexes[0], packages / "APKINDEX.tar.gz")
        repositories.write_text(str(media / "apks") + "\n")
        _apk_run([*apk, "--no-network", "add", "--simulate", *usb_live.PACKAGES])
        verified = work / "verified"
        verified.mkdir()
        _apk_run([*apk, "--no-network", "fetch", "--recursive", "--link",
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
        shutil.copyfile(indexes[0], directory / "alpine-index.tar.gz")
    lock = {"format": 1, "flex": flex, "alpine": alpine, "packages": list(usb_live.PACKAGES)}
    (directory / "media.json").write_text(json.dumps(lock, indent=2) + "\n")
    return lock


def _snapshot(directory):
    return {path.relative_to(directory): path.read_bytes()
            for path in directory.rglob("*")
            if not any(part in (".git", "__pycache__") or part.startswith("result")
                       for part in path.relative_to(directory).parts)
            and path.is_file()}


def _command(arguments, directory, capture=False):
    print("+ " + " ".join(map(str, arguments)), flush=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([str(arg) for arg in arguments], cwd=directory,
                            env=env, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def _publish(directory, staged, original):
    if _snapshot(directory) != original:
        raise ValueError("CrOS Kit source changed during update; refusing to publish staged results")
    # Prepare every replacement before changing any original file. Restore the
    # old bytes if a filesystem error interrupts the short replacement phase.
    with tempfile.TemporaryDirectory(prefix=".cros-kit-publish-", dir=directory) as temporary:
        pending = Path(temporary)
        for name in PUBLISHED:
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
        runtime = staged / "rw-init.sh"
        (staged / "rw-init.sh.sha256").write_text(
            f"{make_usb.file_hash(runtime, 'sha256')}  {runtime.name}\n")
        print("Updating nixpkgs and rust-overlay; firmware sources and Cargo hashes stay explicitly pinned.",
              flush=True)
        _command(["nix", "flake", "update", "nixpkgs", "rust-overlay"], staged)
        outputs = _command(["nix", "build", "--no-link", "--print-out-paths",
                            ".#efi", ".#rootdev", ".#alpine-repo", ".#flex-archive", ".#alpine-iso"],
                           staged, capture=True).splitlines()
        for name in ("crdyboot-debug.efi", "crdyboot-debug.efi.sha256",
                     "THIRD_PARTY_NOTICES.txt", "build-info.json", "rootdev-compat.so"):
            candidates = [Path(output) / name for output in outputs if (Path(output) / name).is_file()]
            if len(candidates) != 1:
                raise ValueError(f"Expected exactly one built artifact: {name}")
            shutil.copyfile(candidates[0], staged / name)
        shim = staged / "rootdev-compat.so"
        (staged / "rootdev-compat.so.sha256").write_text(
            f"{make_usb.file_hash(shim, 'sha256')}  {shim.name}\n")
        _command(["nix", "flake", "check"], staged)
        print("Running the complete local suite; verbose output reports any capability-dependent skips. "
              "This command does not acquire root privileges.", flush=True)
        _command(["nix", "develop", "path:" + str(staged), "--command", "python3",
                  "-m", "unittest", "discover", "-s", str(staged), "-p", "test_*.py", "-v"], staged)
        _publish(directory, staged, original)
    print("Updated locked media, toolchain inputs and built artifacts after validation.", flush=True)


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
