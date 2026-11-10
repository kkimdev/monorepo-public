"""Check published-snapshot updates with disposable, local Git repositories.

Git operations are real; root commands and the installed setup are harmless
stubs. No network or host configuration changes are involved.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


UPDATE = Path(__file__).resolve().parents[1] / "update.bash"
REAL_GIT = shutil.which("git")

STUB = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
with Path(os.environ["UPDATE_TEST_AUDIT"]).open("a") as log:
    log.write(json.dumps([name, *sys.argv[1:]]) + "\n")
if name == "git":
    os.execv(os.environ["UPDATE_TEST_REAL_GIT"], ["git", *sys.argv[1:]])
if name == "sudo":
    sys.exit(0)
raise SystemExit("Unexpected stub command: " + name)
'''


@unittest.skipUnless(REAL_GIT, "Git is required for local update tests")
class PublishedSnapshotUpdates(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cros update test ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / "origin remote.git"
        self.publisher = self.root / "publisher checkout"
        self.checkout = self.root / "employee checkout"
        self.audit = self.root / "calls.jsonl"
        self.result_file = self.root / "setup-result"
        self.environment = os.environ.copy()
        self.environment.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Setup Test",
            "GIT_AUTHOR_EMAIL": "setup@example.test",
            "GIT_COMMITTER_NAME": "Setup Test",
            "GIT_COMMITTER_EMAIL": "setup@example.test",
            "GIT_TERMINAL_PROMPT": "0",
            "UPDATE_TEST_AUDIT": str(self.audit),
            "UPDATE_TEST_REAL_GIT": REAL_GIT,
            "UPDATE_TEST_RESULT": str(self.result_file),
        })
        self.environment.pop("BASH_ENV", None)
        self.environment.pop("ENV", None)
        self.git("init", "--bare", "--initial-branch=main", str(self.remote))
        self.git("clone", str(self.remote), str(self.publisher))
        (self.publisher / "chromeos-dev-setup").mkdir()
        self.write_setup(self.publisher, "old")
        (self.publisher / "README.md").write_text("initial snapshot\n")
        self.commit(self.publisher, "Initial published snapshot")
        self.git("-C", str(self.publisher), "push", "origin", "main")
        self.git("clone", str(self.remote), str(self.checkout))
        self.setup = self.checkout / "chromeos-dev-setup" / "setup.bash"
        self.bin = self.root / "stub commands"
        self.bin.mkdir()
        driver = self.bin / "driver"
        driver.write_text(STUB)
        driver.chmod(0o755)
        for name in ("git", "sudo"):
            (self.bin / name).symlink_to(driver)
        self.environment["PATH"] = str(self.bin) + os.pathsep + os.environ["PATH"]

    def git(self, *args, check=True):
        return subprocess.run(
            [REAL_GIT, *args], env=self.environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check,
            timeout=15,
        )

    def commit(self, checkout, message):
        self.git("-C", str(checkout), "add", "--all")
        self.git("-C", str(checkout), "commit", "-m", message)

    def write_setup(self, checkout, version):
        (checkout / "chromeos-dev-setup" / "setup.bash").write_text(
            "#!/usr/bin/env bash\n"
            f"printf '{version}:%s' \"$#\" > \"$UPDATE_TEST_RESULT\"\n"
            'cat -- "$(dirname -- "$0")/flake.lock" > "${UPDATE_TEST_RESULT}.lock"\n'
        )
        (checkout / "chromeos-dev-setup" / "flake.lock").write_text(json.dumps({"snapshot": version}) + "\n")

    def publish_updated_snapshot(self):
        self.write_setup(self.publisher, "new")
        self.commit(self.publisher, "Publish updated setup")
        self.git("-C", str(self.publisher), "push", "origin", "main")

    def run_update(self, setup=None):
        return subprocess.run(
            ["bash", str(UPDATE), str(setup or self.setup)],
            env=self.environment, cwd=self.root, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        )

    def calls(self):
        return [json.loads(line) for line in self.audit.read_text().splitlines()] if self.audit.exists() else []

    def assert_no_system_or_setup(self):
        self.assertFalse(any(call[0] == "sudo" for call in self.calls()))
        self.assertFalse(self.result_file.exists())
        self.assertFalse(self.result_file.with_suffix(".lock").exists())

    def assert_rejected_before_pull(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any("pull" in call or "fetch" in call for call in self.calls()))
        self.assert_no_system_or_setup()

    def test_fast_forward_runs_new_setup_without_update_flag_with_spaces_in_paths(self):
        self.publish_updated_snapshot()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.result_file.read_text(), "new:0")
        self.assertEqual(json.loads(self.result_file.with_suffix(".lock").read_text()), {"snapshot": "new"})
        self.assertEqual(self.git("-C", str(self.checkout), "status", "--porcelain").stdout, "")
        calls = self.calls()
        pulls = [call for call in calls if call[0] == "git" and "pull" in call]
        self.assertEqual(pulls, [["git", "-C", str(self.checkout), "pull", "--ff-only", "origin", "main"]])
        self.assertEqual([call for call in calls if call[0] == "sudo"], [
            ["sudo", "apt-get", "update"],
            ["sudo", "apt-get", "full-upgrade", "-y"],
            ["sudo", "apt-get", "autoremove", "-y"],
            ["sudo", "determinate-nixd", "upgrade"],
        ])
        self.assertLess(calls.index(pulls[0]), next(i for i, call in enumerate(calls) if call[0] == "sudo"))

    def test_sparse_shallow_clone_updates_and_supports_local_contributions(self):
        unrelated = self.publisher / "unrelated"
        unrelated.mkdir()
        (unrelated / "data.txt").write_text("outside the employee setup\n")
        self.commit(self.publisher, "Add unrelated project")
        self.git("-C", str(self.publisher), "push", "origin", "main")
        self.git("--git-dir", str(self.remote), "config", "uploadpack.allowFilter", "true")
        self.checkout = self.root / "employee sparse checkout"
        self.git(
            "clone", "--filter=blob:none", "--sparse", "--depth=1",
            self.remote.as_uri(), str(self.checkout),
        )
        self.git("-C", str(self.checkout), "sparse-checkout", "set", "chromeos-dev-setup")
        self.setup = self.checkout / "chromeos-dev-setup" / "setup.bash"
        self.assertEqual(self.git("-C", str(self.checkout), "rev-parse", "--is-shallow-repository").stdout.strip(), "true")
        self.assertFalse((self.checkout / "unrelated").exists())

        self.publish_updated_snapshot()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.result_file.read_text(), "new:0")
        self.assertEqual(json.loads((self.setup.parent / "flake.lock").read_text()), {"snapshot": "new"})
        self.assertEqual(self.git("-C", str(self.checkout), "sparse-checkout", "list").stdout.strip(), "chromeos-dev-setup")
        self.assertEqual(self.git("-C", str(self.checkout), "config", "--get", "core.sparseCheckout").stdout.strip(), "true")
        self.assertFalse((self.checkout / "unrelated").exists())

        self.git("-C", str(self.checkout), "switch", "-c", "employee/contribution")
        (self.setup.parent / "contribution.txt").write_text("employee contribution\n")
        self.commit(self.checkout, "Employee contribution")
        self.assertEqual(self.git("-C", str(self.checkout), "branch", "--show-current").stdout.strip(), "employee/contribution")
        self.assertEqual(self.git("-C", str(self.checkout), "log", "-1", "--format=%s").stdout.strip(), "Employee contribution")
        self.assertEqual(self.git("-C", str(self.checkout), "status", "--porcelain").stdout, "")
        self.assertEqual(self.git("--git-dir", str(self.remote), "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.strip(), "main")

    def test_dirty_tracked_file_anywhere_in_repo_is_refused_before_pull(self):
        (self.checkout / "README.md").write_text("employee work\n")
        result = self.run_update()
        self.assertIn("clean repository", result.stderr)
        self.assert_rejected_before_pull(result)
        self.assertEqual((self.checkout / "README.md").read_text(), "employee work\n")

    def test_untracked_file_anywhere_in_repo_is_refused_before_pull(self):
        untracked = self.checkout / "employee notes.txt"
        untracked.write_text("keep this\n")
        result = self.run_update()
        self.assertIn("clean repository", result.stderr)
        self.assert_rejected_before_pull(result)
        self.assertEqual(untracked.read_text(), "keep this\n")

    def test_feature_branch_is_refused_before_pull(self):
        self.git("-C", str(self.checkout), "checkout", "-b", "employee-work")
        result = self.run_update()
        self.assertIn("main branch", result.stderr)
        self.assert_rejected_before_pull(result)

    def test_detached_head_is_refused_before_pull(self):
        self.git("-C", str(self.checkout), "checkout", "--detach")
        result = self.run_update()
        self.assertIn("detached HEAD", result.stderr)
        self.assert_rejected_before_pull(result)

    def test_fetch_failure_prevents_system_and_setup_work(self):
        self.git("-C", str(self.checkout), "remote", "set-url", "origin", str(self.root / "missing remote.git"))
        before = self.git("-C", str(self.checkout), "rev-parse", "HEAD").stdout
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any("pull" in call for call in self.calls()))
        self.assertEqual(self.git("-C", str(self.checkout), "rev-parse", "HEAD").stdout, before)
        self.assert_no_system_or_setup()

    def test_divergent_main_prevents_system_and_setup_work(self):
        self.publish_updated_snapshot()
        (self.checkout / "local.txt").write_text("local committed work\n")
        self.commit(self.checkout, "Local main change")
        before = self.git("-C", str(self.checkout), "rev-parse", "HEAD").stdout
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git("-C", str(self.checkout), "rev-parse", "HEAD").stdout, before)
        self.assertEqual((self.checkout / "local.txt").read_text(), "local committed work\n")
        self.assert_no_system_or_setup()


if __name__ == "__main__":
    unittest.main()
