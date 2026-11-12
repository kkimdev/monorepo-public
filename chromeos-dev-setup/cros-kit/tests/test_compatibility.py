"""Keep installer and early-boot contract parsing consistent without mounting."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from cros_kit import install
from tests import runtime_functions


SOURCE = runtime_functions()


class CompatibilityContractTests(unittest.TestCase):
    def test_both_readers_agree_on_literal_contracts_and_never_execute_them(self):
        supported = install.compatibility()
        plain = "".join(f"{key}={value}\n" for key, value in supported.items())
        entries = plain.splitlines()
        cases = [(plain, True), (plain.rstrip("\n"), True),
                 ("# comment\n\n" + plain, True),
                 ("\n".join(reversed(entries)) + "\n", True),
                 ("", False), ("\n".join(entries[1:]), False),
                 (plain + entries[0] + "\n", False),
                 (plain + "UNKNOWN=1\n", False),
                 (plain + " \n", False), (" # comment\n" + plain, False),
                 (plain.replace("\n", "\r\n"), False),
                 (plain.replace("\n", "\v"), False),
                 (plain.replace("FLEX_OS_VERSION=", "FLEX_OS_VERSION=\0"), False),
                 (plain.replace("FLEX_OS_VERSION=", "FLEX_OS_VER\0SION="), False),
                 ("# binary\0comment\n" + plain, False),
                 (plain.replace(supported["FLEX_OS_VERSION"], "1.2"), False),
                 (plain.replace(supported["FLEX_OS_VERSION"], "1.2.3.4"), False),
                 (plain.replace(supported["FLEX_KERNEL_RELEASE"], ".kernel"), False),
                 (plain.replace(supported["FLEX_KERNEL_RELEASE"], "-kernel"), False),
                 (plain.replace(supported["FLEX_ROOT_SHA256"], "a" * 63), False),
                 (plain.replace(supported["FLEX_STARTUP_SHA256"], "g" * 64), False),
                 (plain + 'touch "$FLEX_TEST_EXECUTED"\n', False),
                 (plain.replace(supported["FLEX_KERNEL_RELEASE"],
                                '$(touch "$FLEX_TEST_EXECUTED")'), False)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executed = root / "executed"
            environment = dict(os.environ, FLEX_TEST_RUNTIME=str(root),
                               FLEX_TEST_EXECUTED=str(executed), **supported)
            # All OS observations are fixtures; no host mounts or OS files change.
            harness = '''
RW_RUNTIME=$FLEX_TEST_RUNTIME
sed() { printf '%s\\n' "$FLEX_OS_VERSION"; }
uname() { printf '%s\\n' "$FLEX_KERNEL_RELEASE"; }
cat() { printf 'root_hexdigest=%s\\n' "$FLEX_ROOT_SHA256"; }
sha256sum() { printf '%s  startup.conf\\n' "$FLEX_STARTUP_SHA256"; }
rw_supported
'''
            for text, expected in cases:
                with self.subTest(contract=text):
                    (root / "compatibility.conf").write_bytes(text.encode())
                    try:
                        install.compatibility(root)
                        accepted = True
                    except ValueError:
                        accepted = False
                    result = subprocess.run(["/bin/sh", "-c", SOURCE + harness],
                                            env=environment, capture_output=True,
                                            text=True, timeout=5)
                    self.assertEqual(accepted, expected)
                    self.assertEqual(result.returncode == 0, expected, result.stderr)
                    self.assertFalse(executed.exists())


if __name__ == "__main__":
    unittest.main()
