"""Exercise the launcher with fake Wine and curl; no network or user prefix."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


RUNNER = Path(__file__).resolve().parents[1] / "kakaotalk-runner"
BASH = shutil.which("bash")
INSTALLER_URL = "https://example.invalid/KakaoTalk_Setup.exe"

STUB = r'''#!/usr/bin/python3
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with Path(os.environ["KAKAO_TEST_LOG"]).open("a") as log:
    log.write(json.dumps([name, *args]) + "\n")
prefix = Path(os.environ["WINEPREFIX"])
if name == "curl":
    target = Path(args[args.index("--output") + 1])
    target.write_bytes(b"test installer")
    sys.exit(int(os.environ.get("KAKAO_TEST_DOWNLOAD_EXIT", "0")))
if name == "wineboot":
    (prefix / "drive_c/users/test").mkdir(parents=True)
elif name == "wine":
    if args[0].endswith("KakaoTalk_Setup.exe"):
        if not Path(args[0]).is_file():
            raise SystemExit("Installer was not downloaded")
        status = int(os.environ.get("KAKAO_TEST_INSTALL_EXIT", "0"))
        if not status and os.environ.get("KAKAO_TEST_SKIP_EXE") != "1":
            exe = prefix / "drive_c/Program Files/Kakao/KakaoTalk/KakaoTalk.exe"
            exe.parent.mkdir(parents=True)
            exe.touch()
        sys.exit(status)
    elif args[0] != "reg" and not Path(args[0]).is_file():
        raise SystemExit("Attempted to launch absent KakaoTalk")
elif name == "pgrep":
    sys.exit(1)
elif name not in ("regedit", "sleep"):
    raise SystemExit("Unexpected test command: " + name)
'''


@unittest.skipUnless(BASH, "Bash is required for launcher tests")
class KakaoTalkRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="kakao runner test ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "commands/bin"
        self.bin.mkdir(parents=True)
        driver = self.bin / "test-driver"
        driver.write_text(STUB)
        driver.chmod(0o755)
        for command in ("wine", "wineboot", "regedit", "curl", "pgrep", "sleep"):
            (self.bin / command).symlink_to(driver)
        self.tmp = self.root / "temporary downloads"
        self.tmp.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.data = self.root / "application data"
        self.exe = self.data / "kakaotalk/drive_c/Program Files/Kakao/KakaoTalk/KakaoTalk.exe"
        self.log = self.root / "commands.jsonl"
        fonts = self.root / "fonts/share/fonts"
        fonts.mkdir(parents=True)
        (fonts / "Nanum.ttf").touch()
        script = RUNNER.read_text()
        for key, value in {
            "bash": str(Path(BASH).parent.parent),
            "wine": str(self.bin.parent),
            "curl": str(self.bin.parent),
            "nanum": str(fonts.parent.parent),
            "installerUrl": INSTALLER_URL,
        }.items():
            script = script.replace("@" + key + "@", value)
        self.runner = self.root / "kakaotalk"
        self.runner.write_text(script)
        self.environment = {
            "PATH": str(self.bin) + ":/usr/bin:/bin",
            "HOME": str(self.home),
            "TMPDIR": str(self.tmp),
            "XDG_DATA_HOME": str(self.data),
            "XMODIFIERS": "@im=test",
            "KAKAO_TEST_LOG": str(self.log),
        }

    def run_launcher(self, *args, **controls):
        result = subprocess.run(
            [BASH, str(self.runner), *args],
            env={**self.environment, **controls},
            capture_output=True, text=True, timeout=10,
        )
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(list(self.tmp.iterdir()), [], result.stderr)
        return result, calls

    def launches(self, calls):
        return [call for call in calls if call[:2] == ["wine", str(self.exe)]]

    def test_first_run_downloads_before_prefix_initialization_and_cleans_up(self):
        result, calls = self.run_launcher("--example-argument")
        self.assertEqual(result.returncode, 0, result.stderr)
        download = next(call for call in calls if call[0] == "curl")
        self.assertLess(calls.index(download), next(
            index for index, call in enumerate(calls) if call[0] == "wineboot"
        ))
        self.assertEqual(download[-1], INSTALLER_URL)
        self.assertIn("--fail", download)
        self.assertIn("--location", download)
        for option, value in (("--proto", "=https"), ("--proto-redir", "=https"),
                              ("--retry", "2"), ("--connect-timeout", "30")):
            self.assertEqual(download[download.index(option) + 1], value)
        downloaded_file = Path(download[download.index("--output") + 1])
        self.assertTrue(downloaded_file.is_relative_to(self.tmp))
        self.assertFalse(downloaded_file.exists())
        self.assertTrue(self.exe.is_file())
        self.assertEqual(self.launches(calls), [["wine", str(self.exe), "--example-argument"]])

    def test_existing_install_skips_download_and_prefix_initialization(self):
        self.exe.parent.mkdir(parents=True)
        self.exe.write_text("existing installation")
        result, calls = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[0] in ("curl", "wineboot", "regedit") for call in calls))
        self.assertEqual(self.exe.read_text(), "existing installation")
        self.assertEqual(self.launches(calls), [["wine", str(self.exe)]])

    def test_download_failure_preserves_uninitialized_prefix(self):
        result, calls = self.run_launcher(KAKAO_TEST_DOWNLOAD_EXIT="22")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([call[0] for call in calls], ["curl"])
        self.assertFalse((self.data / "kakaotalk").exists())

    def test_failed_installer_is_not_launched(self):
        result, calls = self.run_launcher(KAKAO_TEST_INSTALL_EXIT="7")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(call[0] == "wine" and call[-1] == "/S" for call in calls))
        self.assertEqual(self.launches(calls), [])

    def test_successful_installer_without_executable_is_rejected(self):
        result, calls = self.run_launcher(KAKAO_TEST_SKIP_EXE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(call[0] == "wine" and call[-1] == "/S" for call in calls))
        self.assertEqual(self.launches(calls), [])
        self.assertIn("KakaoTalk.exe", result.stderr)


if __name__ == "__main__":
    unittest.main()
