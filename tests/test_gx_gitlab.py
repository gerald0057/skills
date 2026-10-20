import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

MODULE = Path(__file__).resolve().parents[1] / "skills/gx-gitlab/scripts/gitlab_client.py"
SPEC = importlib.util.spec_from_file_location("gx_gitlab_client", MODULE)
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


TOKEN = "glpat-dummy-local-test-only"
SHA = "b" * 40


class TestLocalConfiguration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "config.json"
        self.addCleanup(self.tmp.cleanup)

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ci.main(["--config", str(self.config), *args])
        return code, out.getvalue(), err.getvalue()

    def configure(self):
        return self.invoke("configure", "--url", "http://localhost", "--allow-http")

    def reject_before_access(self, *args):
        with patch.object(ci, "read_private") as read, \
                patch.object(ci, "write_private_new") as write, \
                patch.object(ci, "Client") as client:
            code, _, err = self.invoke(*args)
        self.assertEqual(code, 2)
        self.assertIn("--project", err)
        read.assert_not_called()
        write.assert_not_called()
        client.assert_not_called()

    def test_configure_private_and_never_overwrite(self):
        self.assertEqual(self.configure()[0], 0)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.config.read_text()), {"url": "http://localhost", "allow_http": True})
        before = self.config.read_bytes()
        self.assertEqual(self.configure()[0], 2)
        self.assertEqual(self.config.read_bytes(), before)

    def test_http_needs_opt_in_and_rejects_unsafe_urls(self):
        for url in ("http://host", "ftp://host", "https://a:b@host",
                    "https://host?token=x", "https://host#x", "https://host\\x",
                    "https://host:65536", "https://host/a/../b", "https://host/api/v4"):
            with self.subTest(url=url), self.assertRaises(ci.AccessError):
                ci.validate_url(url)
        self.assertEqual(ci.validate_url("https://host/gitlab/"), "https://host/gitlab")

    def test_rejects_bad_project_paths(self):
        for project in ("", "0", "group//project", "../project", "group/repo.git", "https://host/p"):
            with self.subTest(project=project), self.assertRaises(ci.AccessError):
                ci.validate_project(project)
        self.assertEqual(ci.validate_project("123"), "123")
        self.assertEqual(ci.validate_project("group/sub/repo"), "group/sub/repo")

    def test_secret_file_permissions_and_symlink(self):
        token = self.root / "config.token"
        ci.write_private_new(token, TOKEN)
        token.chmod(0o644)
        with self.assertRaises(ci.AccessError):
            ci.read_private(token)
        token.chmod(0o600)
        link = self.root / "link"
        link.symlink_to(token)
        with self.assertRaises(ci.AccessError):
            ci.read_private(link)

    def test_login_requires_terminal(self):
        self.configure()
        with patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(self.invoke("login")[0], 2)
        self.assertFalse(self.config.with_suffix(".token").exists())

    def test_login_hidden_and_replace_explicit(self):
        self.configure()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                patch("sys.stdin.isatty", return_value=True), \
                patch.object(err, "isatty", return_value=True), \
                patch("getpass.getpass", return_value=TOKEN):
            self.assertEqual(ci.main(["--config", str(self.config), "login"]), 0)
            self.assertEqual(ci.main(["--config", str(self.config), "login"]), 2)
            self.assertEqual(ci.main(["--config", str(self.config), "login", "--replace"]), 0)
        self.assertEqual(ci.read_private(self.config.with_suffix(".token")).strip(), TOKEN)
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue())

    def test_redaction_and_terminal_escapes(self):
        value = (f"\x1b[31m{TOKEN}\x1b[0m\nAPI_TOKEN='hidden value'\n"
                 "password=abc\nAuthorization: Bearer abcdef\nhttp://user:pass@host\n")
        clean = ci.sanitize(value, TOKEN)
        for secret in (TOKEN, "hidden value", "abcdef", "user:pass", "\x1b"):
            self.assertNotIn(secret, clean)
        self.assertIn("[REDACTED]", clean)

    def test_quoted_sensitive_keys_preserve_valid_json(self):
        for key in ("password", "api_key", "PRIVATE-TOKEN", "client_secret", "private-key"):
            with self.subTest(key=key):
                original = {key: 'review-only-"escaped"-\\value', "message": "keep me"}
                clean = ci.sanitize(json.dumps(original))
                self.assertEqual(json.loads(clean), {key: "[REDACTED]", "message": "keep me"})

    def test_single_quoted_sensitive_keys_and_escaped_values(self):
        original = "{'password': 'review\\'s dummy', 'api_key': 'dummy-key', 'message': 'keep me'}"
        clean = ci.sanitize(original)
        self.assertEqual(ast.literal_eval(clean), {
            "password": "[REDACTED]", "api_key": "[REDACTED]", "message": "keep me",
        })

    def test_redaction_keeps_adjacent_fields_and_is_idempotent(self):
        cases = (
            ("password=review-only; status=failed", "password=[REDACTED]; status=failed"),
            ('{"password":"review-only","status":"failed"}',
             '{"password":"[REDACTED]","status":"failed"}'),
            (f"secret={TOKEN}", "secret=[REDACTED]"),
            ('{"password":"","status":"failed"}', '{"password":"[REDACTED]","status":"failed"}'),
        )
        for original, expected in cases:
            with self.subTest(original=original):
                clean = ci.sanitize(original, TOKEN)
                self.assertEqual(clean, expected)
                self.assertEqual(ci.sanitize(clean, TOKEN), expected)

    def test_osc_sequences_preserve_visible_log_text(self):
        for opening_end in ("\x07", "\x1b\\"):
            for closing_end in ("\x07", "\x1b\\"):
                with self.subTest(opening_end=opening_end, closing_end=closing_end):
                    opening = "\x1b]8;;https://example.invalid/build" + opening_end
                    closing = "\x1b]8;;" + closing_end
                    original = (f"before\n{opening}job details{closing}\n"
                                f"ERROR: compilation failed\n{opening}help{closing}\nafter\n")
                    self.assertEqual(ci.sanitize(original),
                                     "before\njob details\nERROR: compilation failed\nhelp\nafter\n")

    def test_current_token_redacted_after_control_cleanup(self):
        token = "review-only-legacy-credential"
        original = token[:10] + "\x1b[31m" + token[10:] + "\x1b[0m"
        self.assertEqual(ci.sanitize(original, token), "[REDACTED]")

    def test_instance_only_config_and_legacy_explicit_config(self):
        self.assertEqual(self.invoke("configure", "--url", "https://example.invalid")[0], 0)
        expected = {"url": "https://example.invalid", "allow_http": False}
        self.assertEqual(ci.load_config(self.config), expected)
        legacy = self.root / "legacy.json"
        ci.write_private_new(legacy, json.dumps({
            **expected, "project": "legacy/old-project",
        }))
        before = legacy.read_bytes()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ci.load_config(legacy), expected)
        self.assertEqual(legacy.read_bytes(), before)
        self.assertIn("legacy project field ignored", err.getvalue())
        self.assertNotIn("legacy/old-project", err.getvalue())
        self.assertEqual(ci.DEFAULT_CONFIG, Path.home() / ".config/skills/gx-gitlab/config.json")

    def test_missing_project_rejected_before_local_or_remote_access(self):
        cases = (
            ("project",), ("branches",), ("tags",), ("commits",), ("issues",), ("mrs",),
            ("releases",), ("packages",), ("package-files", "--id", "1"),
            ("branch", "--name", "main"), ("tag", "--name", "v1"),
            ("commit", "--sha", SHA), ("issue", "--iid", "1"), ("mr", "--iid", "1"),
            ("release", "--tag", "v1"), ("job", "--id", "1"),
            ("pipelines",), ("pipeline", "--id", "1"), ("trace", "--job", "1"),
        )
        ci.write_private_new(self.config, json.dumps({
            "url": "https://example.invalid", "allow_http": False, "project": "legacy/old-project",
        }))
        for args in cases:
            with self.subTest(args=args):
                self.reject_before_access(*args)

    def test_instance_commands_reject_unused_project_before_access(self):
        for args in (("configure", "--url", "https://example.invalid"),
                     ("login",), ("status",), ("projects", "--search", "demo")):
            with self.subTest(args=args):
                self.reject_before_access("--project", "group/project", *args)

    def test_configure_no_longer_accepts_project_option(self):
        with contextlib.redirect_stderr(io.StringIO()), patch.object(ci, "write_private_new") as write:
            with self.assertRaises(SystemExit) as result:
                ci.main(["--config", str(self.config), "configure", "--url", "https://example.invalid",
                         "--project", "group/project"])
        self.assertEqual(result.exception.code, 2)
        write.assert_not_called()
        self.assertFalse(self.config.exists())

    def test_invalid_explicit_project_fails_before_access(self):
        for project in ("", "../project", "https://other.invalid/group/project", "0"):
            for command in ("doctor", "project"):
                with self.subTest(project=project, command=command), \
                        patch.object(ci, "read_private") as read, patch.object(ci, "Client") as client:
                    self.assertEqual(self.invoke("--project", project, command)[0], 2)
                    read.assert_not_called()
                    client.assert_not_called()

    def test_config_schema_rejects_unrelated_fields(self):
        for value in (
            {"url": "https://example.invalid"},
            {"url": "https://example.invalid", "allow_http": "false"},
            {"url": "https://example.invalid", "allow_http": False, "token": "dummy"},
            {"url": "https://example.invalid", "allow_http": False, "default_project": "group/project"},
        ):
            with self.subTest(value=value):
                self.config.write_text(json.dumps(value))
                self.config.chmod(0o600)
                with self.assertRaises(ci.AccessError):
                    ci.load_config(self.config)

    def test_local_setup_and_help_never_construct_network_client(self):
        with patch.object(ci, "Client") as client:
            for args in (("--help",), ("configure", "--help"), ("login", "--help"), ("trace", "--help")):
                with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()), \
                        patch.object(ci, "read_private") as read:
                    with self.assertRaises(SystemExit) as result:
                        ci.main(["--config", str(self.config), *args])
                    self.assertEqual(result.exception.code, 0)
                    read.assert_not_called()
            self.assertEqual(self.configure()[0], 0)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                    patch("sys.stdin.isatty", return_value=True), \
                    patch.object(err, "isatty", return_value=True), \
                    patch("getpass.getpass", return_value=TOKEN):
                self.assertEqual(ci.main(["--config", str(self.config), "login"]), 0)
            client.assert_not_called()

    def test_client_never_inherits_project_from_raw_legacy_config(self):
        config = {"url": "https://example.invalid", "allow_http": False, "project": "legacy/old-project"}
        client = ci.Client(config, TOKEN)
        self.assertIsNone(client.project)
        with self.assertRaisesRegex(ci.AccessError, "requires --project"):
            _ = client.prefix
        self.assertEqual(ci.Client(config, TOKEN, project="group/selected").project, "group/selected")

    def test_limits_and_write_commands_rejected_before_access(self):
        for args in (("projects", "--limit", "101"), ("projects", "--page", "0"),
                     ("retry", "--id", "1"), ("api", "/users"), ("pipelines", "--sha", "abcd")):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    ci.main(["--config", str(self.root / "missing.json"), *args])
                self.assertEqual(result.exception.code, 2)

    def test_json_redaction_preserves_valid_quoting(self):
        original = {"description": 'password="secret with spaces" and a "quote"\n' + TOKEN}
        clean = ci.sanitize_value(original, TOKEN)
        restored = json.loads(json.dumps(clean, ensure_ascii=False))
        self.assertNotIn("secret with spaces", restored["description"])
        self.assertNotIn(TOKEN, restored["description"])
        self.assertIn('"quote"', restored["description"])


class TestHTTPClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.routes = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requests.append((self.path, self.headers.get("PRIVATE-TOKEN")))
                route = cls.routes.get(self.path)
                if callable(route):
                    route = route(self.path)
                status, data, extra = route or (404, {"error": "not found"}, {})
                self.send_response(status)
                self.send_header("Content-Type", "application/json" if isinstance(data, (dict, list)) else "text/plain")
                for key, value in extra.items():
                    self.send_header(key, value)
                self.end_headers()
                payload = json.dumps(data).encode() if isinstance(data, (dict, list)) else data.encode()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.routes.clear()
        self.requests.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "config.json"
        data = {"url": f"http://127.0.0.1:{self.server.server_port}",
                "allow_http": True}
        ci.write_private_new(self.config, json.dumps(data))
        ci.write_private_new(self.config.with_suffix(".token"), TOKEN)
        self.client = ci.Client(data, TOKEN, project="group/sub/project")
        self.prefix = "/api/v4/projects/group%2Fsub%2Fproject"
        self.proxy = patch.dict(os.environ, {"NO_PROXY": "*", "no_proxy": "*"})
        self.proxy.start()
        self.addCleanup(self.proxy.stop)

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ci.main(["--config", str(self.config), *args])
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def invoke_project(self, *args):
        return self.invoke("--project", "group/sub/project", *args)

    def add_legacy_project(self):
        data = json.loads(self.config.read_text())
        data["project"] = "legacy/old-project"
        self.config.write_text(json.dumps(data))

    def test_status_and_doctor_ignore_legacy_project_without_rewriting_config(self):
        self.add_legacy_project()
        before = self.config.read_bytes()
        self.routes["/api/v4/user"] = (200, {"id": 1, "username": "readonly"}, {})
        self.routes["/api/v4/version"] = (200, {"version": "17.8.3"}, {})
        for command in ("status", "doctor"):
            with self.subTest(command=command):
                self.requests.clear()
                code, out, err = self.invoke(command)
                self.assertEqual(code, 0)
                result = json.loads(out)
                self.assertTrue(result["authenticated"])
                self.assertIsNone(result["project"])
                self.assertNotIn("pipeline_list_access", result)
                self.assertEqual([path for path, _ in self.requests], ["/api/v4/user", "/api/v4/version"])
                self.assertIn("legacy project field ignored", err)
                self.assertNotIn("legacy/old-project", out + err)
                self.assertEqual(self.config.read_bytes(), before)

    def test_missing_project_with_legacy_profile_sends_no_request(self):
        self.add_legacy_project()
        code, _, err = self.invoke("pipelines")
        self.assertEqual(code, 2)
        self.assertIn("requires --project", err)
        self.assertEqual(self.requests, [])

    def test_explicit_doctor_only_visits_selected_project(self):
        self.add_legacy_project()
        self.routes["/api/v4/user"] = (200, {"id": 1, "username": "readonly"}, {})
        self.routes["/api/v4/version"] = (200, {"version": "17.8.3"}, {})
        self.routes["/api/v4/projects/42"] = (200, {"id": 42, "path_with_namespace": "group/selected"}, {})
        self.routes["/api/v4/projects/42/pipelines?per_page=1"] = (200, [], {})
        code, out, _ = self.invoke("--project", "42", "doctor")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["project"]["id"], 42)
        self.assertTrue(json.loads(out)["pipeline_list_access"])
        self.assertEqual([path for path, _ in self.requests], [
            "/api/v4/user", "/api/v4/projects/42", "/api/v4/version",
            "/api/v4/projects/42/pipelines?per_page=1",
        ])

    def test_doctor_filters_identity_and_checks_pipelines(self):
        self.routes["/api/v4/user"] = (200, {"id": 1, "username": "ci-read", "email": "private@example.invalid"}, {})
        self.routes[self.prefix] = (200, {"id": 5, "path_with_namespace": "group/sub/project"}, {})
        self.routes[self.prefix + "/pipelines?per_page=1"] = (200, [], {})
        code, out, _ = self.invoke_project("doctor")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["pipeline_list_access"])
        self.assertNotIn("private@example.invalid", out)
        self.assertTrue(all(token == TOKEN for _, token in self.requests))

    def test_doctor_distinguishes_auth_from_project_access(self):
        self.routes["/api/v4/user"] = (200, {"id": 1, "username": "ci-read"}, {})
        code, _, err = self.invoke_project("doctor")
        self.assertEqual(code, 2)
        self.assertIn("Authentication passed; project lookup failed: HTTP 404", err)
        self.routes["/api/v4/user"] = (401, TOKEN, {})
        self.assertIn("Authentication check failed: HTTP 401", self.invoke_project("doctor")[2])

    def test_sha_and_ref_query_are_encoded(self):
        endpoint = self.prefix + f"/pipelines?per_page=10&page=1&order_by=id&sort=desc&sha={SHA}&ref=feature%2Fx"
        self.routes[endpoint] = (200, [{"id": 7, "sha": SHA, "ref": "feature/x"}], {})
        code, out, _ = self.invoke_project("pipelines", "--sha", SHA, "--ref", "feature/x")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["items"][0]["sha"], SHA)
        self.assertEqual(parse_qs(urlsplit(self.requests[0][0]).query)["ref"], ["feature/x"])
        self.routes[endpoint] = (200, [{"sha": "a" * 40}], {})
        self.assertEqual(self.invoke_project("pipelines", "--sha", SHA, "--ref", "feature/x")[0], 2)

    def test_all_job_pages_and_downstream_identity(self):
        endpoint = self.prefix + "/pipelines/7"
        self.routes[endpoint] = (200, {"id": 7, "sha": SHA, "status": "success"}, {})
        self.routes[endpoint + "/jobs?include_retried=false&page=1&per_page=100"] = (
            200, [{"id": 1, "name": "install: [22.04]"}], {"X-Next-Page": "2"})
        self.routes[endpoint + "/jobs?include_retried=false&page=2&per_page=100"] = (
            200, [{"id": 2, "name": "install: [24.04]", "allow_failure": True}], {"X-Next-Page": ""})
        self.routes[endpoint + "/bridges?page=1&per_page=100"] = (
            200, [{"id": 3, "downstream_pipeline": {"id": 8, "project_id": 6, "status": "failed"}}], {})
        code, out, _ = self.invoke_project("pipeline", "--id", "7")
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(len(result["jobs"]), 2)
        self.assertTrue(result["jobs"][1]["allow_failure"])
        self.assertEqual(result["bridges"][0]["downstream_pipeline"]["status"], "failed")
        self.assertFalse(result["downstream_traversed"])

    def test_redirects_never_followed(self):
        for status in (301, 302, 303, 307, 308):
            self.routes[self.prefix] = (status, TOKEN, {"Location": "/credential-sink"})
            before = len(self.requests)
            with self.subTest(status=status), self.assertRaisesRegex(ci.AccessError, "redirect refused"):
                self.client.get(self.client.prefix)
            self.assertEqual(len(self.requests) - before, 1)

    def test_errors_do_not_echo_server_payload(self):
        for status in (401, 403, 404, 429, 500):
            self.routes[self.prefix] = (status, TOKEN, {})
            with self.subTest(status=status), self.assertRaisesRegex(ci.AccessError, f"HTTP {status}") as caught:
                self.client.get(self.client.prefix)
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_non_json_and_html_are_not_success(self):
        self.routes[self.prefix] = (200, "<html>Sign in</html>", {})
        with self.assertRaisesRegex(ci.AccessError, "non-JSON"):
            self.client.get(self.client.prefix)
        with self.assertRaisesRegex(ci.AccessError, "HTML"):
            self.client.get(self.client.prefix, raw=True)

    def test_trace_tail_is_numbered_and_redacted(self):
        self.routes[self.prefix + "/jobs/8/trace"] = (200, f"line one\nsecret={TOKEN}\n\x1b[31merror\x1b[0m\n", {})
        code, out, _ = self.invoke_project("trace", "--job", "8", "--tail", "2")
        self.assertEqual(code, 0)
        self.assertNotIn("line one", out)
        self.assertIn("2: secret=[REDACTED]", out)
        self.assertIn("3: error", out)

    def test_trace_preserves_osc_error_and_redacts_json_secret(self):
        opening = "\x1b]8;;https://example.invalid/build\x1b\\"
        closing = "\x1b]8;;\x1b\\"
        trace = (f"{opening}job details{closing}\nERROR: compilation failed\n"
                 '{"password":"review-only-trace-secret","status":"failed"}\n'
                 f"{opening}help{closing}\n")
        self.routes[self.prefix + "/jobs/8/trace"] = (200, trace, {})
        code, out, _ = self.invoke_project("trace", "--job", "8", "--tail", "4")
        self.assertEqual(code, 0)
        self.assertNotIn("review-only-trace-secret", out)
        self.assertNotIn("\x1b", out)
        self.assertIn("showing lines 1-4", out)
        self.assertIn("1: job details", out)
        self.assertIn("2: ERROR: compilation failed", out)
        self.assertIn('3: {"password":"[REDACTED]","status":"failed"}', out)
        self.assertIn("4: help", out)

    def test_issue_description_redacts_embedded_json_secret(self):
        self.routes[self.prefix + "/issues/12"] = (200, {
            "iid": 12, "description": '{"api_key":"review-only-issue-secret","message":"keep me"}',
        }, {})
        code, out, _ = self.invoke_project("issue", "--iid", "12")
        self.assertEqual(code, 0)
        self.assertNotIn("review-only-issue-secret", out)
        description = json.loads(out)["issue"]["description"]
        self.assertEqual(json.loads(description), {"api_key": "[REDACTED]", "message": "keep me"})

    def test_response_size_limit_fails_explicitly(self):
        self.routes[self.prefix] = (200, "x" * 65, {})
        with patch.object(ci, "MAX_RESPONSE", 64), self.assertRaisesRegex(ci.AccessError, "exceeds"):
            self.client.get(self.client.prefix, raw=True)

    def test_pagination_cannot_silently_skip_pages(self):
        self.routes[self.prefix + "?page=1&per_page=100"] = (200, [], {"X-Next-Page": "3"})
        with self.assertRaisesRegex(ci.AccessError, "pagination"):
            self.client.pages(self.client.prefix)

    def test_status_without_project_and_version_is_not_ci_verdict(self):
        ci_config = {"url": f"http://127.0.0.1:{self.server.server_port}",
                     "allow_http": True}
        client = ci.Client(ci_config, TOKEN)
        self.routes["/api/v4/user"] = (200, {"id": 1, "username": "readonly"}, {})
        self.routes["/api/v4/version"] = (200, {"version": "17.8.3", "revision": "abc", "enterprise": False}, {})
        result = ci.connection_status(client)
        self.assertTrue(result["authenticated"])
        self.assertIsNone(result["project"])
        self.assertEqual(result["server_version"]["version"], "17.8.3")
        self.assertNotIn("pipeline_list_access", result)
        self.assertEqual(len(self.requests), 2)
        self.requests.clear()
        result = ci.connection_status(self.client)  # status ignores even an explicitly scoped client.
        self.assertIsNone(result["project"])
        self.assertEqual([path for path, _ in self.requests], ["/api/v4/user", "/api/v4/version"])
        self.routes["/api/v4/version"] = (403, TOKEN, {})
        self.assertFalse(ci.connection_status(client)["server_version"]["available"])
        with self.assertRaisesRegex(ci.AccessError, "requires --project"):
            ci.query_detail(client, ci.parser().parse_args(["project"]))

    def test_status_rejects_fake_identity(self):
        self.routes["/api/v4/user"] = (200, {"logged_in": False}, {})
        with self.assertRaisesRegex(ci.AccessError, "valid user identity"):
            ci.connection_status(self.client)

    def test_project_search_is_bounded_and_defaults_to_membership(self):
        endpoint = "/api/v4/projects?per_page=2&page=1&simple=true&order_by=id&sort=desc&membership=true&search=demo"
        self.routes[endpoint] = (
            200, [{"id": 5, "path_with_namespace": "group/demo", "runners_token": "do-not-show"}],
            {"X-Next-Page": "2"},
        )
        code, out, _ = self.invoke("projects", "--search", "demo", "--limit", "2")
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["next_page"], 2)
        self.assertEqual(result["per_page"], 2)
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("do-not-show", out)
        broad = endpoint.replace("&membership=true", "")
        self.routes[broad] = (200, [], {})
        self.assertEqual(self.invoke("projects", "--search", "demo", "--limit", "2", "--all-accessible")[0], 0)

    def test_explicit_project_does_not_modify_profile(self):
        self.add_legacy_project()
        before = self.config.read_bytes()
        self.routes["/api/v4/projects/42"] = (
            200, {"id": 42, "path_with_namespace": "other/project", "ci_variables": {"x": "private"}}, {})
        code, out, _ = self.invoke("--project", "42", "project")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["project"]["id"], 42)
        self.assertNotIn("ci_variables", out)
        self.assertEqual(before, self.config.read_bytes())

    def test_general_list_endpoint_mapping_and_summary(self):
        cases = [
            (["branches", "--search", "feat"], "/repository/branches?per_page=20&page=1&search=feat",
             {"name": "feat/a", "commit": {"id": SHA, "author_email": "private@example.invalid"}}),
            (["tags"], "/repository/tags?per_page=20&page=1", {"name": "v1", "target": SHA}),
            (["commits", "--ref", "feat/x"], "/repository/commits?per_page=20&page=1&ref_name=feat%2Fx",
             {"id": SHA, "message": "not-in-list", "author_email": "private@example.invalid"}),
            (["issues", "--search", "bug"], "/issues?per_page=20&page=1&search=bug&state=opened",
             {"iid": 3, "description": "not-in-list"}),
            (["mrs", "--state", "merged"], "/merge_requests?per_page=20&page=1&state=merged",
             {"iid": 4, "state": "merged", "description": "not-in-list"}),
            (["releases"], "/releases?per_page=20&page=1", {"tag_name": "v1", "description": "not-in-list"}),
            (["packages", "--name", "demo"], "/packages?per_page=20&page=1&package_name=demo",
             {"id": 6, "name": "demo", "package_type": "generic"}),
            (["package-files", "--id", "6"], "/packages/6/package_files?per_page=20&page=1",
             {"id": 9, "file_name": "demo.deb", "file_sha256": "d" * 64}),
        ]
        for command, path, item in cases:
            with self.subTest(command=command):
                self.routes[self.prefix + path] = (200, [item], {})
                code, out, _ = self.invoke_project(*command)
                self.assertEqual(code, 0)
                self.assertEqual(len(json.loads(out)["items"]), 1)
                self.assertNotIn("not-in-list", out)
                self.assertNotIn("private@example.invalid", out)

    def test_general_details_validate_identity_and_do_not_follow_assets(self):
        cases = [
            (["branch", "--name", "feature/x"], "/repository/branches/feature%2Fx",
             {"name": "feature/x", "commit": {"id": SHA}}),
            (["tag", "--name", "release/v1"], "/repository/tags/release%2Fv1", {"name": "release/v1"}),
            (["commit", "--sha", SHA], f"/repository/commits/{SHA}", {"id": SHA, "message": "detail"}),
            (["issue", "--iid", "12"], "/issues/12", {"id": 999, "iid": 12, "description": "detail"}),
            (["mr", "--iid", "13"], "/merge_requests/13",
             {"id": 888, "iid": 13, "head_pipeline": {"id": 20, "status": "failed"}}),
            (["release", "--tag", "release/v1"], "/releases/release%2Fv1",
             {"tag_name": "release/v1", "assets": {"links": [{"url": "https://another.invalid/private"}]}}),
            (["job", "--id", "5"], "/jobs/5", {"id": 5, "status": "failed", "failure_reason": "script_failure"}),
        ]
        for command, path, data in cases:
            with self.subTest(command=command):
                before = len(self.requests)
                self.routes[self.prefix + path] = (200, data, {})
                code, out, _ = self.invoke_project(*command)
                self.assertEqual(code, 0)
                json.loads(out)
                self.assertEqual(len(self.requests) - before, 1)
        self.routes[self.prefix + "/issues/12"] = (200, {"id": 12, "iid": 22}, {})
        self.assertEqual(self.invoke_project("issue", "--iid", "12")[0], 2)

    def test_pipeline_pages_check_ref_and_pagination(self):
        endpoint = self.prefix + f"/pipelines?per_page=10&page=2&order_by=id&sort=desc&sha={SHA}&ref=main"
        self.routes[endpoint] = (200, [{"sha": SHA, "ref": "wrong"}], {})
        self.assertEqual(self.invoke_project("pipelines", "--sha", SHA, "--ref", "main", "--page", "2")[0], 2)
        self.routes[endpoint] = (200, [{"sha": SHA, "ref": "main"}], {"X-Next-Page": "4"})
        self.assertEqual(self.invoke_project("pipelines", "--sha", SHA, "--ref", "main", "--page", "2")[0], 2)

    def test_new_config_does_not_implicitly_read_legacy_token(self):
        missing = Path(self.tmp.name) / "no-profile.json"
        with patch.object(ci, "DEFAULT_CONFIG", missing):
            out = io.StringIO()
            with contextlib.redirect_stderr(out):
                self.assertEqual(ci.main(["status"]), 2)
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
