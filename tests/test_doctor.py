from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DOCTOR = ROOT / "scripts" / "doctor.sh"


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="skills doctor ")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        # Only these text utilities and our mocks are visible, never real clients.
        for name in ("awk", "sed"):
            executable = shutil.which(name)
            self.assertIsNotNone(executable)
            (self.bin / name).symlink_to(executable)
        self.mock("git", '[ "$*" = "--version" ] || exit 97\necho "git version 2.34.1"')
        self.mock_gh()
        self.mock("python3", '[ "$*" = "--version" ] || exit 97\necho "Python 3.13.0"')
        self.mock("ssh", '[ "$*" = "-V" ] || exit 97\necho "OpenSSH_9.0" >&2')
        for name in ("codex", "claude", "opencode", "dsh"):
            self.mock(name, f'[ "$*" = "--version" ] || exit 97\necho "{name} v2.2.7"')

    def mock(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def mock_gh(self, version: str = "2.102.0", help_text: str = "OpenCode (opencode)", help_status: int = 0) -> None:
        self.mock("gh", f'''case "$*" in
  --version) echo "gh version {version}" ;;
  'skill install --help') echo '{help_text}'; exit {help_status} ;;
  *) exit 97 ;;
esac''')

    def doctor(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        # Do not inherit credentials or the user's PATH/configuration.
        return subprocess.run(
            ["/bin/sh", str(DOCTOR), *arguments], cwd=self.home,
            env={"HOME": str(self.home), "PATH": str(self.bin), "LC_ALL": "C"},
            text=True, capture_output=True, check=False,
        )

    def test_all_available(self) -> None:
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[PASS] opencode: opencode v2.2.7", result.stdout)
        self.assertIn("[PASS] dsh: dsh v2.2.7", result.stdout)
        self.assertIn("[PASS] gh skill: opencode agent is available", result.stdout)
        self.assertIn("Summary: 10 passed, 0 warnings, 0 failures.", result.stdout)
        self.assertEqual({path.name for path in self.home.iterdir()}, {"bin"})

    def test_missing_optional_agents_only_warns(self) -> None:
        for name in ("codex", "claude", "opencode", "dsh", "ssh"):
            (self.bin / name).unlink()
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("codex", "claude", "opencode", "dsh", "ssh"):
            self.assertIn(f"[WARN] {name}: not found", result.stdout)
        self.assertIn("5 warnings, 0 failures.", result.stdout)

    def test_failed_opencode_version_only_warns(self) -> None:
        self.mock("opencode", "exit 1")
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[WARN] opencode: found at", result.stdout)
        self.assertIn("version check failed", result.stdout)

    def test_opencode_version_uses_last_nonempty_line(self) -> None:
        self.mock("opencode", 'printf "startup notice\\nopencode v2.2.7\\n\\n"')
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[PASS] opencode: opencode v2.2.7", result.stdout)

    def test_gh_without_opencode_support_warns(self) -> None:
        self.mock_gh(help_text="Codex (codex)")
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[WARN] gh skill: opencode agent is unavailable", result.stdout)
        self.assertIn("scripts/install-local.sh opencode", result.stdout)

    def test_old_gh_fails(self) -> None:
        self.mock_gh(version="2.89.0")
        result = self.doctor()
        self.assertEqual(result.returncode, 1)
        self.assertIn("older than required 2.90.0", result.stdout)

    def test_missing_gh_fails(self) -> None:
        (self.bin / "gh").unlink()
        result = self.doctor()
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL] gh: not found", result.stdout)

    def test_missing_skill_command_fails(self) -> None:
        self.mock_gh(help_status=1)
        result = self.doctor()
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL] gh skill: install command is unavailable", result.stdout)

    def test_unknown_arguments(self) -> None:
        result = self.doctor("--unknown")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
