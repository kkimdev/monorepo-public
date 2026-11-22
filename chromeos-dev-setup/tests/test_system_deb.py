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

# Exactly the bytes the old setup.bash wrote with its heredocs.
OLD_ANDROID_RULES = (
    "# adb 로 붙는 안드로이드 기기. Crostini 의 기본 노드는 root:root 0644 라 사용자가 열 수 없다.\n"
    'SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", MODE="0666"\n'
    'SUBSYSTEM=="usb", ATTR{idVendor}=="22b8", MODE="0666"\n'
)
OLD_TMP_MOUNT_OVERRIDE = (
    "[Mount]\n"
    "What=/var/lib/tmp\n"
    "Type=none\n"
    "Options=bind,nosuid,nodev\n"
    "DirectoryMode=1777\n"
)
OLD_TMPFILES_CONF = (
    "# Keep the vendor cleanup policy but remove short-lived build artifacts sooner.\n"
    "q /tmp 1777 root root 2d\n"
    "q /var/tmp 1777 root root 30d\n"
)

CROS_IM_CONTROL = (
    "Package: cros-im\n"
    "Version: 0.0.1\n"
    "Architecture: all\n"
    "Maintainer: test <test@example.invalid>\n"
    "Description: dummy cros-im for the cros-dev-system test\n"
)

PACKAGE_FILES = (
    "/usr/lib/udev/rules.d/51-cros-android.rules",
    "/usr/lib/tmpfiles.d/00-cros-tmp.conf",
    "/usr/lib/systemd/system/tmp.mount.d/50-cros-disk-backed.conf",
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
        for name in ("postinst", "postrm"):
            script = SYSTEM / "tree/DEBIAN" / name
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

    def prepare(self, tmpfiles_conf=OLD_TMPFILES_CONF):
        """Start a container with the old setup.bash files and a dummy cros-im."""
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

        self.write_file(
            container, "/etc/udev/rules.d/51-android.rules", OLD_ANDROID_RULES
        )
        self.write_file(
            container,
            "/etc/systemd/system/tmp.mount.d/override.conf",
            OLD_TMP_MOUNT_OVERRIDE,
        )
        self.write_file(container, "/etc/tmpfiles.d/tmp.conf", tmpfiles_conf)

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

    def assert_absent(self, container, path):
        result = self.exec_in(container, f"test ! -e {path}", check=False)
        self.assertEqual(result.returncode, 0, f"{path} still exists")

    def test_install_migrates_identical_files_and_wins_tmpfiles_order(self):
        container = self.prepare()
        output = self.install(container)

        self.assertIn("Skipping disk-backed /tmp configuration", output)
        for path in (
            "/etc/udev/rules.d/51-android.rules",
            "/etc/systemd/system/tmp.mount.d/override.conf",
            "/etc/tmpfiles.d/tmp.conf",
        ):
            self.assertIn(f"Removed obsolete {path}", output)
            self.assert_absent(container, path)
        self.assert_absent(container, "/etc/systemd/system/tmp.mount.d")

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

    def test_modified_file_is_kept_with_warning_and_purge_keeps_tmp(self):
        modified_tmpfiles = "# locally modified\nq /tmp 1777 root root 7d\n"
        container = self.prepare(tmpfiles_conf=modified_tmpfiles)
        self.exec_in(
            container, "mkdir -p /var/lib/tmp && touch /var/lib/tmp/user-data"
        )

        output = self.install(container)
        self.assertIn(
            "WARNING: /etc/tmpfiles.d/tmp.conf differs from the file "
            "the old setup.bash wrote; leaving it in place.",
            output,
        )
        self.assertEqual(
            self.exec_in(container, "cat /etc/tmpfiles.d/tmp.conf").stdout,
            modified_tmpfiles,
        )
        self.assert_absent(container, "/etc/udev/rules.d/51-android.rules")
        self.assert_absent(container, "/etc/systemd/system/tmp.mount.d/override.conf")
        self.assert_absent(container, "/etc/systemd/system/tmp.mount.d")

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
