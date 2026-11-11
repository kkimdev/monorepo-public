"""Exercise download failure, checksum verification and temporary cleanup."""

import os
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
RESOLVER_URL = "https://api.github.com/repos/kkimdev/monorepo-public/commits/main"
RESOLVED_REVISION = "a" * 40
SOURCE_URL = "https://raw.githubusercontent.com/kkimdev/monorepo-public"


class BootstrapTests(unittest.TestCase):
    def invoke(self, failure="", revision=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / "bin"
            commands.mkdir()
            fixtures = root / "fixtures"
            fixtures.mkdir()
            for name in ("install.py", "compatibility.conf", "crdyboot-debug.efi", "crdyboot-debug.efi.sha256"):
                (fixtures / name).write_bytes((HERE / name).read_bytes())
            script = b"#!/bin/sh\nexec /sbin/init\n"
            (fixtures / "rw-init.sh").write_bytes(script)
            (fixtures / "rw-init.sh.sha256").write_text(hashlib.sha256(script).hexdigest() + "  rw-init.sh\n")
            library = b"ELF fixture library"
            (fixtures / "rootdev-compat.so").write_bytes(library)
            (fixtures / "rootdev-compat.so.sha256").write_text(hashlib.sha256(library).hexdigest() + "  rootdev-compat.so\n")
            (commands / "curl").write_text("""#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith('https://'))
with pathlib.Path(os.environ['URL_LOG']).open('a') as log:
    log.write(url + '\\n')
destination = pathlib.Path(args[args.index('-o') + 1])
if url == 'https://api.github.com/repos/kkimdev/monorepo-public/commits/main':
    if os.environ.get('FAILURE') == 'resolve':
        sys.exit(22)
    revision = 'invalid' if os.environ.get('FAILURE') == 'revision' else 'a' * 40
    destination.write_text(json.dumps({'sha': revision}))
    sys.exit(0)
assert url.startswith('https://raw.githubusercontent.com/kkimdev/monorepo-public/')
name = url.rsplit('/', 1)[1]
if os.environ.get('FAILURE') == 'download':
    sys.exit(22)
shutil.copyfile(pathlib.Path(os.environ['FIXTURES']) / name, destination)
if name.endswith('.efi') and os.environ.get('FAILURE') == 'checksum':
    destination.write_bytes(b'corrupted download')
if name == 'rw-init.sh' and os.environ.get('FAILURE') == 'rw-checksum':
    destination.write_bytes(b'corrupted init script')
if name == 'rootdev-compat.so' and os.environ.get('FAILURE') == 'rootdev-checksum':
    destination.write_bytes(b'corrupted library')
""")
            (commands / "sudo").write_text("""#!/usr/bin/env python3
import os, pathlib, sys
script = pathlib.Path(sys.argv[2])
assert script.is_file()
assert (script.parent / 'crdyboot-debug.efi').is_file()
assert (script.parent / 'rw-init.sh').is_file()
assert (script.parent / 'rw-init.sh.sha256').is_file()
assert (script.parent / 'rootdev-compat.so').is_file()
assert (script.parent / 'rootdev-compat.so.sha256').is_file()
assert (script.parent / 'compatibility.conf').is_file()
pathlib.Path(os.environ['CALL_LOG']).write_text(str(script.parent))
sys.exit(7 if os.environ.get('FAILURE') == 'installer' else 0)
""")
            for command in commands.iterdir():
                command.chmod(0o755)
            log = root / "called"
            url_log = root / "urls"
            env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
                       FIXTURES=str(fixtures), CALL_LOG=str(log), FAILURE=failure,
                       URL_LOG=str(url_log), TMPDIR=str(root))
            env.pop("CROS_KIT_REV", None)
            if revision is not None:
                env["CROS_KIT_REV"] = revision
            result = subprocess.run(["bash", str(HERE / "install.bash"),
                                     "install", "--disk", "/dev/FAKE"],
                                    env=env, capture_output=True, text=True)
            called = log.exists()
            self.assertEqual(list(root.glob("cros-kit.*")), [], "Temporary files leaked")
            urls = url_log.read_text().splitlines() if url_log.exists() else []
            return result, called, urls

    def test_verified_download_invokes_installer_and_cleans_up(self):
        result, called, urls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(called)
        self.assertEqual(urls.count(RESOLVER_URL), 1)
        self.assertEqual(urls[0], RESOLVER_URL)
        self.assert_payload_urls(urls[1:], RESOLVED_REVISION)
        self.assertIn(RESOLVED_REVISION, result.stdout)

    def assert_payload_urls(self, urls, revision):
        names = ("install.py", "compatibility.conf", "crdyboot-debug.efi",
                 "crdyboot-debug.efi.sha256", "rw-init.sh", "rw-init.sh.sha256",
                 "rootdev-compat.so", "rootdev-compat.so.sha256")
        self.assertEqual(urls, [f"{SOURCE_URL}/{revision}/chromeos-dev-setup/cros-kit/{name}"
                                for name in names])

    def test_explicit_revision_skips_resolution(self):
        revision = "b" * 40
        result, called, urls = self.invoke(revision=revision)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(called)
        self.assert_payload_urls(urls, revision)
        self.assertIn(revision, result.stdout)

    def test_invalid_explicit_revision_stops_before_download(self):
        result, called, urls = self.invoke(revision="main")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(called)
        self.assertEqual(urls, [])

    def test_resolver_failure_stops_before_payload_download(self):
        result, called, urls = self.invoke("resolve")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(called)
        self.assertEqual(urls, [RESOLVER_URL])

    def test_invalid_resolved_revision_stops_before_payload_download(self):
        result, called, urls = self.invoke("revision")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(called)
        self.assertEqual(urls, [RESOLVER_URL])

    def test_download_failure_does_not_invoke_installer(self):
        result, called, _ = self.invoke("download")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(called)

    def test_checksum_failure_does_not_invoke_installer(self):
        result, called, _ = self.invoke("checksum")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(called)

    def test_rw_script_checksum_failure_does_not_invoke_installer(self):
        result, called, _ = self.invoke("rw-checksum")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(called)

    def test_rootdev_checksum_failure_does_not_invoke_installer(self):
        result, called, _ = self.invoke("rootdev-checksum")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(called)

    def test_installer_failure_propagates_and_cleans_up(self):
        result, called, _ = self.invoke("installer")
        self.assertEqual(result.returncode, 7)
        self.assertTrue(called)


if __name__ == "__main__":
    unittest.main()
