"""Exercise the bootstrap `setup.bash` with fake system tools and a fake repo.

Git, Python, and the shell are real; apt, sudo, systemd, Nix, and the installer
are a Python driver on PATH that records argv as JSON lines. Nothing is built,
installed, or downloaded, and no host file is touched.

The Crostini `/dev/wl0` check is a bash `[[ -c ]]` reserved-word test, so it
cannot be stubbed through PATH. Where the device does not exist the tested copy
has that single check neutralised; `Guards` still tests the real check inside a
private mount namespace when one is available.

Run with: python3 -m unittest discover -s chromeos-dev-setup/tests -v
"""

import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from functools import lru_cache


TESTS = Path(__file__).resolve().parent
SETUP = TESTS.parent / "setup.bash"
DEVICE_CHECK = "[[ -c /dev/wl0 ]]"
REAL_GIT = shutil.which("git")
UNSHARE = shutil.which("unshare")


@lru_cache(maxsize=1)
def namespace_available():
    """Unprivileged user and mount namespaces are needed to mask host paths."""
    if not UNSHARE:
        return False
    probe = subprocess.run(
        [UNSHARE, "-rm", "true"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
    )
    return probe.returncode == 0


def is_character_device(path):
    try:
        return stat.S_ISCHR(os.stat(path).st_mode)
    except OSError:
        return False


STUB = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
audit = Path(os.environ["SETUP_TEST_AUDIT"])


def variable(key, default=None):
    return os.environ.get(key, default)


def emit(extra=None):
    record = {"argv": [name, *args]}
    if extra:
        record.update(extra)
    with (audit / "calls.jsonl").open("a") as log:
        log.write(json.dumps(record) + "\n")


extra = {}
if name == "id" and args[:1] == ["-nG"]:
    extra["groups"] = variable("SETUP_TEST_GROUPS", "user adm")
emit(extra)

if name == "id":
    if args[:1] == ["-u"]:
        print(variable("SETUP_TEST_UID", "1000"))
        sys.exit(0)
    if args[:1] == ["-nG"]:
        print(variable("SETUP_TEST_GROUPS", "user adm"))
        sys.exit(0)
    sys.exit("unexpected id call: " + repr(args))

if name == "uname":
    print(variable("SETUP_TEST_MACHINE", "x86_64"))
    sys.exit(0)

if name == "sudo":
    if args[:1] == ["tee"]:
        # Never write under /etc; record the piped text instead.
        with (audit / "tee.log").open("a") as log:
            log.write(sys.stdin.read())
        sys.exit(0)
    if args[:1] == ["env"]:
        os.execvp("env", args)
    os.execvp(args[0], args)

if name == "git":
    os.execv(os.environ["SETUP_TEST_REAL_GIT"], ["git", *args])

if name == "nix":
    if args[:2] == ["config", "show"]:
        users = variable("SETUP_TEST_TRUSTED_USERS")
        if users is None:
            users = "root"
            # The bootstrap appends to nix.custom.conf through `sudo tee`; the
            # sudo stub records that line instead of writing /etc.
            custom_conf = audit / "tee.log"
            if variable("SETUP_TEST_IGNORE_CUSTOM_CONF", "0") != "1" and custom_conf.exists():
                for line in custom_conf.read_text().splitlines():
                    key, _, value = line.partition("=")
                    if key.strip() == "extra-trusted-users":
                        users += " " + value.strip()
        print(users)
        sys.exit(0)
    if args[:1] == ["run"]:
        sys.exit(int(variable("SETUP_TEST_RUN_EXIT", "0")))
    sys.exit("unexpected nix call: " + repr(args))

if name == "curl":
    installer = variable("SETUP_TEST_INSTALLER_SCRIPT")
    if installer:
        sys.stdout.write(installer)
        sys.exit(0)
    sys.exit(int(variable("SETUP_TEST_CURL_EXIT", "1")))

if name == "tee":
    sys.stdin.read()
    sys.exit(0)

if name in {"apt-get", "apt", "systemctl", "usermod", "chsh"}:
    sys.exit(0)

raise SystemExit("unexpected stub command: " + name)
'''


def base_path_directories():
    """Directories of the real tools the bootstrap needs, without any `nix`."""
    directories = []
    for name in (
        "bash", "sh", "git", "python3", "find", "grep", "sed", "tr", "cut",
        "cmp", "mktemp", "date", "readlink", "dirname", "env", "rm", "mv",
        "mkdir", "cat", "chmod", "ls", "sort", "head", "tail", "wc", "tee",
        "diff", "uniq", "stat", "touch", "ln", "unshare",
    ):
        found = shutil.which(name)
        if found:
            directory = os.path.dirname(found)
            if directory not in directories:
                directories.append(directory)
    for directory in ("/usr/bin", "/bin"):
        if directory not in directories:
            directories.append(directory)
    return directories


@unittest.skipUnless(REAL_GIT, "the setup tests need git")
class SetupTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cros setup test ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.audit = self.root / "audit"
        self.audit.mkdir()
        self.bin = self.root / "stub commands"
        self.bin.mkdir()
        driver = self.bin / "driver"
        driver.write_text(STUB)
        driver.chmod(0o755)
        for name in (
            "sudo", "apt-get", "systemctl", "usermod", "chsh", "curl", "nix",
            "id", "uname", "git", "tee",
        ):
            (self.bin / name).symlink_to(driver)

        self.base_directories = base_path_directories()
        self.environment = os.environ.copy()
        self.environment.update({
            "HOME": str(self.home),
            "USER": "setup-test-user",
            "LOGNAME": "setup-test-user",
            "PATH": os.pathsep.join([str(self.bin), *self.base_directories]),
            "TMPDIR": str(self.root / "tmp"),
            "SETUP_TEST_AUDIT": str(self.audit),
            "SETUP_TEST_REAL_GIT": REAL_GIT,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self.root / "gitconfig-global"),
            "GIT_AUTHOR_NAME": "Setup Test",
            "GIT_AUTHOR_EMAIL": "setup@example.test",
            "GIT_COMMITTER_NAME": "Setup Test",
            "GIT_COMMITTER_EMAIL": "setup@example.test",
            "GIT_TERMINAL_PROMPT": "0",
        })
        (self.root / "tmp").mkdir()
        (self.root / "gitconfig-global").write_text("")
        for name in (
            "BASH_ENV", "ENV",
            "SETUP_TEST_UID", "SETUP_TEST_MACHINE", "SETUP_TEST_GROUPS",
            "SETUP_TEST_TRUSTED_USERS", "SETUP_TEST_INSTALLER_SCRIPT",
            "SETUP_TEST_CURL_EXIT", "SETUP_TEST_RUN_EXIT",
            "SETUP_TEST_IGNORE_CUSTOM_CONF",
        ):
            self.environment.pop(name, None)

        self.has_device = is_character_device("/dev/wl0")
        self.repo = self.make_repository()
        setup_dir = self.repo / "chromeos-dev-setup"
        self.setup = setup_dir / "setup.bash"
        text = SETUP.read_text()
        if not self.has_device:
            text = text.replace(DEVICE_CHECK, "true")
        self.setup.write_text(text)
        self.guarded_setup = setup_dir / "setup-guarded.bash"
        self.guarded_setup.write_text(SETUP.read_text())
        self.git("-C", str(self.repo), "add", "--all")
        self.git("-C", str(self.repo), "commit", "-m", "Test checkout")

    # ------------------------------------------------------------- fixtures

    def make_repository(self):
        repo = self.root / "setup checkout"
        setup = repo / "chromeos-dev-setup"
        setup.mkdir(parents=True)
        (setup / "flake.nix").write_text("{ outputs = _: { }; }\n")
        (setup / "flake.lock").write_text("{}\n")
        (repo / "nixpkgs" / "kakaotalk").mkdir(parents=True)
        (repo / "nixpkgs" / "kakaotalk" / "package.nix").write_text("{ }\n")
        (repo / "README.md").write_text("test checkout\n")
        self.git("-C", str(repo), "init", "--initial-branch=main")
        return repo

    def git(self, *arguments):
        result = subprocess.run(
            [REAL_GIT, *arguments],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"git {' '.join(arguments)} failed: {result.stdout}{result.stderr}"
            )
        return result

    # ------------------------------------------------------------- helpers

    def run_setup(self, *arguments, environment=None, unshare=None, setup=None):
        script = str(setup or self.setup)
        command = ["bash", script, *arguments]
        if unshare:
            runner = self.root / f"namespace-{unshare}.bash"
            if unshare == "hide-device":
                runner.write_text(
                    'mask="$(mktemp)"\n'
                    'mount --bind "$mask" /dev/wl0 || exit 97\n'
                    'exec bash "$@"\n'
                )
            else:
                runner.write_text(
                    "mount -t tmpfs tmpfs /nix/var/nix/profiles || exit 97\n"
                    'exec bash "$@"\n'
                )
            command = ["unshare", "-rm", "bash", str(runner), script, *arguments]
        return subprocess.run(
            command,
            cwd=self.root,
            env=environment or self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    def calls(self):
        path = self.audit / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def argvs(self):
        return [call["argv"] for call in self.calls()]

    def matching(self, predicate):
        return [call for call in self.calls() if predicate(call["argv"])]

    def index(self, predicate):
        for position, argv in enumerate(self.argvs()):
            if predicate(argv):
                return position
        raise AssertionError(f"no call matched; recorded: {self.argvs()}")

    def output(self, result):
        return result.stdout + result.stderr

    def final_nix_run(self):
        runs = [argv for argv in self.argvs() if argv[:1] == ["nix"] and "run" in argv]
        self.assertEqual(len(runs), 1, self.argvs())
        return runs[0]

    def assert_no_privileged_actions(self):
        self.assertEqual(
            self.matching(lambda a: a[0] in {"sudo", "apt-get", "curl", "nix", "usermod"}),
            [],
        )


class Arguments(SetupTestCase):
    def test_unknown_argument_prints_usage_and_exits_two(self):
        result = self.run_setup("--frobnicate")
        self.assertEqual(result.returncode, 2, self.output(result))
        self.assertIn("Usage: bash", result.stderr)
        self.assertIn("setup.bash", result.stderr)
        self.assert_no_privileged_actions()

    def test_maintainer_is_forwarded_to_apply(self):
        result = self.run_setup("--maintainer")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(self.final_nix_run(), [
            "nix", "run", "--accept-flake-config",
            f"{self.repo}/chromeos-dev-setup#cros",
            "--", "apply", "--checkout", str(self.repo), "--maintainer",
        ])


class Guards(SetupTestCase):
    def test_setup_still_guards_the_crostini_device(self):
        self.assertIn(DEVICE_CHECK, SETUP.read_text())

    def test_refuses_to_run_as_root(self):
        environment = dict(self.environment)
        environment["SETUP_TEST_UID"] = "0"
        result = self.run_setup(environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("run as your normal user, not root", result.stderr)
        self.assert_no_privileged_actions()

    def test_refuses_a_non_x86_64_machine(self):
        environment = dict(self.environment)
        environment["SETUP_TEST_MACHINE"] = "aarch64"
        result = self.run_setup(environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("x86_64 Crostini only", result.stderr)
        self.assert_no_privileged_actions()

    def test_refuses_a_missing_virtwl_device(self):
        if self.has_device:
            if not namespace_available():
                self.skipTest("needs unprivileged mount namespaces to mask /dev/wl0")
            result = self.run_setup(unshare="hide-device", setup=self.guarded_setup)
            if result.returncode == 97:
                self.skipTest("cannot create a private mount namespace")
        else:
            result = self.run_setup(setup=self.guarded_setup)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("/dev/wl0", result.stderr)
        self.assert_no_privileged_actions()

    def test_refuses_a_directory_outside_a_git_checkout(self):
        outside = self.root / "outside a checkout"
        outside.mkdir()
        copy = outside / "setup.bash"
        text = SETUP.read_text()
        if not self.has_device:
            text = text.replace(DEVICE_CHECK, "true")
        copy.write_text(text)
        result = self.run_setup(setup=copy)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("is not inside a Git checkout", result.stderr)
        self.assert_no_privileged_actions()


class Bootstrap(SetupTestCase):
    def test_installs_prerequisites_and_hands_off_to_cros_apply(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, self.output(result))

        self.assertIn(["sudo", "apt-get", "update"], self.argvs())
        self.assertIn(
            ["sudo", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install",
             "-y", "git", "curl", "ca-certificates", "xz-utils", "cros-im"],
            self.argvs(),
        )
        # Nix is already on PATH, so the Determinate installer must not run.
        self.assertEqual(self.matching(lambda a: a[0] == "curl"), [])

        expected = [
            "nix", "run", "--accept-flake-config",
            f"{self.repo}/chromeos-dev-setup#cros",
            "--", "apply", "--checkout", str(self.repo),
        ]
        self.assertEqual(self.final_nix_run(), expected)
        # The checkout path has spaces and must remain a single argv element.
        self.assertEqual(self.final_nix_run().count(expected[3]), 1)
        # `cros apply` owns sparse checkouts now.
        self.assertEqual(self.matching(lambda a: "sparse-checkout" in a), [])

        # Debian is upgraded after apply, once the system package has added
        # the security and updates suites.
        nix_run = self.index(lambda a: a[:1] == ["nix"] and "run" in a)
        upgrade = self.index(lambda a: "full-upgrade" in a)
        autoremove = self.index(lambda a: "autoremove" in a)
        self.assertLess(nix_run, upgrade)
        self.assertLess(upgrade, autoremove)
        self.assertIn(
            ["sudo", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "full-upgrade", "-y"],
            self.argvs(),
        )

    def test_failed_apply_skips_the_debian_upgrade(self):
        environment = dict(self.environment)
        environment["SETUP_TEST_RUN_EXIT"] = "1"
        result = self.run_setup(environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertEqual(self.matching(lambda a: "full-upgrade" in a), [])

    def test_trusted_users_added_and_daemon_restarted_only_when_missing(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(
            (self.audit / "tee.log").read_text(), "extra-trusted-users = setup-test-user\n"
        )
        self.assertIn(
            ["sudo", "tee", "-a", "/etc/nix/nix.custom.conf"], self.argvs()
        )
        self.assertIn(
            ["sudo", "systemctl", "restart", "nix-daemon.service"], self.argvs()
        )
        tee = self.index(lambda a: a[:2] == ["sudo", "tee"])
        restart = self.index(lambda a: a[:3] == ["sudo", "systemctl", "restart"])
        self.assertLess(tee, restart)

    def test_dies_when_nix_still_does_not_trust_the_user(self):
        # A Nix that ignores nix.custom.conf must fail loudly instead of
        # continuing to a flake that needs the user to be trusted.
        environment = dict(self.environment)
        environment["SETUP_TEST_IGNORE_CUSTOM_CONF"] = "1"
        result = self.run_setup(environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("Nix still does not trust setup-test-user", result.stderr)
        self.assertIn("/etc/nix/nix.custom.conf", result.stderr)
        # It appended the line and restarted the daemon before giving up.
        self.assertIn(
            ["sudo", "tee", "-a", "/etc/nix/nix.custom.conf"], self.argvs()
        )
        self.assertIn(
            ["sudo", "systemctl", "restart", "nix-daemon.service"], self.argvs()
        )
        # It never hands off to `cros apply`.
        self.assertEqual(
            self.matching(lambda a: a[0] == "nix" and "run" in a), []
        )

    def test_trusted_users_left_alone_when_the_user_is_already_listed(self):
        environment = dict(self.environment)
        environment["SETUP_TEST_TRUSTED_USERS"] = "root setup-test-user"
        result = self.run_setup(environment=environment)
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(self.matching(lambda a: a[:2] == ["sudo", "tee"]), [])
        self.assertEqual(
            self.matching(lambda a: a[:3] == ["sudo", "systemctl", "restart"]), []
        )
        self.assertFalse((self.audit / "tee.log").exists())

    def test_render_group_added_only_when_missing(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn(
            ["sudo", "usermod", "-aG", "render", "setup-test-user"], self.argvs()
        )
        self.assertIn("render group", result.stderr)

        (self.audit / "calls.jsonl").unlink(missing_ok=True)
        environment = dict(self.environment)
        environment["SETUP_TEST_GROUPS"] = "user render adm"
        result = self.run_setup(environment=environment)
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(self.matching(lambda a: a[0] == "usermod"), [])

    def test_git_config_is_left_to_the_user(self):
        gitconfig = self.home / ".gitconfig"
        gitconfig.write_text("[user]\n\tname = Existing\n")
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(gitconfig.read_text(), "[user]\n\tname = Existing\n")


class PipedInstall(SetupTestCase):
    """`curl … | bash` clones the repository and reruns setup from it."""

    def setUp(self):
        super().setUp()
        # A published repository whose setup.bash records how it was run.
        published = self.root / "published"
        (published / "chromeos-dev-setup").mkdir(parents=True)
        (published / "chromeos-dev-setup" / "setup.bash").write_text(
            '#!/usr/bin/env bash\nprintf "%s\\n" "$0" "$@" > "$SETUP_TEST_AUDIT/rerun"\n'
        )
        (published / "README.md").write_text("published\n")
        self.git("-C", str(published), "init", "--initial-branch=main")
        self.git("-C", str(published), "add", "--all")
        self.git("-C", str(published), "commit", "-m", "Published")
        self.checkout = self.home / "workspaces" / "monorepo-public"
        self.environment["CROS_SETUP_REPOSITORY"] = published.as_uri()
        self.environment["CROS_SETUP_CHECKOUT"] = str(self.checkout)

    def run_piped(self, *arguments):
        text = SETUP.read_text()
        if not self.has_device:
            text = text.replace(DEVICE_CHECK, "true")
        return subprocess.run(
            ["bash", "-s", "--", *arguments],
            input=text,
            cwd=self.root,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    def rerun(self):
        return (self.audit / "rerun").read_text().splitlines()

    def test_clones_sparse_and_reruns_from_the_checkout(self):
        result = self.run_piped("--maintainer")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(
            self.rerun(),
            [str(self.checkout / "chromeos-dev-setup" / "setup.bash"), "--maintainer"],
        )
        self.assertEqual(
            self.git("-C", str(self.checkout), "config", "--bool", "core.sparseCheckout").stdout.strip(),
            "true",
        )
        self.assertIn(
            "chromeos-dev-setup",
            self.git("-C", str(self.checkout), "sparse-checkout", "list").stdout.split(),
        )
        # git is already installed, so nothing else ran before the handoff.
        self.assert_no_privileged_actions()

    def test_existing_checkout_is_reused(self):
        self.git("clone", self.environment["CROS_SETUP_REPOSITORY"], str(self.checkout))
        head = self.git("-C", str(self.checkout), "rev-parse", "HEAD").stdout
        result = self.run_piped()
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(self.rerun(), [str(self.checkout / "chromeos-dev-setup" / "setup.bash")])
        self.assertEqual(self.git("-C", str(self.checkout), "rev-parse", "HEAD").stdout, head)

    def test_unrelated_directory_at_the_checkout_path_is_refused(self):
        self.checkout.mkdir(parents=True)
        (self.checkout / "notes.txt").write_text("not a checkout\n")
        result = self.run_piped()
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("is not a monorepo-public checkout", result.stderr)
        self.assertFalse((self.audit / "rerun").exists())

    def test_guards_run_before_cloning(self):
        self.environment["SETUP_TEST_UID"] = "0"
        result = self.run_piped()
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertFalse(self.checkout.exists())

    def test_readme_one_liner_matches_the_defaults(self):
        readme = (SETUP.parent / "README.md").read_text()
        text = SETUP.read_text()
        repository = re.search(r'CROS_SETUP_REPOSITORY:-([^}]+)\}', text).group(1)
        owner_repo = repository.removeprefix("https://github.com/").removesuffix(".git")
        url = f"https://raw.githubusercontent.com/{owner_repo}/main/chromeos-dev-setup/setup.bash"
        self.assertIn(f"curl -fsSL {url} | bash", readme)
        self.assertIn(f"curl -fsSL {url} | bash", text)
        self.assertIn("CROS_SETUP_CHECKOUT:-$HOME/workspaces/monorepo-public}", text)


class NixInstall(SetupTestCase):
    def test_installer_runs_only_when_nix_is_missing(self):
        if not namespace_available():
            self.skipTest("needs unprivileged mount namespaces to hide the Nix profile")
        if not Path("/nix/var/nix/profiles").is_dir():
            self.skipTest("needs /nix/var/nix/profiles to hide the real Nix")
        for directory in self.base_directories:
            if Path(directory, "nix").exists():
                self.skipTest(f"{directory}/nix would make Nix look installed")

        nixbin = self.root / "nix bin"
        nixbin.mkdir()
        (nixbin / "nix").symlink_to(self.bin / "driver")
        installer = (
            'printf \'%s\\n\' "$@" > "$SETUP_TEST_AUDIT/installer-args"\n'
            "mkdir -p /nix/var/nix/profiles/default/etc/profile.d\n"
            f"printf 'export PATH=\"{nixbin}:$PATH\"\\n' "
            "> /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh\n"
        )
        (self.bin / "nix").unlink()
        environment = dict(self.environment)
        environment["SETUP_TEST_INSTALLER_SCRIPT"] = installer
        result = self.run_setup(environment=environment, unshare="hide-nix")
        if result.returncode == 97:
            self.skipTest("cannot create a private mount namespace")
        self.assertEqual(result.returncode, 0, self.output(result))

        self.assertEqual(
            (self.audit / "installer-args").read_text().splitlines(),
            ["install", "--no-confirm", "--extra-conf", "trusted-users = root setup-test-user"],
        )
        self.assertIn(
            ["curl", "--proto", "=https", "--tlsv1.2", "-sSfL",
             "https://install.determinate.systems/nix"],
            self.argvs(),
        )
        self.assertEqual(self.final_nix_run(), [
            "nix", "run", "--accept-flake-config",
            f"{self.repo}/chromeos-dev-setup#cros",
            "--", "apply", "--checkout", str(self.repo),
        ])


if __name__ == "__main__":
    unittest.main()
