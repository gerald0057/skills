#!/usr/bin/env python3
"""Interactive local configuration for the redmine-access skill."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any

import redmine_client as client


NORMAL_WRITES = [
    "issue.create",
    "issue.update",
    "issue.comment",
    "time_entry.create",
    "attachment.upload",
]


def require_tty() -> None:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise client.RedmineAccessError(
            "CONFIG_REQUIRES_TTY：请在本地交互式终端运行配置向导；不要在聊天中粘贴 API Key"
        )


def prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


def confirm(label: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"{label} [{hint}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "是"}


def load_optional(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return client.read_secure_json(path)


def fetch_projects(api: client.RedmineHTTP) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    offset = 0
    while offset < 1000:
        _, response = api.request("GET", "/projects.json", query={"limit": 100, "offset": offset})
        page = response.get("projects", []) if isinstance(response, dict) else []
        projects.extend(item for item in page if isinstance(item, dict))
        total = response.get("total_count", len(projects)) if isinstance(response, dict) else len(projects)
        if not page or len(projects) >= total:
            break
        offset += len(page)
    return projects


def show_identity(api: client.RedmineHTTP) -> None:
    _, response = api.request("GET", "/users/current.json")
    user = response.get("user", {}) if isinstance(response, dict) else {}
    safe = {key: user[key] for key in ("id", "login", "firstname", "lastname") if key in user}
    safe = client.redact_secrets(safe, [api.api_key])
    print("连接验证成功：" + json.dumps(safe, ensure_ascii=False))


def choose_projects(
    projects: list[dict[str, Any]],
    current: list[str] | None = None,
    secret: str | None = None,
) -> list[str]:
    identifiers = {
        item["identifier"]: item.get("name", item["identifier"])
        for item in projects
        if isinstance(item.get("identifier"), str)
    }
    print("可访问项目：")
    for identifier, name in sorted(identifiers.items()):
        display = client.redact_secrets(f"  {identifier}: {name}", [secret] if secret else [])
        print(display)
    default = ",".join(current or [])
    raw = prompt("允许写入的项目 identifier（逗号分隔；none 表示只读）", default or "none")
    selected = [] if raw.lower() == "none" else [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(selected) - set(identifiers))
    if unknown:
        raise client.RedmineAccessError(f"以下项目不在当前账号可访问列表中：{unknown}")
    return list(dict.fromkeys(selected))


def choose_write_operations(current: dict[str, str] | None = None) -> dict[str, str]:
    current_enabled = [name for name in NORMAL_WRITES if (current or {}).get(name) == "confirm"]
    default_enabled = current_enabled if current is not None else NORMAL_WRITES
    default = ",".join(default_enabled) or "none"
    print("可启用的写操作：" + ", ".join(NORMAL_WRITES))
    raw = prompt("逐次确认后允许的写操作（逗号分隔；none 表示全部拒绝）", default)
    enabled = set() if raw.lower() == "none" else {item.strip() for item in raw.split(",") if item.strip()}
    unknown = sorted(enabled - set(NORMAL_WRITES))
    if unknown:
        raise client.RedmineAccessError(f"未知写操作：{unknown}")
    operations = {
        "issue.read": "allow",
        "project.read": "allow",
        "user.read": "allow",
        "metadata.read": "allow",
        "time_entry.read": "allow",
        **{name: ("confirm" if name in enabled else "deny") for name in NORMAL_WRITES},
        "issue.private_comment": "deny",
        **{name: "deny" for name in sorted(client.DELETE_OPERATIONS)},
    }
    if confirm("允许逐次确认后添加私有评论", (current or {}).get("issue.private_comment") == "confirm"):
        operations["issue.private_comment"] = "confirm"
    return operations


def choose_fields(label: str, permitted: set[str], current: list[str], required: set[str] | None = None) -> list[str]:
    raw = prompt(label, ",".join(current))
    selected = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(selected) - permitted)
    if unknown:
        raise client.RedmineAccessError(f"不支持的字段：{unknown}")
    missing = sorted((required or set()) - set(selected))
    if missing:
        raise client.RedmineAccessError(f"缺少必需字段权限：{missing}")
    return list(dict.fromkeys(selected))


def choose_custom_field_ids(current: list[int]) -> list[int]:
    raw = prompt("允许写入的 custom field ID（逗号分隔；none 表示无）", ",".join(map(str, current)) or "none")
    if raw.lower() == "none":
        return []
    try:
        selected = [int(item.strip()) for item in raw.split(",") if item.strip()]
    except ValueError as exc:
        raise client.RedmineAccessError("custom field ID 必须是正整数") from exc
    if not all(item > 0 for item in selected):
        raise client.RedmineAccessError("custom field ID 必须是正整数")
    return list(dict.fromkeys(selected))


def default_policy(write_projects: list[str], operations: dict[str, str]) -> dict[str, Any]:
    return {
        "operations": operations,
        "write_projects": write_projects,
        "issue_create_fields": [
            "project_id",
            "subject",
            "description",
            "tracker_id",
            "priority_id",
            "assigned_to_id",
            "due_date",
            "estimated_hours",
        ],
        "issue_update_fields": [
            "subject",
            "description",
            "status_id",
            "priority_id",
            "assigned_to_id",
            "due_date",
            "estimated_hours",
        ],
        "custom_field_ids": [],
        "max_mutations_per_confirmation": 1,
        "pending_ttl_seconds": 600,
        "max_attachment_bytes": 10_000_000,
        "max_time_entry_hours": 24,
    }


def setup(profile_name: str) -> dict[str, Any]:
    require_tty()
    if not client.PROFILE_RE.fullmatch(profile_name):
        raise client.RedmineAccessError("profile 名称只能包含字母、数字、点、下划线和连字符")
    config = load_optional(
        client.CONFIG_FILE,
        {"version": 1, "default_profile": profile_name, "profiles": {}},
    )
    permissions = load_optional(
        client.PERMISSIONS_FILE,
        {"version": 1, "profiles": {}},
    )
    existing = config.get("profiles", {}).get(profile_name)
    if existing and not confirm(f"profile {profile_name} 已存在，是否重新配置"):
        raise client.RedmineAccessError("已取消")
    server_url = client.validate_server_url(
        prompt("Redmine 服务器地址", existing.get("server_url") if isinstance(existing, dict) else None)
    )
    api_key = getpass.getpass("Redmine API Key（输入不回显）: ").strip()
    if not api_key:
        raise client.RedmineAccessError("API Key 不能为空")
    ca_default = existing.get("ca_bundle") if isinstance(existing, dict) else None
    ca_bundle = prompt("自定义 CA bundle 路径（可留空）", ca_default)
    profile: dict[str, Any] = {"server_url": server_url, "api_key": api_key}
    if ca_bundle:
        ca_path = Path(ca_bundle).expanduser().resolve(strict=True)
        if not ca_path.is_file():
            raise client.RedmineAccessError("CA bundle 不是普通文件")
        profile["ca_bundle"] = str(ca_path)
    api = client.RedmineHTTP(profile)
    show_identity(api)
    projects = fetch_projects(api)
    current_policy = permissions.get("profiles", {}).get(profile_name, {})
    write_projects = choose_projects(projects, current_policy.get("write_projects"), api.api_key)
    operations = choose_write_operations(current_policy.get("operations"))
    policy = default_policy(write_projects, operations)
    if client.contains_secret(policy, [api_key]):
        raise client.RedmineAccessError("权限配置包含 API Key，已拒绝保存")
    config.setdefault("profiles", {})[profile_name] = profile
    permissions.setdefault("profiles", {})[profile_name] = policy
    if not config.get("default_profile") or confirm("将该 profile 设为默认读取 profile", config.get("default_profile") == profile_name):
        config["default_profile"] = profile_name
    client.validate_config(config)
    client.validate_permissions(permissions)
    client.atomic_write_json(client.CONFIG_FILE, config)
    client.atomic_write_json(client.PERMISSIONS_FILE, permissions)
    return {
        "configured": True,
        "profile": profile_name,
        "server_url": server_url,
        "write_projects": write_projects,
        "writes": {name: mode for name, mode in operations.items() if name in client.WRITE_OPERATIONS},
        "config_dir": str(client.CONFIG_ROOT),
    }


def update_permissions(profile_name: str) -> dict[str, Any]:
    require_tty()
    selected, profile, current = client.load_context(profile_name)
    api = client.RedmineHTTP(profile)
    show_identity(api)
    projects = fetch_projects(api)
    write_projects = choose_projects(projects, current.get("write_projects"), api.api_key)
    operations = choose_write_operations(current.get("operations"))
    updated = dict(current)
    updated["write_projects"] = write_projects
    updated["operations"] = operations
    updated["issue_create_fields"] = choose_fields(
        "允许创建 Issue 的字段",
        client.CREATE_ISSUE_FIELDS,
        current.get("issue_create_fields", ["project_id", "subject"]),
        {"project_id", "subject"},
    )
    updated["issue_update_fields"] = choose_fields(
        "允许更新 Issue 的字段",
        client.PERMITTED_UPDATE_FIELDS,
        current.get("issue_update_fields", []),
    )
    updated["custom_field_ids"] = choose_custom_field_ids(current.get("custom_field_ids", []))
    if client.contains_secret(updated, [profile["api_key"]]):
        raise client.RedmineAccessError("权限配置包含 API Key，已拒绝保存")
    document = client.read_secure_json(client.PERMISSIONS_FILE)
    document["profiles"][selected] = updated
    client.validate_permissions(document)
    client.atomic_write_json(client.PERMISSIONS_FILE, document)
    return {
        "updated": True,
        "profile": selected,
        "write_projects": write_projects,
        "operations": operations,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="安全配置 redmine-access")
    subparsers = parser.add_subparsers(dest="command", required=True)
    setup_parser = subparsers.add_parser("setup", help="新增或重新配置 profile")
    setup_parser.add_argument("--profile", default="default")
    permission_parser = subparsers.add_parser("permissions", help="调整已有 profile 的本地写权限")
    permission_parser.add_argument("--profile", required=True)
    show_parser = subparsers.add_parser("show", help="显示脱敏后的配置状态")
    show_parser.add_argument("--profile")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "setup":
            result = setup(args.profile)
        elif args.command == "permissions":
            result = update_permissions(args.profile)
        else:
            result = client.command_status(args.profile)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (client.RedmineAccessError, OSError, ValueError) as exc:
        print(json.dumps({"code": "CONFIG_ERROR", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
