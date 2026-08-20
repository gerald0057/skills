from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-local.sh"


class InstallLocalTests(unittest.TestCase):
    def test_installs_only_selected_skill(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            result = subprocess.run(
                ["sh", str(INSTALLER), "codex", "redmine-access"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            installed = Path(home) / ".agents" / "skills"
            self.assertEqual([path.name for path in installed.iterdir()], ["redmine-access"])
            self.assertEqual(
                (installed / "redmine-access").resolve(),
                (ROOT / "skills" / "redmine-access").resolve(),
            )

    def test_rejects_unknown_skill(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            result = subprocess.run(
                ["sh", str(INSTALLER), "codex", "missing-skill"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("Unknown skill: missing-skill", result.stderr)


if __name__ == "__main__":
    unittest.main()
