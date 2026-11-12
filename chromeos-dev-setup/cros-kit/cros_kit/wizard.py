#!/usr/bin/env python3
"""Interactive, offline setup launched by the CrOS Kit USB."""

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
import json
import io
import os
from pathlib import Path
import subprocess

from . import install as installer


ERRORS = (ValueError, OSError, subprocess.CalledProcessError)


def disks():
    return json.loads(installer.run(
        "lsblk", "--json", "--bytes", "--paths", "--output",
        "NAME,TYPE,SIZE,MODEL,SERIAL,WWN,MAJ:MIN,MOUNTPOINTS,PARTN,LABEL"
    ))["blockdevices"]


def identity(tree):
    return tuple(tree.get(field) for field in
                 ("name", "size", "model", "serial", "wwn", "maj:min"))


@dataclass(frozen=True)
class Target:
    disk: Path
    hardware: tuple
    gpt: str
    esp: str
    version: str
    model: str
    size: int
    serial: str

    def describe(self):
        serial = f", serial {self.serial}" if self.serial else ""
        return (f"{self.disk} - {self.model}, {self.size / 1024**3:.1f} GiB"
                f"{serial} (Flex {self.version})")


def inspect(tree, remove=False):
    children = list(installer.block_nodes(tree))
    # Exclude the live USB even if someone unmounted it, and any in-use disk.
    if (tree.get("type") != "disk"
            or any(node.get("label") in ("CROSKIT", "CROSKIT_LIVE", "FLEXTOOLS") for node in children)
            or any(any(node.get("mountpoints") or []) for node in children)):
        return None
    # Complete the same read-only preflight used just before application. Only
    # fully validated targets are offered for confirmation.
    with redirect_stdout(io.StringIO()):
        info = installer.configure(Path(tree["name"]), remove=remove, dry_run=True)
    return Target(info.disk, identity(tree), info.gpt, info.esp, info.version,
                  (tree.get("model") or "Unknown model").strip(),
                  int(tree["size"]), (tree.get("serial") or "").strip())


def detail(error):
    return (error.stderr or str(error)).strip() if isinstance(error, subprocess.CalledProcessError) else str(error)


def discover(remove=False):
    found = []
    for tree in disks():
        try:
            target = inspect(tree, remove)
            if target:
                found.append(target)
        except ERRORS as error:
            print(f"Skipping {tree.get('name', '?')}: {detail(error)}")
    return found


def recheck(target):
    # GPT and ESP identity are rechecked by configure before its writable mount.
    matches = [tree for tree in disks() if identity(tree) == target.hardware]
    if len(matches) != 1:
        raise ValueError("Selected disk changed or became unavailable; restart setup")


def choose(targets):
    if len(targets) == 1:
        print("\nInstalled Flex: " + targets[0].describe())
        return targets[0]
    print("\nInstalled Flex disks:")
    for index, target in enumerate(targets, 1):
        print(f"  {index}. {target.describe()}")
    while True:
        answer = input("Choose a disk number (Enter to cancel): ").strip()
        if not answer:
            return None
        if answer.isdecimal() and 1 <= int(answer) <= len(targets):
            return targets[int(answer) - 1]
        print("Enter one of the listed numbers.")


def setup(remove=False):
    print("\nCrOS Kit setup (offline)\n")
    print("The installed Flex disk will be detected automatically.")
    print("Review the disk below; press Enter at the confirmation to cancel.\n")
    if os.geteuid() != 0 or not Path("/sys/firmware/efi").is_dir():
        raise ValueError("Boot the tools USB in UEFI mode")
    installer.check_secure_boot()
    print("Looking for installed Flex disks; the tools USB is excluded...", flush=True)
    targets = discover(remove)
    if not targets:
        print("\nNo eligible Flex disk found. Check the reasons above.")
        print("For a new installation, install Flex and boot it once before returning here.")
        print("Unmount other target partitions if needed. Nothing was changed.")
        print("Press Ctrl+Alt+Del to reboot, or run cros-kit to retry.")
        return False
    target = choose(targets)
    if target is None:
        print("Cancelled. Nothing was changed.")
        return False
    if remove:
        print("\nRemove the Configured/Debug boot entries and their EFI files.")
    else:
        print("\nSet Configured Flex as the daily default; keep stock Flex as a clean fallback.")
        print("Configured and Debug share saved OS changes. Debug enables passwordless host root.")
    if input("Continue? [y/N]: ").strip().lower() not in ("y", "yes"):
        print("Cancelled. Nothing was changed.")
        return False
    recheck(target)
    print("Applying CrOS Kit setup...", flush=True)
    with redirect_stdout(io.StringIO()):
        installer.configure(target.disk, remove=remove, expected=(target.gpt, target.esp))
    print("\nDone. Remove the USB after shutdown.")
    if not remove:
        print("Daily boot: ChromeOS Flex Configured (developer mode disabled).")
        print("Choose ChromeOS Flex Debug in the firmware boot menu.")
        print("Then Ctrl+Alt+F2, log in as chronos, and run sudo id.")
    if input("Press Enter to shut down (or type s to stay here): ").strip().lower() != "s":
        installer.run("poweroff")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="remove Configured/Debug boot entries instead of installing/updating them")
    args = parser.parse_args()
    try:
        setup(args.remove)
    except (EOFError, KeyboardInterrupt):
        print("\nSetup stopped.")
        return 1
    except ERRORS as error:
        print(f"\nERROR: {detail(error)}")
        print("Fix the problem, then run cros-kit to retry.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
