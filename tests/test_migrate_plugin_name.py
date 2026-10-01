from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "migrate-plugin-name.py"
SPEC = importlib.util.spec_from_file_location("migrate_plugin_name", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = migration
SPEC.loader.exec_module(migration)


def plugin_list(*identifiers: str, scope: str | None = None) -> dict:
    installed = []
    for identifier in identifiers:
        item = {"pluginId": identifier}
        if scope is not None:
            item = {"id": identifier, "scope": scope}
        installed.append(item)
    return {"installed": installed}


class MigratePluginNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codex = migration.build_client("codex", "user")

    def test_repository_metadata_matches_new_name(self) -> None:
        migration.validate_repository_metadata()

    def test_preview_does_not_run_mutating_commands(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_run(command, *, json_output=False):
            calls.append(tuple(command))
            self.assertTrue(json_output)
            return plugin_list(self.codex.old_id)

        output = io.StringIO()
        with mock.patch.object(migration, "run_command", side_effect=fake_run):
            with contextlib.redirect_stdout(output):
                migration.migrate_client(
                    self.codex,
                    apply=False,
                    skip_refresh=False,
                )

        self.assertEqual(calls, [self.codex.list_plugins])
        self.assertIn("would run: codex plugin marketplace upgrade", output.getvalue())
        self.assertIn("would run: codex plugin add", output.getvalue())
        self.assertIn("would run: codex plugin remove", output.getvalue())

    def test_install_failure_preserves_old_plugin(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_run(command, *, json_output=False):
            command = tuple(command)
            calls.append(command)
            if command == self.codex.list_plugins:
                return plugin_list(self.codex.old_id)
            if command == self.codex.install_new:
                raise migration.MigrationError("install failed")
            return None

        with mock.patch.object(migration, "run_command", side_effect=fake_run):
            with self.assertRaisesRegex(migration.MigrationError, "install failed"):
                migration.migrate_client(
                    self.codex,
                    apply=True,
                    skip_refresh=False,
                )

        self.assertNotIn(self.codex.remove_old, calls)

    def test_failed_install_verification_preserves_old_plugin(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_run(command, *, json_output=False):
            command = tuple(command)
            calls.append(command)
            if command == self.codex.list_plugins:
                return plugin_list(self.codex.old_id)
            return None

        with mock.patch.object(migration, "run_command", side_effect=fake_run):
            with self.assertRaisesRegex(
                migration.MigrationError,
                "old plugin was preserved",
            ):
                migration.migrate_client(
                    self.codex,
                    apply=True,
                    skip_refresh=False,
                )

        self.assertNotIn(self.codex.remove_old, calls)

    def test_apply_installs_new_before_removing_old(self) -> None:
        calls: list[tuple[str, ...]] = []
        states = iter(
            (
                plugin_list(self.codex.old_id),
                plugin_list(self.codex.old_id, self.codex.new_id),
                plugin_list(self.codex.new_id),
            )
        )

        def fake_run(command, *, json_output=False):
            command = tuple(command)
            calls.append(command)
            if command == self.codex.list_plugins:
                return next(states)
            return None

        with mock.patch.object(migration, "run_command", side_effect=fake_run):
            migration.migrate_client(
                self.codex,
                apply=True,
                skip_refresh=False,
            )

        self.assertLess(calls.index(self.codex.install_new), calls.index(self.codex.remove_old))
        self.assertEqual(calls.count(self.codex.list_plugins), 3)

    def test_existing_new_plugin_is_updated_before_old_is_removed(self) -> None:
        calls: list[tuple[str, ...]] = []
        states = iter(
            (
                plugin_list(self.codex.old_id, self.codex.new_id),
                plugin_list(self.codex.old_id, self.codex.new_id),
                plugin_list(self.codex.new_id),
            )
        )

        def fake_run(command, *, json_output=False):
            command = tuple(command)
            calls.append(command)
            if command == self.codex.list_plugins:
                return next(states)
            return None

        with mock.patch.object(migration, "run_command", side_effect=fake_run):
            migration.migrate_client(
                self.codex,
                apply=True,
                skip_refresh=False,
            )

        self.assertLess(
            calls.index(self.codex.refresh_marketplace),
            calls.index(self.codex.update_new),
        )
        self.assertLess(calls.index(self.codex.update_new), calls.index(self.codex.remove_old))

    def test_already_migrated_is_idempotent(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_run(command, *, json_output=False):
            calls.append(tuple(command))
            return plugin_list(self.codex.new_id)

        with mock.patch.object(migration, "run_command", side_effect=fake_run):
            migration.migrate_client(
                self.codex,
                apply=True,
                skip_refresh=False,
            )

        self.assertEqual(calls, [self.codex.list_plugins])

    def test_no_existing_installation_is_not_turned_into_new_install(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_run(command, *, json_output=False):
            calls.append(tuple(command))
            return plugin_list()

        with mock.patch.object(migration, "run_command", side_effect=fake_run):
            migration.migrate_client(
                self.codex,
                apply=True,
                skip_refresh=False,
            )

        self.assertEqual(calls, [self.codex.list_plugins])

    def test_claude_scope_is_used_for_commands_and_state(self) -> None:
        claude = migration.build_client("claude-code", "project")
        self.assertEqual(claude.scope, "project")
        self.assertEqual(claude.install_new[-2:], ("--scope", "project"))
        self.assertEqual(claude.update_new[-2:], ("--scope", "project"))
        self.assertIn("--keep-data", claude.remove_old)

        payload = {
            "installed": [
                {"id": claude.old_id, "scope": "user"},
                {"id": claude.new_id, "scope": "project"},
            ]
        }
        identifiers = migration.installed_ids(payload, "project")
        self.assertEqual(identifiers, {claude.new_id})

    def test_claude_bare_list_output_is_supported(self) -> None:
        claude = migration.build_client("claude-code", "user")
        payload = [
            {"id": claude.old_id, "scope": "user"},
            {"id": claude.new_id, "scope": "project"},
        ]

        identifiers = migration.installed_ids(payload, "user")

        self.assertEqual(identifiers, {claude.old_id})

    def test_run_command_uses_argument_array_without_shell(self) -> None:
        completed = mock.Mock(returncode=0, stdout='{"installed": []}', stderr="")
        with mock.patch.object(migration.subprocess, "run", return_value=completed) as run:
            payload = migration.run_command(self.codex.list_plugins, json_output=True)

        self.assertEqual(payload, {"installed": []})
        args, kwargs = run.call_args
        self.assertEqual(args[0], list(self.codex.list_plugins))
        self.assertNotIn("shell", kwargs)


if __name__ == "__main__":
    unittest.main()
