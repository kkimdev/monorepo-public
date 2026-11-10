"""Exercise setup's failure boundaries with fake tools and a disposable home.

No Nix build, root command, activation, or host configuration change is real.
Run with: python3 -m unittest discover -s chromeos-dev-setup/tests -v
"""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


SETUP = Path(__file__).resolve().parents[1] / "setup.bash"

# The only shell builtin we replace is the Crostini character-device check.
# This lets these tests run outside ChromeOS without creating a real device.
RUNNER = r"""
function [ {
  if [[ "$#" == 3 && "$1" == -c && "$2" == /dev/wl0 && "$3" == ']' ]]; then
    return 0
  fi
  builtin [ "$@"
}
export -f '['
exec bash "$@"
"""


STUB = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
audit = Path(os.environ["SETUP_TEST_AUDIT"])
with (audit / "calls.jsonl").open("a") as log:
    log.write(json.dumps([name, *args]) + "\n")

if os.environ.get("SETUP_TEST_ALLOW_PROFILE_CHECKS") == "1":
    if name in {"node", "npm", "npx", "earlyoom", "cloudflared", "systemctl"}:
        sys.exit(0)
    if name == "process-compose":
        print("simulated optional catalog diagnostic", file=sys.stderr)
        sys.exit(0)

if name == "sudo":
    # Never execute the command: root writes are outside this test's scope.
    if args[:3] == ["btrfs", "subvolume", "show"]:
        print("Subvolume ID: 256")
    if "tee" in args:
        sys.stdin.read()
    sys.exit(0)
if name in {"curl", "systemctl", "apt", "apt-get"}:
    print("Unexpected direct external/system action: " + name, file=sys.stderr)
    sys.exit(95)
if name == "uname":
    print("x86_64")
    sys.exit(0)
if name == "findmnt":
    print("btrfs" if "FSTYPE" in args else "/dev/test")
    sys.exit(0)
if name == "btrfs":
    sys.exit(0)
if name == "dpkg":
    print("/usr/lib/test/gtk-3.0/3.0.0/immodules/im-cros-gtk3.so")
    print("/usr/lib/test/gtk-4.0/4.0.0/immodules/libim-cros-gtk4.so")
    print("/usr/lib/test/qt5/plugins/platforminputcontexts/libcrosplatforminputcontextplugin.so")
    sys.exit(0)
if name == "find":
    if any("immodules.cache" in arg for arg in args):
        print(audit / "immodules.cache")
    elif any("gtk-4.0" in arg for arg in args):
        print("/usr/lib/test/gtk-4.0")
    else:
        print("Unexpected find: " + repr(args), file=sys.stderr)
        sys.exit(96)
    sys.exit(0)
if name == "nix":
    if "flake" in args and "update" in args:
        try:
            flake = args[args.index("--flake") + 1]
        except (ValueError, IndexError):
            print("Expected an explicit staged flake for update", file=sys.stderr)
            sys.exit(97)
        if not flake.startswith("path:"):
            print("Expected a staged path flake for update", file=sys.stderr)
            sys.exit(97)
        stage = Path(flake[5:])
        lock = stage / "flake.lock"
        updated = json.loads(lock.read_text())
        updated["setupTestUpdate"] = True
        lock.write_text(json.dumps(updated) + "\n")
        shutil.copyfile(lock, audit / "updated-staged.lock")
        if os.environ.get("SETUP_TEST_FAIL_UPDATE") == "1":
            print("simulated flake update failure", file=sys.stderr)
            sys.exit(41)
        sys.exit(0)
    if "build" in args:
        installable = next((arg for arg in args if arg.startswith("path:") and "#" in arg), None)
        if installable is None:
            print("Expected a staged path flake installable", file=sys.stderr)
            sys.exit(97)
        stage = Path(installable[5:].split("#", 1)[0])
        shutil.copytree(stage, audit / "staged-config")
        if os.environ.get("SETUP_TEST_FAIL_BUILD") == "1":
            print("simulated package build failure", file=sys.stderr)
            sys.exit(42)
        print(audit / "activation")
        sys.exit(0)
    if "eval" in args:
        print("26.11")
        sys.exit(0)
    if args == ["--version"]:
        print("nix (test stub)")
        sys.exit(0)
    print("Unexpected nix call: " + repr(args), file=sys.stderr)
    sys.exit(98)
raise SystemExit("Unknown test tool: " + name)
'''


def tree_snapshot(root):
    """Include paths, symlink targets, modes, and bytes without following links."""
    result = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        relative = str(path.relative_to(root))
        if path.is_symlink():
            value = ("link", os.readlink(path))
        elif path.is_file():
            value = ("file", stat.S_IMODE(info.st_mode), path.read_bytes())
        else:
            value = ("directory", stat.S_IMODE(info.st_mode))
        result[relative] = value
    return result


class SetupTransactions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cros-setup-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.setup = SETUP
        self.home = self.root / "home"
        self.home.mkdir()
        self.audit = self.root / "audit"
        self.audit.mkdir()
        (self.audit / "immodules.cache").write_text('"im-cros-gtk3.so"\n')
        self.bin = self.root / "bin"
        self.bin.mkdir()
        driver = self.bin / "stub"
        driver.write_text(STUB)
        driver.chmod(0o755)
        for name in ("sudo", "curl", "systemctl", "apt", "apt-get", "nix", "uname", "findmnt", "btrfs", "dpkg", "find"):
            (self.bin / name).symlink_to(driver)
        activation = self.audit / "activation"
        activation.mkdir()
        (activation / "activate").write_text(
            '#!/usr/bin/env bash\n'
            'printf activated > "$SETUP_TEST_AUDIT/activated"\n'
            'cp "$HOME/.config/home-manager/host.json" "$SETUP_TEST_AUDIT/activation-host.json"\n'
            'exit "${SETUP_TEST_ACTIVATION_EXIT:-0}"\n'
        )
        (activation / "activate").chmod(0o755)
        (self.root / "tmp").mkdir()
        self.environment = os.environ.copy()
        self.environment.update({
            "HOME": str(self.home),
            "USER": "setup-test-user",
            "LOGNAME": "setup-test-user",
            "PATH": str(self.bin) + ":/usr/bin:/bin",
            "TMPDIR": str(self.root / "tmp"),
            "SETUP_TEST_AUDIT": str(self.audit),
            "GIT_USER_NAME": "New Employee",
            "GIT_USER_EMAIL": "employee@example.test",
        })
        self.environment.pop("BASH_ENV", None)
        self.environment.pop("ENV", None)
        self.config = self.home / ".config" / "home-manager"
        self.config.mkdir(parents=True)
        for name in ("flake.nix", "flake.lock", "home.nix", "host.json", "codex-wrapper", "patch-orca.py", "update.bash"):
            (self.config / name).write_text("previous installed " + name + "\n")
        wrapper = self.home / ".local" / "bin" / "codex"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\necho previous-wrapper\n")
        wrapper.chmod(0o755)
        profile = self.root / "previous-profile"
        profile.mkdir()
        (self.home / ".nix-profile").symlink_to(profile, target_is_directory=True)

    def run_setup(self, *arguments, environment=None):
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", RUNNER, "setup-test", str(self.setup), *arguments],
            cwd=self.root,
            env=environment or self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )

    def calls(self):
        path = self.audit / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_no_activation(self):
        self.assertFalse((self.audit / "activated").exists())
        self.assertFalse(any(call[0] == "nix" and "run" in call for call in self.calls()))

    def protected_snapshot(self):
        return (
            tree_snapshot(self.config),
            tree_snapshot(self.home / ".local" / "bin"),
            os.readlink(self.home / ".nix-profile"),
        )

    def use_disposable_checkout(self):
        checkout = self.root / "checkout"
        checkout.mkdir()
        for filename in ("setup.bash", "flake.nix", "flake.lock", "home.nix", "patch-orca.py", "update.bash"):
            shutil.copyfile(SETUP.parent / filename, checkout / filename)
        self.setup = checkout / "setup.bash"
        return checkout

    def assert_update_precedes_build(self):
        nix_calls = [call for call in self.calls() if call[0] == "nix"]
        self.assertEqual(len(nix_calls), 2, nix_calls)
        update, build = nix_calls
        self.assertIn("flake", update)
        self.assertIn("update", update)
        self.assertIn("--accept-flake-config", update)
        self.assertIn("build", build)
        self.assertIn("--no-update-lock-file", build)
        updated_flake = update[update.index("--flake") + 1]
        installable = next(arg for arg in build if arg.startswith("path:") and "#" in arg)
        self.assertEqual(updated_flake, installable.split("#", 1)[0])

    def allow_post_activation_cli_checks(self):
        profile_bin = self.home / ".nix-profile" / "bin"
        profile_bin.mkdir()
        for command in ("node", "npm", "npx", "earlyoom", "cloudflared", "process-compose"):
            (profile_bin / command).symlink_to(self.bin / "stub")
        self.environment["SETUP_TEST_ALLOW_PROFILE_CHECKS"] = "1"

    def test_missing_git_identity_stops_before_privileged_actions_or_writes(self):
        for key, value in (("GIT_USER_NAME", None), ("GIT_USER_EMAIL", "")):
            with self.subTest(key=key):
                environment = self.environment.copy()
                if value is None:
                    environment.pop(key, None)
                else:
                    environment[key] = value
                before = tree_snapshot(self.home)
                result = self.run_setup(environment=environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stdout + result.stderr)
                self.assertEqual(tree_snapshot(self.home), before)
                self.assertFalse(any(call[0] in {"sudo", "nix", "curl", "apt", "apt-get", "systemctl"} for call in self.calls()))
                self.assert_no_activation()

    def test_failed_build_preserves_config_wrapper_and_profile(self):
        before = self.protected_snapshot()
        self.environment["SETUP_TEST_FAIL_BUILD"] = "1"
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("simulated package build failure", result.stderr)
        self.assertTrue((self.audit / "staged-config" / "flake.lock").is_file())
        self.assertEqual(self.protected_snapshot(), before)
        self.assert_no_activation()

    def test_failed_update_preserves_source_live_config_wrapper_and_profile(self):
        checkout = self.use_disposable_checkout()
        source_lock = (checkout / "flake.lock").read_bytes()
        before = self.protected_snapshot()
        self.environment["SETUP_TEST_FAIL_UPDATE"] = "1"
        result = self.run_setup("--update")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("simulated flake update failure", result.stderr)
        self.assertTrue((self.audit / "updated-staged.lock").exists())
        self.assertEqual((checkout / "flake.lock").read_bytes(), source_lock)
        self.assertEqual(self.protected_snapshot(), before)
        nix_calls = [call for call in self.calls() if call[0] == "nix"]
        self.assertEqual(len(nix_calls), 1, nix_calls)
        self.assertIn("update", nix_calls[0])
        self.assertFalse(any("build" in call for call in nix_calls))
        self.assertEqual(list((self.root / "tmp").iterdir()), [])
        self.assert_no_activation()

    def assert_update_build_only_preserves_source_and_home(self, *arguments):
        checkout = self.use_disposable_checkout()
        source_lock = (checkout / "flake.lock").read_bytes()
        before = tree_snapshot(self.home)
        result = self.run_setup(*arguments)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_update_precedes_build()
        staged_lock = json.loads((self.audit / "staged-config" / "flake.lock").read_text())
        self.assertIs(staged_lock["setupTestUpdate"], True)
        self.assertEqual((checkout / "flake.lock").read_bytes(), source_lock)
        self.assertEqual(tree_snapshot(self.home), before)
        self.assertFalse(any(call[0] in {"sudo", "curl", "apt", "apt-get", "systemctl"} for call in self.calls()))
        self.assertEqual(list((self.root / "tmp").iterdir()), [])
        self.assert_no_activation()

    def test_update_build_only_validates_updated_lock_without_installing(self):
        self.assert_update_build_only_preserves_source_and_home("--update", "--build-only")

    def test_build_only_update_accepts_combined_flags_in_reverse_order(self):
        self.assert_update_build_only_preserves_source_and_home("--build-only", "--update")

    def test_build_failure_after_update_preserves_source_live_config_wrapper_and_profile(self):
        checkout = self.use_disposable_checkout()
        source_lock = (checkout / "flake.lock").read_bytes()
        before = self.protected_snapshot()
        self.environment["SETUP_TEST_FAIL_BUILD"] = "1"
        result = self.run_setup("--update")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("simulated package build failure", result.stderr)
        self.assert_update_precedes_build()
        staged_lock = json.loads((self.audit / "staged-config" / "flake.lock").read_text())
        self.assertIs(staged_lock["setupTestUpdate"], True)
        self.assertEqual((checkout / "flake.lock").read_bytes(), source_lock)
        self.assertEqual(self.protected_snapshot(), before)
        self.assertEqual(list((self.root / "tmp").iterdir()), [])
        self.assert_no_activation()

    def test_failed_activation_after_update_preserves_source_and_restores_live_config(self):
        checkout = self.use_disposable_checkout()
        source_lock = (checkout / "flake.lock").read_bytes()
        before = self.protected_snapshot()
        self.environment["SETUP_TEST_ACTIVATION_EXIT"] = "43"
        result = self.run_setup("--update")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("activation failed", result.stderr)
        self.assertTrue((self.audit / "activated").exists())
        self.assert_update_precedes_build()
        self.assertEqual((checkout / "flake.lock").read_bytes(), source_lock)
        self.assertEqual(self.protected_snapshot(), before)
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_failed_activation_restores_generated_files_and_unmanaged_wrapper(self):
        # An old generated file may itself be a symlink; rollback must preserve it.
        old_home = self.root / "previous-home.nix"
        old_home.write_text("previous linked Home Manager config\n")
        (self.config / "home.nix").unlink()
        (self.config / "home.nix").symlink_to(old_home)
        before = self.protected_snapshot()
        self.environment["SETUP_TEST_ACTIVATION_EXIT"] = "43"
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("activation failed", result.stderr)
        self.assertTrue((self.audit / "activated").exists())
        activated_host = json.loads((self.audit / "activation-host.json").read_text())
        self.assertEqual(activated_host["gitName"], self.environment["GIT_USER_NAME"])
        self.assertEqual(self.protected_snapshot(), before)
        self.assertEqual(len(list(self.config.parent.glob("home-manager.previous.*"))), 1)
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_build_only_does_not_install_or_activate(self):
        before = tree_snapshot(self.home)
        result = self.run_setup("--build-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(tree_snapshot(self.home), before)
        self.assertFalse(any(call[0] in {"sudo", "curl", "apt", "apt-get", "systemctl"} for call in self.calls()))
        builds = [call for call in self.calls() if call[0] == "nix" and "build" in call]
        self.assertEqual(len(builds), 1)
        for flag in ("--no-update-lock-file", "--no-link", "--print-out-paths", "--impure"):
            self.assertIn(flag, builds[0])
        self.assert_no_activation()

    def test_absent_local_service_catalog_skips_optional_checks_and_continues(self):
        self.allow_post_activation_cli_checks()
        self.assertFalse((self.home / ".config/local-services/process-compose.yaml").exists())
        result = self.run_setup()
        # This fixture intentionally never installs a managed wrapper. Reaching
        # that later guard proves optional-service handling did not stop setup.
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not install a managed executable", result.stderr)
        self.assertTrue((self.audit / "activated").exists())
        self.assertFalse(any(call[0] == "process-compose" for call in self.calls()))
        self.assertFalse(any(call[0] == "systemctl" and "local-services.service" in call for call in self.calls()))
        self.assertNotIn("simulated optional catalog diagnostic", result.stderr)

    def test_present_local_service_catalog_keeps_validation_and_enablement(self):
        self.allow_post_activation_cli_checks()
        catalog = self.home / ".config/local-services/process-compose.yaml"
        catalog.parent.mkdir()
        catalog.write_text('version: "0.5"\nprocesses: {}\n')
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not install a managed executable", result.stderr)
        self.assertIn(["systemctl", "--user", "enable", "local-services.service"], self.calls())
        self.assertIn(["process-compose", "version"], self.calls())
        self.assertIn([
            "process-compose", "-f", str(catalog), "up", "--dry-run", "--tui=false", "--disable-dotenv",
        ], self.calls())

    def test_git_identity_is_data_even_with_quotes_newlines_and_nix_syntax(self):
        name = 'Employee "Quoted" \\\n${builtins.abort "must remain data"} $(touch SHOULD_NOT_EXIST)'
        email = 'employee+"quoted"\\address@example.test'
        self.environment.update(GIT_USER_NAME=name, GIT_USER_EMAIL=email)
        result = self.run_setup("--build-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        stage = self.audit / "staged-config"
        host = json.loads((stage / "host.json").read_text())
        self.assertEqual(host["gitName"], name)
        self.assertEqual(host["gitEmail"], email)
        self.assertEqual(host["username"], self.environment["USER"])
        self.assertEqual(host["homeDirectory"], str(self.home))
        for filename in ("home.nix", "flake.nix"):
            self.assertNotIn(name, (stage / filename).read_text())
        self.assertFalse((self.home / "SHOULD_NOT_EXIST").exists())
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())
        self.assert_no_activation()


if __name__ == "__main__":
    unittest.main()
