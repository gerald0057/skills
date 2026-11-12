from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-local.sh"


class InstallLocalTests(unittest.TestCase):
    def test_all_installs_every_current_skill_and_is_repeatable(self) -> None:
        expected = {
            path.parent.name
            for path in (ROOT / "skills").glob("*/SKILL.md")
        }
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            command = ["sh", str(INSTALLER), "codex", "all"]

            first = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            second = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            installed = Path(home) / ".agents" / "skills"
            self.assertEqual({path.name for path in installed.iterdir()}, expected)
            self.assertEqual(second.stdout.count("already installed"), len(expected))

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

    def test_gx_gitlab_install_creates_only_skill_link(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            result = subprocess.run(
                ["sh", str(INSTALLER), "codex", "gx-gitlab"],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({path.name for path in Path(home).iterdir()}, {".agents"})
            installed = Path(home) / ".agents" / "skills" / "gx-gitlab"
            self.assertTrue(installed.is_symlink())
            self.assertEqual(installed.resolve(), (ROOT / "skills" / "gx-gitlab").resolve())
            self.assertFalse((Path(home) / ".config").exists())

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


class OpenCodeInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="skills install ")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / "home with spaces"
        self.home.mkdir()
        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)
        self.env.pop("XDG_CONFIG_HOME", None)
        self.target = self.home / ".config" / "opencode" / "skills"

    def install(self, *arguments: str, installer: Path = INSTALLER) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(installer), *arguments],
            cwd=self.base, env=self.env, text=True, capture_output=True, check=False,
        )

    def test_all_skills_and_repeat_install(self) -> None:
        expected = {path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")}
        first = self.install("opencode")
        second = self.install("opencode", "all")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual({path.name for path in self.target.iterdir()}, expected)
        self.assertEqual(second.stdout.count("already installed"), len(expected))
        for name in expected:
            self.assertTrue((self.target / name).is_symlink())
            self.assertEqual((self.target / name).resolve(), (ROOT / "skills" / name).resolve())

    def test_selected_skill_creates_only_skill_link(self) -> None:
        result = self.install("opencode", "gx-gitlab")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({path.name for path in self.home.iterdir()}, {".config"})
        self.assertEqual({path.name for path in (self.home / ".config").iterdir()}, {"opencode"})
        self.assertEqual({path.name for path in self.target.parent.iterdir()}, {"skills"})
        self.assertEqual({path.name for path in self.target.iterdir()}, {"gx-gitlab"})
        self.assertEqual((self.target / "gx-gitlab").resolve(), (ROOT / "skills" / "gx-gitlab").resolve())

    def test_custom_xdg_config_home(self) -> None:
        config = self.base / "custom config"
        self.env["XDG_CONFIG_HOME"] = str(config)
        result = self.install("opencode", "git-commit-guide")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (config / "opencode" / "skills" / "git-commit-guide").resolve(),
            (ROOT / "skills" / "git-commit-guide").resolve(),
        )
        self.assertEqual(list(self.home.iterdir()), [])

    def test_empty_xdg_config_home_uses_default(self) -> None:
        self.env["XDG_CONFIG_HOME"] = ""
        result = self.install("opencode", "git-commit-guide")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / "git-commit-guide").is_symlink())

    def test_all_agents_including_default(self) -> None:
        for arguments in (("all", "git-commit-guide"), ()):
            with self.subTest(arguments=arguments):
                result = self.install(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                for target in (
                    self.home / ".agents" / "skills",
                    self.home / ".claude" / "skills",
                    self.target,
                ):
                    self.assertEqual(
                        (target / "git-commit-guide").resolve(),
                        (ROOT / "skills" / "git-commit-guide").resolve(),
                    )

    def test_all_agents_respects_custom_xdg_config_home(self) -> None:
        config = self.base / "custom config"
        self.env["XDG_CONFIG_HOME"] = str(config)
        result = self.install("all", "git-commit-guide")
        self.assertEqual(result.returncode, 0, result.stderr)
        for target in (
            self.home / ".agents" / "skills",
            self.home / ".claude" / "skills",
            config / "opencode" / "skills",
        ):
            self.assertTrue((target / "git-commit-guide").is_symlink())
        self.assertFalse((self.home / ".config").exists())

    def test_refuses_existing_file_directory_and_links(self) -> None:
        self.target.mkdir(parents=True)
        for kind in ("file", "directory", "wrong-link", "dangling-link"):
            with self.subTest(kind=kind):
                name = "git-commit-guide"
                link = self.target / name
                existing = self.base / kind
                if kind == "file":
                    link.write_text("keep me", encoding="utf-8")
                elif kind == "directory":
                    link.mkdir()
                    (link / "keep").write_text("keep me", encoding="utf-8")
                else:
                    if kind == "wrong-link":
                        existing.mkdir()
                    link.symlink_to(existing)
                result = self.install("opencode", name)
                self.assertEqual(result.returncode, 1)
                self.assertIn("refusing to replace existing", result.stderr)
                if kind == "file":
                    self.assertEqual(link.read_text(encoding="utf-8"), "keep me")
                    link.unlink()
                elif kind == "directory":
                    self.assertEqual((link / "keep").read_text(encoding="utf-8"), "keep me")
                    (link / "keep").unlink()
                    link.rmdir()
                else:
                    self.assertEqual(link.readlink(), existing)
                    link.unlink()

    def test_unknown_skill_and_invalid_arguments(self) -> None:
        for arguments in (
            ("opencode", "missing-skill"),
            ("opencode", "../git-commit-guide"),
            ("unknown-agent",),
            ("opencode", "git-commit-guide", "extra"),
        ):
            with self.subTest(arguments=arguments):
                result = self.install(*arguments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("installed", result.stdout)

    def test_repository_path_with_spaces(self) -> None:
        repository = self.base / "repo with spaces"
        scripts = repository / "scripts"
        skill = repository / "skills" / "example"
        scripts.mkdir(parents=True)
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("example", encoding="utf-8")
        installer = scripts / "install-local.sh"
        shutil.copyfile(INSTALLER, installer)
        result = self.install("opencode", "example", installer=installer)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target / "example").resolve(), skill.resolve())


if __name__ == "__main__":
    unittest.main()
