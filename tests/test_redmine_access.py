from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from pathlib import Path
from unittest import mock


CLIENT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "redmine-access"
    / "scripts"
    / "redmine_client.py"
)
SPEC = importlib.util.spec_from_file_location("redmine_access_client", CLIENT_PATH)
assert SPEC and SPEC.loader
client = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = client
SPEC.loader.exec_module(client)


def sample_profile(server_url: str = "https://redmine.example.test") -> dict:
    return {"server_url": server_url, "api_key": "top-secret-key"}


def sample_policy() -> dict:
    operations = {
        "issue.read": "allow",
        "project.read": "allow",
        "user.read": "allow",
        "metadata.read": "allow",
        "time_entry.read": "allow",
        "issue.create": "confirm",
        "issue.update": "confirm",
        "issue.comment": "confirm",
        "issue.private_comment": "deny",
        "time_entry.create": "confirm",
        "attachment.upload": "confirm",
        "attachment.download": "confirm",
        **{name: "deny" for name in client.DELETE_OPERATIONS},
    }
    return {
        "operations": operations,
        "write_projects": ["firmware"],
        "attachment_download_projects": ["firmware"],
        "issue_create_fields": ["project_id", "subject", "description"],
        "issue_update_fields": ["status_id", "assigned_to_id", "description"],
        "custom_field_ids": [],
        "max_mutations_per_confirmation": 1,
        "pending_ttl_seconds": 600,
        "max_attachment_bytes": 1_000_000,
        "max_attachment_download_bytes": 1_000_000,
        "max_time_entry_hours": 12,
    }


@contextlib.contextmanager
def isolated_runtime():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config_root = root / "config" / "skills" / "redmine-access"
        runtime_root = root / "state" / "skills" / "redmine-access"
        with mock.patch.multiple(
            client,
            CONFIG_ROOT=config_root,
            CONFIG_FILE=config_root / "config.json",
            PERMISSIONS_FILE=config_root / "permissions.json",
            RUNTIME_ROOT=runtime_root,
            PENDING_DIR=runtime_root / "pending",
            AUDIT_FILE=runtime_root / "audit.jsonl",
            DOWNLOAD_ROOT=root / "data" / "skills" / "redmine-access" / "downloads",
        ):
            yield root


def write_context(profile: dict | None = None, policy: dict | None = None) -> None:
    profile = profile or sample_profile()
    policy = policy or sample_policy()
    client.atomic_write_json(
        client.CONFIG_FILE,
        {"version": 1, "default_profile": "writer", "profiles": {"writer": profile}},
    )
    client.atomic_write_json(
        client.PERMISSIONS_FILE,
        {"version": 1, "profiles": {"writer": policy}},
    )


class FakeRedmine:
    def __init__(self, *, ignore_writes: bool = False) -> None:
        self.base_url = "https://redmine.example.test"
        self.issue = {
            "id": 42,
            "project": {"id": 9, "name": "Firmware"},
            "subject": "Example",
            "status": {"id": 1, "name": "New"},
            "assigned_to": {"id": 3, "name": "A"},
            "updated_on": "2026-08-20T01:00:00Z",
            "attachments": [
                {
                    "id": 77,
                    "filename": "radio.log",
                    "filesize": 19,
                    "content_type": "text/plain",
                    "content_url": "https://redmine.example.test/attachments/download/77/radio.log",
                    "created_on": "2026-08-20T01:00:00Z",
                }
            ],
        }
        self.write_count = 0
        self.download_count = 0
        self.ignore_writes = ignore_writes

    def request(self, method, endpoint, *, query=None, json_body=None, binary_body=None, content_type=None):
        if method == "GET" and endpoint == "/issues/42.json":
            return 200, {"issue": json.loads(json.dumps(self.issue))}
        if method == "GET" and endpoint == "/projects/9.json":
            return 200, {"project": {"id": 9, "identifier": "firmware", "name": "Firmware"}}
        if method == "PUT" and endpoint == "/issues/42.json":
            self.write_count += 1
            fields = json_body["issue"]
            if not self.ignore_writes:
                if "status_id" in fields:
                    self.issue["status"] = {"id": fields["status_id"], "name": "Changed"}
                if "assigned_to_id" in fields:
                    self.issue["assigned_to"] = {"id": fields["assigned_to_id"], "name": "Changed"}
                if "description" in fields:
                    self.issue["description"] = fields["description"]
                self.issue["updated_on"] = "2026-08-20T01:01:00Z"
            return 204, None
        raise AssertionError(f"unexpected request: {method} {endpoint} {query}")

    def download(self, endpoint, destination, *, expected_size, maximum_size):
        self.download_count += 1
        content = b"ERROR radio failed\n"
        if endpoint != "/attachments/download/77/radio.log":
            raise AssertionError(f"unexpected download endpoint: {endpoint}")
        if len(content) != expected_size or expected_size > maximum_size:
            raise AssertionError("unexpected download size")
        destination.write_bytes(content)
        os.chmod(destination, 0o600)
        return {
            "path": str(destination),
            "bytes": len(content),
            "sha256": client.hashlib.sha256(content).hexdigest(),
        }


class BinaryResponse:
    status = 200

    def __init__(self, content: bytes, declared_size: int | None = None) -> None:
        self.stream = io.BytesIO(content)
        self.headers = {}
        if declared_size is not None:
            self.headers["Content-Length"] = str(declared_size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)


class RedmineAccessTests(unittest.TestCase):
    def test_external_http_and_url_credentials_are_rejected(self):
        with self.assertRaises(client.RedmineAccessError):
            client.validate_server_url("http://redmine.example.test")
        with self.assertRaises(client.RedmineAccessError):
            client.validate_server_url("https://user:pass@redmine.example.test")
        self.assertEqual(
            client.validate_server_url("http://127.0.0.1:8080/redmine/"),
            "http://127.0.0.1:8080/redmine",
        )

    def test_delete_is_rejected_before_network(self):
        api = client.RedmineHTTP(sample_profile())
        with mock.patch.object(api.opener, "open") as opened:
            with self.assertRaisesRegex(client.RedmineAccessError, "永久禁止"):
                api.request("DELETE", "/issues/42.json")
            with self.assertRaises(client.RedmineAccessError):
                api.request("PATCH", "/issues/42.json")
            with self.assertRaises(client.RedmineAccessError):
                api.request("GET", "https://other.example/issues.json")
            with self.assertRaises(client.RedmineAccessError):
                api.request("GET", "/../users.json")
            opened.assert_not_called()

    def test_write_transport_failure_is_indeterminate_and_not_retried(self):
        api = client.RedmineHTTP(sample_profile())
        with mock.patch.object(api.opener, "open", side_effect=URLError("timeout")) as opened:
            with self.assertRaises(client.RequestOutcomeUnknown):
                api.request("POST", "/issues.json", json_body={"issue": {"subject": "x"}})
            self.assertEqual(opened.call_count, 1)

    def test_json_error_redaction_handles_escaped_api_key(self):
        secret = 'key-with-"quote-and-\\slash'
        raw = json.dumps({"message": secret, "errors": [f"bad {secret}"]}).encode()
        detail = client._decode_error(raw, secret)
        self.assertNotIn(secret, detail)
        self.assertNotIn("quote-and", detail)
        self.assertIn("[REDACTED]", detail)

    def test_write_allow_and_delete_confirm_make_policy_invalid(self):
        policy = sample_policy()
        policy["operations"]["issue.update"] = "allow"
        document = {"version": 1, "profiles": {"writer": policy}}
        with self.assertRaises(client.RedmineAccessError):
            client.validate_permissions(document)
        policy = sample_policy()
        policy["operations"]["issue.delete"] = "confirm"
        with self.assertRaises(client.RedmineAccessError):
            client.validate_permissions({"version": 1, "profiles": {"writer": policy}})
        policy = sample_policy()
        policy["operations"]["attachment.download"] = "allow"
        with self.assertRaises(client.RedmineAccessError):
            client.validate_permissions({"version": 1, "profiles": {"writer": policy}})

    def test_insecure_config_permissions_fail_closed(self):
        with isolated_runtime():
            write_context()
            os.chmod(client.CONFIG_FILE, 0o644)
            with self.assertRaisesRegex(client.RedmineAccessError, "权限过宽"):
                client.load_context()

    def test_prepare_write_requires_explicit_profile_before_config_load(self):
        with isolated_runtime(), contextlib.redirect_stdout(io.StringIO()) as output:
            result = client.main(["prepare-update-issue", "42", "--payload-file", "/missing"])
            self.assertEqual(result, 1)
            payload = json.loads(output.getvalue())
            self.assertIn("--profile", payload["error"])
            self.assertNotEqual(payload["code"], "CONFIG_MISSING")

    def test_current_user_output_never_contains_api_key(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _limit):
                return json.dumps(
                    {
                        "user": {
                            "id": 7,
                            "login": "top-secret-key",
                            "api_key": "server-returned-secret",
                        }
                    }
                ).encode()

        api = client.RedmineHTTP(sample_profile("http://127.0.0.1:8080/redmine"))
        with (
            mock.patch.object(api.opener, "open", return_value=Response()) as opened,
            mock.patch.object(
                client,
                "load_context",
                return_value=("writer", sample_profile(), sample_policy()),
            ),
            mock.patch.object(client, "RedmineHTTP", return_value=api),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(client.main(["current-user"]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result, {"id": 7, "login": "[REDACTED]"})
            self.assertNotIn("top-secret-key", output.getvalue())
            self.assertNotIn("server-returned-secret", output.getvalue())
            request = opened.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:8080/redmine/users/current.json")
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(headers["x-redmine-api-key"], "top-secret-key")

    def test_pending_tamper_is_detected(self):
        with isolated_runtime():
            write_context()
            fake = FakeRedmine()
            prepared = client.prepare_update_issue(
                fake,
                "writer",
                sample_profile(),
                sample_policy(),
                42,
                {"status_id": 2},
            )
            path = client.PENDING_DIR / f"{prepared['approval_id']}.json"
            value = json.loads(path.read_text())
            value["action"]["body"]["issue"]["status_id"] = 5
            path.write_text(json.dumps(value))
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(client.RedmineAccessError, "已被修改"):
                client.load_pending(prepared["approval_id"])

    def test_attachment_url_and_filename_are_fail_closed(self):
        with self.assertRaisesRegex(client.RedmineAccessError, "同源"):
            client.attachment_download_endpoint(
                "https://redmine.example.test",
                "https://files.example.test/attachments/download/77/radio.log",
                77,
                "radio.log",
            )
        with self.assertRaisesRegex(client.RedmineAccessError, "ID"):
            client.attachment_download_endpoint(
                "https://redmine.example.test",
                "https://redmine.example.test/attachments/download/88/radio.log",
                77,
                "radio.log",
            )
        for filename in ("../secret", "dir/file", "dir\\file", "bad\x00name"):
            with self.assertRaises(client.RedmineAccessError):
                client.safe_attachment_filename(filename)

    def test_download_permission_denial_happens_before_network(self):
        policy = sample_policy()
        policy["operations"]["attachment.download"] = "deny"
        api = mock.Mock()
        with self.assertRaisesRegex(client.RedmineAccessError, "attachment.download"):
            client.prepare_attachment_download(
                api,
                "writer",
                sample_profile(),
                policy,
                42,
                77,
                None,
            )
        api.request.assert_not_called()

    def test_legacy_policy_stays_valid_but_download_defaults_to_deny(self):
        policy = sample_policy()
        policy["operations"].pop("attachment.download")
        policy.pop("attachment_download_projects")
        policy.pop("max_attachment_download_bytes")
        client.validate_permissions(
            {"version": 1, "profiles": {"writer": policy}}
        )
        with self.assertRaisesRegex(client.RedmineAccessError, "当前：deny"):
            client.require_operation(policy, "attachment.download")

    def test_successful_attachment_download_is_confirmed_private_and_single_use(self):
        with isolated_runtime():
            write_context()
            fake = FakeRedmine()
            prepared = client.prepare_attachment_download(
                fake,
                "writer",
                sample_profile(),
                sample_policy(),
                42,
                77,
                None,
            )
            self.assertNotIn("content_url", json.dumps(prepared))
            self.assertNotIn("/attachments/download/", json.dumps(prepared))
            approval_id = prepared["approval_id"]
            with mock.patch.object(client, "RedmineHTTP", return_value=fake):
                result = client.apply_pending(approval_id, approval_id)
            downloaded = Path(result["result"]["path"])
            self.assertEqual(downloaded.read_bytes(), b"ERROR radio failed\n")
            self.assertEqual(stat.S_IMODE(downloaded.stat().st_mode), 0o600)
            self.assertEqual(fake.download_count, 1)
            with self.assertRaises(client.RedmineAccessError):
                client.load_pending(approval_id)
            audit = client.AUDIT_FILE.read_text()
            self.assertNotIn("top-secret-key", audit)
            self.assertNotIn("radio.log", audit)
            self.assertNotIn("ERROR radio failed", audit)

    def test_validly_signed_download_cannot_change_endpoint(self):
        with isolated_runtime() as root:
            write_context()
            destination = root / "malicious.log"
            prepared = client.create_pending(
                profile_name="writer",
                profile=sample_profile(),
                policy=sample_policy(),
                operation="attachment.download",
                project_identifier="firmware",
                target={
                    "issue_id": 42,
                    "attachment_id": 77,
                    "subject": "Example",
                },
                action={
                    "kind": "attachment_download",
                    "issue_id": 42,
                    "attachment": {
                        "id": 77,
                        "filename": "radio.log",
                        "size": 19,
                        "content_type": "text/plain",
                        "created_on": "2026-08-20T01:00:00Z",
                        "endpoint": "/projects/9.json",
                        "destination": str(destination),
                    },
                },
                preview={"benign": True},
                before_updated_on="2026-08-20T01:00:00Z",
            )
            fake = FakeRedmine()
            with mock.patch.object(client, "RedmineHTTP", return_value=fake):
                with self.assertRaisesRegex(client.RedmineAccessError, "下载路径"):
                    client.apply_pending(prepared["approval_id"], prepared["approval_id"])
            self.assertFalse(destination.exists())
            self.assertEqual(fake.download_count, 0)

    def test_binary_download_streams_and_never_overwrites(self):
        content = b"abc123"
        api = client.RedmineHTTP(sample_profile("http://127.0.0.1:8080/redmine"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            destination = root / "77-radio.log"
            response = BinaryResponse(content, len(content))
            with mock.patch.object(api.opener, "open", return_value=response) as opened:
                result = api.download(
                    "/attachments/download/77/radio.log",
                    destination,
                    expected_size=len(content),
                    maximum_size=100,
                )
            self.assertEqual(destination.read_bytes(), content)
            self.assertEqual(result["sha256"], client.hashlib.sha256(content).hexdigest())
            request = opened.call_args.args[0]
            self.assertEqual(
                request.full_url,
                "http://127.0.0.1:8080/redmine/attachments/download/77/radio.log",
            )
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(headers["x-redmine-api-key"], "top-secret-key")
            with mock.patch.object(api.opener, "open") as not_opened:
                with self.assertRaisesRegex(client.RedmineAccessError, "拒绝覆盖"):
                    api.download(
                        "/attachments/download/77/radio.log",
                        destination,
                        expected_size=len(content),
                        maximum_size=100,
                    )
                not_opened.assert_not_called()

    def test_binary_download_size_mismatch_leaves_no_file(self):
        api = client.RedmineHTTP(sample_profile("http://127.0.0.1:8080/redmine"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            destination = root / "77-radio.log"
            response = BinaryResponse(b"short", 5)
            with mock.patch.object(api.opener, "open", return_value=response):
                with self.assertRaisesRegex(client.RedmineAccessError, "大小"):
                    api.download(
                        "/attachments/download/77/radio.log",
                        destination,
                        expected_size=19,
                        maximum_size=100,
                    )
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob("*.part")), [])

    def test_binary_download_stream_limit_and_redirect_leave_no_file(self):
        api = client.RedmineHTTP(sample_profile("http://127.0.0.1:8080/redmine"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            destination = root / "77-radio.log"
            response = BinaryResponse(b"too-long", None)
            with mock.patch.object(api.opener, "open", return_value=response):
                with self.assertRaisesRegex(client.RedmineAccessError, "超过"):
                    api.download(
                        "/attachments/download/77/radio.log",
                        destination,
                        expected_size=3,
                        maximum_size=100,
                    )
            self.assertFalse(destination.exists())
            redirect = HTTPError(
                "http://127.0.0.1:8080/redmine/attachments/download/77/radio.log",
                302,
                "Found",
                {},
                io.BytesIO(),
            )
            with mock.patch.object(api.opener, "open", side_effect=redirect):
                with self.assertRaisesRegex(client.RedmineAccessError, "HTTP 302"):
                    api.download(
                        "/attachments/download/77/radio.log",
                        destination,
                        expected_size=3,
                        maximum_size=100,
                    )
            self.assertFalse(destination.exists())

    def test_validly_signed_pending_cannot_change_operation_semantics(self):
        with isolated_runtime():
            write_context()
            fake = FakeRedmine()
            prepared = client.create_pending(
                profile_name="writer",
                profile=sample_profile(),
                policy=sample_policy(),
                operation="issue.update",
                project_identifier="firmware",
                target={"issue_id": 42, "subject": "Example"},
                action={
                    "kind": "json",
                    "method": "PUT",
                    "endpoint": "/projects/9.json",
                    "body": {"issue": {"status_id": 2}},
                },
                preview={"benign": True},
                before_updated_on="2026-08-20T01:00:00Z",
            )
            approval_id = prepared["approval_id"]
            with mock.patch.object(client, "RedmineHTTP", return_value=fake):
                with self.assertRaisesRegex(client.RedmineAccessError, "内置语义"):
                    client.apply_pending(approval_id, approval_id)
            self.assertEqual(fake.write_count, 0)

    def test_stale_update_does_not_write(self):
        with isolated_runtime():
            write_context()
            fake = FakeRedmine()
            prepared = client.prepare_update_issue(
                fake,
                "writer",
                sample_profile(),
                sample_policy(),
                42,
                {"status_id": 2},
            )
            fake.issue["updated_on"] = "2026-08-20T02:00:00Z"
            with mock.patch.object(client, "RedmineHTTP", return_value=fake):
                with self.assertRaisesRegex(client.RedmineAccessError, "已发生变化"):
                    client.apply_pending(prepared["approval_id"], prepared["approval_id"])
            self.assertEqual(fake.write_count, 0)

    def test_successful_update_is_single_use_and_verified(self):
        with isolated_runtime():
            write_context()
            fake = FakeRedmine()
            prepared = client.prepare_update_issue(
                fake,
                "writer",
                sample_profile(),
                sample_policy(),
                42,
                {"status_id": 2},
            )
            approval_id = prepared["approval_id"]
            with mock.patch.object(client, "RedmineHTTP", return_value=fake):
                result = client.apply_pending(approval_id, approval_id)
            self.assertTrue(result["result"]["verification"]["verified"])
            self.assertEqual(fake.write_count, 1)
            with self.assertRaises(client.RedmineAccessError):
                client.load_pending(approval_id)
            audit = client.AUDIT_FILE.read_text()
            self.assertNotIn("top-secret-key", audit)
            self.assertNotIn("Example", audit)

    def test_server_ignored_update_is_indeterminate_and_not_retried(self):
        with isolated_runtime():
            write_context()
            fake = FakeRedmine(ignore_writes=True)
            prepared = client.prepare_update_issue(
                fake,
                "writer",
                sample_profile(),
                sample_policy(),
                42,
                {"status_id": 2},
            )
            approval_id = prepared["approval_id"]
            with mock.patch.object(client, "RedmineHTTP", return_value=fake):
                with self.assertRaises(client.RequestOutcomeUnknown):
                    client.apply_pending(approval_id, approval_id)
            self.assertEqual(fake.write_count, 1)
            with self.assertRaises(client.RedmineAccessError):
                client.load_pending(approval_id)
            self.assertIn("indeterminate-no-automatic-retry", client.AUDIT_FILE.read_text())

    def test_long_preview_is_compact_but_pending_binds_full_payload(self):
        with isolated_runtime():
            write_context()
            fake = FakeRedmine()
            description = "x" * 2000
            prepared = client.prepare_update_issue(
                fake,
                "writer",
                sample_profile(),
                sample_policy(),
                42,
                {"description": description},
            )
            compact = prepared["preview"]["after"]["description"]
            self.assertEqual(compact["length"], 2000)
            self.assertLess(len(compact["preview"]), 600)
            _, pending = client.load_pending(prepared["approval_id"])
            self.assertEqual(pending["action"]["body"]["issue"]["description"], description)

    def test_private_comment_requires_separate_permission(self):
        fake = FakeRedmine()
        with self.assertRaisesRegex(client.RedmineAccessError, "issue.private_comment"):
            client.prepare_comment(
                fake,
                "writer",
                sample_profile(),
                sample_policy(),
                42,
                "private note",
                True,
            )

    def test_unknown_issue_filter_is_rejected_without_network(self):
        args = argparse.Namespace(
            command="issues",
            filter=["cf_7=secret"],
            sort=None,
            limit=25,
            offset=0,
        )
        api = mock.Mock()
        with self.assertRaisesRegex(client.RedmineAccessError, "无效过滤字段"):
            client.command_read(args, api, sample_policy())
        api.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
