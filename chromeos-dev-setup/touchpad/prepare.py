#!/usr/bin/env python3
"""Prepare or refresh an unpacked Secure Shell dev fork in ChromeOS My files.

Upstream documents the development extension ID and crosh API access:
https://chromium.googlesource.com/apps/libapps/+/8fe95d29adf17d304e36faf70e441c304b376fb2/nassh/docs/hack.md
"""

import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request
import zipfile


# Immutable official CRX blob: avoid combining a latest-version URL with a pinned hash.
CRX_URL = (
    "https://clients2.googleusercontent.com/crx/blobs/"
    "ARGaeZI7n6PriApHKC4yh8qtuxob4TY6Ul83U3lnh_nwPeImNmtnHlk2IWoTmkEsPkea-Eucv_7GaukcaVh-xXEgukxdjjMvx9j_nNZTt9W1hxAmzUqVUaO0pp7ubNec2CNOAMZSmuU-DPjBZE-0hC3i3DCvun4IMmW3iw/"
    "ALGKCNFJNAJFHGIMADIMBJHMPAEOHHLN_0_82_9828_27275.crx"
)
CRX_SHA256 = "6dabb72b0166c0187fbd86136cbf81d79c2814d8bc2ed36934ba90ddabe3431e"
# Public development key from the pinned upstream nassh manifest (not a secret).
DEVELOPMENT_KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAl8sdzpPbCg8pDh/1zrwU17WdrQROlsJsuxGA0RkMX0T0+b/Tarh4YcSA5NDhAqVO3hA8acmYnjkCCOMpP0roceVLBUOag9WxhpXak4A0ZPrwZi2bLPjx+gxkccyAp8bXwskSvjaL+vF/+zN8PT9yDz/Np7WJmfakksDPAZSoCnbykVbaEtpG8qbbUPgmjndrHkgV9cMjpNLAd+GnlARyQ/DceBPtA27z06N/nV+hS7TRIQJTx7XwH6rGKSJ1gn61/wtBWqZQyL3PA6997++55GYtWVB4pmLPXa0vog3GF2wFYf3geD9v2jfP4t9PhPZy9zESr6fP4nqd1HXd6pzamwIDAQAB"
NATIVE_FOLDER = Path("/mnt/chromeos/MyFiles/cros-touchpad")
SOURCE = Path(__file__).resolve().parent
ASSETS = ("apply.mjs", "startup.js", "page.mjs", "page.html")


def build_extension(crx, destination):
    """Build into a new directory; leave a loaded extension's files untouched."""
    if hashlib.sha256(crx).hexdigest() != CRX_SHA256:
        raise ValueError("Secure Shell download checksum mismatch; nothing was installed.")
    with zipfile.ZipFile(io.BytesIO(crx)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        background = manifest["background"]
        if background != {"service_worker": "js/nassh_background_main.js", "type": "module"}:
            raise ValueError("Unexpected upstream background; review the pinned extension.")
        destination.mkdir()
        archive.extractall(destination)
    manifest.update(
        key=DEVELOPMENT_KEY,
        name="Touchpad startup (Secure Shell dev preview)",
        description="Local touchpad startup helper based on Secure Shell dev.",
        author="Local touchpad preview; upstream ChromiumOS Authors",
    )
    manifest.pop("update_url", None)
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    background_file = destination / background["service_worker"]
    with background_file.open("a") as output:
        output.write("\n// Local touchpad startup addition.\nimport '../touchpad/startup.js';\n")
    assets = destination / "touchpad"
    assets.mkdir()
    for name in ASSETS:
        shutil.copyfile(SOURCE / name, assets / name)


def installation_instructions(updated):
    if updated:
        print("chrome://extensions → Touchpad startup → Reload")
    else:
        print("chrome://extensions → Developer mode → Load unpacked → My files/cros-touchpad/extension")
    print("Keep this folder so Chrome can load it after reboot.")


def prepare(native_folder=NATIVE_FOLDER):
    if not native_folder.is_dir():
        raise ValueError("In ChromeOS Files, create My files/cros-touchpad, right-click it → Share with Linux, then rerun this command.")
    destination = native_folder / "extension"
    updating = destination.exists()
    if updating:
        manifest = destination / "manifest.json"
        if (not manifest.is_file()
                or json.loads(manifest.read_text()).get("key") != DEVELOPMENT_KEY
                or not (destination / "touchpad").is_dir()):
            raise ValueError(f"{destination} already exists and is not this preview; nothing was replaced.")
    for name in ASSETS:
        if not (SOURCE / name).is_file():
            raise ValueError(f"Missing {SOURCE / name}; update the setup checkout.")
    print("Downloading the pinned official Secure Shell development build…", flush=True)
    with urllib.request.urlopen(CRX_URL, timeout=60) as response:
        crx = response.read()
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=native_folder) as temporary:
        staged = Path(temporary) / "extension"
        build_extension(crx, staged)
        previous = Path(temporary) / "previous"
        try:
            if updating:
                destination.rename(previous)
            staged.rename(destination)
        except BaseException:
            if previous.exists():
                previous.rename(destination)
            raise
    print("Updated My files/cros-touchpad/extension." if updating else
          "Prepared My files/cros-touchpad/extension; Chrome installation is still required.")
    installation_instructions(updating)
    return destination


if __name__ == "__main__":
    if len(sys.argv) != 1:
        sys.exit("Usage: python3 touchpad/prepare.py")
    try:
        prepare()
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        sys.exit(f"ERROR: {error}")
