"""Build and exercise the local `cros-dev-system` Debian package.

The build test does not depend on flake.nix: it calls the package expression
directly.  The install tests run inside a disposable debian:trixie podman
container and are skipped when podman is unavailable.

Run with:
    python3 -m unittest discover -s chromeos-dev-setup/tests -p 'test_system_deb.py' -v
"""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid


REPO = Path(__file__).resolve().parents[2]
SYSTEM = Path(__file__).resolve().parents[1] / "system"
PODMAN = shutil.which("podman")
IMAGE = "docker.io/library/debian:trixie"

CROS_IM_CONTROL = (
    "Package: cros-im\n"
    "Version: 0.0.1\n"
    "Architecture: all\n"
    "Maintainer: test <test@example.invalid>\n"
    "Description: dummy cros-im for the cros-dev-system test\n"
)

DEBIAN_SOURCES_LIST = "/etc/apt/sources.list.d/cros-dev-debian.list"

PACKAGE_FILES = (
    "/usr/lib/udev/rules.d/51-cros-android.rules",
    "/usr/lib/tmpfiles.d/00-cros-tmp.conf",
    "/usr/lib/systemd/system/tmp.mount.d/50-cros-disk-backed.conf",
    "/usr/lib/cros-dev-system/debian-sources",
    "/etc/apt/apt.conf.d/50cros-dev-system",
)


def run(command, **kwargs):
    """Run a command and capture text output, raising on failure."""
    return subprocess.run(
        command, check=True, capture_output=True, text=True, **kwargs
    )


def nix_expression(system_dir):
    """Evaluate the package expression without reading flake.nix."""
    return f"""
let
  pkgs = (builtins.getFlake "nixpkgs").legacyPackages.x86_64-linux;
in
pkgs.callPackage (builtins.toPath {json.dumps(str(system_dir))}) {{ }}
"""


def build_deb(system_dir=SYSTEM):
    """Build the package and return the path to the .deb file."""
    result = subprocess.run(
        [
            "nix",
            "build",
            "--no-link",
            "--print-out-paths",
            "--impure",
            "--expr",
            nix_expression(system_dir),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise AssertionError(
            "nix build failed:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    output = Path(result.stdout.strip().splitlines()[-1])
    return output / "cros-dev-system.deb"


def deb_field(deb, field):
    """Read one control field from a built .deb."""
    return run(["dpkg-deb", "-f", str(deb), field]).stdout.strip()


@unittest.skipUnless(shutil.which("nix"), "Nix is required to build the package")
class PackageBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deb = build_deb()

    def test_output_contains_exactly_one_deb(self):
        self.assertEqual(
            [entry.name for entry in self.deb.parent.iterdir()],
            ["cros-dev-system.deb"],
        )
        self.assertTrue(self.deb.is_file())

    def test_control_metadata(self):
        self.assertEqual(deb_field(self.deb, "Package"), "cros-dev-system")
        self.assertEqual(deb_field(self.deb, "Architecture"), "all")
        self.assertEqual(
            deb_field(self.deb, "Maintainer"),
            "chromeos-dev-setup <noreply@example.invalid>",
        )
        self.assertEqual(deb_field(self.deb, "Priority"), "optional")
        self.assertEqual(deb_field(self.deb, "Section"), "admin")
        self.assertEqual(
            deb_field(self.deb, "Depends"),
            "uidmap, cros-im, btrfs-progs, e2fsprogs, curl, ca-certificates, "
            "git, xz-utils, python3",
        )
        self.assertEqual(
            deb_field(self.deb, "Conflicts"),
            "vim, vim-tiny, command-not-found",
        )

    def test_version_is_tree_hash(self):
        version = deb_field(self.deb, "Version")
        self.assertRegex(version, r"^1\+[0-9a-z]{12}$")

    def test_building_twice_gives_same_version(self):
        self.assertEqual(
            deb_field(self.deb, "Version"),
            deb_field(build_deb(), "Version"),
        )

    def test_changing_a_debian_script_changes_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "system"
            shutil.copytree(SYSTEM, copy)
            postinst = copy / "tree/DEBIAN/postinst"
            postinst.write_text(
                postinst.read_text() + "\n# version-change probe\n"
            )
            self.assertNotEqual(
                deb_field(self.deb, "Version"),
                deb_field(build_deb(copy), "Version"),
            )

    def test_maintainer_scripts_are_valid_posix_sh(self):
        for script in (
            SYSTEM / "tree/DEBIAN/postinst",
            SYSTEM / "tree/DEBIAN/postrm",
            SYSTEM / "tree/usr/lib/cros-dev-system/debian-sources",
        ):
            result = subprocess.run(
                ["sh", "-n", str(script)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(shutil.which("nix"), "Nix is required to build the package")
@unittest.skipUnless(PODMAN, "podman is required for container tests")
class PackageContainerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if subprocess.run(
            [PODMAN, "info"], capture_output=True
        ).returncode != 0:
            raise unittest.SkipTest("podman is not usable")
        cls.deb = build_deb()
        cls.containers = []
        cls.image_preexisting = (
            subprocess.run(
                [PODMAN, "image", "exists", IMAGE],
                capture_output=True,
            ).returncode
            == 0
        )
        if not cls.image_preexisting:
            run([PODMAN, "pull", IMAGE], timeout=900)

    @classmethod
    def tearDownClass(cls):
        for name in cls.containers:
            subprocess.run(
                [PODMAN, "rm", "-f", name],
                capture_output=True,
            )
        if not cls.image_preexisting:
            subprocess.run(
                [PODMAN, "rmi", "-f", IMAGE],
                capture_output=True,
            )

    def exec_in(self, container, script, check=True, timeout=900):
        return subprocess.run(
            [PODMAN, "exec", container, "sh", "-c", script],
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )

    def write_file(self, container, path, content):
        subprocess.run(
            [
                PODMAN,
                "exec",
                "-i",
                container,
                "sh",
                "-c",
                'mkdir -p "$(dirname "$1")" && cat > "$1"',
                "sh",
                path,
            ],
            input=content,
            text=True,
            check=True,
        )

    def new_container(self):
        name = f"cros-dev-system-test-{uuid.uuid4().hex[:12]}"
        run(
            [PODMAN, "run", "-d", "--name", name, IMAGE, "sleep", "infinity"],
            timeout=120,
        )
        self.containers.append(name)
        return name

    def prepare(self):
        """Start a container with systemd, vim-tiny, and a dummy cros-im."""
        container = self.new_container()
        self.exec_in(
            container,
            "apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
            "systemd vim-tiny",
        )

        # Plain Debian has no cros-im, so satisfy the dependency locally.
        self.write_file(container, "/tmp/cros-im/DEBIAN/control", CROS_IM_CONTROL)
        self.exec_in(
            container,
            "dpkg-deb --build /tmp/cros-im /tmp/cros-im.deb >/dev/null && "
            "dpkg -i /tmp/cros-im.deb >/dev/null",
        )

        run(
            [PODMAN, "cp", str(self.deb), f"{container}:/tmp/cros-dev-system.deb"],
            timeout=120,
        )
        return container

    def install(self, container):
        result = self.exec_in(
            container,
            "CROS_DEV_SYSTEM_SKIP_TMP=1 DEBIAN_FRONTEND=noninteractive "
            "apt-get install -y --allow-downgrades /tmp/cros-dev-system.deb",
            check=False,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        return output

    def test_install_owns_its_files_and_wins_tmpfiles_order(self):
        container = self.prepare()
        output = self.install(container)

        self.assertIn("Skipping disk-backed /tmp configuration", output)

        version = self.exec_in(
            container, "dpkg-query -W -f='${Version}' cros-dev-system"
        ).stdout.strip()
        self.assertRegex(version, r"^1\+[0-9a-z]{12}$")

        uidmap = self.exec_in(
            container, "dpkg-query -W -f='${db:Status-Status}' uidmap"
        ).stdout.strip()
        self.assertEqual(uidmap, "installed")

        vim_tiny = self.exec_in(
            container,
            "dpkg-query -W -f='${Status}' vim-tiny 2>/dev/null || true",
        ).stdout
        self.assertNotIn("install ok installed", vim_tiny)

        for path in PACKAGE_FILES:
            owner = self.exec_in(container, f"dpkg -S {path}").stdout
            self.assertTrue(owner.startswith("cros-dev-system:"), owner)

        config = self.exec_in(
            container, "systemd-tmpfiles --cat-config 2>/dev/null"
        ).stdout
        headers = [
            line
            for line in config.splitlines()
            if line.startswith("# /usr/lib/tmpfiles.d/")
        ]
        self.assertLess(
            headers.index("# /usr/lib/tmpfiles.d/00-cros-tmp.conf"),
            headers.index("# /usr/lib/tmpfiles.d/tmp.conf"),
        )
        self.assertIn("q /tmp 1777 root root 2d", config)
        self.assertIn("q /var/tmp 1777 root root 30d", config)

        # systemd-tmpfiles reports duplicate paths while it parses its config
        # (--cat-config only concatenates in newer systemd releases).
        dry_run = self.exec_in(
            container, "systemd-tmpfiles --create --dry-run 2>&1 || true"
        ).stdout
        duplicate_tmp = [
            line
            for line in dry_run.splitlines()
            if "Duplicate line for path" in line and '"/tmp"' in line
        ]
        self.assertTrue(duplicate_tmp, dry_run)
        self.assertTrue(
            any("/usr/lib/tmpfiles.d/tmp.conf" in line for line in duplicate_tmp),
            duplicate_tmp,
        )
        self.assertFalse(
            any("00-cros-tmp.conf" in line for line in duplicate_tmp),
            duplicate_tmp,
        )

        # Step 1 is skipped above, so this exercises its non-btrfs guard alone.
        guard = self.exec_in(
            container,
            "sh /var/lib/dpkg/info/cros-dev-system.postinst configure",
            check=False,
        )
        self.assertNotEqual(guard.returncode, 0)
        self.assertIn("not btrfs", guard.stdout + guard.stderr)

    def sources_file(self, container):
        result = self.exec_in(
            container, f"cat {DEBIAN_SOURCES_LIST}", check=False
        )
        return result.stdout if result.returncode == 0 else None

    def test_debian_sources_follow_the_release_and_existing_sources(self):
        container = self.prepare()
        # The debian:trixie image already lists security and updates.
        self.install(container)
        self.assertIsNone(self.sources_file(container))
        hook = self.exec_in(container, "apt-config dump APT::Update::Pre-Invoke").stdout
        self.assertIn("/usr/lib/cros-dev-system/debian-sources", hook)

        # Crostini's image lists only `trixie main`.
        self.exec_in(
            container,
            "rm /etc/apt/sources.list.d/debian.sources && "
            "echo 'deb http://deb.debian.org/debian trixie main' > /etc/apt/sources.list",
        )
        # The hook writes the file; apt read its sources before the hook ran.
        self.exec_in(container, "apt-get update -qq")
        self.assertEqual(
            self.sources_file(container),
            "# Managed by cros-dev-system; rewritten before every apt update.\n"
            "deb https://deb.debian.org/debian-security trixie-security main\n"
            "deb https://deb.debian.org/debian trixie-updates main\n",
        )
        self.exec_in(container, "apt-get update -qq")
        policy = self.exec_in(container, "apt-cache policy").stdout
        self.assertIn("trixie-security", policy)
        self.assertIn("trixie-updates", policy)

        # A release upgrade changes the codename; the next update follows it.
        self.exec_in(
            container,
            "sed -i 's/^VERSION_CODENAME=.*/VERSION_CODENAME=forky/' /etc/os-release && "
            "sh /usr/lib/cros-dev-system/debian-sources",
        )
        self.assertIn("forky-security", self.sources_file(container))
        self.assertNotIn("trixie", self.sources_file(container))

        # A suite another file provides (even after a comment) is not repeated.
        self.exec_in(
            container,
            "printf '# deb x forky-updates main\\ndeb https://deb.debian.org/debian-security forky-security main\\n'"
            " >> /etc/apt/sources.list && sh /usr/lib/cros-dev-system/debian-sources",
        )
        self.assertEqual(
            self.sources_file(container),
            "# Managed by cros-dev-system; rewritten before every apt update.\n"
            "deb https://deb.debian.org/debian forky-updates main\n",
        )

        self.exec_in(container, "apt-get remove -y cros-dev-system")
        self.assertIsNone(self.sources_file(container))

    def test_purge_keeps_tmp_data(self):
        container = self.prepare()
        self.exec_in(
            container, "mkdir -p /var/lib/tmp && touch /var/lib/tmp/user-data"
        )
        self.install(container)

        purge = self.exec_in(
            container, "apt-get purge -y cros-dev-system", check=False
        )
        self.assertEqual(purge.returncode, 0, purge.stdout + purge.stderr)
        self.assertEqual(
            self.exec_in(container, "test -e /var/lib/tmp/user-data", check=False)
            .returncode,
            0,
        )
        self.assertNotEqual(
            self.exec_in(
                container,
                "dpkg -S /usr/lib/udev/rules.d/51-cros-android.rules",
                check=False,
            ).returncode,
            0,
        )


if __name__ == "__main__":
    unittest.main()
