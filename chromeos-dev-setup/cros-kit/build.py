#!/usr/bin/env python3
"""Validate and package the EFI produced by efi.nix (entirely offline).

For a maintainer build, run `nix build path:.#efi` in this directory.
Nix owns source fetching, dependency vendoring, patching and compilation.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess


TARGET = "x86_64-unknown-uefi"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_template(data):
    """Refuse malformed, timestamped or already personalized PE templates."""
    try:
        if data[:2] != b"MZ":
            raise ValueError("Template is not a PE image")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ValueError("Template is not a PE image")
        machine, count, timestamp = struct.unpack_from("<HHI", data, pe + 4)
        optional_size = struct.unpack_from("<H", data, pe + 20)[0]
        if machine != 0x8664 or struct.unpack_from("<H", data, pe + 24)[0] != 0x20B:
            raise ValueError("Template must be x86_64 PE32+")
        if timestamp:
            raise ValueError("Template PE timestamp must be zero")
        table = pe + 24 + optional_size
        if table + count * 40 > len(data):
            raise ValueError("Truncated PE section table")
        keys = []
        for index in range(count):
            offset = table + index * 40
            if data[offset:offset + 8].rstrip(b"\0") == b".vbpubk":
                size, _, raw_size, pointer = struct.unpack_from("<IIII", data, offset + 8)
                if pointer + raw_size > len(data):
                    raise ValueError("Truncated .vbpubk section")
                keys.append((size, raw_size, data[pointer:pointer + raw_size]))
        if len(keys) != 1 or keys[0][:2] != (8192, 8192) or any(keys[0][2]):
            raise ValueError("Template must contain one zero-filled 8192-byte .vbpubk section")
    except struct.error as error:
        raise ValueError("Truncated PE image") from error


def notices(source, destination):
    metadata = json.loads(run(
        "cargo", "metadata", "--frozen", "--offline", "--format-version=1",
        "--filter-platform", TARGET, cwd=source, capture_output=True).stdout)
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {p["id"]: p for p in metadata["resolve"]["nodes"]}
    pending = [p["id"] for p in metadata["packages"] if p["name"] == "crdyboot"]
    linked = set()
    while pending:
        package = pending.pop()
        if package in linked:
            continue
        linked.add(package)
        pending.extend(d["pkg"] for d in nodes[package]["deps"]
                       if any(k["kind"] is None for k in d["dep_kinds"]))
    sections = ["Copyright and license notices for crdyboot-debug.efi\n"]
    for name, folder in [("crdyboot", source),
                         ("vboot_reference", source / "third_party/vboot_reference")]:
        sections.append(f"\n=== {name} ===\n" + (folder / "LICENSE").read_text())
    for package in sorted(linked):
        item = packages[package]
        if not item["source"]:
            continue  # Workspace components share crdyboot's BSD license.
        root = Path(item["manifest_path"]).parent
        files = sorted(p for p in root.iterdir() if p.is_file() and
                       p.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")))
        if not files:
            raise ValueError(f"License text missing for {item['name']}")
        sections.append(f"\n=== {item['name']} {item['version']} ({item['license']}) ===\n")
        sections.extend(f"\n--- {p.name} ---\n" + p.read_text() for p in files)
    sysroot = Path(run("rustc", "--print", "sysroot",
                       capture_output=True, text=True).stdout.strip())
    # This includes notices for core, alloc and compiler builtins.
    rust_docs = sysroot / "share/doc/rust"
    sections.append("\n=== Rust standard library copyright notices (HTML) ===\n" +
                    (rust_docs / "COPYRIGHT-library.html").read_text())
    for name in ["MIT.txt", "Apache-2.0.txt", "LLVM-exception.txt"]:
        sections.append(f"\n--- Rust {name} ---\n" + (rust_docs / "licenses" / name).read_text())
    destination.write_text("\n".join(sections))


def tool_version(name):
    lines = run(name, "--version", capture_output=True, text=True).stdout.splitlines()
    # llvm-ar starts with a generic project banner; the version is on line two.
    return next((line.strip() for line in lines if "LLVM version " in line), lines[0])


def package(source, artifact, output, provenance):
    data = artifact.read_bytes()
    validate_template(data)
    output.mkdir(parents=True, exist_ok=True)
    notices(source, output / "THIRD_PARTY_NOTICES.txt")
    destination = output / "crdyboot-debug.efi"
    shutil.copyfile(artifact, destination)
    destination.with_suffix(".efi.sha256").write_text(f"{digest(destination)}  {destination.name}\n")
    info = {
        **provenance,
        "artifact": destination.name,
        "bytes": len(data),
        "sha256": digest(destination),
        "cargo_lock_sha256": digest(source / "Cargo.lock"),
        "tools": {name: tool_version(name) for name in ("rustc", "cargo", "clang", "llvm-ar")},
        "key_section": "8192 zero bytes; install.py supplies the target's stock key",
    }
    (output / "build-info.json").write_text(json.dumps(info, indent=2, sort_keys=True) + "\n")
    print(f"Built {destination}: {digest(destination)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()
    package(args.source, args.artifact, args.output, json.loads(args.provenance.read_text()))


if __name__ == "__main__":
    main()
