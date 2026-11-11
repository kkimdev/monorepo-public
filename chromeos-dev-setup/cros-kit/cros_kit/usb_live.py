#!/usr/bin/env python3
"""Prepare the offline Alpine tools tree; never mount or write a block device."""

import gzip
import hashlib
import io
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from .install import compatibility, verified_payload


# Alpine splits util-linux into subpackages. blockdev is in util-linux-misc.
# initramfs adds alpine-base and openssl (signed modloop) during diskless boot.
PACKAGES = ("alpine-base", "openssl", "python3", "efibootmgr", "lsblk",
            "mount", "umount", "util-linux-misc", "agetty")
INSTALLER_FILES = ("__init__.py", "install.py", "wizard.py", "compatibility.conf", "crdyboot-debug.efi",
                   "crdyboot-debug.efi.sha256", "rw-init.sh", "rw-init.sh.sha256",
                   "rootdev-compat.so", "rootdev-compat.so.sha256",
                   "THIRD_PARTY_NOTICES.txt", "build-info.json")


def run(arguments):
    result = subprocess.run([str(argument) for argument in arguments],
                            capture_output=True, text=True)
    # apk 3.0 fetch can print solver errors while returning zero.
    if result.returncode or re.search(r"(?m)^ERROR:", result.stderr):
        detail = (result.stderr or result.stdout).strip()
        raise ValueError(f"{arguments[0]} failed: {detail}")
    return result.stdout


def _overlay(path):
    inittab = """::sysinit:/sbin/openrc sysinit
::sysinit:/sbin/openrc boot
::wait:/sbin/openrc default
tty1::respawn:/sbin/agetty --autologin root --noclear tty1 115200 linux
tty2::respawn:/sbin/getty 38400 tty2
::ctrlaltdel:/sbin/reboot
::shutdown:/sbin/openrc shutdown
"""
    profile = """# Offer one short command from any interactive tools shell.
case $- in
  *i*)
    for cros_kit_root in /media/*/cros-kit; do
      [ -f "$cros_kit_root/cros_kit/wizard.py" ] || continue
      CROS_KIT_ROOT=$cros_kit_root
      export CROS_KIT_ROOT
      alias cros-kit='PYTHONPATH="$CROS_KIT_ROOT" python3 -P -m cros_kit.wizard'
      printf '\\n%s\\n' 'Run cros-kit to set up CrOS Kit (disk detected automatically).'
      break
    done
    unset cros_kit_root
    ;;
esac
"""
    files = {
        "etc/.default_boot_services": b"",
        "etc/apk/world": ("\n".join(PACKAGES) + "\n").encode(),
        "etc/hostname": b"cros-kit\n",
        "etc/inittab": inittab.encode(),
        "etc/motd": b"",
        "etc/profile.d/cros-kit.sh": profile.encode(),
    }
    # Leave repositories to initramfs: it records the actual discovered local
    # /media/<device>/apks path, which is not necessarily /media/CROSKIT.
    with path.open("wb") as output:
        with gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name, contents in sorted(files.items()):
                    info = tarfile.TarInfo(name)
                    info.mode = 0o644
                    info.size = len(contents)
                    archive.addfile(info, io.BytesIO(contents))


def prepare_media(iso_path, destination, work, apk_command="apk", bsdtar_command="bsdtar",
                  expected_version=None):
    """Validate Alpine media and prepare trusted keys plus a private APK root."""
    destination, work = Path(destination), Path(work)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Alpine staging directory must be empty")
    run([bsdtar_command, "-xf", Path(iso_path).resolve(), "-C", destination])
    # ISO media has read-only modes; this is a writable staging copy.
    destination.chmod(0o755)
    for item in destination.rglob("*"):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError("Unexpected nonregular file in Alpine ISO")
        item.chmod(0o755 if item.is_dir() else 0o644)
    release = (destination / ".alpine-release").read_text().strip()
    match = re.fullmatch(r"alpine-standard-(\d+\.\d+\.\d+)(?: \d+)?", release)
    if not match:
        raise ValueError("Expected Alpine STANDARD release media")
    version = match[1]
    if expected_version is not None and version != expected_version:
        raise ValueError("Alpine ISO version differs from official release metadata")
    for name in ("boot/vmlinuz-lts", "boot/initramfs-lts", "boot/modloop-lts",
                 "apks/.boot_repository"):
        if not (destination / name).is_file():
            raise ValueError(f"Missing Alpine boot file: {name}")
    packages_dir = destination / "apks/x86_64"
    keys_apk = list(packages_dir.glob("alpine-keys-*.apk"))
    if len(keys_apk) != 1:
        raise ValueError("Expected one trusted alpine-keys package in the ISO")
    keys = work / "keys"
    keys.mkdir()
    run([bsdtar_command, "-xf", keys_apk[0], "-C", keys])
    keys_dir = keys / "etc/apk/keys"
    if not list(keys_dir.glob("*.pub")):
        raise ValueError("Official Alpine ISO contains no package signing keys")
    root, cache = work / "root", work / "cache"
    root.mkdir()
    cache.mkdir()
    repositories = work / "repositories"
    repositories.write_text("")
    apk = [apk_command, "--root", root, "--arch", "x86_64",
           "--keys-dir", keys_dir, "--cache-dir", cache,
           "--repositories-file", repositories]
    run([*apk, "add", "--initdb", "--usermode", "--no-network"])
    return version, packages_dir, apk, repositories


def prepare_live(iso_path, destination, installer_dir, repository,
                 apk_command="apk", bsdtar_command="bsdtar"):
    """Extract a checksum-verified STANDARD ISO and add signed offline tools.

    The caller supplies a hash-verified ISO and locked local APK repository,
    plus an empty staging directory. All apk state is temporary; package
    installation scripts never execute on the maker's host.
    """
    destination = Path(destination).resolve()
    installer_dir = Path(installer_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Alpine staging directory must be empty")
    for name in ("crdyboot-debug.efi", "rw-init.sh", "rootdev-compat.so"):
        verified_payload(name, installer_dir)
    for name in INSTALLER_FILES:
        if not (installer_dir / name).is_file():
            raise ValueError(f"Missing CrOS Kit payload: {name}")
    compatibility(installer_dir)
    repository = Path(repository).resolve()
    with tempfile.TemporaryDirectory(prefix="cros-kit-apk-") as temporary:
        work = Path(temporary)
        version, packages_dir, apk, repositories = prepare_media(
            iso_path, destination, work, apk_command, bsdtar_command)
        # The flake supplies exact, hash-locked files and the signed index.
        # Never refresh repositories or resolve against the live network here.
        for package in packages_dir.glob("*.apk"):
            package.unlink()
        for package in (repository / "x86_64").iterdir():
            if package.name == "APKINDEX.tar.gz" or package.suffix == ".apk":
                shutil.copyfile(package, packages_dir / package.name)
        repositories.write_text(str(destination / "apks") + "\n")
        # Check both solving and fetching against the final local-only media.
        # Fetch also checks every file is present; no network/cache fallback.
        run([*apk, "--no-network", "add", "--simulate", *PACKAGES])
        verified = work / "verified"
        verified.mkdir()
        run([*apk, "--no-network", "fetch", "--recursive", "--link",
              "--output", verified, *PACKAGES])
        package_count = len(list(verified.glob("*.apk")))
        if not package_count:
            raise ValueError("Offline Alpine tools package set is empty")
    payload = destination / "cros-kit/cros_kit"
    payload.mkdir(parents=True)
    for name in INSTALLER_FILES:
        shutil.copyfile(installer_dir / name, payload / name)
    _overlay(destination / "cros-kit.apkovl.tar.gz")
    return {"version": version, "repository": str(repository),
            "packages": package_count,
            "index_sha256": hashlib.sha256((packages_dir / "APKINDEX.tar.gz").read_bytes()).hexdigest()}
