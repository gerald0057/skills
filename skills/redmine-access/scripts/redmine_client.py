#!/usr/bin/env python3
"""Guarded, dependency-free Redmine REST client for the redmine-access skill."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


CONFIG_ROOT = Path.home() / ".config" / "skills" / "redmine-access"
CONFIG_FILE = CONFIG_ROOT / "config.json"
PERMISSIONS_FILE = CONFIG_ROOT / "permissions.json"
_state_home = os.environ.get("XDG_STATE_HOME")
STATE_ROOT = (
    Path(_state_home).expanduser()
    if _state_home and Path(_state_home).expanduser().is_absolute()
    else Path.home() / ".local" / "state"
)
RUNTIME_ROOT = STATE_ROOT / "skills" / "redmine-access"
PENDING_DIR = RUNTIME_ROOT / "pending"
AUDIT_FILE = RUNTIME_ROOT / "audit.jsonl"

MAX_CONFIG_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 8_000_000
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ISSUE_FILTERS = {
    "issue_id",
    "project_id",
    "tracker_id",
    "status_id",
    "assigned_to_id",
    "parent_id",
    "created_on",
    "updated_on",
}
ISSUE_SORT_FIELDS = {
    "id",
    "priority",
    "status",
    "subject",
    "assigned_to",
    "created_on",
    "updated_on",
    "start_date",
    "due_date",
    "estimated_hours",
    "done_ratio",
}

READ_OPERATIONS = {
    "issue.read",
    "project.read",
    "user.read",
    "metadata.read",
    "time_entry.read",
}
WRITE_OPERATIONS = {
    "issue.create",
    "issue.update",
    "issue.comment",
    "issue.private_comment",
    "time_entry.create",
    "attachment.upload",
}
DELETE_OPERATIONS = {
    "issue.delete",
    "project.delete",
    "user.delete",
    "time_entry.delete",
    "attachment.delete",
}
ALLOWED_OPERATIONS = READ_OPERATIONS | WRITE_OPERATIONS | DELETE_OPERATIONS

CREATE_ISSUE_FIELDS = {
    "project_id",
    "subject",
    "tracker_id",
    "priority_id",
    "description",
    "assigned_to_id",
    "custom_fields",
    "estimated_hours",
    "start_date",
    "due_date",
}
PERMITTED_UPDATE_FIELDS = {
    "subject",
    "description",
    "status_id",
    "priority_id",
    "assigned_to_id",
    "custom_fields",
    "estimated_hours",
    "start_date",
    "due_date",
}
TIME_ENTRY_FIELDS = {
    "issue_id",
    "project_id",
    "spent_on",
    "hours",
    "activity_id",
    "comments",
}
TIME_ENTRY_FILTERS = {
    "project_id",
    "issue_id",
    "user_id",
    "activity_id",
    "from",
    "to",
    "spent_on",
}
ISSUE_INCLUDES = {
    "children",
    "attachments",
    "relations",
    "changesets",
    "journals",
    "watchers",
    "allowed_statuses",
}
METADATA_ENDPOINTS = {
    "statuses": ("metadata.read", "/issue_statuses.json"),
    "trackers": ("metadata.read", "/trackers.json"),
    "priorities": ("metadata.read", "/enumerations/issue_priorities.json"),
    "activities": ("metadata.read", "/enumerations/time_entry_activities.json"),
    "queries": ("metadata.read", "/queries.json"),
}


class RedmineAccessError(RuntimeError):
    """Expected, user-actionable failure."""


class RequestOutcomeUnknown(RedmineAccessError):
    """A write may have reached Redmine, so retrying would be unsafe."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def keyed_integrity(value: Any, api_key: str) -> str:
    return hmac.new(api_key.encode("utf-8"), canonical_json(value), hashlib.sha256).hexdigest()


def redact_secrets(value: Any, secrets_to_redact: list[str]) -> Any:
    secrets_clean = [secret for secret in secrets_to_redact if secret]
    if isinstance(value, str):
        for secret in secrets_clean:
            value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {
            redact_secrets(key, secrets_clean) if isinstance(key, str) else key: redact_secrets(item, secrets_clean)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item, secrets_clean) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item, secrets_clean) for item in value]
    return value


def contains_secret(value: Any, secrets_to_find: list[str]) -> bool:
    secrets_clean = [secret for secret in secrets_to_find if secret]
    if isinstance(value, str):
        return any(secret in value for secret in secrets_clean)
    if isinstance(value, dict):
        return any(
            (isinstance(key, str) and contains_secret(key, secrets_clean))
            or contains_secret(item, secrets_clean)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_secret(item, secrets_clean) for item in value)
    return False


def print_json(value: Any, pretty: bool = False, secrets_to_redact: list[str] | None = None) -> None:
    value = redact_secrets(value, secrets_to_redact or [])
    if pretty:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _check_secure(path: Path, *, directory: bool = False) -> None:
    if path.is_symlink():
        raise RedmineAccessError(f"拒绝使用符号链接配置路径：{path}")
    if not path.exists():
        raise RedmineAccessError(f"缺少配置路径：{path}")
    if directory != path.is_dir():
        kind = "目录" if directory else "文件"
        raise RedmineAccessError(f"配置路径不是预期的{kind}：{path}")
    if path.stat().st_mode & 0o077:
        expected = "0700" if directory else "0600"
        raise RedmineAccessError(f"配置权限过宽：{path}；请设置为 {expected}")


def read_secure_json(path: Path) -> dict[str, Any]:
    _check_secure(CONFIG_ROOT, directory=True)
    _check_secure(path)
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise RedmineAccessError(f"配置文件过大：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RedmineAccessError(f"无法读取配置 {path.name}：{exc}") from exc
    if not isinstance(value, dict):
        raise RedmineAccessError(f"配置根节点必须是对象：{path}")
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    if path.parent.is_symlink():
        raise RedmineAccessError(f"拒绝写入符号链接目录：{path.parent}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_server_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise RedmineAccessError("Redmine 地址必须是完整的 http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RedmineAccessError("Redmine 地址不能包含凭据、查询参数或 fragment")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RedmineAccessError("非本机 Redmine 必须使用 HTTPS")
    return value


def validate_config(config: dict[str, Any]) -> None:
    if config.get("version") != 1:
        raise RedmineAccessError("config.json version 必须为 1")
    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise RedmineAccessError("config.json 必须包含至少一个 profile")
    default = config.get("default_profile")
    if default not in profiles:
        raise RedmineAccessError("default_profile 不存在")
    for name, profile in profiles.items():
        if not isinstance(name, str) or not PROFILE_RE.fullmatch(name):
            raise RedmineAccessError(f"无效的 profile 名称：{name!r}")
        if not isinstance(profile, dict):
            raise RedmineAccessError(f"profile {name} 必须是对象")
        validate_server_url(str(profile.get("server_url", "")))
        key = profile.get("api_key")
        if not isinstance(key, str) or not key or "\n" in key or "\r" in key:
            raise RedmineAccessError(f"profile {name} 缺少有效 API Key")
        public_profile = {item_key: item for item_key, item in profile.items() if item_key != "api_key"}
        if key in name or contains_secret(public_profile, [key]):
            raise RedmineAccessError(f"profile {name} 的非凭据配置包含 API Key")
        ca_bundle = profile.get("ca_bundle")
        if ca_bundle is not None and (not isinstance(ca_bundle, str) or not ca_bundle):
            raise RedmineAccessError(f"profile {name} 的 ca_bundle 无效")


def validate_permissions(document: dict[str, Any]) -> None:
    if document.get("version") != 1:
        raise RedmineAccessError("permissions.json version 必须为 1")
    profiles = document.get("profiles")
    if not isinstance(profiles, dict):
        raise RedmineAccessError("permissions.json 缺少 profiles")
    for name, policy in profiles.items():
        if not isinstance(name, str) or not PROFILE_RE.fullmatch(name):
            raise RedmineAccessError(f"无效的权限 profile：{name!r}")
        if not isinstance(policy, dict):
            raise RedmineAccessError(f"profile {name} 权限必须是对象")
        operations = policy.get("operations")
        if not isinstance(operations, dict):
            raise RedmineAccessError(f"profile {name} 缺少 operations")
        unknown = set(operations) - ALLOWED_OPERATIONS
        if unknown:
            raise RedmineAccessError(f"profile {name} 包含未知操作：{sorted(unknown)}")
        for operation, mode in operations.items():
            allowed_modes = {"allow", "deny"} if operation in READ_OPERATIONS else {"confirm", "deny"}
            if operation in DELETE_OPERATIONS:
                allowed_modes = {"deny"}
            if mode not in allowed_modes:
                raise RedmineAccessError(
                    f"profile {name} 的 {operation} 不允许设置为 {mode!r}"
                )
        projects = policy.get("write_projects", [])
        if not isinstance(projects, list) or not all(
            isinstance(item, str) and item and item != "*" for item in projects
        ):
            raise RedmineAccessError(f"profile {name} 的 write_projects 无效")
        create_fields = policy.get("issue_create_fields", [])
        if not isinstance(create_fields, list) or not all(
            isinstance(item, str) and item in CREATE_ISSUE_FIELDS for item in create_fields
        ):
            raise RedmineAccessError(f"profile {name} 的 issue_create_fields 无效")
        if not {"project_id", "subject"}.issubset(create_fields):
            raise RedmineAccessError(f"profile {name} 的 issue_create_fields 必须包含 project_id 和 subject")
        fields = policy.get("issue_update_fields", [])
        if not isinstance(fields, list) or not all(
            isinstance(item, str) and item in PERMITTED_UPDATE_FIELDS for item in fields
        ):
            raise RedmineAccessError(f"profile {name} 的 issue_update_fields 无效")
        custom_fields = policy.get("custom_field_ids", [])
        if not isinstance(custom_fields, list) or not all(
            isinstance(item, int) and item > 0 for item in custom_fields
        ):
            raise RedmineAccessError(f"profile {name} 的 custom_field_ids 无效")
        if policy.get("max_mutations_per_confirmation", 1) != 1:
            raise RedmineAccessError("V1 仅允许每次确认执行一个变更")
        ttl = policy.get("pending_ttl_seconds", 600)
        if not isinstance(ttl, int) or not 60 <= ttl <= 3600:
            raise RedmineAccessError("pending_ttl_seconds 必须在 60 到 3600 之间")
        maximum = policy.get("max_attachment_bytes", 10_000_000)
        if not isinstance(maximum, int) or not 1 <= maximum <= 100_000_000:
            raise RedmineAccessError("max_attachment_bytes 必须在 1 到 100000000 之间")
        maximum_hours = policy.get("max_time_entry_hours", 24)
        if not isinstance(maximum_hours, (int, float)) or not 0 < maximum_hours <= 24:
            raise RedmineAccessError("max_time_entry_hours 必须大于 0 且不超过 24")


def load_context(profile_name: str | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    config = read_secure_json(CONFIG_FILE)
    permissions = read_secure_json(PERMISSIONS_FILE)
    validate_config(config)
    validate_permissions(permissions)
    selected = profile_name or config["default_profile"]
    if selected not in config["profiles"]:
        raise RedmineAccessError(f"配置中不存在 profile：{selected}")
    if selected not in permissions["profiles"]:
        raise RedmineAccessError(f"权限中不存在 profile：{selected}")
    profile = config["profiles"][selected]
    policy = permissions["profiles"][selected]
    if contains_secret(policy, [profile["api_key"]]):
        raise RedmineAccessError(f"profile {selected} 的权限配置包含 API Key")
    return selected, profile, policy


def require_operation(policy: dict[str, Any], operation: str) -> None:
    if operation in DELETE_OPERATIONS or operation.endswith(".delete"):
        raise RedmineAccessError("redmine-access 永久禁止删除操作")
    expected = "allow" if operation in READ_OPERATIONS else "confirm"
    actual = policy.get("operations", {}).get(operation, "deny")
    if actual != expected:
        raise RedmineAccessError(f"权限策略拒绝操作 {operation}（当前：{actual}）")


def require_write_project(policy: dict[str, Any], identifier: str) -> None:
    allowed = policy.get("write_projects", [])
    if identifier not in allowed:
        raise RedmineAccessError(f"项目 {identifier!r} 不在 write_projects 范围内")


def validate_custom_fields(policy: dict[str, Any], payload: dict[str, Any]) -> None:
    if "custom_fields" not in payload:
        return
    values = payload["custom_fields"]
    if not isinstance(values, list) or not all(
        isinstance(item, dict) and isinstance(item.get("id"), int) for item in values
    ):
        raise RedmineAccessError("custom_fields 必须是包含整数 id 的对象列表")
    allowed = set(policy.get("custom_field_ids", []))
    requested = {item["id"] for item in values}
    if not requested.issubset(allowed):
        raise RedmineAccessError(f"未授权的 custom field ID：{sorted(requested - allowed)}")


def reject_api_key_content(value: Any, profile: dict[str, Any], label: str) -> None:
    if contains_secret(value, [profile["api_key"]]):
        raise RedmineAccessError(f"{label} 包含当前 profile 的 API Key，已拒绝处理")


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class RedmineHTTP:
    def __init__(self, profile: dict[str, Any]):
        self.base_url = validate_server_url(profile["server_url"])
        self.api_key = profile["api_key"]
        parsed = urlparse(self.base_url)
        handlers: list[Any] = [RejectRedirects()]
        if parsed.scheme == "https":
            context = ssl.create_default_context(cafile=profile.get("ca_bundle"))
            handlers.append(HTTPSHandler(context=context))
        self.opener = build_opener(*handlers)

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: Any | None = None,
        binary_body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, Any]:
        method = method.upper()
        if method == "DELETE":
            raise RedmineAccessError("redmine-access 永久禁止 HTTP DELETE")
        if method not in {"GET", "POST", "PUT"}:
            raise RedmineAccessError(f"不支持 HTTP 方法：{method}")
        if (
            not endpoint.startswith("/")
            or "://" in endpoint
            or "?" in endpoint
            or any(part == ".." for part in endpoint.split("/"))
        ):
            raise RedmineAccessError("API endpoint 必须是受控的站内路径")
        url = self.base_url + endpoint
        if query:
            url += "?" + urlencode(query, doseq=True)
        headers = {
            "Accept": "application/json",
            "X-Redmine-API-Key": self.api_key,
            "User-Agent": "redmine-access-skill/1",
        }
        data: bytes | None = None
        if json_body is not None and binary_body is not None:
            raise RedmineAccessError("请求不能同时包含 JSON 和二进制正文")
        if json_body is not None:
            data = canonical_json(json_body)
            headers["Content-Type"] = "application/json"
        elif binary_body is not None:
            data = binary_body
            headers["Content-Type"] = content_type or "application/octet-stream"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise RedmineAccessError("Redmine 响应超过大小限制")
                status = response.status
        except HTTPError as exc:
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
            detail = _decode_error(raw, self.api_key)
            raise RedmineAccessError(f"Redmine HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if method in {"POST", "PUT"}:
                raise RequestOutcomeUnknown(
                    "写请求结果未知；禁止自动重试，请先读取 Redmine 核对"
                ) from exc
            raise RedmineAccessError(f"Redmine 请求失败：{exc}") from exc
        if not raw:
            return status, None
        try:
            return status, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RedmineAccessError("Redmine 返回了无效 JSON") from exc


def _decode_error(raw: bytes, secret: str) -> str:
    if not raw:
        return "无响应正文"
    try:
        value = json.loads(raw[:MAX_RESPONSE_BYTES].decode("utf-8"))
        value = redact_secrets(value, [secret])
        if isinstance(value, dict) and isinstance(value.get("errors"), list):
            detail = "; ".join(str(item) for item in value["errors"][:10])
        else:
            detail = json.dumps(value, ensure_ascii=False)[:1000]
    except (UnicodeDecodeError, json.JSONDecodeError):
        detail = raw[:1000].decode("utf-8", errors="replace")
    return detail.replace(secret, "[REDACTED]")


def get_issue(api: RedmineHTTP, issue_id: int, includes: list[str] | None = None) -> dict[str, Any]:
    query = {"include": ",".join(includes)} if includes else None
    _, response = api.request("GET", f"/issues/{issue_id}.json", query=query)
    issue = response.get("issue") if isinstance(response, dict) else None
    if not isinstance(issue, dict):
        raise RedmineAccessError("Redmine issue 响应格式无效")
    return issue


def get_project(api: RedmineHTTP, reference: str | int) -> dict[str, Any]:
    encoded = quote(str(reference), safe="")
    _, response = api.request("GET", f"/projects/{encoded}.json")
    project = response.get("project") if isinstance(response, dict) else None
    if not isinstance(project, dict) or not isinstance(project.get("identifier"), str):
        raise RedmineAccessError("Redmine project 响应缺少 identifier")
    return project


def issue_project(api: RedmineHTTP, issue: dict[str, Any]) -> dict[str, Any]:
    project = issue.get("project")
    if not isinstance(project, dict) or "id" not in project:
        raise RedmineAccessError("Issue 响应缺少 project")
    return get_project(api, project["id"])


def issue_summary(
    issue: dict[str, Any], *, description: bool = False, journal_limit: int = 10
) -> dict[str, Any]:
    keys = (
        "id",
        "project",
        "tracker",
        "status",
        "priority",
        "subject",
        "assigned_to",
        "author",
        "start_date",
        "due_date",
        "done_ratio",
        "created_on",
        "updated_on",
    )
    result = {key: issue[key] for key in keys if key in issue}
    if description and "description" in issue:
        result["description"] = issue["description"]
    for key in ("journals", "attachments", "relations", "children", "allowed_statuses"):
        if key in issue:
            if key == "journals" and isinstance(issue[key], list):
                result[key] = issue[key][-journal_limit:]
                result["journals_returned"] = len(result[key])
                result["journals_total"] = len(issue[key])
            else:
                result[key] = issue[key]
    return result


def project_summary(project: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "name", "identifier", "status", "is_public", "created_on", "updated_on")
    return {key: project[key] for key in keys if key in project}


def time_entry_summary(entry: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "project",
        "issue",
        "user",
        "activity",
        "hours",
        "comments",
        "spent_on",
        "created_on",
        "updated_on",
    )
    return {key: entry[key] for key in keys if key in entry}


def load_payload(path: str) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise RedmineAccessError(f"payload 文件不存在或不安全：{source}")
    if source.stat().st_size > MAX_CONFIG_BYTES:
        raise RedmineAccessError("payload 文件过大")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RedmineAccessError(f"无法读取 payload：{exc}") from exc
    if not isinstance(value, dict):
        raise RedmineAccessError("payload 根节点必须是对象")
    return value


def load_text(path: str) -> str:
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise RedmineAccessError(f"文本文件不存在或不安全：{source}")
    if source.stat().st_size > MAX_CONFIG_BYTES:
        raise RedmineAccessError("文本文件过大")
    try:
        return source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RedmineAccessError(f"无法读取 UTF-8 文本文件：{exc}") from exc


def _policy_snapshot(policy: dict[str, Any]) -> str:
    return fingerprint(policy)


def cleanup_pending(now: datetime | None = None) -> None:
    if not PENDING_DIR.exists():
        return
    _check_secure(RUNTIME_ROOT, directory=True)
    _check_secure(PENDING_DIR, directory=True)
    current = now or utc_now()
    for path in PENDING_DIR.glob("chg-*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if parse_time(value["expires_at"]) <= current:
                path.unlink()
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue


def compact_preview(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 500:
        return {
            "length": len(value),
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            "preview": value[:500] + "…",
        }
    if isinstance(value, dict):
        return {key: compact_preview(item) for key, item in value.items()}
    if isinstance(value, list):
        return [compact_preview(item) for item in value]
    return value


def create_pending(
    *,
    profile_name: str,
    profile: dict[str, Any],
    policy: dict[str, Any],
    operation: str,
    project_identifier: str,
    target: dict[str, Any],
    action: dict[str, Any],
    preview: dict[str, Any],
    before_updated_on: str | None = None,
) -> dict[str, Any]:
    require_operation(policy, operation)
    require_write_project(policy, project_identifier)
    if RUNTIME_ROOT.is_symlink() or PENDING_DIR.is_symlink():
        raise RedmineAccessError("拒绝使用符号链接状态目录")
    PENDING_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(RUNTIME_ROOT, 0o700)
    os.chmod(PENDING_DIR, 0o700)
    cleanup_pending()
    created = utc_now()
    ttl = policy.get("pending_ttl_seconds", 600)
    approval_id = f"chg-{secrets.token_hex(16)}"
    pending: dict[str, Any] = {
        "version": 1,
        "approval_id": approval_id,
        "created_at": iso_time(created),
        "expires_at": iso_time(created + timedelta(seconds=ttl)),
        "profile": profile_name,
        "server_url": validate_server_url(profile["server_url"]),
        "operation": operation,
        "project_identifier": project_identifier,
        "target": target,
        "action": action,
        "preview": compact_preview(preview),
        "before_updated_on": before_updated_on,
        "policy_fingerprint": _policy_snapshot(policy),
    }
    pending["integrity"] = keyed_integrity(pending, profile["api_key"])
    atomic_write_json(PENDING_DIR / f"{approval_id}.json", pending)
    return {
        "approval_required": True,
        "approval_id": approval_id,
        "expires_at": pending["expires_at"],
        "operation": operation,
        "profile": profile_name,
        "server_url": pending["server_url"],
        "project": project_identifier,
        "target": target,
        "preview": pending["preview"],
        "next": f"用户明确确认 {approval_id} 后，执行 apply {approval_id} --confirm {approval_id}",
    }


def load_pending(approval_id: str) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(r"chg-[0-9a-f]{32}", approval_id):
        raise RedmineAccessError("无效的操作编号")
    path = PENDING_DIR / f"{approval_id}.json"
    _check_secure(RUNTIME_ROOT, directory=True)
    _check_secure(PENDING_DIR, directory=True)
    _check_secure(path)
    try:
        pending = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RedmineAccessError(f"无法读取待确认操作：{exc}") from exc
    if not isinstance(pending, dict) or pending.get("version") != 1:
        raise RedmineAccessError("待确认操作格式无效")
    stored = pending.pop("integrity", None)
    pending_profile = pending.get("profile")
    if not isinstance(pending_profile, str):
        raise RedmineAccessError("待确认操作缺少 profile")
    _, profile, _ = load_context(pending_profile)
    if not isinstance(stored, str) or not secrets.compare_digest(
        stored, keyed_integrity(pending, profile["api_key"])
    ):
        raise RedmineAccessError("待确认操作内容已被修改")
    pending["integrity"] = stored
    if pending.get("approval_id") != approval_id:
        raise RedmineAccessError("操作编号不匹配")
    if parse_time(pending["expires_at"]) <= utc_now():
        path.unlink(missing_ok=True)
        raise RedmineAccessError("待确认操作已过期，请重新生成预览")
    return path, pending


def append_audit(pending: dict[str, Any], result: str) -> None:
    target = pending.get("target") if isinstance(pending.get("target"), dict) else {}
    safe_target = {key: target[key] for key in ("issue_id", "project") if key in target}
    body = pending.get("action", {}).get("body") or {}
    if isinstance(body, dict) and len(body) == 1 and isinstance(next(iter(body.values()), None), dict):
        body = next(iter(body.values()))
    record = {
        "time": iso_time(utc_now()),
        "approval_id": pending.get("approval_id"),
        "profile": pending.get("profile"),
        "operation": pending.get("operation"),
        "project": pending.get("project_identifier"),
        "target": safe_target,
        "fields": sorted(body.keys()) if isinstance(body, dict) else [],
        "result": result,
    }
    if AUDIT_FILE.parent.is_symlink() or AUDIT_FILE.is_symlink():
        raise RedmineAccessError("拒绝使用符号链接审计路径")
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(AUDIT_FILE.parent, 0o700)
    if AUDIT_FILE.exists() and AUDIT_FILE.stat().st_mode & 0o077:
        raise RedmineAccessError("审计文件权限过宽；请设置为 0600")
    descriptor = os.open(AUDIT_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def try_append_audit(pending: dict[str, Any], result: str) -> bool:
    try:
        append_audit(pending, result)
        return True
    except (OSError, RedmineAccessError):
        return False


def prepare_create_issue(
    api: RedmineHTTP,
    profile_name: str,
    profile: dict[str, Any],
    policy: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    require_operation(policy, "issue.create")
    reject_api_key_content(payload, profile, "创建 payload")
    allowed = set(policy.get("issue_create_fields", []))
    unknown = set(payload) - allowed
    if unknown:
        raise RedmineAccessError(f"创建 Issue 包含未授权字段：{sorted(unknown)}")
    if not payload.get("project_id") or not isinstance(payload.get("subject"), str) or not payload["subject"].strip():
        raise RedmineAccessError("创建 Issue 必须提供 project_id 和非空 subject")
    validate_custom_fields(policy, payload)
    project = get_project(api, payload["project_id"])
    identifier = project["identifier"]
    return create_pending(
        profile_name=profile_name,
        profile=profile,
        policy=policy,
        operation="issue.create",
        project_identifier=identifier,
        target={"project": identifier},
        action={"kind": "json", "method": "POST", "endpoint": "/issues.json", "body": {"issue": payload}},
        preview={"create": payload},
    )


def prepare_update_issue(
    api: RedmineHTTP,
    profile_name: str,
    profile: dict[str, Any],
    policy: dict[str, Any],
    issue_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    require_operation(policy, "issue.update")
    reject_api_key_content(payload, profile, "更新 payload")
    if not payload:
        raise RedmineAccessError("更新 payload 不能为空")
    allowed = set(policy.get("issue_update_fields", []))
    unknown = set(payload) - allowed
    if unknown:
        raise RedmineAccessError(f"更新包含未授权字段：{sorted(unknown)}")
    validate_custom_fields(policy, payload)
    issue = get_issue(api, issue_id)
    project = issue_project(api, issue)
    current = {key: issue.get(key) for key in payload}
    return create_pending(
        profile_name=profile_name,
        profile=profile,
        policy=policy,
        operation="issue.update",
        project_identifier=project["identifier"],
        target={"issue_id": issue_id, "subject": issue.get("subject")},
        action={"kind": "json", "method": "PUT", "endpoint": f"/issues/{issue_id}.json", "body": {"issue": payload}},
        preview={"before": current, "after": payload},
        before_updated_on=issue.get("updated_on"),
    )


def prepare_comment(
    api: RedmineHTTP,
    profile_name: str,
    profile: dict[str, Any],
    policy: dict[str, Any],
    issue_id: int,
    notes: str,
    private: bool,
) -> dict[str, Any]:
    operation = "issue.private_comment" if private else "issue.comment"
    require_operation(policy, operation)
    reject_api_key_content(notes, profile, "评论")
    if not notes.strip():
        raise RedmineAccessError("评论内容不能为空")
    issue = get_issue(api, issue_id)
    project = issue_project(api, issue)
    body = {"notes": notes, "private_notes": private}
    return create_pending(
        profile_name=profile_name,
        profile=profile,
        policy=policy,
        operation=operation,
        project_identifier=project["identifier"],
        target={"issue_id": issue_id, "subject": issue.get("subject")},
        action={"kind": "json", "method": "PUT", "endpoint": f"/issues/{issue_id}.json", "body": {"issue": body}},
        preview={"comment": notes, "private": private},
        before_updated_on=issue.get("updated_on"),
    )


def prepare_time_entry(
    api: RedmineHTTP,
    profile_name: str,
    profile: dict[str, Any],
    policy: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    require_operation(policy, "time_entry.create")
    reject_api_key_content(payload, profile, "工时 payload")
    unknown = set(payload) - TIME_ENTRY_FIELDS
    if unknown:
        raise RedmineAccessError(f"工时 payload 包含不允许字段：{sorted(unknown)}")
    if "hours" not in payload or "activity_id" not in payload:
        raise RedmineAccessError("登记工时必须提供 hours 和 activity_id")
    if ("issue_id" in payload) == ("project_id" in payload):
        raise RedmineAccessError("登记工时必须且只能提供 issue_id 或 project_id")
    try:
        hours = float(payload["hours"])
    except (TypeError, ValueError) as exc:
        raise RedmineAccessError("hours 必须是数字") from exc
    if not 0 < hours <= float(policy.get("max_time_entry_hours", 24)):
        raise RedmineAccessError("hours 超出本地权限策略范围")
    if "comments" in payload and (
        not isinstance(payload["comments"], str) or len(payload["comments"]) > 255
    ):
        raise RedmineAccessError("工时 comments 必须是最多 255 字符的字符串")
    validate_custom_fields(policy, payload)
    target: dict[str, Any]
    before: str | None = None
    if "issue_id" in payload:
        issue_id = int(payload["issue_id"])
        issue = get_issue(api, issue_id)
        project = issue_project(api, issue)
        target = {"issue_id": issue_id, "subject": issue.get("subject")}
        before = issue.get("updated_on")
    else:
        project = get_project(api, payload["project_id"])
        target = {"project": project["identifier"]}
    return create_pending(
        profile_name=profile_name,
        profile=profile,
        policy=policy,
        operation="time_entry.create",
        project_identifier=project["identifier"],
        target=target,
        action={"kind": "json", "method": "POST", "endpoint": "/time_entries.json", "body": {"time_entry": payload}},
        preview={"create_time_entry": payload},
        before_updated_on=before,
    )


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def file_contains(path: Path, needle: bytes) -> bool:
    if not needle:
        return False
    overlap = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            data = overlap + chunk
            if needle in data:
                return True
            overlap = data[-(len(needle) - 1) :] if len(needle) > 1 else b""
    return False


def prepare_attachment(
    api: RedmineHTTP,
    profile_name: str,
    profile: dict[str, Any],
    policy: dict[str, Any],
    issue_id: int,
    source: str,
    description: str | None,
) -> dict[str, Any]:
    require_operation(policy, "attachment.upload")
    raw_path = Path(source).expanduser()
    if raw_path.is_symlink():
        raise RedmineAccessError("附件路径不能是符号链接")
    reject_api_key_content({"path": str(raw_path), "description": description}, profile, "附件信息")
    path = raw_path.resolve(strict=True)
    if not path.is_file():
        raise RedmineAccessError("附件必须是普通文件")
    size = path.stat().st_size
    if size > policy.get("max_attachment_bytes", 10_000_000):
        raise RedmineAccessError("附件超过本地权限策略的大小限制")
    if file_contains(path, profile["api_key"].encode("utf-8")):
        raise RedmineAccessError("附件内容包含当前 profile 的 API Key，已拒绝上传")
    issue = get_issue(api, issue_id)
    project = issue_project(api, issue)
    attachment = {
        "path": str(path),
        "filename": path.name,
        "size": size,
        "sha256": file_digest(path),
        "description": description,
    }
    return create_pending(
        profile_name=profile_name,
        profile=profile,
        policy=policy,
        operation="attachment.upload",
        project_identifier=project["identifier"],
        target={"issue_id": issue_id, "subject": issue.get("subject")},
        action={"kind": "attachment", "issue_id": issue_id, "attachment": attachment},
        preview={"attachment": {key: value for key, value in attachment.items() if key != "path"}},
        before_updated_on=issue.get("updated_on"),
    )


def _verify_pending_current(api: RedmineHTTP, pending: dict[str, Any]) -> None:
    operation = pending.get("operation")
    action = pending.get("action", {})
    if operation == "issue.create":
        project = get_project(api, action["body"]["issue"]["project_id"])
        if project["identifier"] != pending.get("project_identifier"):
            raise RedmineAccessError("创建目标项目已变化，原确认失效")
    if operation == "time_entry.create" and "project_id" in action.get("body", {}).get("time_entry", {}):
        project = get_project(api, action["body"]["time_entry"]["project_id"])
        if project["identifier"] != pending.get("project_identifier"):
            raise RedmineAccessError("工时目标项目已变化，原确认失效")
    target = pending.get("target", {})
    issue_id = target.get("issue_id") if isinstance(target, dict) else None
    before = pending.get("before_updated_on")
    if issue_id is not None and before is not None:
        issue = get_issue(api, int(issue_id))
        if target.get("subject") is not None and target.get("subject") != issue.get("subject"):
            raise RedmineAccessError("目标 Issue 摘要已变化，原确认失效")
        if issue.get("updated_on") != before:
            raise RedmineAccessError("目标 Issue 已发生变化，原确认失效，请重新生成预览")
        project = issue_project(api, issue)
        if project["identifier"] != pending.get("project_identifier"):
            raise RedmineAccessError("目标 Issue 所属项目已变化，原确认失效")


def validate_pending_semantics(
    pending: dict[str, Any], profile: dict[str, Any], policy: dict[str, Any]
) -> None:
    operation = pending.get("operation")
    action = pending.get("action")
    target = pending.get("target")
    if operation not in WRITE_OPERATIONS or not isinstance(action, dict) or not isinstance(target, dict):
        raise RedmineAccessError("待确认操作的语义类型无效")
    require_operation(policy, operation)
    if operation == "issue.create":
        expected = {"kind", "method", "endpoint", "body"}
        issue = action.get("body", {}).get("issue") if isinstance(action.get("body"), dict) else None
        if (
            set(action) != expected
            or action.get("kind") != "json"
            or action.get("method") != "POST"
            or action.get("endpoint") != "/issues.json"
            or not isinstance(issue, dict)
            or set(issue) - set(policy.get("issue_create_fields", []))
            or not issue.get("project_id")
            or not isinstance(issue.get("subject"), str)
            or not issue["subject"].strip()
        ):
            raise RedmineAccessError("待确认的 Issue 创建动作不符合内置语义")
        validate_custom_fields(policy, issue)
        reject_api_key_content(issue, profile, "待确认创建 payload")
        if pending.get("preview") != compact_preview({"create": issue}):
            raise RedmineAccessError("待确认预览与 Issue 创建动作不匹配")
        return
    if operation in {"issue.update", "issue.comment", "issue.private_comment"}:
        issue_id = target.get("issue_id")
        expected_endpoint = f"/issues/{issue_id}.json"
        body = action.get("body", {}).get("issue") if isinstance(action.get("body"), dict) else None
        if (
            set(action) != {"kind", "method", "endpoint", "body"}
            or action.get("kind") != "json"
            or action.get("method") != "PUT"
            or action.get("endpoint") != expected_endpoint
            or not isinstance(issue_id, int)
            or issue_id <= 0
            or not isinstance(body, dict)
            or not isinstance(pending.get("before_updated_on"), str)
        ):
            raise RedmineAccessError("待确认的 Issue 更新动作不符合内置语义")
        if operation == "issue.update":
            if not body or set(body) - set(policy.get("issue_update_fields", [])):
                raise RedmineAccessError("待确认的 Issue 更新字段超出权限")
            validate_custom_fields(policy, body)
            preview = pending.get("preview")
            if not isinstance(preview, dict) or preview.get("after") != compact_preview(body):
                raise RedmineAccessError("待确认预览与 Issue 更新动作不匹配")
        else:
            if set(body) != {"notes", "private_notes"} or not isinstance(body.get("notes"), str) or not body["notes"].strip():
                raise RedmineAccessError("待确认评论的字段无效")
            expected_private = operation == "issue.private_comment"
            if body.get("private_notes") is not expected_private:
                raise RedmineAccessError("待确认评论的私有属性与操作权限不匹配")
            if pending.get("preview") != compact_preview(
                {"comment": body["notes"], "private": expected_private}
            ):
                raise RedmineAccessError("待确认预览与评论动作不匹配")
        reject_api_key_content(body, profile, "待确认 Issue payload")
        return
    if operation == "time_entry.create":
        entry = action.get("body", {}).get("time_entry") if isinstance(action.get("body"), dict) else None
        if (
            set(action) != {"kind", "method", "endpoint", "body"}
            or action.get("kind") != "json"
            or action.get("method") != "POST"
            or action.get("endpoint") != "/time_entries.json"
            or not isinstance(entry, dict)
            or set(entry) - TIME_ENTRY_FIELDS
            or ("issue_id" in entry) == ("project_id" in entry)
            or "hours" not in entry
            or "activity_id" not in entry
        ):
            raise RedmineAccessError("待确认的工时动作不符合内置语义")
        if "issue_id" in entry:
            try:
                entry_issue_id = int(entry["issue_id"])
            except (TypeError, ValueError) as exc:
                raise RedmineAccessError("待确认工时的 issue_id 无效") from exc
            if target.get("issue_id") != entry_issue_id:
                raise RedmineAccessError("待确认工时的目标 Issue 不匹配")
            if not isinstance(pending.get("before_updated_on"), str):
                raise RedmineAccessError("待确认工时缺少 Issue 版本快照")
        elif target.get("project") != pending.get("project_identifier"):
            raise RedmineAccessError("待确认工时的目标项目不匹配")
        try:
            hours = float(entry["hours"])
        except (TypeError, ValueError) as exc:
            raise RedmineAccessError("待确认工时的 hours 无效") from exc
        if not 0 < hours <= float(policy.get("max_time_entry_hours", 24)):
            raise RedmineAccessError("待确认工时超出权限范围")
        if "comments" in entry and (
            not isinstance(entry["comments"], str) or len(entry["comments"]) > 255
        ):
            raise RedmineAccessError("待确认工时的 comments 无效")
        reject_api_key_content(entry, profile, "待确认工时 payload")
        if pending.get("preview") != compact_preview({"create_time_entry": entry}):
            raise RedmineAccessError("待确认预览与工时动作不匹配")
        return
    if operation == "attachment.upload":
        attachment = action.get("attachment")
        issue_id = target.get("issue_id")
        if (
            set(action) != {"kind", "issue_id", "attachment"}
            or action.get("kind") != "attachment"
            or action.get("issue_id") != issue_id
            or not isinstance(issue_id, int)
            or issue_id <= 0
            or not isinstance(attachment, dict)
            or set(attachment) != {"path", "filename", "size", "sha256", "description"}
            or not isinstance(attachment.get("path"), str)
            or not isinstance(attachment.get("filename"), str)
            or not isinstance(attachment.get("size"), int)
            or not 0 <= attachment["size"] <= policy.get("max_attachment_bytes", 10_000_000)
            or Path(attachment["path"]).name != attachment["filename"]
            or not re.fullmatch(r"[0-9a-f]{64}", str(attachment.get("sha256", "")))
            or attachment.get("description") is not None
            and not isinstance(attachment.get("description"), str)
            or not isinstance(pending.get("before_updated_on"), str)
        ):
            raise RedmineAccessError("待确认的附件动作不符合内置语义")
        reject_api_key_content(attachment, profile, "待确认附件信息")
        expected_preview = {
            "attachment": {
                key: value for key, value in attachment.items() if key != "path"
            }
        }
        if pending.get("preview") != compact_preview(expected_preview):
            raise RedmineAccessError("待确认预览与附件动作不匹配")
        return
    raise RedmineAccessError("待确认操作没有内置语义验证器")


def _same_value(actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return actual is expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return float(actual) == float(expected)
    return str(actual) == str(expected)


def _issue_value(issue: dict[str, Any], field: str) -> Any:
    nested = {
        "project_id": "project",
        "tracker_id": "tracker",
        "status_id": "status",
        "priority_id": "priority",
        "category_id": "category",
        "fixed_version_id": "fixed_version",
        "assigned_to_id": "assigned_to",
        "parent_issue_id": "parent",
    }
    if field in nested:
        value = issue.get(nested[field])
        return value.get("id") if isinstance(value, dict) else None
    return issue.get(field)


def _verify_issue_fields(api: RedmineHTTP, issue_id: int, expected: dict[str, Any]) -> dict[str, Any]:
    issue = get_issue(api, issue_id)
    mismatches: dict[str, Any] = {}
    for field, wanted in expected.items():
        if field == "custom_fields":
            actual_fields = {
                item.get("id"): item.get("value")
                for item in issue.get("custom_fields", [])
                if isinstance(item, dict)
            }
            for item in wanted:
                if not _same_value(actual_fields.get(item["id"]), item.get("value")):
                    mismatches[f"custom_fields.{item['id']}"] = {
                        "expected": item.get("value"),
                        "actual": actual_fields.get(item["id"]),
                    }
            continue
        actual = _issue_value(issue, field)
        if not _same_value(actual, wanted):
            mismatches[field] = {"expected": wanted, "actual": actual}
    return mismatches


def _verify_json_write(
    api: RedmineHTTP,
    pending: dict[str, Any],
    response: Any,
) -> dict[str, Any]:
    operation = pending["operation"]
    body = pending["action"]["body"]
    if operation == "issue.create":
        issue_id = response.get("issue", {}).get("id") if isinstance(response, dict) else None
        if not issue_id:
            raise RequestOutcomeUnknown("Issue 创建请求已发送，但响应缺少 ID；禁止自动重试")
        created_issue = get_issue(api, int(issue_id))
        created_project = issue_project(api, created_issue)
        if created_project["identifier"] != pending["project_identifier"]:
            raise RequestOutcomeUnknown("Issue 已创建但项目验证不一致；禁止自动重试")
        expected = {key: value for key, value in body["issue"].items() if key != "project_id"}
        mismatches = _verify_issue_fields(api, int(issue_id), expected)
        if mismatches:
            raise RequestOutcomeUnknown(
                f"Issue 已创建但以下字段写后验证不一致：{sorted(mismatches)}；禁止自动重试"
            )
        return {"verified": True, "issue_id": issue_id}
    if operation == "issue.update":
        issue_id = int(pending["target"]["issue_id"])
        mismatches = _verify_issue_fields(api, issue_id, body["issue"])
        if mismatches:
            raise RequestOutcomeUnknown(
                f"Issue 更新已发送但以下字段写后验证不一致：{sorted(mismatches)}；禁止自动重试"
            )
        return {"verified": True, "issue_id": issue_id}
    if operation in {"issue.comment", "issue.private_comment"}:
        issue_id = int(pending["target"]["issue_id"])
        issue = get_issue(api, issue_id, ["journals"])
        notes = body["issue"]["notes"]
        private = body["issue"].get("private_notes", False)
        journals = issue.get("journals", [])
        found = any(
            isinstance(item, dict)
            and item.get("notes") == notes
            and bool(item.get("private_notes", False)) == private
            for item in journals[-10:]
        )
        if not found:
            raise RequestOutcomeUnknown("评论请求已发送但写后未找到对应 journal；禁止自动重试")
        return {"verified": True, "issue_id": issue_id}
    if operation == "time_entry.create":
        entry_id = response.get("time_entry", {}).get("id") if isinstance(response, dict) else None
        if not entry_id:
            raise RequestOutcomeUnknown("工时请求已发送，但响应缺少 ID；禁止自动重试")
        _, fetched = api.request("GET", f"/time_entries/{int(entry_id)}.json")
        entry = fetched.get("time_entry", {}) if isinstance(fetched, dict) else {}
        mismatches = {}
        for key, value in body["time_entry"].items():
            if key in {"issue_id", "project_id", "custom_fields"}:
                continue
            actual = (
                entry.get("activity", {}).get("id")
                if key == "activity_id" and isinstance(entry.get("activity"), dict)
                else entry.get(key)
            )
            if not _same_value(actual, value):
                mismatches[key] = {"expected": value, "actual": actual}
        if mismatches:
            raise RequestOutcomeUnknown(
                f"工时已登记但以下字段写后验证不一致：{sorted(mismatches)}；禁止自动重试"
            )
        return {"verified": True, "time_entry_id": entry_id}
    raise RedmineAccessError(f"缺少 {operation} 的写后验证器")


def apply_pending(approval_id: str, confirmation: str) -> dict[str, Any]:
    if not secrets.compare_digest(approval_id, confirmation):
        raise RedmineAccessError("--confirm 必须与操作编号完全一致")
    path, pending = load_pending(approval_id)
    profile_name, profile, policy = load_context(pending["profile"])
    if profile_name != pending["profile"] or validate_server_url(profile["server_url"]) != pending["server_url"]:
        raise RedmineAccessError("profile 或服务器地址已变化，原确认失效")
    if _policy_snapshot(policy) != pending["policy_fingerprint"]:
        raise RedmineAccessError("权限策略已变化，原确认失效")
    operation = pending["operation"]
    require_operation(policy, operation)
    require_write_project(policy, pending["project_identifier"])
    validate_pending_semantics(pending, profile, policy)
    api = RedmineHTTP(profile)
    _verify_pending_current(api, pending)
    action = pending["action"]
    running = path.with_suffix(".running")
    os.replace(path, running)
    try:
        if action.get("kind") == "json":
            status, response = api.request(
                action["method"], action["endpoint"], json_body=action["body"]
            )
            expected_status = 201 if action["method"] == "POST" else 204
            if status != expected_status:
                raise RequestOutcomeUnknown(
                    f"写请求返回非预期状态 {status}（预期 {expected_status}）；禁止自动重试"
                )
            try:
                verification = _verify_json_write(api, pending, response)
            except RequestOutcomeUnknown:
                raise
            except RedmineAccessError as exc:
                raise RequestOutcomeUnknown(
                    "写请求已成功返回，但写后读取验证失败；禁止自动重试"
                ) from exc
            result = {"status": status, "verification": verification}
        elif action.get("kind") == "attachment":
            attachment = action["attachment"]
            source = Path(attachment["path"])
            if not source.is_file() or source.stat().st_size != attachment["size"] or file_digest(source) != attachment["sha256"]:
                raise RedmineAccessError("附件内容已变化，原确认失效")
            upload_started = False
            try:
                upload_status, upload_response = api.request(
                    "POST",
                    "/uploads.json",
                    query={"filename": attachment["filename"]},
                    binary_body=source.read_bytes(),
                )
                upload_started = True
                if upload_status != 201:
                    raise RequestOutcomeUnknown(
                        f"附件上传返回非预期状态 {upload_status}；禁止自动重试"
                    )
                token = upload_response.get("upload", {}).get("token") if isinstance(upload_response, dict) else None
                if not token:
                    raise RequestOutcomeUnknown("附件上传响应缺少 token；禁止自动重试")
                upload = {"token": token, "filename": attachment["filename"]}
                if attachment.get("description"):
                    upload["description"] = attachment["description"]
                update_status, _ = api.request(
                    "PUT",
                    f"/issues/{int(action['issue_id'])}.json",
                    json_body={"issue": {"uploads": [upload]}},
                )
                if update_status != 204:
                    raise RequestOutcomeUnknown(
                        f"附件关联返回非预期状态 {update_status}；禁止自动重试"
                    )
                issue = get_issue(api, int(action["issue_id"]), ["attachments"])
                found = any(
                    isinstance(item, dict)
                    and item.get("filename") == attachment["filename"]
                    and item.get("filesize") == attachment["size"]
                    for item in issue.get("attachments", [])
                )
                if not found:
                    raise RequestOutcomeUnknown(
                        "附件请求已发送但写后验证未找到对应附件；禁止自动重试"
                    )
            except RequestOutcomeUnknown:
                raise
            except RedmineAccessError as exc:
                if upload_started:
                    raise RequestOutcomeUnknown(
                        "附件上传已开始但未能完成关联验证；禁止自动重试"
                    ) from exc
                raise
            result = {
                "upload_status": upload_status,
                "issue_update_status": update_status,
                "verified": True,
            }
        else:
            raise RedmineAccessError("待确认操作包含未知动作")
        audit_written = try_append_audit(pending, "success")
        return {
            "approval_id": approval_id,
            "operation": operation,
            "result": result,
            "replayable": False,
            "audit_written": audit_written,
        }
    except RequestOutcomeUnknown:
        try_append_audit(pending, "indeterminate-no-automatic-retry")
        raise
    except Exception:
        try_append_audit(pending, "failed-no-automatic-retry")
        raise
    finally:
        running.unlink(missing_ok=True)


def command_status(profile_name: str | None) -> dict[str, Any]:
    selected, profile, policy = load_context(profile_name)
    return redact_secrets({
        "configured": True,
        "profile": selected,
        "server_url": validate_server_url(profile["server_url"]),
        "write_projects": policy.get("write_projects", []),
        "operations": policy.get("operations", {}),
    }, [profile["api_key"]])


def command_read(args: argparse.Namespace, api: RedmineHTTP, policy: dict[str, Any]) -> Any:
    if args.command == "current-user":
        require_operation(policy, "user.read")
        _, response = api.request("GET", "/users/current.json")
        user = response.get("user", {}) if isinstance(response, dict) else {}
        keys = ("id", "login", "firstname", "lastname", "mail", "created_on", "last_login_on")
        return {key: user[key] for key in keys if key in user}
    if args.command == "projects":
        require_operation(policy, "project.read")
        _, response = api.request("GET", "/projects.json", query={"limit": args.limit, "offset": args.offset})
        projects = response.get("projects", []) if isinstance(response, dict) else []
        return {
            "projects": [project_summary(item) for item in projects if isinstance(item, dict)],
            "total_count": response.get("total_count") if isinstance(response, dict) else None,
            "offset": response.get("offset") if isinstance(response, dict) else None,
            "limit": response.get("limit") if isinstance(response, dict) else None,
        }
    if args.command == "project":
        require_operation(policy, "project.read")
        project = get_project(api, args.reference)
        return project if args.full else project_summary(project)
    if args.command == "project-memberships":
        require_operation(policy, "project.read")
        require_operation(policy, "user.read")
        encoded = quote(args.reference, safe="")
        _, response = api.request(
            "GET",
            f"/projects/{encoded}/memberships.json",
            query={"limit": args.limit, "offset": args.offset},
        )
        memberships = response.get("memberships", []) if isinstance(response, dict) else []
        compact = []
        for item in memberships:
            if not isinstance(item, dict):
                continue
            compact.append(
                {key: item[key] for key in ("id", "project", "user", "group", "roles") if key in item}
            )
        return {
            "memberships": compact,
            "total_count": response.get("total_count") if isinstance(response, dict) else None,
            "offset": response.get("offset") if isinstance(response, dict) else None,
            "limit": response.get("limit") if isinstance(response, dict) else None,
        }
    if args.command == "issues":
        require_operation(policy, "issue.read")
        query: dict[str, Any] = {"limit": args.limit, "offset": args.offset}
        for item in args.filter:
            if "=" not in item:
                raise RedmineAccessError(f"过滤条件必须是 key=value：{item}")
            key, value = item.split("=", 1)
            if key not in ISSUE_FILTERS:
                raise RedmineAccessError(f"无效过滤字段：{key}")
            query[key] = value
        if args.sort:
            for item in args.sort.split(","):
                parts = item.split(":", 1)
                if parts[0] not in ISSUE_SORT_FIELDS or (len(parts) == 2 and parts[1] not in {"asc", "desc"}):
                    raise RedmineAccessError(f"无效排序字段：{item}")
            query["sort"] = args.sort
        _, response = api.request("GET", "/issues.json", query=query)
        issues = response.get("issues", []) if isinstance(response, dict) else []
        return {
            "issues": [issue_summary(item) for item in issues if isinstance(item, dict)],
            "total_count": response.get("total_count") if isinstance(response, dict) else None,
            "offset": response.get("offset") if isinstance(response, dict) else None,
            "limit": response.get("limit") if isinstance(response, dict) else None,
        }
    if args.command == "issue":
        require_operation(policy, "issue.read")
        includes = []
        if args.include:
            includes = [item.strip() for item in args.include.split(",") if item.strip()]
            unknown = set(includes) - ISSUE_INCLUDES
            if unknown:
                raise RedmineAccessError(f"不支持的 include：{sorted(unknown)}")
        issue = get_issue(api, args.issue_id, includes)
        return issue if args.full else issue_summary(
            issue, description=args.description, journal_limit=args.journal_limit
        )
    if args.command == "metadata":
        operation, endpoint = METADATA_ENDPOINTS[args.kind]
        require_operation(policy, operation)
        query = {"limit": args.limit, "offset": args.offset} if args.kind == "users" else None
        _, response = api.request("GET", endpoint, query=query)
        return response
    if args.command == "time-entries":
        require_operation(policy, "time_entry.read")
        query: dict[str, Any] = {"limit": args.limit, "offset": args.offset}
        for item in args.filter:
            if "=" not in item:
                raise RedmineAccessError(f"过滤条件必须是 key=value：{item}")
            key, value = item.split("=", 1)
            if key not in TIME_ENTRY_FILTERS:
                raise RedmineAccessError(f"无效工时过滤字段：{key}")
            query[key] = value
        _, response = api.request("GET", "/time_entries.json", query=query)
        entries = response.get("time_entries", []) if isinstance(response, dict) else []
        return {
            "time_entries": [time_entry_summary(item) for item in entries if isinstance(item, dict)],
            "total_count": response.get("total_count") if isinstance(response, dict) else None,
            "offset": response.get("offset") if isinstance(response, dict) else None,
            "limit": response.get("limit") if isinstance(response, dict) else None,
        }
    if args.command == "time-entry":
        require_operation(policy, "time_entry.read")
        _, response = api.request("GET", f"/time_entries/{args.entry_id}.json")
        entry = response.get("time_entry") if isinstance(response, dict) else None
        if not isinstance(entry, dict):
            raise RedmineAccessError("Redmine time entry 响应格式无效")
        return entry if args.full else time_entry_summary(entry)
    raise RedmineAccessError(f"未知读取命令：{args.command}")


def bounded_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("limit 必须在 1 到 100 之间")
    return parsed


def positive_issue_id(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Issue ID 必须为正整数")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="受权限控制的 Redmine REST 客户端")
    parser.add_argument("--profile", help="使用指定的已配置 profile")
    parser.add_argument("--pretty", action="store_true", help="缩进 JSON 输出")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="检查本地配置和权限，不联网")
    subparsers.add_parser("permissions", help="显示当前权限，不显示 API Key")
    subparsers.add_parser("current-user", help="查询当前 API 用户")

    projects = subparsers.add_parser("projects", help="分页查询项目摘要")
    projects.add_argument("--limit", type=bounded_limit, default=20)
    projects.add_argument("--offset", type=int, default=0)

    project = subparsers.add_parser("project", help="查询单个项目；默认仅返回摘要")
    project.add_argument("reference", help="项目 ID 或 identifier")
    project.add_argument("--full", action="store_true")

    memberships = subparsers.add_parser("project-memberships", help="查询项目成员和角色摘要")
    memberships.add_argument("reference", help="项目 ID 或 identifier")
    memberships.add_argument("--limit", type=bounded_limit, default=20)
    memberships.add_argument("--offset", type=int, default=0)

    issues = subparsers.add_parser("issues", help="分页查询 Issue 摘要")
    issues.add_argument("--filter", action="append", default=[], metavar="KEY=VALUE")
    issues.add_argument("--sort")
    issues.add_argument("--limit", type=bounded_limit, default=20)
    issues.add_argument("--offset", type=int, default=0)

    issue = subparsers.add_parser("issue", help="查询单个 Issue；默认仅返回摘要")
    issue.add_argument("issue_id", type=positive_issue_id)
    issue.add_argument("--include", help="逗号分隔的关联数据")
    issue.add_argument("--description", action="store_true", help="在摘要中包含描述")
    issue.add_argument("--journal-limit", type=bounded_limit, default=10, help="摘要最多返回最近 N 条 journal")
    issue.add_argument("--full", action="store_true", help="返回完整原始 Issue JSON")

    metadata = subparsers.add_parser("metadata", help="查询受控的 Redmine 元数据")
    metadata.add_argument("kind", choices=sorted(METADATA_ENDPOINTS))
    metadata.add_argument("--limit", type=bounded_limit, default=20)
    metadata.add_argument("--offset", type=int, default=0)

    time_entries = subparsers.add_parser("time-entries", help="分页查询工时摘要")
    time_entries.add_argument("--filter", action="append", default=[], metavar="KEY=VALUE")
    time_entries.add_argument("--limit", type=bounded_limit, default=20)
    time_entries.add_argument("--offset", type=int, default=0)

    time_entry_read = subparsers.add_parser("time-entry", help="查询单条工时")
    time_entry_read.add_argument("entry_id", type=positive_issue_id)
    time_entry_read.add_argument("--full", action="store_true")

    create = subparsers.add_parser("prepare-create-issue", help="准备创建 Issue，暂不写入")
    create.add_argument("--payload-file", required=True)

    update = subparsers.add_parser("prepare-update-issue", help="准备更新 Issue，暂不写入")
    update.add_argument("issue_id", type=positive_issue_id)
    update.add_argument("--payload-file", required=True)

    comment = subparsers.add_parser("prepare-comment", help="准备添加评论，暂不写入")
    comment.add_argument("issue_id", type=positive_issue_id)
    comment.add_argument("--notes-file", required=True)
    comment.add_argument("--private", action="store_true")

    time_entry = subparsers.add_parser("prepare-time-entry", help="准备登记工时，暂不写入")
    time_entry.add_argument("--payload-file", required=True)

    attachment = subparsers.add_parser("prepare-attachment", help="准备上传附件，暂不写入")
    attachment.add_argument("issue_id", type=positive_issue_id)
    attachment.add_argument("path")
    attachment.add_argument("--description")

    apply = subparsers.add_parser("apply", help="执行已被用户明确确认的一次性变更")
    apply.add_argument("approval_id")
    apply.add_argument("--confirm", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output_secrets: list[str] = []
    try:
        if args.command in {"status", "permissions"}:
            value = command_status(args.profile)
            print_json(value, args.pretty)
            return 0
        if args.command == "apply":
            _, pending = load_pending(args.approval_id)
            _, apply_profile, _ = load_context(pending["profile"])
            output_secrets = [apply_profile["api_key"]]
            print_json(
                apply_pending(args.approval_id, args.confirm),
                args.pretty,
                output_secrets,
            )
            return 0
        if args.command.startswith("prepare-") and not args.profile:
            raise RedmineAccessError("准备写操作必须通过 --profile 显式选择 profile")
        profile_name, profile, policy = load_context(args.profile)
        output_secrets = [profile["api_key"]]
        api = RedmineHTTP(profile)
        if args.command in {
            "current-user",
            "projects",
            "project",
            "project-memberships",
            "issues",
            "issue",
            "metadata",
            "time-entries",
            "time-entry",
        }:
            value = command_read(args, api, policy)
        elif args.command == "prepare-create-issue":
            value = prepare_create_issue(api, profile_name, profile, policy, load_payload(args.payload_file))
        elif args.command == "prepare-update-issue":
            value = prepare_update_issue(api, profile_name, profile, policy, args.issue_id, load_payload(args.payload_file))
        elif args.command == "prepare-comment":
            notes = load_text(args.notes_file)
            value = prepare_comment(api, profile_name, profile, policy, args.issue_id, notes, args.private)
        elif args.command == "prepare-time-entry":
            value = prepare_time_entry(api, profile_name, profile, policy, load_payload(args.payload_file))
        elif args.command == "prepare-attachment":
            value = prepare_attachment(api, profile_name, profile, policy, args.issue_id, args.path, args.description)
        else:
            raise RedmineAccessError(f"未知命令：{args.command}")
        print_json(value, args.pretty, output_secrets)
        return 0
    except (RedmineAccessError, OSError, ValueError) as exc:
        message = str(exc)
        if isinstance(exc, RequestOutcomeUnknown):
            code = "INDETERMINATE"
        elif message.startswith("缺少配置路径"):
            code = "CONFIG_MISSING"
        elif "配置" in message and any(word in message for word in ("无效", "过宽", "无法读取", "必须")):
            code = "CONFIG_INVALID"
        elif "已过期" in message:
            code = "EXPIRED"
        elif "权限策略已变化" in message:
            code = "POLICY_CHANGED"
        elif "目标 Issue 已发生变化" in message:
            code = "STALE"
        else:
            code = "ERROR"
        print_json({"code": code, "error": message}, args.pretty, output_secrets)
        return 1


if __name__ == "__main__":
    sys.exit(main())
