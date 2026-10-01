#!/usr/bin/env python3
"""Migrate the installed bundle from smartrf-skills to skills safely."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent.parent
OLD_PLUGIN = "smartrf-skills"
NEW_PLUGIN = "skills"
MARKETPLACE = "gerald0057-skills"


class MigrationError(RuntimeError):
    """Raised when a migration precondition or command fails."""


@dataclass(frozen=True)
class ClientCommands:
    name: str
    executable: str
    list_plugins: tuple[str, ...]
    refresh_marketplace: tuple[str, ...]
    install_new: tuple[str, ...]
    update_new: tuple[str, ...]
    remove_old: tuple[str, ...]
    scope: str | None = None

    @property
    def old_id(self) -> str:
        return f"{OLD_PLUGIN}@{MARKETPLACE}"

    @property
    def new_id(self) -> str:
        return f"{NEW_PLUGIN}@{MARKETPLACE}"


def load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"cannot read {path.relative_to(ROOT)}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MigrationError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return payload


def validate_repository_metadata() -> None:
    codex = load_object(ROOT / ".codex-plugin" / "plugin.json")
    claude = load_object(ROOT / ".claude-plugin" / "plugin.json")
    marketplace = load_object(ROOT / ".claude-plugin" / "marketplace.json")

    if codex.get("name") != NEW_PLUGIN or claude.get("name") != NEW_PLUGIN:
        raise MigrationError(
            f"repository manifests must both use the new plugin name {NEW_PLUGIN!r}"
        )
    if marketplace.get("name") != MARKETPLACE:
        raise MigrationError(f"repository marketplace must be named {MARKETPLACE!r}")
    entries = marketplace.get("plugins")
    if not isinstance(entries, list) or not any(
        isinstance(entry, dict)
        and entry.get("name") == NEW_PLUGIN
        and entry.get("source") == "./"
        for entry in entries
    ):
        raise MigrationError(
            f"repository marketplace must publish {NEW_PLUGIN!r} from './'"
        )


def build_client(name: str, claude_scope: str) -> ClientCommands:
    if name == "codex":
        return ClientCommands(
            name="codex",
            executable="codex",
            list_plugins=("codex", "plugin", "list", "--json"),
            refresh_marketplace=(
                "codex",
                "plugin",
                "marketplace",
                "upgrade",
                MARKETPLACE,
            ),
            install_new=("codex", "plugin", "add", f"{NEW_PLUGIN}@{MARKETPLACE}"),
            update_new=("codex", "plugin", "add", f"{NEW_PLUGIN}@{MARKETPLACE}"),
            remove_old=("codex", "plugin", "remove", f"{OLD_PLUGIN}@{MARKETPLACE}"),
        )
    if name == "claude-code":
        return ClientCommands(
            name="claude-code",
            executable="claude",
            list_plugins=("claude", "plugin", "list", "--json"),
            refresh_marketplace=(
                "claude",
                "plugin",
                "marketplace",
                "update",
                MARKETPLACE,
            ),
            install_new=(
                "claude",
                "plugin",
                "install",
                f"{NEW_PLUGIN}@{MARKETPLACE}",
                "--scope",
                claude_scope,
            ),
            update_new=(
                "claude",
                "plugin",
                "update",
                f"{NEW_PLUGIN}@{MARKETPLACE}",
                "--scope",
                claude_scope,
            ),
            remove_old=(
                "claude",
                "plugin",
                "uninstall",
                f"{OLD_PLUGIN}@{MARKETPLACE}",
                "--scope",
                claude_scope,
                "--keep-data",
            ),
            scope=claude_scope,
        )
    raise MigrationError(f"unsupported agent: {name}")


def run_command(command: Sequence[str], *, json_output: bool = False) -> Any:
    print(f"$ {shlex.join(command)}")
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise MigrationError(f"command failed ({result.returncode}): {detail}")
    if json_output:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise MigrationError(f"command returned invalid JSON: {exc}") from exc
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)
    return None


def installed_ids(payload: Any, scope: str | None) -> set[str]:
    if isinstance(payload, list):
        installed = payload
    elif isinstance(payload, dict) and isinstance(payload.get("installed"), list):
        installed = payload["installed"]
    else:
        raise MigrationError("plugin list JSON does not contain an installed plugin array")

    result: set[str] = set()
    for entry in installed:
        if not isinstance(entry, dict):
            continue
        if scope is not None and entry.get("scope") != scope:
            continue
        plugin_id = entry.get("pluginId") or entry.get("id")
        if isinstance(plugin_id, str):
            result.add(plugin_id)
    return result


def read_state(client: ClientCommands) -> tuple[bool, bool]:
    payload = run_command(client.list_plugins, json_output=True)
    identifiers = installed_ids(payload, client.scope)
    return client.old_id in identifiers, client.new_id in identifiers


def preview_actions(
    client: ClientCommands,
    *,
    old_installed: bool,
    new_installed: bool,
    skip_refresh: bool,
) -> list[tuple[str, ...]]:
    if not old_installed:
        return []

    actions: list[tuple[str, ...]] = []
    if not skip_refresh:
        actions.append(client.refresh_marketplace)
    actions.append(client.update_new if new_installed else client.install_new)
    actions.append(client.remove_old)
    return actions


def migrate_client(client: ClientCommands, *, apply: bool, skip_refresh: bool) -> None:
    old_installed, new_installed = read_state(client)
    print(
        f"{client.name}: old={'installed' if old_installed else 'not-installed'}, "
        f"new={'installed' if new_installed else 'not-installed'}"
    )

    if not old_installed and not new_installed:
        print(f"{client.name}: nothing to migrate")
        return
    if not old_installed and new_installed:
        print(f"{client.name}: already migrated")
        return

    actions = preview_actions(
        client,
        old_installed=old_installed,
        new_installed=new_installed,
        skip_refresh=skip_refresh,
    )
    if not apply:
        for command in actions:
            print(f"would run: {shlex.join(command)}")
        return

    if not skip_refresh:
        run_command(client.refresh_marketplace)
    run_command(client.update_new if new_installed else client.install_new)
    old_installed, new_installed = read_state(client)
    if not new_installed:
        raise MigrationError(
            f"{client.name}: new plugin was not visible after installation or update; "
            "old plugin was preserved"
        )

    run_command(client.remove_old)
    old_installed, new_installed = read_state(client)
    if old_installed or not new_installed:
        raise MigrationError(
            f"{client.name}: final state is invalid "
            f"(old={old_installed}, new={new_installed})"
        )
    print(f"{client.name}: migration complete")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate an existing smartrf-skills plugin installation to skills. "
            "The default mode only previews changes."
        )
    )
    parser.add_argument(
        "--agent",
        choices=("codex", "claude-code", "all"),
        default="all",
        help="client to migrate (default: all installed clients)",
    )
    parser.add_argument(
        "--claude-scope",
        choices=("user", "project", "local"),
        default="user",
        help="Claude Code installation scope (default: user)",
    )
    parser.add_argument(
        "--skip-refresh",
        action="store_true",
        help="do not refresh the configured marketplace before installing the new name",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the migration; without this flag only a preview is shown",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_repository_metadata()
        requested = (
            ("codex", "claude-code") if args.agent == "all" else (args.agent,)
        )
        available_clients = 0
        for name in requested:
            client = build_client(name, args.claude_scope)
            if shutil.which(client.executable) is None:
                if args.agent == "all":
                    print(f"{client.name}: skipped because {client.executable} is unavailable")
                    continue
                raise MigrationError(f"required command not found: {client.executable}")
            available_clients += 1
            migrate_client(client, apply=args.apply, skip_refresh=args.skip_refresh)
        if available_clients == 0:
            raise MigrationError("no supported plugin client is available")
    except MigrationError as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    if not args.apply:
        print("Preview only; rerun with --apply to perform the migration.")
    else:
        print("Start a new Codex or Claude Code task to load the renamed plugin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
