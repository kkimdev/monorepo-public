"""Exercise the `cros` CLI with disposable repositories and stub system tools.

Git, jq, and Python are real. Nix, apt, sudo, systemd, and the desktop session
are replaced by a Python driver on PATH that records every argv as JSON lines,
so no build, root command, network access, or host configuration is involved.

Run with: python3 -m unittest discover -s chromeos-dev-setup/tests -v
"""

import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


TESTS = Path(__file__).resolve().parent
CROS = TESTS.parent / "cros" / "cros.bash"
REAL_GIT = shutil.which("git")

SESSION_UNITS = [
    "atuin-daemon.service",
    "sommelier@0.service",
    "sommelier@1.service",
    "sommelier-x@0.service",
    "sommelier-x@1.service",
    "sommelier-rs.service",
    "cros-garcon.service",
]


def find_jq():
    """The packaged `cros` has jq from Nix; a test machine may not."""
    found = shutil.which("jq")
    if found:
        return found
    for candidate in sorted(Path("/nix/store").glob("*-jq-*/bin/jq")):
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return None


JQ = find_jq()

# The driver is symlinked under every stubbed command name. It records the
# argv first, then either emulates the command or execs the real binary.
STUB = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
audit = Path(os.environ["CROS_TEST_AUDIT"])


def variable(key, default=None):
    return os.environ.get(key, default)


def host_json_path():
    return Path(os.environ["CROS_HOST_DIR"]) / "host.json"


def emit(extra=None):
    record = {"argv": [name, *args]}
    if extra:
        record.update(extra)
    with (audit / "calls.jsonl").open("a") as log:
        log.write(json.dumps(record) + "\n")


extra = {}
if name == "apt-get":
    extra["host_json_exists"] = host_json_path().exists()
if name == "nix" and args[:1] == ["build"]:
    installable = next((arg for arg in args if "#" in arg), "")
    if installable.endswith("#system-deb"):
        extra["installable"] = "system-deb"
        extra["host_json_exists"] = host_json_path().exists()
    elif "#homeConfigurations" in installable:
        extra["installable"] = "home"
        host = host_json_path()
        extra["host_json"] = json.loads(host.read_text()) if host.exists() else None
if name == "nix" and args[:1] == ["run"]:
    extra["no_reminders"] = os.environ.get("CROS_NO_REMINDERS")
emit(extra)

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
    os.execv(variable("CROS_TEST_REAL_GIT"), ["git", *args])

if name == "find" and args[:1] == ["/usr/lib/x86_64-linux-gnu"]:
    # write_host_data searches the x86_64 GTK directory; use the fixtures.
    if any("immodules.cache" in arg for arg in args):
        if variable("CROS_TEST_GTK3"):
            print(variable("CROS_TEST_GTK3"))
    elif any("gtk-4.0" in arg for arg in args):
        if variable("CROS_TEST_GTK4"):
            print(variable("CROS_TEST_GTK4"))
    sys.exit(0)

if name == "find":
    os.execv(variable("CROS_TEST_REAL_FIND"), ["find", *args])

if name == "rm":
    # `cros reset --hard` removes Wayland sockets; never touch the real ones.
    if any(arg.startswith("/run/user/") for arg in args):
        sys.exit(0)
    os.execv(variable("CROS_TEST_REAL_RM"), ["rm", *args])

if name == "dpkg-deb":
    print(variable("CROS_TEST_DEB_VERSION", "1.0.0"))
    sys.exit(0)

if name == "dpkg-query":
    installed = variable("CROS_TEST_INSTALLED_VERSION")
    if not installed:
        sys.exit(1)
    # Status-Abbrev is three characters, including its trailing space.
    print(variable("CROS_TEST_INSTALLED_STATUS", "ii ") + installed)
    sys.exit(0)

if name == "nix":
    subcommand = args[0] if args else ""
    if subcommand == "build":
        installable = next((arg for arg in args if "#" in arg), "")
        if installable.endswith("#system-deb"):
            print(audit / "system-deb-out")
        elif "#homeConfigurations" in installable:
            if variable("CROS_TEST_HOME_BUILD_EXIT", "0") != "0":
                sys.exit(int(variable("CROS_TEST_HOME_BUILD_EXIT")))
            print(audit / "activation")
        else:
            sys.exit("unexpected build installable: " + installable)
        sys.exit(0)
    if subcommand == "run":
        sys.exit(int(variable("CROS_TEST_RUN_EXIT", "0")))
    if subcommand == "flake" and args[1:2] in (["update"], ["lock"]):
        flake = args[args.index("--flake") + 1]
        lock = Path(flake) / "flake.lock"
        data = json.loads(lock.read_text())
        # --override-input NAME github:OWNER/REPO/REV pins NAME to REV.
        pinned = set()
        for position, arg in enumerate(args):
            if arg == "--override-input":
                name, url = args[position + 1], args[position + 2]
                pinned.add(name)
                data["nodes"][name]["locked"]["rev"] = url.rsplit("/", 1)[1]
                data["nodes"][name]["locked"]["lastModified"] = int(variable("CROS_TEST_COMMIT_EPOCH", "1609459200"))
        # A named (not pinned) GitHub input moves to its newest commit.
        if (args[1] == "update" and "nixpkgs" in args and "nixpkgs" not in pinned
                and variable("CROS_TEST_UPDATE_CHANGE", "1") == "1"):
            data["nodes"]["nixpkgs"]["locked"]["rev"] = variable("CROS_TEST_UPDATE_REV", "f" * 40)
        lock.write_text(json.dumps(data, indent=2) + "\n")
        sys.exit(int(variable("CROS_TEST_UPDATE_EXIT", "0")))
    if subcommand == "store" and args[1:2] == ["diff-closures"]:
        print(variable("CROS_TEST_DIFF_CLOSURES", "test-package: 1.0 -> 2.0"))
        sys.exit(0)
    if subcommand == "store" and args[1:2] == ["optimise"]:
        sys.exit(0)
    sys.exit("unexpected nix call: " + repr(args))

if name == "curl":
    # The GitHub commits API: the newest commit before the cooldown cutoff.
    if variable("CROS_TEST_CURL_FAIL"):
        sys.exit(22)
    epoch = int(variable("CROS_TEST_COMMIT_EPOCH",
                         "1609459200" if variable("CROS_TEST_UPDATE_CHANGE", "1") == "1" else "1546300800"))
    import datetime
    date = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(json.dumps([{"sha": variable("CROS_TEST_COMMIT_SHA", "c" * 40),
                       "commit": {"committer": {"date": date}}}]))
    sys.exit(0)

if name == "nix-collect-garbage":
    sys.exit(0)

if name == "determinate-nixd":
    sys.exit(int(variable("CROS_TEST_NIXD_EXIT", "0")))

if name == "gh":
    print("OPEN")
    sys.exit(0)

if name == "systemctl" and "show" in args:
    # loaded_units asks for LoadState; CROS_TEST_MISSING_UNITS are not loaded.
    missing = variable("CROS_TEST_MISSING_UNITS", "").split()
    print("not-found" if args[-1] in missing else "loaded")
    sys.exit(0)

if name == "apt-get" and "-s" in args:
    # `cros audit` simulates an upgrade and counts the security archive.
    for package in variable("CROS_TEST_APT_SECURITY", "").split():
        print(f"Inst {package} [1.0] (1.1 Debian-Security:trixie-security [amd64])")
    sys.exit(0)

if name in {"systemctl", "apt-get"}:
    sys.exit(0)

raise SystemExit("unexpected stub command: " + name)
'''

DEFAULT_LOCK = {
    "nodes": {
        "root": {"inputs": {"nixpkgs": "nixpkgs", "kakaotalk": "kakaotalk", "host": "host"}},
        "nixpkgs": {
            "original": {"type": "github", "owner": "NixOS", "repo": "nixpkgs", "ref": "nixpkgs-unstable"},
            "locked": {
                "lastModified": 1577836800,
                "narHash": "sha256-test",
                "owner": "NixOS",
                "repo": "nixpkgs",
                "rev": "a" * 40,
                "type": "github",
            }
        },
        "kakaotalk": {"original": {"type": "path", "path": "../nixpkgs/kakaotalk"},
                      "locked": {"path": "../nixpkgs/kakaotalk", "type": "path"}},
        "host": {"original": {"type": "path", "path": "./host-example"},
                 "locked": {"path": "./host-example", "type": "path"}},
    },
    "root": "root",
    "version": 7,
}


def epoch(text):
    """Seconds since the epoch for a UTC timestamp such as 2026-03-10T00:00:00Z."""
    moment = datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    return int(moment.replace(tzinfo=datetime.timezone.utc).timestamp())


def base_path_directories():
    """Directories of the real tools the scripts need, without any `nix`."""
    directories = []
    for name in (
        "bash", "sh", "git", "python3", "find", "grep", "sed", "tr", "cut",
        "cmp", "mktemp", "date", "readlink", "realpath", "dirname", "env",
        "rm", "mv", "mkdir", "cat", "chmod", "ls", "sort", "head", "tail",
        "wc", "tee", "diff", "uniq", "stat", "touch", "ln",
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


@unittest.skipUnless(JQ and REAL_GIT, "the cros tests need jq and git")
class CrosTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cros cli test ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.audit = self.root / "audit"
        self.audit.mkdir()
        self.host_dir = self.home / ".config" / "cros" / "host"
        self.host_dir.mkdir(parents=True)

        self.bin = self.root / "stub commands"
        self.bin.mkdir()
        driver = self.bin / "driver"
        driver.write_text(STUB)
        driver.chmod(0o755)
        for name in (
            "sudo", "apt-get", "dpkg-deb", "dpkg-query", "systemctl", "curl",
            "determinate-nixd", "gh", "nix", "nix-collect-garbage",
            "find", "rm", "git",
        ):
            (self.bin / name).symlink_to(driver)

        self.gtk3 = self.audit / "immodules.cache"
        self.gtk3.write_text('"im-cros-gtk3.so"\n')
        self.gtk4 = self.audit / "gtk-4.0"
        self.gtk4.mkdir()

        system_deb = self.audit / "system-deb-out"
        system_deb.mkdir()
        (system_deb / "cros-dev-system.deb").write_text("test deb\n")
        activation = self.audit / "activation"
        activation.mkdir()
        activate = activation / "activate"
        activate.write_text(
            "#!/usr/bin/env bash\n"
            "printf '{\"argv\": [\"activate\"], \"backup\": \"%s\"}\\n' "
            "\"${HOME_MANAGER_BACKUP_EXT:-}\" >> \"$CROS_TEST_AUDIT/calls.jsonl\"\n"
            "exit \"${CROS_TEST_ACTIVATE_EXIT:-0}\"\n"
        )
        activate.chmod(0o755)

        self.cros = self.root / "cros"
        self.cros.write_text(
            "#!/usr/bin/env bash\nset -o errexit -o nounset -o pipefail\n"
            + CROS.read_text()
        )
        self.cros.chmod(0o755)

        git_global = self.root / "gitconfig-global"
        git_global.write_text("")
        self.git_environment = {
            "PATH": os.pathsep.join([str(self.bin), os.path.dirname(JQ)] + base_path_directories()),
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(git_global),
            "GIT_AUTHOR_NAME": "Cros Test",
            "GIT_AUTHOR_EMAIL": "cros@example.test",
            "GIT_COMMITTER_NAME": "Cros Test",
            "GIT_COMMITTER_EMAIL": "cros@example.test",
            "GIT_TERMINAL_PROMPT": "0",
        }

        self.environment = os.environ.copy()
        self.environment.update(self.git_environment)
        self.environment.update({
            "USER": "cros-test-user",
            "LOGNAME": "cros-test-user",
            "CROS_HOST_DIR": str(self.host_dir),
            "TMPDIR": str(self.root / "tmp"),
            "CROS_TEST_AUDIT": str(self.audit),
            "CROS_TEST_GTK3": str(self.gtk3),
            "CROS_TEST_GTK4": str(self.gtk4),
            "CROS_TEST_DEB_VERSION": "1.0.0",
            "CROS_TEST_REAL_GIT": REAL_GIT,
            "CROS_TEST_REAL_FIND": shutil.which("find"),
            "CROS_TEST_REAL_RM": shutil.which("rm"),
            # The installed GPU driver link; tests never read the real one.
            "CROS_GPU_DRIVERS_CONF": str(self.audit / "etc-non-nixos-gpu.conf"),
            # `cros reset --hard` deletes Wayland sockets here, never the real ones.
            "XDG_RUNTIME_DIR": str(self.audit / "run"),
        })
        (self.root / "tmp").mkdir()
        self.environment.pop("BASH_ENV", None)
        self.environment.pop("ENV", None)

        self.repo = self.make_checkout()

    # ------------------------------------------------------------- fixtures

    def make_checkout(self, path=None, commit_date=None):
        path = Path(path) if path else self.root / "checkout"
        path.mkdir(parents=True, exist_ok=True)
        setup = path / "chromeos-dev-setup"
        setup.mkdir()
        (setup / "flake.nix").write_text("{ outputs = _: { }; }\n")
        (setup / "flake.lock").write_text(json.dumps(DEFAULT_LOCK, indent=2) + "\n")
        (setup / "host-example").mkdir()
        (setup / "host-example" / "host.json").write_text("{}\n")
        (path / "nixpkgs" / "kakaotalk").mkdir(parents=True)
        (path / "nixpkgs" / "kakaotalk" / "package.nix").write_text("{ }\n")
        (path / "README.md").write_text("test checkout\n")
        self.git("-C", str(path), "init", "--initial-branch=main")
        self.commit(path, "Initial checkout", date=commit_date)
        return path

    def git(self, *arguments, env=None, check=True):
        result = subprocess.run(
            [REAL_GIT, *arguments],
            env=env or self.git_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"git {' '.join(arguments)} failed: {result.stdout}{result.stderr}"
            )
        return result

    def commit(self, checkout, message, date=None):
        self.git("-C", str(checkout), "add", "--all")
        environment = dict(self.git_environment)
        if date:
            environment["GIT_AUTHOR_DATE"] = date
            environment["GIT_COMMITTER_DATE"] = date
        self.git("-C", str(checkout), "commit", "-m", message, env=environment)

    def add_origin(self, checkout=None):
        checkout = Path(checkout) if checkout else self.repo
        remote = self.root / "origin remote.git"
        self.git("init", "--bare", "--initial-branch=main", str(remote))
        self.git("-C", str(checkout), "remote", "add", "origin", str(remote))
        self.git("-C", str(checkout), "push", "origin", "main")
        return remote

    def write_host_json(self, **fields):
        (self.host_dir / "host.json").write_text(json.dumps(fields) + "\n")

    # ------------------------------------------------------------- helpers

    def run_cros(self, *arguments, environment=None):
        return subprocess.run(
            [str(self.cros), *arguments],
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

    def index(self, predicate):
        for position, argv in enumerate(self.argvs()):
            if predicate(argv):
                return position
        raise AssertionError(f"no call matched; recorded: {self.argvs()}")

    def matching(self, predicate):
        return [call for call in self.calls() if predicate(call["argv"])]

    def output(self, result):
        return result.stdout + result.stderr

    def apply(self, *arguments, environment=None):
        return self.run_cros("apply", *arguments, environment=environment)


class Dispatch(CrosTestCase):
    def test_no_arguments_prints_usage_and_exits_two(self):
        result = self.run_cros()
        self.assertEqual(result.returncode, 2, self.output(result))
        self.assertIn("Usage: cros <command>", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_help_flags_print_usage_and_exit_zero(self):
        for flag in ("--help", "-h", "help"):
            with self.subTest(flag=flag):
                result = self.run_cros(flag)
                self.assertEqual(result.returncode, 0, self.output(result))
                self.assertIn("Usage: cros <command>", result.stdout)

    def test_help_after_a_command_shows_help_without_running_it(self):
        self.write_host_json(checkout=str(self.repo), maintainer=False)
        for arguments in (["bump", "--help"], ["apply", "-h"], ["sync", "--help"],
                          ["bump", "nixpkgs", "--help"]):
            with self.subTest(arguments=arguments):
                result = self.run_cros(*arguments)
                self.assertEqual(result.returncode, 0, self.output(result))
                self.assertIn("Usage: cros <command>", result.stdout)
                self.assertEqual(self.calls(), [])

    def test_command_list_matches_help_and_dispatch(self):
        listed = self.run_cros("__commands")
        self.assertEqual(listed.returncode, 0, self.output(listed))
        names = [line.split(":", 1)[0] for line in listed.stdout.splitlines()]
        self.assertEqual(names, ["sync", "apply", "check", "audit", "bump", "publish", "clean", "reset"])
        self.assertTrue(all(line.split(":", 1)[1] for line in listed.stdout.splitlines()))
        usage = self.run_cros("--help").stdout
        for name in names:
            self.assertRegex(usage, rf"(?m)^  {name}\b")
            # Every listed command dispatches to a handler, not "unknown command".
            self.assertIn(f"\ncmd_{name}() {{", CROS.read_text())

    def test_unknown_command_reports_the_name_and_exits_one(self):
        result = self.run_cros("frobnicate")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("unknown command: frobnicate", result.stderr)
        self.assertIn("Usage: cros", result.stderr)


class ResolveCheckout(CrosTestCase):
    def test_apply_without_host_json_names_the_bootstrap_script(self):
        result = self.apply()
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn(str(self.host_dir / "host.json"), result.stderr)
        self.assertIn("setup.bash", result.stderr)
        self.assertEqual(self.argvs(), [])

    def test_explicit_checkout_is_resolved_and_recorded(self):
        result = self.apply("--checkout", str(self.repo))
        self.assertEqual(result.returncode, 0, self.output(result))
        host = json.loads((self.host_dir / "host.json").read_text())
        self.assertEqual(host["checkout"], str(self.repo))

    def test_recorded_checkout_is_used_when_none_is_given(self):
        self.write_host_json(checkout=str(self.repo), maintainer=False)
        result = self.apply()
        self.assertEqual(result.returncode, 0, self.output(result))
        builds = self.matching(lambda a: a[:2] == ["nix", "build"])
        self.assertTrue(builds)
        for call in builds:
            self.assertTrue(call["argv"][-1].startswith(str(self.repo / "chromeos-dev-setup")))

    def test_directory_with_spaces_is_passed_as_one_argument(self):
        checkout = self.make_checkout(self.root / "checkout with spaces")
        result = self.apply("--checkout", str(checkout))
        self.assertEqual(result.returncode, 0, self.output(result))
        expected = f"{checkout}/chromeos-dev-setup#system-deb"
        build = next(
            call for call in self.calls()
            if call["argv"][:2] == ["nix", "build"] and call["argv"][-1].endswith("#system-deb")
        )
        self.assertEqual(build["argv"][-1], expected)
        self.assertEqual(build["argv"].count(expected), 1)

    def test_plain_directory_that_is_not_a_checkout_is_refused(self):
        plain = self.root / "not a checkout"
        plain.mkdir()
        result = self.apply("--checkout", str(plain))
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("not a Git checkout", result.stderr)
        self.assertEqual(self.matching(lambda a: a[0] != "git"), [])

    def test_checkout_without_the_flake_is_refused(self):
        bare = self.root / "checkout without flake"
        bare.mkdir()
        (bare / "README.md").write_text("no flake here\n")
        self.git("-C", str(bare), "init", "--initial-branch=main")
        self.commit(bare, "Initial")
        result = self.apply("--checkout", str(bare))
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn(f"missing {bare}/chromeos-dev-setup/flake.nix", result.stderr)


class Apply(CrosTestCase):
    def test_order_system_package_host_data_build_activation(self):
        result = self.apply("--checkout", str(self.repo))
        self.assertEqual(result.returncode, 0, self.output(result))

        system_build = self.index(
            lambda a: a[:2] == ["nix", "build"] and a[-1].endswith("#system-deb")
        )
        install = self.index(lambda a: a[0] == "apt-get" and "install" in a)
        home_build = self.index(
            lambda a: a[:2] == ["nix", "build"] and "#homeConfigurations" in a[-1]
        )
        activate = self.index(lambda a: a[0] == "activate")
        # Both builds succeed before anything is installed or activated.
        self.assertLess(system_build, home_build)
        self.assertLess(home_build, install)
        self.assertLess(install, activate)

        system_call = self.calls()[system_build]
        self.assertIs(system_call["host_json_exists"], False)
        # GTK is looked up only in the x86_64 directory, never i386.
        finds = [argv for argv in self.argvs() if argv[0] == "find"]
        self.assertTrue(finds)
        self.assertTrue(all(argv[1] == "/usr/lib/x86_64-linux-gnu" for argv in finds))
        home_call = self.calls()[home_build]
        self.assertIsNotNone(home_call["host_json"])
        self.assertEqual(home_call["host_json"]["checkout"], str(self.repo))

        host = json.loads((self.host_dir / "host.json").read_text())
        self.assertEqual(host["username"], "cros-test-user")
        self.assertEqual(host["homeDirectory"], str(self.home))
        self.assertEqual(host["checkout"], str(self.repo))
        self.assertEqual(host["gtk3Cache"], str(self.gtk3))
        self.assertEqual(host["gtk4Path"], str(self.gtk4))
        self.assertIs(host["maintainer"], False)
        self.assertEqual(
            sorted(host),
            ["checkout", "gtk3Cache", "gtk4Path", "homeDirectory", "maintainer", "username"],
        )
        # Atomic replace: only host.json is left behind, no mktemp leftovers.
        self.assertEqual(list(self.host_dir.iterdir()), [self.host_dir / "host.json"])

        home_argv = self.argvs()[home_build]
        override = home_argv.index("--override-input")
        self.assertEqual(home_argv[override + 1:override + 3], ["host", f"path:{self.host_dir}"])
        self.assertIn("--accept-flake-config", home_argv)

        activation = self.calls()[activate]
        self.assertEqual(activation["backup"], "backup")
        self.assertIn("applied", result.stderr)

    def test_failed_host_data_write_leaves_the_previous_file_intact(self):
        # host.json is replaced atomically: a failed write must not truncate it.
        if os.geteuid() == 0:
            self.skipTest("root ignores the directory permissions this test needs")
        self.write_host_json(checkout=str(self.repo), maintainer=True, username="previous")
        before = (self.host_dir / "host.json").read_bytes()
        os.chmod(self.host_dir, 0o555)
        self.addCleanup(os.chmod, self.host_dir, 0o755)
        result = self.apply("--checkout", str(self.repo))
        self.assertNotEqual(result.returncode, 0, self.output(result))
        self.assertEqual((self.host_dir / "host.json").read_bytes(), before)
        self.assertEqual(list(self.host_dir.iterdir()), [self.host_dir / "host.json"])

    def test_current_system_package_is_not_reinstalled(self):
        environment = dict(self.environment)
        environment["CROS_TEST_INSTALLED_VERSION"] = "1.0.0"
        result = self.apply("--checkout", str(self.repo), environment=environment)
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("system package 1.0.0 is current", result.stderr)
        self.assertEqual(self.matching(lambda a: a[0] == "apt-get"), [])

    def test_half_configured_system_package_is_reinstalled(self):
        # A failed postinst leaves the same version with status "iF".
        environment = dict(self.environment)
        environment["CROS_TEST_INSTALLED_VERSION"] = "1.0.0"
        environment["CROS_TEST_INSTALLED_STATUS"] = "iF "
        result = self.apply("--checkout", str(self.repo), environment=environment)
        self.assertEqual(result.returncode, 0, self.output(result))
        deb = str(self.audit / "system-deb-out" / "cros-dev-system.deb")
        self.assertIn(
            ["apt-get", "install", "-y", "--allow-downgrades", deb],
            self.argvs(),
        )
        self.assertIn(
            "installing system package 1.0.0 (installed: iF 1.0.0)", result.stderr
        )

    def test_changed_system_package_installs_with_allow_downgrades(self):
        environment = dict(self.environment)
        environment["CROS_TEST_INSTALLED_VERSION"] = "0.9.0"
        result = self.apply("--checkout", str(self.repo), environment=environment)
        self.assertEqual(result.returncode, 0, self.output(result))
        deb = str(self.audit / "system-deb-out" / "cros-dev-system.deb")
        self.assertIn(
            ["apt-get", "install", "-y", "--allow-downgrades", deb],
            self.argvs(),
        )
        self.assertIn(
            ["sudo", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install",
             "-y", "--allow-downgrades", deb],
            self.argvs(),
        )
        self.assertIn("installing system package 1.0.0 (installed: ii 0.9.0)", result.stderr)

    def test_maintainer_is_preserved_and_set_by_flag(self):
        scenarios = [
            ("default", None, False),
            ("preserved", True, True),
            ("flag", False, True),
        ]
        for label, recorded, expected in scenarios:
            with self.subTest(scenario=label):
                (self.host_dir / "host.json").unlink(missing_ok=True)
                if recorded is not None:
                    self.write_host_json(checkout=str(self.repo), maintainer=recorded)
                arguments = ["--checkout", str(self.repo)]
                if label == "flag":
                    arguments.append("--maintainer")
                result = self.apply(*arguments)
                self.assertEqual(result.returncode, 0, self.output(result))
                host = json.loads((self.host_dir / "host.json").read_text())
                self.assertIs(host["maintainer"], expected)

    def test_gtk3_cache_without_cros_im_entry_is_refused(self):
        cache = self.audit / "cache without cros"
        cache.write_text('"im-ibus.so"\n')
        environment = dict(self.environment)
        environment["CROS_TEST_GTK3"] = str(cache)
        result = self.apply("--checkout", str(self.repo), environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("GTK3's immodules cache", result.stderr)
        self.assertFalse((self.host_dir / "host.json").exists())

    def test_missing_gtk3_cache_is_refused(self):
        environment = dict(self.environment)
        environment.pop("CROS_TEST_GTK3")
        result = self.apply("--checkout", str(self.repo), environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("GTK3's immodules cache", result.stderr)

    def test_missing_gtk4_directory_is_refused(self):
        environment = dict(self.environment)
        environment.pop("CROS_TEST_GTK4")
        result = self.apply("--checkout", str(self.repo), environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("GTK4's module directory", result.stderr)

    def test_desktop_services_are_started_without_stopping_them(self):
        result = self.apply("--checkout", str(self.repo))
        self.assertEqual(result.returncode, 0, self.output(result))
        systemctl = [argv for argv in self.argvs() if argv[0] == "systemctl" and "show" not in argv]
        self.assertEqual(systemctl, [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "reset-failed", "earlyoom.service", *SESSION_UNITS],
            ["systemctl", "--user", "start", "earlyoom.service", *SESSION_UNITS],
        ])

    def test_failed_apply_keeps_following_the_previous_checkout(self):
        self.write_host_json(checkout=str(self.repo), maintainer=True)
        before = (self.host_dir / "host.json").read_bytes()
        worktree = self.root / "test worktree"
        self.git("-C", str(self.repo), "worktree", "add", "-b", "try", str(worktree))
        for variable in ("CROS_TEST_HOME_BUILD_EXIT", "CROS_TEST_ACTIVATE_EXIT"):
            with self.subTest(failure=variable):
                environment = dict(self.environment)
                environment[variable] = "1"
                result = self.apply("--checkout", str(worktree), environment=environment)
                self.assertEqual(result.returncode, 1, self.output(result))
                self.assertIn(f"this machine still follows {self.repo}", result.stderr)
                self.assertEqual((self.host_dir / "host.json").read_bytes(), before)
                if variable == "CROS_TEST_HOME_BUILD_EXIT":
                    # Nothing is installed when a build fails.
                    self.assertEqual(self.matching(lambda a: a[0] == "apt-get"), [])
                self.assertEqual(list(self.host_dir.iterdir()), [self.host_dir / "host.json"])

    def test_successful_apply_follows_the_new_checkout(self):
        self.write_host_json(checkout=str(self.repo), maintainer=False)
        worktree = self.root / "test worktree"
        self.git("-C", str(self.repo), "worktree", "add", "-b", "try", str(worktree))
        result = self.apply("--checkout", str(worktree))
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertNotIn("restored", result.stderr)
        host = json.loads((self.host_dir / "host.json").read_text())
        self.assertEqual(host["checkout"], str(worktree))

    def add_gpu_setup(self):
        """Give the activation package Home Manager's GPU setup, as a store path."""
        package = self.audit / "non-nixos-gpu"
        (package / "bin").mkdir(parents=True)
        (package / "lib" / "tmpfiles.d").mkdir(parents=True)
        (package / "lib" / "tmpfiles.d" / "non-nixos-gpu.conf").write_text("L+ /run/opengl-driver\n")
        setup = package / "bin" / "non-nixos-gpu-setup"
        setup.write_text(
            "#!/usr/bin/env bash\n"
            "printf '{\"argv\": [\"non-nixos-gpu-setup\"]}\\n' >> \"$CROS_TEST_AUDIT/calls.jsonl\"\n"
        )
        setup.chmod(0o755)
        home_path = self.audit / "activation" / "home-path" / "bin"
        home_path.mkdir(parents=True)
        (home_path / "non-nixos-gpu-setup").symlink_to(setup)
        return package / "lib" / "tmpfiles.d" / "non-nixos-gpu.conf"

    def test_stale_gpu_driver_link_is_updated_after_activation(self):
        self.add_gpu_setup()
        result = self.apply("--checkout", str(self.repo))
        self.assertEqual(result.returncode, 0, self.output(result))
        activate = self.index(lambda a: a[0] == "activate")
        setup = self.index(lambda a: a[0] == "non-nixos-gpu-setup")
        self.assertLess(activate, setup)
        self.assertIn(["sudo", str(self.audit / "non-nixos-gpu" / "bin" / "non-nixos-gpu-setup")], self.argvs())

    def test_current_gpu_driver_link_is_left_alone(self):
        conf = self.add_gpu_setup()
        (self.audit / "etc-non-nixos-gpu.conf").symlink_to(conf)
        result = self.apply("--checkout", str(self.repo))
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(self.matching(lambda a: a[0] == "non-nixos-gpu-setup"), [])

    def test_units_that_are_not_loaded_are_skipped(self):
        environment = dict(self.environment)
        environment["CROS_TEST_MISSING_UNITS"] = "atuin-daemon.service sommelier-x@1.service"
        result = self.apply("--checkout", str(self.repo), environment=environment)
        self.assertEqual(result.returncode, 0, self.output(result))
        present = [u for u in SESSION_UNITS if u not in ("atuin-daemon.service", "sommelier-x@1.service")]
        systemctl = [argv for argv in self.argvs() if argv[0] == "systemctl" and "show" not in argv]
        self.assertEqual(systemctl[-1], ["systemctl", "--user", "start", "earlyoom.service", *present])

    def test_unknown_apply_option_is_refused(self):
        result = self.apply("--frobnicate")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("unknown apply option: --frobnicate", result.stderr)
        self.assertEqual(self.argvs(), [])

    def test_checkout_option_without_a_value_is_refused(self):
        result = self.apply("--checkout")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("--checkout needs a directory", result.stderr)


class Sync(CrosTestCase):
    def setUp(self):
        super().setUp()
        self.write_host_json(checkout=str(self.repo), maintainer=False)
        self.add_origin()

    def test_pulls_upgrades_and_then_runs_apply(self):
        result = self.run_cros("sync")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn(
            ["git", "-C", str(self.repo), "pull", "--ff-only", "origin", "main"],
            self.argvs(),
        )
        apt = [argv for argv in self.argvs() if argv[0] == "apt-get"]
        self.assertEqual(apt, [
            ["apt-get", "update"],
            ["apt-get", "full-upgrade", "-y"],
            ["apt-get", "autoremove", "-y"],
        ])
        self.assertIn(
            ["sudo", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "full-upgrade", "-y"],
            self.argvs(),
        )
        self.assertIn(["determinate-nixd", "upgrade"], self.argvs())
        self.assertEqual(self.argvs()[-1], [
            "nix", "run", "--accept-flake-config",
            f"{self.repo}/chromeos-dev-setup#cros", "--", "apply",
        ])

    def test_refuses_a_branch_other_than_main_before_pulling(self):
        self.git("-C", str(self.repo), "switch", "-c", "employee-work")
        result = self.run_cros("sync")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("requires the main branch", result.stderr)
        self.assert_no_pull_or_sudo()

    def test_refuses_dirty_tracked_and_untracked_files_before_pulling(self):
        for label in ("tracked", "untracked"):
            with self.subTest(label=label):
                self.git("-C", str(self.repo), "reset", "--hard", "--quiet")
                (self.repo / "employee notes.txt").unlink(missing_ok=True)
                target = (
                    self.repo / "README.md"
                    if label == "tracked"
                    else self.repo / "employee notes.txt"
                )
                target.write_text("keep this\n")
                (self.audit / "calls.jsonl").unlink(missing_ok=True)
                result = self.run_cros("sync")
                self.assertEqual(result.returncode, 1, self.output(result))
                self.assertIn("clean checkout, including untracked files", result.stderr)
                self.assertEqual(target.read_text(), "keep this\n")
                self.assert_no_pull_or_sudo()

    def test_detached_head_is_refused(self):
        self.git("-C", str(self.repo), "checkout", "--detach")
        result = self.run_cros("sync")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("detached HEAD", result.stderr)
        self.assert_no_pull_or_sudo()

    def test_determinate_nix_upgrade_failure_is_only_a_warning(self):
        environment = dict(self.environment)
        environment["CROS_TEST_NIXD_EXIT"] = "1"
        result = self.run_cros("sync", environment=environment)
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("Determinate Nix upgrade failed", result.stderr)
        self.assertEqual(self.argvs()[-1][:2], ["nix", "run"])

    def test_sync_takes_no_arguments(self):
        result = self.run_cros("sync", "extra")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("sync takes no arguments", result.stderr)

    def assert_no_pull_or_sudo(self):
        self.assertFalse(any("pull" in argv for argv in self.argvs()))
        self.assertFalse(any(argv[0] in {"sudo", "apt-get"} for argv in self.argvs()))


class Bump(CrosTestCase):
    def setUp(self):
        super().setUp()
        self.write_host_json(checkout=str(self.repo), maintainer=False)
        self.remote = self.add_origin()
        self.lock = self.repo / "chromeos-dev-setup" / "flake.lock"

    def test_already_current_lock_makes_no_commit(self):
        head = self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout
        environment = dict(self.environment)
        environment["CROS_TEST_UPDATE_CHANGE"] = "0"
        result = self.run_cros("bump", environment=environment)
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("flake inputs are already current", result.stderr)
        self.assertEqual(self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout, head)
        self.assertEqual(self.matching(lambda a: a[:2] == ["nix", "run"]), [])
        # nixpkgs keeps its commit (the cooldown candidate is older); the
        # path inputs are updated without a cooldown.
        self.assertIn("cooldown: nixpkgs keeps its locked commit", result.stderr)
        self.assertIn(
            ["nix", "flake", "update", "--accept-flake-config", "kakaotalk", "host",
             "--flake", str(self.repo / "chromeos-dev-setup")],
            self.argvs(),
        )

    def test_changed_lock_applies_and_commits_only_the_lock(self):
        original = self.lock.read_bytes()
        result = self.run_cros("bump")
        self.assertEqual(result.returncode, 0, self.output(result))
        apply_call = next(
            call for call in self.calls()
            if call["argv"][:2] == ["nix", "run"]
        )
        self.assertEqual(
            apply_call["argv"],
            ["nix", "run", "--accept-flake-config",
             f"{self.repo}/chromeos-dev-setup#cros", "--", "apply"],
        )
        # The nested apply must not print its own reminders.
        self.assertEqual(apply_call["no_reminders"], "1")
        message = self.git("-C", str(self.repo), "log", "-1", "--format=%B").stdout
        self.assertIn("Inputs:", message)
        # Only nixpkgs changed; path inputs and unchanged nodes are not listed.
        self.assertIn(
            "Inputs:\n- nixpkgs: aaaaaaaaaaaa (2020-01-01) -> cccccccccccc (2021-01-01)\n",
            message + "\n",
        )
        self.assertIn("Cooldown: third-party inputs at their newest commit before", message)
        self.assertIn("- nixpkgs:", result.stdout)
        self.assertEqual(
            self.git("-C", str(self.repo), "show", "--name-only", "--format=").stdout.strip(),
            "chromeos-dev-setup/flake.lock",
        )
        # The updated lock is kept and is exactly what was committed.
        self.assertNotEqual(self.lock.read_bytes(), original)
        self.assertEqual(
            self.git("-C", str(self.repo), "show", "HEAD:chromeos-dev-setup/flake.lock").stdout,
            self.lock.read_text(),
        )
        self.assertEqual(
            self.git("-C", str(self.repo), "status", "--porcelain").stdout, ""
        )
        self.assertIn("run 'cros publish'", result.stderr)

    def test_apply_failure_restores_the_lock_and_makes_no_commit(self):
        head = self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout
        original = self.lock.read_bytes()
        environment = dict(self.environment)
        environment["CROS_TEST_RUN_EXIT"] = "1"
        result = self.run_cros("bump", environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("apply failed with the updated inputs", result.stderr)
        self.assertIn("restored the previous flake.lock", result.stderr)
        self.assertEqual(self.lock.read_bytes(), original)
        self.assertEqual(self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout, head)

    def test_failing_commit_restores_the_lock_and_makes_no_commit(self):
        # A pre-commit hook can reject the lock after a successful apply.
        head = self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout
        original = self.lock.read_bytes()
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/usr/bin/env bash\nexit 1\n")
        hook.chmod(0o755)
        result = self.run_cros("bump")
        self.assertNotEqual(result.returncode, 0, self.output(result))
        self.assertIn("restored the previous flake.lock", result.stderr)
        self.assertEqual(self.lock.read_bytes(), original)
        self.assertEqual(self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout, head)
        self.assertEqual(
            self.git("-C", str(self.repo), "status", "--porcelain").stdout, ""
        )

    def test_selected_inputs_are_passed_to_flake_update(self):
        result = self.run_cros("bump", "nixpkgs", "kakaotalk")
        self.assertEqual(result.returncode, 0, self.output(result))
        update = next(argv for argv in self.argvs() if argv[:3] == ["nix", "flake", "update"])
        self.assertEqual(
            update,
            ["nix", "flake", "update", "--accept-flake-config", "kakaotalk",
             "--override-input", "nixpkgs", "github:NixOS/nixpkgs/" + "c" * 40,
             "--flake", str(self.repo / "chromeos-dev-setup")],
        )
        self.assertNotIn(["host"], [argv[4:5] for argv in self.argvs() if argv[:3] == ["nix", "flake", "update"]])

    def test_cooldown_asks_github_for_the_newest_commit_before_the_cutoff(self):
        result = self.run_cros("bump", "nixpkgs")
        self.assertEqual(result.returncode, 0, self.output(result))
        [curl] = [argv for argv in self.argvs() if argv[0] == "curl"]
        url = curl[-1]
        self.assertTrue(url.startswith("https://api.github.com/repos/NixOS/nixpkgs/commits?until="), url)
        self.assertIn("&per_page=1&sha=nixpkgs-unstable", url)
        cutoff = datetime.datetime.strptime(url.split("until=")[1].split("&")[0], "%Y-%m-%dT%H:%M:%SZ")
        age = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - cutoff
        self.assertAlmostEqual(age.total_seconds() / 86400, 7, delta=0.01)
        # Only the pinned input: `nix flake lock`, never an update of everything.
        [lock] = [argv for argv in self.argvs() if argv[:2] == ["nix", "flake"]]
        self.assertEqual(lock[:3], ["nix", "flake", "lock"])
        self.assertIn("cooldown: nixpkgs at " + "c" * 40, result.stderr)

    def test_no_cooldown_updates_every_input_without_github(self):
        result = self.run_cros("bump", "--no-cooldown")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(self.matching(lambda a: a[0] == "curl"), [])
        self.assertIn(
            ["nix", "flake", "update", "--accept-flake-config", "nixpkgs", "kakaotalk",
             "host", "--flake", str(self.repo / "chromeos-dev-setup")],
            self.argvs(),
        )
        message = self.git("-C", str(self.repo), "log", "-1", "--format=%B").stdout
        self.assertNotIn("Cooldown:", message)

    def test_inputs_of_the_repository_owner_skip_the_cooldown(self):
        # origin's configured URL names the owner; insteadOf keeps it fetchable.
        url = "https://github.com/NixOS/monorepo-public.git"
        self.git("-C", str(self.repo), "remote", "set-url", "origin", url)
        self.git("-C", str(self.repo), "config", f"url.{self.remote}.insteadOf", url)
        result = self.run_cros("bump", "nixpkgs")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(self.matching(lambda a: a[0] == "curl"), [])
        self.assertIn(
            ["nix", "flake", "update", "--accept-flake-config", "nixpkgs",
             "--flake", str(self.repo / "chromeos-dev-setup")],
            self.argvs(),
        )

    def test_github_failure_changes_nothing(self):
        head = self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout
        original = self.lock.read_bytes()
        environment = dict(self.environment)
        environment["CROS_TEST_CURL_FAIL"] = "1"
        result = self.run_cros("bump", environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("could not look up NixOS/nixpkgs's commits", result.stderr)
        self.assertEqual(self.matching(lambda a: a[:2] == ["nix", "flake"]), [])
        self.assertEqual(self.lock.read_bytes(), original)
        self.assertEqual(self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout, head)

    def test_unknown_input_and_option_are_refused(self):
        for arguments, message in ((["home-manager"], "no input named home-manager"),
                                   (["--frobnicate"], "unknown bump option: --frobnicate")):
            with self.subTest(arguments=arguments):
                result = self.run_cros("bump", *arguments)
                self.assertEqual(result.returncode, 1, self.output(result))
                self.assertIn(message, result.stderr)

    def push_from_another_clone(self, name, path, content):
        other = self.root / name
        self.git("clone", str(self.remote), str(other))
        (other / path).write_text(content)
        self.commit(other, f"Change {path} elsewhere")
        self.git("-C", str(other), "push", "origin", "main")
        return self.git("--git-dir", str(self.remote), "rev-parse", "main").stdout.strip()

    def test_published_commits_are_pulled_before_the_update(self):
        published = self.push_from_another_clone("other clone", "elsewhere.txt", "published\n")
        result = self.run_cros("bump")
        self.assertEqual(result.returncode, 0, self.output(result))
        pull = self.index(lambda a: a[:1] == ["git"] and "pull" in a)
        update = self.index(lambda a: a[:2] == ["nix", "flake"] and "update" in a)
        self.assertLess(pull, update)
        self.assertEqual(
            self.git("-C", str(self.repo), "rev-parse", "HEAD~1").stdout.strip(), published
        )

    def test_missing_git_identity_is_refused_before_anything_changes(self):
        strict = self.root / "gitconfig-strict"
        strict.write_text("[user]\n\tuseConfigOnly = true\n")
        environment = dict(self.environment)
        for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                    "GIT_COMMITTER_EMAIL", "EMAIL"):
            environment.pop(key, None)
        environment["GIT_CONFIG_GLOBAL"] = str(strict)
        result = self.run_cros("bump", environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("set your Git identity first", result.stderr)
        self.assertFalse(any("pull" in argv for argv in self.argvs()))
        self.assertEqual(self.matching(lambda a: a[:2] == ["nix", "flake"]), [])

    def test_pulled_commits_are_applied_when_inputs_are_current(self):
        self.push_from_another_clone("other clone", "elsewhere.txt", "published\n")
        environment = dict(self.environment)
        environment["CROS_TEST_UPDATE_CHANGE"] = "0"
        result = self.run_cros("bump", environment=environment)
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("applying the commits pulled from origin/main", result.stderr)
        self.assertEqual(len(self.matching(lambda a: a[:2] == ["nix", "run"])), 1)

    def test_diverged_main_is_refused_before_the_update(self):
        self.push_from_another_clone("other clone", "elsewhere.txt", "published\n")
        (self.repo / "local.txt").write_text("unpublished\n")
        self.commit(self.repo, "Local change")
        head = self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout
        original = self.lock.read_bytes()
        result = self.run_cros("bump")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("cannot fast-forward", result.stderr)
        self.assertEqual(self.matching(lambda a: a[:2] == ["nix", "flake"]), [])
        self.assertEqual(self.lock.read_bytes(), original)
        self.assertEqual(self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout, head)

    def test_refuses_non_main_and_dirty_checkouts(self):
        self.git("-C", str(self.repo), "switch", "-c", "work")
        branch = self.run_cros("bump")
        self.assertEqual(branch.returncode, 1, self.output(branch))
        self.assertIn("requires the main branch", branch.stderr)
        self.git("-C", str(self.repo), "switch", "main")
        (self.repo / "notes.txt").write_text("untracked\n")
        dirty = self.run_cros("bump")
        self.assertEqual(dirty.returncode, 1, self.output(dirty))
        self.assertIn("clean checkout", dirty.stderr)
        self.assertEqual(self.matching(lambda a: a[:2] == ["nix", "flake"]), [])


class Publish(CrosTestCase):
    def setUp(self):
        super().setUp()
        self.write_host_json(checkout=str(self.repo), maintainer=False)
        self.remote = self.add_origin()

    def remote_main(self):
        return self.git("--git-dir", str(self.remote), "rev-parse", "main").stdout.strip()

    def test_pushes_committed_work_to_origin_main(self):
        (self.repo / "change.txt").write_text("publish me\n")
        self.commit(self.repo, "A change")
        head = self.git("-C", str(self.repo), "rev-parse", "HEAD").stdout.strip()
        result = self.run_cros("publish")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("published 1 commit(s)", result.stderr)
        self.assertEqual(self.remote_main(), head)
        self.assertIn(
            ["git", "-C", str(self.repo), "push", "origin", "HEAD:main"],
            self.argvs(),
        )

    def test_nothing_to_publish_when_not_ahead(self):
        result = self.run_cros("publish")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("nothing to publish", result.stderr)
        self.assertFalse(any("push" in argv for argv in self.argvs()))

    def test_refuses_when_origin_has_commits_missing_locally(self):
        other = self.root / "other clone"
        self.git("clone", str(self.remote), str(other))
        (other / "elsewhere.txt").write_text("someone else\n")
        self.git("-C", str(other), "add", "--all")
        self.git("-C", str(other), "commit", "-m", "Elsewhere")
        self.git("-C", str(other), "push", "origin", "main")
        before = self.remote_main()
        result = self.run_cros("publish")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("run 'cros sync' first", result.stderr)
        self.assertEqual(self.remote_main(), before)
        self.assertFalse(any("push" in argv for argv in self.argvs()))

    def test_refuses_dirty_worktree(self):
        (self.repo / "README.md").write_text("dirty\n")
        result = self.run_cros("publish")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("clean checkout", result.stderr)
        self.assertFalse(any("push" in argv for argv in self.argvs()))

    def test_publish_takes_no_arguments(self):
        result = self.run_cros("publish", "extra")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("publish takes no arguments", result.stderr)


class Reminders(CrosTestCase):
    def bump_without_changes(self, maintainer=True, **extra):
        self.write_host_json(checkout=str(self.repo), maintainer=maintainer)
        environment = dict(self.environment)
        environment["CROS_TEST_UPDATE_CHANGE"] = "0"
        environment.update(extra)
        return self.run_cros("bump", environment=environment)

    def test_unpublished_commits_are_reported_to_maintainers(self):
        self.add_origin()
        (self.repo / "local.txt").write_text("local work\n")
        self.commit(self.repo, "Local change")
        result = self.bump_without_changes()
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("1 commit(s) are not published", result.stderr)
        self.assertIn("cros publish", result.stderr)

    def test_non_maintainers_get_no_reminders(self):
        self.add_origin()
        (self.repo / "local.txt").write_text("local work\n")
        self.commit(self.repo, "Local change")
        result = self.bump_without_changes(maintainer=False)
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertNotIn("reminder:", result.stderr)

    def test_no_reminders_variable_silences_them(self):
        self.add_origin()
        (self.repo / "local.txt").write_text("local work\n")
        self.commit(self.repo, "Local change")
        result = self.bump_without_changes(CROS_NO_REMINDERS="1")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertNotIn("reminder:", result.stderr)
        self.assertNotIn("not published", result.stderr)

    def test_branch_checkout_is_reported_instead_of_maintainer_reminders(self):
        self.write_host_json(checkout=str(self.repo), maintainer=True)
        self.add_origin()
        (self.repo / "local.txt").write_text("local work\n")
        self.commit(self.repo, "Local change")
        self.git("-C", str(self.repo), "switch", "-c", "work")
        result = self.apply()
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn(f"follows {self.repo} on work; switch it back to main", result.stderr)
        self.assertNotIn("not published", result.stderr)

    def test_applied_worktree_names_the_command_back_to_the_primary_checkout(self):
        worktree = self.root / "test worktree"
        self.git("-C", str(self.repo), "worktree", "add", "-b", "try", str(worktree))
        result = self.apply("--checkout", str(worktree))
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn(
            f"follows {worktree} on try; return with 'cros apply --checkout {self.repo}'",
            result.stderr,
        )

    def test_main_checkout_of_a_non_maintainer_gets_no_reminders(self):
        result = self.apply("--checkout", str(self.repo))
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertNotIn("reminder:", result.stderr)

    def test_shallow_repository_skips_the_lock_age_reminder(self):
        checkout = self.make_checkout(
            self.root / "old lock checkout",
            commit_date="2020-01-01T00:00:00 +0000",
        )
        remote = self.add_origin(checkout)
        shallow = self.root / "shallow clone"
        self.git("clone", "--depth", "1", remote.as_uri(), str(shallow))
        self.assertEqual(
            self.git("-C", str(shallow), "rev-parse", "--is-shallow-repository").stdout.strip(),
            "true",
        )
        self.write_host_json(checkout=str(shallow), maintainer=True)
        result = self.apply()
        self.assertEqual(result.returncode, 0, self.output(result))
        # The single old commit would otherwise look like a stale lock.
        self.assertNotIn("flake inputs were last updated", result.stderr)
        self.assertNotIn("reminder:", result.stderr)

    def test_old_lock_is_reported(self):
        checkout = self.make_checkout(
            self.root / "old lock checkout",
            commit_date="2020-01-01T00:00:00 +0000",
        )
        self.write_host_json(checkout=str(checkout), maintainer=True)
        remote = self.root / "origin remote.git"
        self.git("init", "--bare", "--initial-branch=main", str(remote))
        self.git("-C", str(checkout), "remote", "add", "origin", str(remote))
        self.git("-C", str(checkout), "push", "origin", "main")
        environment = dict(self.environment)
        environment["CROS_TEST_UPDATE_CHANGE"] = "0"
        result = self.run_cros("bump", environment=environment)
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("flake inputs were last updated", result.stderr)
        self.assertIn("run 'cros bump'", result.stderr)
        self.assertNotIn("not published", result.stderr)


class Reset(CrosTestCase):
    def test_reset_starts_units_without_stopping_them(self):
        result = self.run_cros("reset")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(
            [argv for argv in self.argvs() if argv[0] == "systemctl" and "show" not in argv],
            [
                ["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "reset-failed", "earlyoom.service", *SESSION_UNITS],
                ["systemctl", "--user", "start", "earlyoom.service", *SESSION_UNITS],
            ],
        )
        self.assertEqual(self.matching(lambda a: a[0] == "rm"), [])

    def test_reset_hard_stops_restarts_and_removes_wayland_sockets(self):
        result = self.run_cros("reset", "--hard")
        self.assertEqual(result.returncode, 0, self.output(result))
        systemctl = [argv for argv in self.argvs() if argv[0] == "systemctl" and "show" not in argv]
        self.assertEqual(systemctl, [
            ["systemctl", "--user", "stop", *SESSION_UNITS],
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "reset-failed", "earlyoom.service", *SESSION_UNITS],
            ["systemctl", "--user", "start", "earlyoom.service", *SESSION_UNITS],
        ])
        removals = [argv for argv in self.argvs() if argv[0] == "rm"]
        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0][1], "-f")
        self.assertEqual(len(removals[0]) - 2, 4)
        for path in removals[0][2:]:
            self.assertTrue(path.startswith(str(self.audit / "run") + "/"))
            self.assertIn("wayland-", path)

    def test_unknown_reset_option_is_refused(self):
        result = self.run_cros("reset", "--frobnicate")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("unknown reset option: --frobnicate", result.stderr)
        self.assertEqual(self.argvs(), [])


class CheckAndClean(CrosTestCase):
    def test_check_builds_both_without_installing_or_activating(self):
        self.write_host_json(checkout=str(self.repo), maintainer=False)
        before = (self.host_dir / "host.json").read_bytes()
        result = self.run_cros("check")
        self.assertEqual(result.returncode, 0, self.output(result))
        builds = [argv for argv in self.argvs() if argv[:2] == ["nix", "build"]]
        self.assertEqual(len(builds), 2, builds)
        self.assertTrue(builds[0][-1].endswith("#system-deb"))
        self.assertIn("#homeConfigurations", builds[1][-1])
        self.assertIn("build passed", result.stderr)
        self.assertEqual(self.matching(lambda a: a[0] in {"apt-get", "sudo"}), [])
        self.assertEqual(self.matching(lambda a: a[0] == "activate"), [])
        self.assertEqual((self.host_dir / "host.json").read_bytes(), before)

    def check_with(self, installed_version=None, active_profile=None):
        self.write_host_json(checkout=str(self.repo), maintainer=False)
        if active_profile:
            profile = self.home / ".local/state/nix/profiles/home-manager"
            profile.parent.mkdir(parents=True)
            profile.symlink_to(active_profile)
        environment = dict(self.environment)
        if installed_version:
            environment["CROS_TEST_INSTALLED_VERSION"] = installed_version
        return self.run_cros("check", environment=environment)

    def test_check_reports_a_machine_that_runs_the_build(self):
        result = self.check_with("1.0.0", self.audit / "activation")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("this machine runs this build", result.stderr)

    def test_check_names_each_part_the_machine_does_not_run(self):
        cases = [
            ("0.9.0", self.audit / "activation", "(system package)"),
            ("1.0.0", self.audit, "(Home Manager)"),
            (None, None, "(system package, Home Manager)"),
        ]
        for installed, profile, parts in cases:
            with self.subTest(parts=parts):
                shutil.rmtree(self.home / ".local", ignore_errors=True)
                result = self.check_with(installed, profile)
                self.assertEqual(result.returncode, 0, self.output(result))
                self.assertIn(f"does not run this build {parts}; run 'cros apply'", result.stderr)

    def test_check_reports_stale_gpu_drivers(self):
        package = self.audit / "non-nixos-gpu"
        (package / "bin").mkdir(parents=True)
        (package / "lib" / "tmpfiles.d").mkdir(parents=True)
        (package / "lib" / "tmpfiles.d" / "non-nixos-gpu.conf").write_text("L+\n")
        (package / "bin" / "non-nixos-gpu-setup").write_text("#!/bin/sh\n")
        (package / "bin" / "non-nixos-gpu-setup").chmod(0o755)
        (self.audit / "activation" / "home-path" / "bin").mkdir(parents=True)
        (self.audit / "activation" / "home-path" / "bin" / "non-nixos-gpu-setup").symlink_to(
            package / "bin" / "non-nixos-gpu-setup")
        result = self.check_with("1.0.0", self.audit / "activation")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("does not run this build (GPU drivers); run 'cros apply'", result.stderr)
        self.assertEqual(self.matching(lambda a: a[0] == "sudo"), [])

    def test_clean_keeps_recent_generations_and_optimises(self):
        result = self.run_cros("clean")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(self.argvs(), [
            ["nix-collect-garbage", "--delete-older-than", "14d"],
            ["nix", "store", "optimise"],
        ])


class Audit(CrosTestCase):
    """`cros audit` reads git history, generations, and apt; all local."""
    CHECKOUT_INITIAL = "2026-02-01T00:00:00Z"
    LOCK_CHANGED = "2026-03-10T00:00:00Z"

    def setUp(self):
        super().setUp()
        self.checkout = self.make_checkout(self.root / "audit checkout",
                                          commit_date=self.CHECKOUT_INITIAL)
        self.write_host_json(checkout=str(self.checkout), maintainer=False)
        self.lock = self.checkout / "chromeos-dev-setup" / "flake.lock"

    def add_input(self, name, owner, repo, soaked_days):
        """Adds a GitHub input pinned soaked_days before the lock commit."""
        lock = json.loads(self.lock.read_text())
        lock["nodes"]["root"]["inputs"][name] = name
        lock["nodes"][name] = {
            "original": {"type": "github", "owner": owner, "repo": repo, "ref": "main"},
            "locked": {
                "lastModified": epoch(self.LOCK_CHANGED) - soaked_days * 86400,
                "narHash": "sha256-test",
                "owner": owner,
                "repo": repo,
                "rev": name[0] * 40,
                "type": "github",
            },
        }
        self.lock.write_text(json.dumps(lock, indent=2) + "\n")
        self.commit(self.checkout, f"Pin {name}", date=self.LOCK_CHANGED)

    def add_generations(self, before, after):
        profiles = self.home / ".local/state/nix/profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        for number, path in ((1, before), (2, after)):
            path.mkdir(exist_ok=True)
            (profiles / f"home-manager-{number}-link").symlink_to(path)
        (profiles / "home-manager").symlink_to("home-manager-2-link")

    def test_inputs_pinned_with_enough_soak_report_no_findings(self):
        self.add_input("llm-agents", "numtide", "llm-agents", soaked_days=30)
        result = self.run_cros("audit")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("no findings", result.stderr)
        self.assertNotIn("Cooldown shortfall", self.output(result))
        # Only local reads: no build, no privilege, no network.
        self.assertEqual(self.matching(lambda a: a[0] in {"curl", "sudo", "nix"}), [])

    def test_input_pinned_inside_the_cooldown_is_reported(self):
        self.add_input("llm-agents", "numtide", "llm-agents", soaked_days=2)
        result = self.run_cros("audit")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("Cooldown shortfall", result.stdout)
        self.assertIn("- llm-agents: 2.0 days (pinned lllllll, 2026-03-08)", result.stdout)
        # The lock's own inputs (nixpkgs, pinned in 2020) are old enough.
        self.assertNotIn("- nixpkgs:", result.stdout)

    def test_inputs_of_the_repository_owner_and_paths_skip_the_cooldown(self):
        self.git("-C", str(self.checkout), "remote", "add", "origin",
                 "https://github.com/kkimdev/monorepo-public.git")
        self.add_input("sommelier-rs", "kkimdev", "sommelier-rs", soaked_days=0)
        result = self.run_cros("audit")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("no findings", result.stderr)
        self.assertNotIn("sommelier-rs", self.output(result))

    def test_downgrade_in_the_running_generation_is_reported(self):
        self.add_generations(self.audit / "generation-1", self.audit / "generation-2")
        environment = dict(self.environment)
        environment["CROS_TEST_DIFF_CLOSURES"] = "openssl: 3.5.1 → 3.5.0"
        result = self.run_cros("audit", environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("Downgrades in the running generation", result.stdout)
        self.assertIn("- openssl: 3.5.1 -> 3.5.0", result.stdout)
        [diff] = [argv for argv in self.argvs() if argv[:3] == ["nix", "store", "diff-closures"]]
        self.assertEqual(diff[3:], [str(self.audit / "generation-1"),
                                    str(self.audit / "generation-2")])

    def test_updates_between_generations_are_not_downgrades(self):
        self.add_generations(self.audit / "generation-1", self.audit / "generation-2")
        environment = dict(self.environment)
        environment["CROS_TEST_DIFF_CLOSURES"] = "python3: 3.14.9 → 3.14.10"
        result = self.run_cros("audit", environment=environment)
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("no findings", result.stderr)

    def test_pending_debian_security_updates_are_reported(self):
        environment = dict(self.environment)
        environment["CROS_TEST_APT_SECURITY"] = "openssl libc6"
        result = self.run_cros("audit", environment=environment)
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("Debian security updates pending (2)", result.stdout)
        self.assertIn("- openssl", result.stdout)
        self.assertIn("- libc6", result.stdout)
        # A simulation installs nothing.
        self.assertEqual(self.matching(lambda a: a[0] == "sudo"), [])
        self.assertIn(["-s", "full-upgrade"],
                      [argv[1:] for argv in self.argvs() if argv[0] == "apt-get"])

    def test_audit_takes_no_arguments(self):
        result = self.run_cros("audit", "extra")
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("audit takes no arguments", result.stderr)
        self.assertEqual(self.argvs(), [])


class SparseCheckout(CrosTestCase):
    def sparse_clone(self):
        remote = self.add_origin()
        checkout = self.root / "sparse checkout"
        self.git("clone", "--sparse", str(remote), str(checkout))
        self.git("-C", str(checkout), "sparse-checkout", "set", "chromeos-dev-setup")
        self.assertFalse((checkout / "nixpkgs").exists())
        return checkout

    def test_apply_adds_setup_and_lock_relative_paths(self):
        checkout = self.sparse_clone()
        result = self.apply("--checkout", str(checkout))
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn(
            ["git", "-C", str(checkout), "sparse-checkout", "add",
             "chromeos-dev-setup", "nixpkgs/kakaotalk"],
            self.argvs(),
        )
        listed = self.git("-C", str(checkout), "sparse-checkout", "list").stdout.split()
        self.assertIn("chromeos-dev-setup", listed)
        self.assertIn("nixpkgs/kakaotalk", listed)
        # host-example is inside chromeos-dev-setup and must not be re-added.
        self.assertNotIn("chromeos-dev-setup/host-example", listed)

    def test_full_checkouts_do_not_touch_sparse_checkout(self):
        result = self.apply("--checkout", str(self.repo))
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertEqual(self.matching(lambda a: "sparse-checkout" in a), [])


if __name__ == "__main__":
    unittest.main()
