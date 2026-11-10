"""Run the README install command against a local Git fixture, without network."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("manifest.json", "apply.mjs", "startup.js", "page.mjs", "page.html")


class TouchpadCheckout(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="touchpad-checkout-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.shared_root = self.directory / "shared ChromeOS My files"
        self.shared_root.mkdir()
        self.native = self.shared_root / "cros-touchpad"
        self.linux = self.directory / "empty Linux folder"
        self.linux.mkdir()
        self.repository = self.directory / "fixture repository"
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1",
                        TMPDIR=str(self.linux))
        self.git("init", "--initial-branch=main", str(self.repository))
        self.source = self.repository / "chromeos-dev-setup/touchpad"
        self.source.mkdir(parents=True)
        for name in ASSETS:
            shutil.copyfile(ROOT / "touchpad" / name, self.source / name)
        (self.source.parent / "setup.bash").write_text("# unrelated installer\n")
        (self.repository / "unrelated").mkdir()
        (self.repository / "unrelated/private.txt").write_text("excluded fixture\n")
        self.commit()

    def git(self, *args):
        return subprocess.run(["git", *args], env=self.env, check=True,
                              capture_output=True, text=True)

    def commit(self):
        self.git("-C", str(self.repository), "add", ".")
        self.git("-C", str(self.repository), "-c", "user.name=Touchpad Test",
                 "-c", "user.email=touchpad@example.test", "commit", "-m", "fixture")

    def install(self, remote=None):
        section = (ROOT / "README.md").read_text().split(
            "### Touchpad initial touch delay", 1)[1]
        block = re.search(r"^   ```bash\n(.*?)^   ```", section, re.M | re.S)
        self.assertIsNotNone(block, "Touchpad section needs its own bash command")
        command = textwrap.dedent(block.group(1))
        command = command.replace("/mnt/chromeos/MyFiles", str(self.shared_root))
        command = command.replace("https://github.com/kkimdev/monorepo-public.git",
                                  remote or self.repository.as_uri())
        return subprocess.run(["bash", "-c", command], cwd=self.linux, env=self.env,
                              capture_output=True, text=True)

    def assert_installed(self):
        destination = self.native / "extension"
        self.assertEqual(sorted(p.name for p in self.native.iterdir()), ["extension"])
        self.assertEqual(list(destination.rglob(".git")), [])
        for name in ASSETS:
            self.assertEqual((destination / "chromeos-dev-setup/touchpad" / name).read_bytes(),
                             (self.source / name).read_bytes())
        self.assertFalse((destination / "chromeos-dev-setup/setup.bash").exists())
        self.assertFalse((destination / "unrelated").exists())
        self.assertEqual(list(self.linux.iterdir()), [])

    def test_command_replaces_old_install_and_reloads_latest_source_without_linux_files(self):
        old = self.native / "extension"
        old.mkdir(parents=True)
        (old / "manifest.json").write_text('{"name": "Old Secure Shell preview"}')
        (old / "js").mkdir()
        (old / "js/nassh_background_main.js").write_text("// Previous SSH extension\n")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed()
        self.assertFalse((old / "manifest.json").exists())
        self.assertFalse((old / "js").exists())

        with (self.source / "apply.mjs").open("a") as source:
            source.write("\n// Updated remote fixture\n")
        self.commit()
        (old / "stale-file.txt").write_text("remove on reinstall")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed()
        self.assertFalse((old / "stale-file.txt").exists())

    def test_first_install_creates_target_folder(self):
        self.assertFalse(self.native.exists())
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed()

    def test_missing_shared_folder_aborts_before_removing_relative_linux_extension(self):
        self.shared_root.rmdir()
        protected = self.linux / "extension"
        protected.mkdir()
        sentinel = protected / "personal.txt"
        sentinel.write_text("keep this")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), "keep this")
        self.assertEqual(list(self.linux.iterdir()), [protected])
        self.assertFalse(self.shared_root.exists())
        self.assertFalse(self.native.exists())

    def test_failed_clone_cleans_temporary_metadata(self):
        result = self.install((self.directory / "missing repository").as_uri())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.linux.iterdir()), [])
        self.assertEqual(list(self.native.rglob(".git")), [])


if __name__ == "__main__":
    unittest.main()
