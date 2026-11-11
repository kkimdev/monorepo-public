#!/usr/bin/env python3
"""Make a UEFI USB with the locked official Flex installer and offline CrOS Kit tools."""

import argparse
import fcntl
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
import time
import urllib.parse
import uuid
import zipfile
import zlib

from . import install as installer
from . import usb_live

HERE = Path(__file__).resolve().parent.parent
MIB = 1024 * 1024
CHUNK = 8 * MIB
TOOLS_LABEL = "CROSKIT"
DATA_TYPE = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def stable_release(entries):
    candidates = [item for item in entries if item.get("channel") == "STABLE"
                  and item.get("model") == "ChromeOS Flex"]
    if not candidates:
        raise ValueError("Google's recovery metadata has no stable Flex image")
    release = max(candidates, key=lambda item: tuple(map(int, item["version"].split("."))))
    url = urllib.parse.urlparse(release["url"])
    if (url.scheme != "https" or url.hostname != "dl.google.com"
            or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.bin", release["file"])
            or not re.fullmatch(r"[0-9a-f]{40}", release["sha1"])
            or release["filesize"] <= 0 or release["zipfilesize"] <= 0):
        raise ValueError("Unexpected official Flex download metadata")
    return release


def file_hash(path, algorithm):
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        while data := stream.read(CHUNK):
            digest.update(data)
    return digest.hexdigest()


def extract_flex(archive, release, output):
    with zipfile.ZipFile(archive) as source:
        info = source.getinfo(release["file"])
        if info.file_size != release["filesize"]:
            raise ValueError("Flex archive image size differs from official metadata")
        with source.open(info) as image, output.open("xb") as target:
            while data := image.read(CHUNK):
                if data.count(0) == len(data):
                    target.seek(len(data), os.SEEK_CUR)
                else:
                    target.write(data)
            target.truncate(info.file_size)


def align(value, boundary=MIB):
    return (value + boundary - 1) // boundary * boundary


def gpt_records(stream, total_size, replacement_entries=None):
    """Return validated GPT writes, relocating its backup to the actual disk end."""
    _, parts = installer.read_gpt(stream)
    # Official recovery images must contain A, even when its GPT boot flags
    # are unset. Installed A/B eligibility is checked separately by install.py.
    if installer.read_at(stream, parts[2]["first"] * 512, 8) != b"CHROMEOS":
        raise ValueError("Official image KERN-A has no ChromeOS keyblock")
    if installer.read_at(stream, parts[3]["first"] * 512 + 1080, 2) != b"\x53\xef":
        raise ValueError("Official image ROOT-A has no ext filesystem signature")
    stream.seek(0)
    mbr = bytearray(stream.read(512))
    header = bytearray(stream.read(512))
    entries_lba, count, stride = struct.unpack_from("<QII", header, 72)
    table_bytes = count * stride
    table_sectors = (table_bytes + 511) // 512
    stream.seek(entries_lba * 512)
    entries = bytes(replacement_entries) if replacement_entries is not None else stream.read(table_bytes)
    if len(entries) != table_bytes or total_size % 512:
        raise ValueError("Invalid GPT output size")
    sectors = total_size // 512
    last_usable = sectors - table_sectors - 2
    first_usable = struct.unpack_from("<Q", header, 40)[0]
    if last_usable < first_usable:
        raise ValueError("Output is too small for GPT")
    for index in range(count):
        pos = index * stride
        if entries[pos:pos + 16] != bytes(16):
            first, last = struct.unpack_from("<QQ", entries, pos + 32)
            if not first_usable <= first <= last <= last_usable:
                raise ValueError("Output is too small for its partitions")
    protective = [pos for pos in range(446, 510, 16) if mbr[pos + 4] == 0xEE]
    if len(protective) != 1 or mbr[510:512] != b"\x55\xaa":
        raise ValueError("Flex image has no unique protective MBR")
    struct.pack_into("<I", mbr, protective[0] + 12, min(sectors - 1, 0xFFFFFFFF))
    old_backup = struct.unpack_from("<Q", header, 32)[0]
    struct.pack_into("<Q", header, 32, sectors - 1)
    struct.pack_into("<Q", header, 48, last_usable)
    struct.pack_into("<I", header, 88, zlib.crc32(entries))
    header[16:20] = bytes(4)
    header_size = struct.unpack_from("<I", header, 12)[0]
    struct.pack_into("<I", header, 16, zlib.crc32(header[:header_size]))
    backup = bytearray(header)
    struct.pack_into("<QQ", backup, 24, sectors - 1, 1)
    struct.pack_into("<Q", backup, 72, sectors - table_sectors - 1)
    backup[16:20] = bytes(4)
    struct.pack_into("<I", backup, 16, zlib.crc32(backup[:header_size]))
    records = [(0, bytes(mbr)), (512, bytes(header)), (entries_lba * 512, entries)]
    if old_backup != sectors - 1:
        records.append(((old_backup - table_sectors) * 512, bytes((table_sectors + 1) * 512)))
    records += [((sectors - table_sectors - 1) * 512, entries), ((sectors - 1) * 512, bytes(backup))]
    return records


def add_tools_partition(stream, tools_size):
    installer.read_gpt(stream)
    stream.seek(512)
    header = stream.read(512)
    entries_lba, count, stride = struct.unpack_from("<QII", header, 72)
    if count < 13 or tools_size <= 0 or tools_size % 512:
        raise ValueError("Unsupported tools partition size/layout")
    stream.seek(entries_lba * 512)
    entries = bytearray(stream.read(count * stride))
    pos = 12 * stride
    if any(entries[pos:pos + stride]):
        raise ValueError("Official image already uses partition 13; refusing to replace it")
    stream.seek(0, os.SEEK_END)
    start = align(stream.tell())
    total_size = align(start + tools_size + ((count * stride + 511) // 512 + 1) * 512)
    entries[pos:pos + 16] = uuid.UUID(DATA_TYPE).bytes_le
    entries[pos + 16:pos + 32] = uuid.uuid4().bytes_le
    struct.pack_into("<QQQ", entries, pos + 32, start // 512, (start + tools_size) // 512 - 1, 0)
    label = TOOLS_LABEL.encode("utf-16-le")
    entries[pos + 56:pos + 56 + len(label)] = label
    records = gpt_records(stream, total_size, entries)
    stream.truncate(total_size)
    for offset, data in records:
        stream.seek(offset)
        stream.write(data)
    return dict(first=start // 512, size=tools_size, total_size=total_size)


def copy_region(source, target, count):
    while count:
        data = source.read(min(count, CHUNK))
        if not data:
            raise ValueError("Unexpected end of image")
        target.write(data)
        count -= len(data)


def write_all(stream, data):
    remaining = memoryview(data)
    while remaining:
        written = stream.write(remaining)
        if not written:
            raise OSError("USB write made no progress")
        remaining = remaining[written:]


def grub_config():
    # cmdpath identifies this USB, unlike the stock FAT UUID copied to installed disks.
    return r'''set timeout=10
set default=0
if ! regexp --set=1:usb_disk '^\(([^,]+),gpt12\)' "$cmdpath"; then
  echo "Cannot identify this Flex USB."
  sleep --interruptible 60
  halt
fi
set flex_esp="($usb_disk,gpt12)"
set tools="($usb_disk,gpt13)"
export flex_esp tools
menuentry 'Install ChromeOS Flex' {
  set root="${flex_esp}"
  chainloader /efi/boot/crdybootx64.efi
}
menuentry 'Set up CrOS Kit (after installing Flex)' {
  set root="${tools}"
  linux /boot/vmlinuz-lts modules=loop,squashfs,sd-mod,usb-storage quiet
  initrd /boot/initramfs-lts
}
'''


def build_image(image, live, work):
    with image.open("rb") as source:
        _, parts = installer.read_gpt(source)
        esp_part = parts[12]
        esp_size = (esp_part["last"] - esp_part["first"] + 1) * 512
        source.seek(esp_part["first"] * 512)
        with (work / "esp.img").open("wb") as target:
            copy_region(source, target, esp_size)
    esp = work / "esp.img"
    run("mcopy", "-i", str(esp), "::/efi/boot/crdybootx64.efi", str(work / "stock.efi"))
    stock = (work / "stock.efi").read_bytes()
    start, length = installer.key_section(stock)
    installer.validate_key(stock[start:start + length])
    run("mcopy", "-i", str(esp), "::/efi/boot/bootx64.efi", str(live / "official-bootx64.efi"))
    config = work / "grub.cfg"
    config.write_text(grub_config())
    grub = work / "bootx64.efi"
    run("grub-mkstandalone", "--format=x86_64-efi", f"--output={grub}",
        "--modules=part_gpt fat chain linux normal regexp sleep halt", f"boot/grub/grub.cfg={config}")
    run("mcopy", "-o", "-i", str(esp), str(grub), "::/efi/boot/bootx64.efi")
    payload_size = sum(path.stat().st_size for path in live.rglob("*") if path.is_file())
    tools_size = max(512 * MIB, align(payload_size * 5 // 4 + 32 * MIB))
    fat = work / "tools.img"
    with fat.open("wb") as target:
        target.truncate(tools_size)
    run("mkfs.fat", "-F", "32", "-n", TOOLS_LABEL, str(fat))
    run("mcopy", "-s", "-i", str(fat), *(str(path) for path in sorted(live.iterdir())), "::/")
    with image.open("r+b") as output:
        tools = add_tools_partition(output, tools_size)
        output.seek(esp_part["first"] * 512)
        with esp.open("rb") as source:
            copy_region(source, output, esp_size)
        output.seek(tools["first"] * 512)
        with fat.open("rb") as source:
            copy_region(source, output, tools_size)
        output.flush()
        os.fsync(output.fileno())
        installer.read_gpt(output)
    return tools


def check_usb(path):
    path = path.resolve(strict=True)
    if not stat.S_ISBLK(path.stat().st_mode):
        raise ValueError("--disk must be a whole USB block device")
    devices = json.loads(run("lsblk", "--json", "--paths", "--output",
                             "NAME,TYPE,TRAN,SIZE,MODEL,SERIAL,MAJ:MIN,MOUNTPOINTS", str(path)))["blockdevices"]
    if len(devices) != 1 or devices[0]["type"] != "disk" or devices[0].get("tran") != "usb":
        raise ValueError("--disk must be a whole USB disk, never an internal disk")
    nodes = []

    def visit(node):
        nodes.append(node)
        for child in node.get("children", []):
            visit(child)

    visit(devices[0])
    swaps = {line.split()[0] for line in Path("/proc/swaps").read_text().splitlines()[1:]}
    for node in nodes:
        if any(node.get("mountpoints") or []) or node["name"] in swaps:
            raise ValueError(f"USB is mounted/in use: {node['name']}")
        holders = Path("/sys/dev/block") / node["maj:min"] / "holders"
        if holders.is_dir() and any(holders.iterdir()):
            raise ValueError(f"USB has an active device-mapper/RAID holder: {node['name']}")
    sys_device = Path("/sys/dev/block") / devices[0]["maj:min"]
    size = int((sys_device / "size").read_text()) * 512
    logical_sector = int((sys_device / "queue/logical_block_size").read_text())
    if logical_sector != 512:
        raise ValueError("This USB builder requires a 512-byte logical sector USB")
    try:
        diskseq = int((sys_device / "diskseq").read_text())
    except (OSError, ValueError) as error:
        raise ValueError("USB creation requires Linux 5.15+ diskseq support") from error
    if diskseq <= 0:
        raise ValueError("USB has no valid kernel disk identity")
    return dict(path=str(path), device=path.stat().st_rdev, size=size, diskseq=diskseq,
                serial=devices[0].get("serial") or "", model=devices[0].get("model") or "")


def select_usb():
    if not sys.stdin.isatty():
        raise ValueError("USB selection needs a terminal; specify --disk /dev/DEVICE --erase")
    devices = json.loads(run("lsblk", "--json", "--paths", "--bytes", "--nodeps",
                             "--output", "NAME,TYPE,TRAN,SIZE,MODEL,SERIAL"))["blockdevices"]
    choices = []
    print("USB disks (the selected disk will be erased):", flush=True)
    for device in sorted(devices, key=lambda item: item["name"]):
        if device["type"] != "disk" or device.get("tran") != "usb":
            continue
        description = (f"{device['name']} | {device.get('model') or 'Unknown model'} | "
                       f"{int(device['size']) / 1024**3:.1f} GiB | "
                       f"serial: {device.get('serial') or 'unavailable'}")
        try:
            identity = check_usb(Path(device["name"]))
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            print(f"  - {description} [unavailable: {error}]", flush=True)
            continue
        description = (f"{identity['path']} | {identity['model'] or 'Unknown model'} | "
                       f"{identity['size'] / 1024**3:.1f} GiB | "
                       f"serial: {identity['serial'] or 'unavailable'}")
        choices.append(identity)
        print(f"  {len(choices)}. {description}", flush=True)
    if not choices:
        raise ValueError("No available USB disks; connect an unmounted USB and try again")
    while True:
        answer = input("Select USB number (q to cancel): ").strip()
        if answer.lower() == "q" or not answer:
            raise ValueError("Cancelled; no USB was written")
        for number, identity in enumerate(choices, 1):
            if answer == str(number):
                return identity
        print(f"Enter a number from 1 to {len(choices)}, or q to cancel.", flush=True)


def patched_chunks(source, records):
    offset = 0
    while data := source.read(CHUNK):
        data = bytearray(data)
        for position, replacement in records:
            left, right = max(offset, position), min(offset + len(data), position + len(replacement))
            if left < right:
                data[left - offset:right - offset] = replacement[left - position:right - position]
        yield offset, data
        offset += len(data)


def flash_image(image, expected):
    current = check_usb(Path(expected["path"]))
    if current != expected:
        raise ValueError("USB identity/size changed since preparation; refusing to erase it")
    image_size = image.stat().st_size
    if image_size > current["size"]:
        raise ValueError("USB is too small for the combined image")
    with image.open("rb") as source:
        records = gpt_records(source, current["size"])
        # O_EXCL also refuses devices held by mounts outside this namespace.
        descriptor = os.open(current["path"], os.O_RDWR | os.O_EXCL)
        try:
            if os.fstat(descriptor).st_rdev != current["device"]:
                raise ValueError("USB device changed before writing")
            try:
                diskseq = struct.unpack("Q", fcntl.ioctl(descriptor, 0x80081280, bytes(8)))[0]  # BLKGETDISKSEQ
            except OSError as error:
                raise ValueError("USB creation requires Linux 5.15+ BLKGETDISKSEQ support") from error
            if diskseq != current["diskseq"]:
                raise ValueError("USB device instance changed before writing")
            if struct.unpack("Q", fcntl.ioctl(descriptor, 0x80081272, bytes(8)))[0] != current["size"]:
                raise ValueError("USB size changed before writing")
            with os.fdopen(descriptor, "r+b", buffering=0, closefd=False) as target:
                for phase in ("Writing", "Verifying"):
                    print(f"{phase} {current['path']}", flush=True)
                    source.seek(0)
                    last = time.monotonic()
                    for offset, data in patched_chunks(source, records):
                        target.seek(offset)
                        if phase == "Writing":
                            write_all(target, data)
                        elif target.read(len(data)) != data:
                            raise ValueError(f"USB readback differs at byte {offset}")
                        if time.monotonic() - last >= 5:
                            print(f"  {phase}: {(offset + len(data)) / MIB:.0f} MiB", flush=True)
                            last = time.monotonic()
                    for offset, data in records:
                        start = max(offset, image_size)
                        if start < offset + len(data):
                            data = data[start - offset:]
                            target.seek(start)
                            if phase == "Writing":
                                write_all(target, data)
                            elif target.read(len(data)) != data:
                                raise ValueError("USB backup GPT readback differs")
                    if phase == "Writing":
                        target.flush()
                        os.fsync(target.fileno())
                        # Discard clean block caches so verification reads the USB.
                        fcntl.ioctl(target.fileno(), 0x1261)  # BLKFLSBUF
        finally:
            os.close(descriptor)
    try:
        run("blockdev", "--rereadpt", current["path"])
    except subprocess.CalledProcessError:
        print("USB written and verified; reconnect it to refresh the partition list.")
    print("Ready. Disable Secure Boot and boot this USB in UEFI mode.")


def publish_image(image, destination):
    """Publish a complete image atomically, without replacing an existing file."""
    with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.", suffix=".part",
                                     dir=destination.parent) as output, image.open("rb") as source:
        while data := source.read(CHUNK):
            if data.count(0) == len(data):
                output.seek(len(data), os.SEEK_CUR)
            else:
                output.write(data)
        output.truncate(image.stat().st_size)
        output.flush()
        os.fsync(output.fileno())
        # A sibling temporary file guarantees the same filesystem. link() is an
        # atomic no-replace publication, including if another file appeared meanwhile.
        os.link(output.name, destination)


def locked_media():
    media = json.loads((HERE / "media/media.json").read_text())
    if media["format"] != 1 or media["packages"] != list(usb_live.PACKAGES):
        raise ValueError("Media lock format or requested package list changed; run the update command")
    release = stable_release([media["flex"]])
    installer.require_supported_version(release["version"])
    for item in (release, media["alpine"]):
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("Invalid media SHA256 lock")
    return media


def locked_inputs(media):
    try:
        archive = Path(os.environ["CROS_KIT_FLEX_ARCHIVE"])
        iso = Path(os.environ["CROS_KIT_ALPINE_ISO"])
        repository = Path(os.environ["CROS_KIT_ALPINE_REPO"])
    except KeyError as error:
        raise ValueError("Use nix run path:" + str(HERE) + " to obtain the locked inputs") from error
    for path, item, size_key in ((archive, media["flex"], "zipfilesize"),
                                  (iso, media["alpine"], "size")):
        if path.stat().st_size != item[size_key] or file_hash(path, "sha256") != item["sha256"]:
            raise ValueError(f"Locked input checksum/size mismatch: {path.name}")
    package_dir = repository / "x86_64"
    if file_hash(package_dir / "APKINDEX.tar.gz", "sha256") != media["alpine"]["index_sha256"]:
        raise ValueError("Locked Alpine index checksum mismatch")
    expected = {item["filename"] for item in media["alpine"]["packages"]}
    if {path.name for path in package_dir.glob("*.apk")} != expected:
        raise ValueError("Locked Alpine package set differs")
    for item in media["alpine"]["packages"]:
        path = package_dir / item["filename"]
        if path.stat().st_size != item["size"] or file_hash(path, "sha256") != item["sha256"]:
            raise ValueError(f"Locked Alpine package checksum/size mismatch: {path.name}")
    return archive, iso, repository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--disk", type=Path, help="whole USB disk to erase; omit to select interactively")
    target.add_argument("--image", type=Path, help="create a new image file (filesystem must support hard links)")
    parser.add_argument("--erase", action="store_true", help="required to overwrite --disk")
    parser.add_argument("--dry-run", action="store_true", help="show locked versions and target without building/writing")
    parser.add_argument("--write-image", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--expected-target", help=argparse.SUPPRESS)
    parser.add_argument("--tool-path", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.disk and not args.erase and not args.dry_run:
            raise ValueError("--disk erases the USB; pass --erase explicitly")
        if args.write_image:
            if not args.disk or not args.erase or not args.expected_target or args.dry_run:
                raise ValueError("Invalid internal write request")
            if args.tool_path:
                os.environ["PATH"] = args.tool_path
            expected = json.loads(args.expected_target)
            if Path(expected["path"]) != args.disk.resolve():
                raise ValueError("Write target differs from prepared USB")
            flash_image(args.write_image, expected)
            return
        expected = check_usb(args.disk) if args.disk else None
        if args.disk is None and args.image is None:
            expected = select_usb()
            args.disk = Path(expected["path"])
            if not args.erase and not args.dry_run:
                print(f"Erase ALL data on {expected['path']} ({expected['model']}, "
                      f"{expected['size'] / 1024**3:.1f} GiB, serial: {expected['serial']})?", flush=True)
                if input(f"Type {expected['path']} to confirm: ").strip() != expected["path"]:
                    raise ValueError("Cancelled; no USB was written")
                args.erase = True
        if args.image and args.image.exists():
            raise ValueError("Output image already exists; choose a new path")
        media = locked_media()
        release, alpine = media["flex"], media["alpine"]
        print(f"Locked Flex: {release['chrome_version']} / {release['version']}", flush=True)
        print(f"CrOS Kit tools: Alpine {alpine['version']} (offline)", flush=True)
        if expected:
            print(f"Target: {expected['path']} | {expected['model']} | "
                  f"{expected['size'] / 1024**3:.1f} GiB | serial: {expected['serial']}", flush=True)
        else:
            print(f"Target: {args.image}", flush=True)
        if args.dry_run:
            return
        tools = ("apk", "bsdtar", "grub-mkstandalone", "mcopy", "mkfs.fat")
        missing = [tool for tool in tools if not shutil.which(tool)]
        if missing:
            raise ValueError("Missing tools: " + ", ".join(missing) + "; use nix run path:" + str(HERE))
        try:
            payload = Path(os.environ["CROS_KIT_PAYLOAD"])
        except KeyError as error:
            raise ValueError("Use nix run path:" + str(HERE) + " to build the installer payload") from error
        archive, iso, repository = locked_inputs(media)
        # Recovery images exceed many systems' /tmp quotas or tmpfs capacity.
        staging = (args.image.resolve().parent if args.image else
                   Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "cros-kit")
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cros-kit-usb-", dir=staging) as directory:
            work = Path(directory)
            image = work / "cros-kit.img"
            print("Preparing official Flex image", flush=True)
            extract_flex(archive, release, image)
            live = work / "live"
            usb_live.prepare_live(iso, live, payload, repository)
            (live / "cros-kit.json").write_text(json.dumps(media, indent=2) + "\n")
            build_image(image, live, work)
            if args.image:
                publish_image(image, args.image)
                print(f"Image ready: {args.image}", flush=True)
            else:
                if os.geteuid() == 0:
                    flash_image(image, expected)
                else:
                    print("Image prepared; requesting root only to write and verify the USB.", flush=True)
                    subprocess.run(["sudo", "env", "PYTHONPATH=" + str(HERE), sys.executable,
                                    "-P", "-m", "cros_kit.make_usb", "--disk", expected["path"],
                                    "--erase", "--write-image", str(image), "--expected-target", json.dumps(expected),
                                    "--tool-path", os.environ["PATH"]], check=True)
    except (EOFError, KeyboardInterrupt):
        parser.exit(1, "Cancelled; no further work requested.\n")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        parser.exit(1, f"ERROR: {detail}\n")


if __name__ == "__main__":
    main()
