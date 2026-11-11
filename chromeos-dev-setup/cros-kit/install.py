#!/usr/bin/env python3
"""Configure normal and Debug Flex boots from a UEFI Linux live USB."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib

# Keep the Flex backend's installed names stable for updates and removal.
LABEL = "ChromeOS Flex Debug"
LOADER = r"\EFI\cros-flex-debug\bootx64.efi"
NORMAL_LABEL = "ChromeOS Flex Configured"
NORMAL_LOADER = r"\EFI\cros-flex-debug\normalx64.efi"
OWN_DIR = Path("EFI/cros-flex-debug")
OWN_FILES = ("bootx64.efi", "normalx64.efi", "rw-init.sh",
             "rootdev-compat.so", "rootdev-compat.so.sha256", "compatibility.conf", "install.json")
ESP_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
KERNEL_TYPE = "fe3a2a5d-4f32-41a7-b725-accc3285a309"
ROOT_TYPE = "3cb8e202-3b7e-47dd-8a3c-7ff2a13cfcec"


def fail(message):
    raise ValueError(message)


def run(*args):
    result = subprocess.run(args, check=True, text=True, capture_output=True)
    return result.stdout


def read_at(stream, offset, size):
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        fail("Truncated disk or executable")
    return data


def read_gpt(stream, sector_size=512):
    """Validate the primary GPT, ChromeOS layout and FAT ESP signature."""
    header = bytearray(read_at(stream, sector_size, sector_size))
    if header[:8] != b"EFI PART":
        fail("Target has no GPT")
    size, checksum = struct.unpack_from("<II", header, 12)
    if not 92 <= size <= sector_size:
        fail("Invalid GPT header size")
    header[16:20] = bytes(4)
    if zlib.crc32(header[:size]) != checksum:
        fail("GPT header checksum mismatch")
    entries_lba, count, stride, checksum = struct.unpack_from("<QIII", header, 72)
    if count < 12 or count > 4096 or stride < 128 or stride > 1024:
        fail("Invalid GPT partition table size")
    entries = read_at(stream, entries_lba * sector_size, count * stride)
    if zlib.crc32(entries) != checksum:
        fail("GPT partition table checksum mismatch")
    parts = {}
    first_usable, last_usable = struct.unpack_from("<QQ", header, 40)
    current_lba, backup_lba = struct.unpack_from("<QQ", header, 24)
    disk_size = stream.seek(0, os.SEEK_END)
    table_end = entries_lba + (count * stride + sector_size - 1) // sector_size
    table_sectors = table_end - entries_lba
    if (disk_size % sector_size or current_lba != 1 or entries_lba < 2
            or not table_end <= first_usable <= last_usable
            or not last_usable + table_sectors < backup_lba < disk_size // sector_size):
        fail("GPT geometry does not fit the target disk")
    if header[56:72] == bytes(16):
        fail("GPT disk GUID must not be zero")
    seen_guids = set()
    for number in range(1, count + 1):
        entry = entries[(number - 1) * stride:number * stride]
        if entry[:16] == bytes(16):
            continue
        first, last = struct.unpack_from("<QQ", entry, 32)
        if not first_usable <= first <= last <= last_usable:
            fail("Partition lies outside GPT usable range")
        guid = entry[16:32]
        if guid == bytes(16) or guid in seen_guids:
            fail("GPT partition GUIDs must be nonzero and unique")
        seen_guids.add(guid)
        parts[number] = dict(number=number,
                             type=str(uuid.UUID(bytes_le=entry[:16])),
                             uuid=str(uuid.UUID(bytes_le=entry[16:32])),
                             first=first, last=last,
                             attributes=struct.unpack_from("<Q", entry, 48)[0],
                             label=entry[56:128].decode("utf-16-le").rstrip("\0"))
    expected = {2: ("KERN-A", KERNEL_TYPE), 3: ("ROOT-A", ROOT_TYPE),
                12: ("EFI-SYSTEM", ESP_TYPE)}
    for number, (label, kind) in expected.items():
        if number not in parts or (parts[number]["label"], parts[number]["type"]) != (label, kind):
            fail(f"Target is not a supported Flex disk: partition {number} must be {label}")
    ranges = sorted((part["first"], part["last"]) for part in parts.values())
    if any(left[1] >= right[0] for left, right in zip(ranges, ranges[1:])):
        fail("Overlapping GPT partitions")
    esp = read_at(stream, parts[12]["first"] * sector_size, 512)
    if esp[510:512] != b"\x55\xaa" or b"FAT" not in esp[54:90]:
        fail("EFI partition has no FAT signature")
    return str(uuid.UUID(bytes_le=bytes(header[56:72]))), parts


def key_section(data):
    """Return the unique .vbpubk raw range in an x64 EFI application."""
    if len(data) < 64 or data[:2] != b"MZ":
        fail("Not a PE executable")
    pe = struct.unpack_from("<I", data, 60)[0]
    if pe + 24 > len(data) or data[pe:pe + 4] != b"PE\0\0":
        fail("Invalid PE header")
    machine, count = struct.unpack_from("<HH", data, pe + 4)
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    if machine != 0x8664 or optional_size < 112 or optional + optional_size > len(data):
        fail("Expected an x86-64 EFI executable")
    if struct.unpack_from("<H", data, optional)[0] != 0x20B or struct.unpack_from("<H", data, optional + 68)[0] != 10:
        fail("Expected a PE32+ EFI application")
    found = []
    for index in range(count):
        pos = optional + optional_size + index * 40
        if pos + 40 > len(data):
            fail("Truncated PE section table")
        if data[pos:pos + 8].rstrip(b"\0") == b".vbpubk":
            virtual_size, _, length, offset = struct.unpack_from("<IIII", data, pos + 8)
            if length < 32 or offset + length > len(data) or not 32 <= virtual_size <= 8192 or length > 8192:
                fail("Invalid .vbpubk section bounds")
            found.append((offset, min(length, virtual_size)))
    if len(found) != 1:
        fail("Expected exactly one .vbpubk section")
    return found[0]


def validate_key(key):
    if len(key) < 32:
        fail("Truncated kernel verification key")
    offset, length, algorithm, version = struct.unpack_from("<QQQQ", key)
    # vboot RSA1024/2048/4096/8192, plus the exponent-3 variants.
    rsa_bytes = (128, 256, 512, 1024, 256, 384)
    if algorithm >= len(rsa_bytes) * 3 or version > 0xFFFFFFFF or offset < 32 or offset % 4 or offset + length > len(key):
        fail("Invalid packed kernel verification key")
    signature_size = rsa_bytes[algorithm // 3]
    if length != signature_size * 2 + 8 or struct.unpack_from("<I", key, offset)[0] != signature_size // 4:
        fail("Kernel verification key size does not match its algorithm")
    return key


def make_loader(template, stock):
    start, length = key_section(template)
    if length != 8192 or any(template[start:start + length]):
        fail("Template must have an empty 8192-byte .vbpubk section")
    key_start, key_length = key_section(stock)
    key = validate_key(stock[key_start:key_start + key_length])
    if key_length > length:
        fail("Installed verification key does not fit the template")
    result = bytearray(template)
    result[start:start + length] = key.ljust(length, b"\0")
    return bytes(result)


def verified_payload(name, directory=None):
    directory = Path(directory) if directory is not None else Path(__file__).parent
    contents = (directory / name).read_bytes()
    expected = (directory / (name + ".sha256")).read_text().split()
    if (len(expected) != 2 or expected[1] != name
            or expected[0] != hashlib.sha256(contents).hexdigest()):
        fail(f"CrOS Kit {name} checksum mismatch")
    return contents


def verified_rw_init(directory=None):
    return verified_payload("rw-init.sh", directory)


def verified_rootdev(directory=None):
    return verified_payload("rootdev-compat.so", directory)


def compatibility(directory=None):
    """Read inert, strictly validated release pins; never evaluate shell syntax."""
    directory = Path(directory) if directory is not None else Path(__file__).parent
    patterns = {"FLEX_OS_VERSION": r"[0-9]+\.[0-9]+\.[0-9]+",
                "FLEX_ROOT_SHA256": r"[0-9a-f]{64}",
                "FLEX_KERNEL_RELEASE": r"[A-Za-z0-9][A-Za-z0-9._+-]*",
                "FLEX_STARTUP_SHA256": r"[0-9a-f]{64}"}
    values = {}
    text = (directory / "compatibility.conf").read_bytes().decode("utf-8")
    if "\0" in text:
        fail("Invalid CrOS Kit compatibility.conf")
    for line in text.split("\n"):
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (not separator or key not in patterns or key in values
                or re.fullmatch(patterns[key], value) is None):
            fail("Invalid CrOS Kit compatibility.conf")
        values[key] = value
    if values.keys() != patterns.keys():
        fail("Incomplete CrOS Kit compatibility.conf")
    return values


def require_supported_version(version, directory=None):
    supported = compatibility(directory)
    if version != supported["FLEX_OS_VERSION"]:
        fail(f"Unsupported Flex version {version}; this build supports {supported['FLEX_OS_VERSION']}. "
             "Use stock Flex until a matching CrOS Kit build is available")
    return supported


def flex_release(text):
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    board = fields.get("CHROMEOS_RELEASE_BOARD", "").strip('"')
    if not re.fullmatch(r"reven(?:-signed-[A-Za-z0-9_-]+)?", board):
        fail("Not a ChromeOS Flex (reven) installation")
    return fields.get("CHROMEOS_RELEASE_VERSION", "unknown").strip('"')


def bootable_roots(stream, parts, sector_size):
    """Check all GPT-eligible slots, rather than guessing which vboot will select."""
    roots = []
    for number, kernel in parts.items():
        if kernel["type"] != KERNEL_TYPE:
            continue
        attributes = kernel["attributes"]
        priority, tries, successful = (attributes >> 48) & 15, (attributes >> 52) & 15, (attributes >> 56) & 1
        if not priority or not (tries or successful):
            continue
        slot = {2: "A", 4: "B"}.get(number)
        root = parts.get(number + 1, {})
        if (slot is None or kernel["label"] != "KERN-" + slot
                or root.get("label") != "ROOT-" + slot or root.get("type") != ROOT_TYPE):
            fail("Unsupported bootable Flex kernel/root slot layout")
        if read_at(stream, kernel["first"] * sector_size, 8) != b"CHROMEOS":
            fail(f"KERN-{slot} has no ChromeOS keyblock")
        if read_at(stream, root["first"] * sector_size + 1080, 2) != b"\x53\xef":
            fail(f"ROOT-{slot} has no ext filesystem signature")
        roots.append(number + 1)
    if not roots:
        fail("No bootable Flex A/B slot found")
    return roots


def check_partition_mapping(partition, part, sector_size):
    device = partition.stat().st_rdev
    sys_partition = Path("/sys/dev/block") / f"{os.major(device)}:{os.minor(device)}"
    if (int((sys_partition / "start").read_text()) * 512 != part["first"] * sector_size
            or int((sys_partition / "size").read_text()) * 512 != (part["last"] - part["first"] + 1) * sector_size):
        fail("Kernel partition mapping differs from GPT; run partprobe or reboot the live USB")


def root_partitions(disk, numbers, parts, sector_size):
    trees = json.loads(run("lsblk", "--json", "--paths", "--output", "NAME,TYPE,PARTN", str(disk)))["blockdevices"]
    if len(trees) != 1 or trees[0]["type"] != "disk" or trees[0]["name"] != str(disk):
        fail("Cannot identify Flex root partitions")
    result = []
    for number in numbers:
        matches = [child for child in trees[0].get("children", [])
                   if child.get("partn") == number and child.get("type") == "part"]
        if len(matches) != 1:
            fail(f"Cannot identify Flex root partition {number}; run partprobe or reboot")
        partition = Path(matches[0]["name"])
        if not partition.is_absolute() or not stat.S_ISBLK(partition.stat().st_mode):
            fail("Flex root partition must be a block device")
        check_partition_mapping(partition, parts[number], sector_size)
        result.append((number, partition))
    return result


@contextmanager
def root_files(partition):
    with mounted_filesystem(partition, "ext4", "ro,noload,nosuid,nodev,noexec",
                            "cros-kit-root-") as directory:
        yield directory


def validate_installation(disk, parts, sector_size):
    supported = compatibility()
    with disk.open("rb", buffering=0) as stream:
        numbers = bootable_roots(stream, parts, sector_size)
    # An old successful fallback may remain after an OS upgrade. Validate all
    # highest-priority choices; lower-priority boots enforce their own runtime gate.
    priority = max((parts[number - 1]["attributes"] >> 48) & 15 for number in numbers)
    numbers = [number for number in numbers
               if ((parts[number - 1]["attributes"] >> 48) & 15) == priority]
    for number, partition in root_partitions(disk, numbers, parts, sector_size):
        with root_files(partition) as directory:
            version = flex_release((directory / "etc/lsb-release").read_text())
            if version != supported["FLEX_OS_VERSION"]:
                fail(f"ROOT-{chr(65 + (number - 3) // 2)} has unsupported Flex version {version}; "
                     f"this build supports {supported['FLEX_OS_VERSION']}. Use stock Flex")
            startup = directory / "etc/init/startup.conf"
            if hashlib.sha256(startup.read_bytes()).hexdigest() != supported["FLEX_STARTUP_SHA256"]:
                fail(f"Flex partition {number} startup.conf differs from the supported OS")
            if not (directory / "lib/modules" / supported["FLEX_KERNEL_RELEASE"]).is_dir():
                fail(f"Flex partition {number} is missing the supported kernel modules")
    return supported


def firmware_entries(text):
    order_match = re.search(r"^BootOrder:\s*([0-9A-Fa-f,]+)\s*$", text, re.M)
    order = order_match.group(1).upper().split(",") if order_match else []
    entries = {}
    for match in re.finditer(r"^Boot([0-9A-Fa-f]{4})(\*?)\s+(.+)$", text, re.M):
        number, active, value = match.groups()
        path = re.search(r"HD\(12,GPT,([0-9A-Fa-f-]+),[^)]*\)/"
                         r"(?:File\(([^)]+)\)|([\\][^\t\r\n]*?\.efi)\s*$)", value, re.I)
        if path:
            label = value.split("\t", 1)[0].split(" HD(", 1)[0].rstrip()
            entries[number.upper()] = dict(label=label, active=bool(active),
                                            uuid=path[1].lower(), path=(path[2] or path[3]).lower())
    return order, entries


def owned_entries(entries, esp_uuid, label=LABEL, loader=LOADER):
    return [number for number, entry in entries.items()
            if entry["uuid"] == esp_uuid and entry["label"] == label
            and entry["path"] == loader.lower()]


def check_secure_boot(efivars=Path("/sys/firmware/efi/efivars"), mounts=Path("/proc/self/mounts")):
    global_guid = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
    try:
        value = (efivars / f"SecureBoot-{global_guid}").read_bytes()
    except FileNotFoundError:
        # Like Linux efi_get_secureboot_mode(), EFI_NOT_FOUND means disabled,
        # but first distinguish unsupported firmware from unavailable efivarfs.
        if not any(len(fields) >= 3 and fields[1] == str(efivars) and fields[2] == "efivarfs"
                   for fields in (line.split() for line in mounts.read_text().splitlines())):
            fail("Cannot determine Secure Boot status: efivarfs is not mounted")
        order = (efivars / f"BootOrder-{global_guid}").read_bytes()
        if len(order) < 6 or (len(order) - 4) % 2:
            fail("Cannot determine Secure Boot status: malformed BootOrder variable")
        return "unsupported"
    if len(value) != 5 or value[4] not in (0, 1):
        fail("Cannot determine Secure Boot status: malformed SecureBoot variable")
    if value[4] == 1:
        fail("Disable UEFI Secure Boot in firmware settings first")
    return "disabled"


def check_environment(disk):
    if os.geteuid() != 0:
        fail("Run with sudo from a UEFI Linux live USB")
    if not Path("/sys/firmware/efi").is_dir():
        fail("Boot the Linux live USB in UEFI mode")
    print(f"Secure Boot: {check_secure_boot()}")
    for command in ("lsblk", "blockdev", "mount", "umount", "efibootmgr"):
        if not shutil.which(command):
            fail(f"Required command is missing: {command}")
    disk = disk.resolve(strict=True)
    if not stat.S_ISBLK(disk.stat().st_mode):
        fail("--disk must name a whole block device, not a file")
    tree = json.loads(run("lsblk", "--json", "--paths", "--output", "NAME,TYPE,MAJ:MIN,MOUNTPOINTS,PARTN", str(disk)))["blockdevices"]
    if len(tree) != 1 or tree[0]["type"] != "disk":
        fail("--disk must name a whole disk")
    nodes = []
    def visit(node):
        nodes.append(node)
        for child in node.get("children", []):
            visit(child)
    visit(tree[0])
    for node in nodes:
        if any(node.get("mountpoints") or []):
            fail(f"Target is mounted/in use: {node['name']}; unmount all its partitions first")
    # Also refuse active device-mapper/RAID holders and swap.
    for node in nodes:
        sys_block = Path("/sys/dev/block") / node["maj:min"]
        if (sys_block / "holders").is_dir() and any((sys_block / "holders").iterdir()):
            fail(f"Target has an active device-mapper/RAID holder: {node['name']}")
    swaps = Path("/proc/swaps").read_text().splitlines()[1:]
    if any(line.split()[0] in {node["name"] for node in nodes} for line in swaps):
        fail("Target has active swap")
    esp_nodes = [node["name"] for node in nodes if node.get("partn") == 12]
    if len(esp_nodes) != 1:
        fail("Cannot identify target partition 12; run partprobe after installing Flex")
    return disk, Path(esp_nodes[0]), int(run("blockdev", "--getss", str(disk)))


@contextmanager
def mounted_filesystem(partition, filesystem, options, prefix):
    # Never recursively clean a mountpoint: failed unmounts must leave the
    # mounted filesystem (including the original EFI files) untouched.
    directory = Path(tempfile.mkdtemp(prefix=prefix))
    mounted = False
    try:
        run("mount", "-t", filesystem, "-o", options, str(partition), str(directory))
        mounted = True
        yield directory
    finally:
        if mounted:
            try:
                run("umount", str(directory))
            except (OSError, subprocess.CalledProcessError) as error:
                detail = (error.stderr or str(error)).strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
                fail(f"Could not unmount {directory}: {detail}. "
                     "Mount and files retained; unmount it manually before retrying")
        directory.rmdir()


@contextmanager
def mounted_esp(partition, writable):
    options = ("rw" if writable else "ro") + ",nosuid,nodev,noexec"
    with mounted_filesystem(partition, "vfat", options, "cros-kit-esp-") as directory:
        yield directory


def atomic_write(path, data):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def manage(args):
    disk, partition, sector_size = check_environment(args.disk)
    with disk.open("rb", buffering=0) as stream:
        disk_uuid, parts = read_gpt(stream, sector_size)
    check_partition_mapping(partition, parts[12], sector_size)
    if args.action == "install":
        validate_installation(disk, parts, sector_size)
        config_data = Path(__file__).with_name("compatibility.conf").read_bytes()
    esp_uuid = parts[12]["uuid"]
    order, entries = firmware_entries(run("efibootmgr", "--verbose"))
    ours = owned_entries(entries, esp_uuid)
    normal = owned_entries(entries, esp_uuid, NORMAL_LABEL, NORMAL_LOADER)
    writable = args.action != "status" and not args.dry_run
    with mounted_esp(partition, writable) as esp:
        stock_path = esp / "EFI/BOOT/crdybootx64.efi"
        stock = key = None
        if args.action != "remove":
            try:
                stock = stock_path.read_bytes()
                key_start, key_length = key_section(stock)
                key = validate_key(stock[key_start:key_start + key_length])
            except (OSError, ValueError) as error:
                if args.action == "install":
                    raise
                print(f"Stock kernel key unavailable: {error}")
        directory = esp / OWN_DIR
        target = directory / "bootx64.efi"
        normal_target = directory / "normalx64.efi"
        rw_target = directory / "rw-init.sh"
        rootdev_target = directory / "rootdev-compat.so"
        marker = directory / "install.json"
        if directory.exists():
            allowed = set(OWN_FILES) | {name + ".tmp" for name in OWN_FILES}
            if any(path.name not in allowed or not path.is_file() for path in directory.iterdir()):
                fail(f"Unexpected files in {OWN_DIR}; refusing to modify them")
            if marker.exists():
                state = json.loads(marker.read_text())
                if state.get("format") not in (1, 2, 3) or state.get("esp_uuid") != esp_uuid:
                    fail("Existing installation marker belongs to another disk/version")
            elif any((directory / name).exists() for name in OWN_FILES if name != "install.json"):
                fail("Existing custom EFI has no installation marker; refusing to overwrite it")
        print(f"Target: {disk} (GPT {disk_uuid}), ESP: {partition}")
        if key is not None:
            print(f"Installed kernel key SHA256: {hashlib.sha256(key).hexdigest()}")
        if args.action == "status":
            print(f"Debug EFI: {'present' if target.exists() else 'absent'}; firmware entries: {','.join(ours) or 'none'}")
            print(f"Configured EFI: {'present' if normal_target.exists() else 'absent'}; firmware entries: {','.join(normal) or 'none'}")
            print(f"RW init script: {'present' if rw_target.exists() else 'absent'}")
            print(f"Root device compatibility library: {'present' if rootdev_target.exists() else 'absent'}")
            print(f"BootOrder: {','.join(order)}")
            if target.exists() and key is not None:
                installed = target.read_bytes()
                start, length = key_section(installed)
                print(f"Debug key matches stock: {installed[start:start + length] == key.ljust(length, bytes(1))}")
            return
        if args.action == "install":
            stock_entries = [number for number, entry in entries.items()
                             if entry["uuid"] == esp_uuid and entry["active"]
                             and entry["path"] == r"\efi\boot\bootx64.efi" and number in order]
            if not stock_entries:
                fail("No active stock Flex firmware entry in BootOrder for this disk; boot/install stock Flex first")
            image = make_loader(args.template.read_bytes(), stock)
            rw_script = verified_rw_init()
            rootdev_library = verified_rootdev()
            rootdev_manifest = (hashlib.sha256(rootdev_library).hexdigest() + "  rootdev-compat.so\n").encode()
            print(f"Debug EFI SHA256: {hashlib.sha256(image).hexdigest()}")
            if args.dry_run:
                print(f"Dry run: write both EFI modes, rw-init.sh, rootdev-compat.so and its checksum in {OWN_DIR}; make '{NORMAL_LABEL}' default, keep stock fallback, '{LABEL}' last")
                return
            directory.mkdir(exist_ok=True)
            marker_data = (json.dumps(dict(format=3, esp_uuid=esp_uuid)) + "\n").encode()
            if not marker.exists():
                atomic_write(marker, marker_data)
            # Publish all bootstrap dependencies before the init script/loaders.
            for path, contents in ((directory / "compatibility.conf", config_data),
                                   (rootdev_target, rootdev_library),
                                   (directory / "rootdev-compat.so.sha256", rootdev_manifest)):
                if not path.exists() or path.read_bytes() != contents:
                    atomic_write(path, contents)
                if path.read_bytes() != contents:
                    fail("Bootstrap dependency copy verification failed; EFI and BootOrder were not changed")
            # The new loader must never become visible before its init script.
            if not rw_target.exists() or rw_target.read_bytes() != rw_script:
                atomic_write(rw_target, rw_script)
            if rw_target.read_bytes() != rw_script:
                fail("RW init copy verification failed; EFI and BootOrder were not changed")
            for path in (target, normal_target):
                if not path.exists() or path.read_bytes() != image:
                    atomic_write(path, image)
                if path.read_bytes() != image:
                    fail("EFI copy verification failed; BootOrder was not changed")
            if marker.read_bytes() != marker_data:
                atomic_write(marker, marker_data)
            selected = {}
            all_owned = []
            for label, loader in ((LABEL, LOADER), (NORMAL_LABEL, NORMAL_LOADER)):
                matches = owned_entries(entries, esp_uuid, label, loader)
                if not matches:
                    run("efibootmgr", "--create-only", "--disk", str(disk), "--part", "12", "--label", label, "--loader", loader)
                    _, entries = firmware_entries(run("efibootmgr", "--verbose"))
                    matches = owned_entries(entries, esp_uuid, label, loader)
                    if not matches:
                        fail(f"Firmware did not create '{label}'; stock BootOrder preserved")
                selected[label] = next((number for number in order if number in matches), matches[0])
                all_owned.extend(matches)
            # Both modes share persistent configuration; only Debug enables root.
            new_order = ([selected[NORMAL_LABEL]] + [number for number in order if number not in all_owned]
                         + [selected[LABEL]])
            if new_order != order:
                run("efibootmgr", "--bootorder", ",".join(new_order))
                confirmed_order, _ = firmware_entries(run("efibootmgr", "--verbose"))
                if confirmed_order != new_order:
                    fail("Firmware did not retain the requested boot order; EFI file retained")
            for chosen in selected.values():
                if not entries[chosen]["active"]:
                    run("efibootmgr", "--bootnum", chosen, "--active")
            for number in all_owned:
                if number not in selected.values():
                    run("efibootmgr", "--bootnum", number, "--delete-bootnum")
            final_order, final_entries = firmware_entries(run("efibootmgr", "--verbose"))
            if (final_order != new_order or any(
                    owned_entries(final_entries, esp_uuid, label, loader) != [selected[label]]
                    or not final_entries[selected[label]]["active"]
                    for label, loader in ((LABEL, LOADER), (NORMAL_LABEL, NORMAL_LOADER)))):
                fail("Firmware did not retain the requested Configured/Debug entries/order")
            print(f"Installed. '{NORMAL_LABEL}' is the default; stock Flex is a clean fallback.")
            print("Saved OS changes are shared by Configured and Debug boots.")
            print(f"For host root, select '{LABEL}' in the firmware boot menu, press Ctrl+Alt+F2, log in as chronos, then run: sudo id")
            print("Configured boots keep developer mode disabled; use stock Flex for initial OS-update testing.")
        else:
            if args.dry_run:
                print(f"Dry run: remove only {OWN_DIR} files and matching Configured/Debug firmware entries")
                return
            all_owned = ours + normal
            new_order = [number for number in order if number not in all_owned]
            if new_order != order:
                run("efibootmgr", "--bootorder", ",".join(new_order))
            for number in all_owned:
                run("efibootmgr", "--bootnum", number, "--delete-bootnum")
            final_order, final_entries = firmware_entries(run("efibootmgr", "--verbose"))
            if (final_order != new_order or owned_entries(final_entries, esp_uuid)
                    or owned_entries(final_entries, esp_uuid, NORMAL_LABEL, NORMAL_LOADER)):
                fail("Firmware did not remove the Configured/Debug entries; EFI files retained")
            if directory.exists():
                for name in (*OWN_FILES, *(name + ".tmp" for name in OWN_FILES)):
                    (directory / name).unlink(missing_ok=True)
                directory.rmdir()
            print("Removed. Stock firmware entries, kernels, root filesystem and user data retained.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "remove", "status"))
    parser.add_argument("--disk", type=Path, required=True, help="installed Flex whole disk, e.g. /dev/nvme0n1")
    parser.add_argument("--template", type=Path, default=Path(__file__).with_name("crdyboot-debug.efi"))
    parser.add_argument("--dry-run", action="store_true", help="validate and show changes without writing disk/firmware")
    args = parser.parse_args()
    try:
        manage(args)
    except (ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        parser.exit(1, f"ERROR: {detail}\n")


if __name__ == "__main__":
    main()
