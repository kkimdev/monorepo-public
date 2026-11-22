"""Check that systemd units share the shells' PATH from home.sessionPath.

Apps the ChromeOS launcher starts through cros-garcon and the local-services
supervisor get PATH from home.nix, not from a shell. Both must start with
every home.sessionPath entry, in order, followed by the Nix profile bin; each
keeps its own system directories after that. The CI host data
(host-example) keeps the evaluation pure.

Run with:
    python3 -m unittest discover -s chromeos-dev-setup/tests -p 'test_home_path.py' -v
"""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


FLAKE = Path(__file__).resolve().parents[1]
EXTRA_ENTRY = "/opt/session-path-test/bin"

GARCON_SYSTEM_DIRS = [
    "/usr/local/sbin", "/usr/local/bin", "/usr/local/games",
    "/usr/sbin", "/usr/bin", "/usr/games", "/sbin", "/bin",
]
LOCAL_SERVICES_SYSTEM_DIRS = [
    "/nix/var/nix/profiles/default/bin", "/usr/local/bin", "/usr/bin", "/bin",
]

# Renders the relevant values of the CI configuration, as is and with one more
# home.sessionPath entry added by a module (as a host's local.nix would).
APPLY = """
configuration:
let
  view = c: {
    sessionPath = c.config.home.sessionPath;
    profileBin = "${c.config.home.profileDirectory}/bin";
    garcon = c.config.xdg.configFile."systemd/user/cros-garcon.service.d/override.conf".text;
    localServices = c.config.systemd.user.services.local-services.Service.Environment;
  };
in {
  base = view configuration;
  extended = view (configuration.extendModules {
    modules = [ { home.sessionPath = [ "%s" ]; } ];
  });
}
""" % EXTRA_ENTRY


def path_value(lines, prefix):
    """Return the PATH directories from the single line that sets it."""
    values = [line[len(prefix):] for line in lines if line.startswith(prefix)]
    if len(values) != 1:
        raise AssertionError(f"expected one {prefix!r} line, got {values}")
    return values[0].rstrip('"').split(":")


@unittest.skipUnless(shutil.which("nix"), "Nix is required to evaluate home.nix")
class SessionPathTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            [
                "nix", "eval", "--accept-flake-config", "--json",
                f"{FLAKE}#homeConfigurations.ci", "--apply", APPLY,
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if result.returncode != 0:
            raise AssertionError(f"nix eval failed:\n{result.stderr}")
        cls.views = json.loads(result.stdout)

    def assert_unit_paths(self, view):
        prefix = view["sessionPath"] + [view["profileBin"]]
        garcon = path_value(view["garcon"].splitlines(), 'Environment="PATH=')
        local_services = path_value(view["localServices"], "PATH=")
        self.assertEqual(garcon, prefix + GARCON_SYSTEM_DIRS)
        self.assertEqual(local_services, prefix + LOCAL_SERVICES_SYSTEM_DIRS)

    def test_units_start_with_session_path_and_profile_bin(self):
        view = self.views["base"]
        self.assertIn("/home/ci/.local/bin", view["sessionPath"])
        self.assert_unit_paths(view)

    def test_added_session_path_entry_reaches_the_units(self):
        view = self.views["extended"]
        self.assertIn(EXTRA_ENTRY, view["sessionPath"])
        self.assert_unit_paths(view)


if __name__ == "__main__":
    unittest.main()
