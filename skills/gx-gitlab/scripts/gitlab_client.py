#!/usr/bin/env python3
"""Read-only GitLab information and CI client. Python 3.10+, POSIX, stdlib only."""

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_CONFIG = Path.home() / ".config/skills/gx-gitlab/config.json"
MAX_RESPONSE = 16 * 1024 * 1024
MAX_PAGES = 100


class AccessError(Exception):
    pass


def validate_url(value, allow_http=False):
    if not isinstance(value, str) or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise AccessError("GitLab URL must not contain whitespace or backslashes")
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError:
        raise AccessError("Invalid GitLab URL or port") from None
    if (parts.scheme not in ("https", "http") or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or port == 0):
        raise AccessError("Use a GitLab root HTTP(S) URL without credentials, query or fragment")
    path = parts.path.rstrip("/")
    if (not re.fullmatch(r"(?:/[A-Za-z0-9_.~-]+)*", path)
            or any(part in (".", "..") for part in path.split("/"))):
        raise AccessError("Invalid GitLab deployment subpath")
    if path.endswith("/api/v4"):
        raise AccessError("Use the GitLab root URL, not the /api/v4 URL")
    if parts.scheme == "http" and allow_http is not True:
        raise AccessError("HTTP sends tokens in plaintext; configure --allow-http only after approval")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def validate_project(value):
    if not isinstance(value, str) or not value:
        raise AccessError("Project must be a numeric ID or group/project path")
    if value.isascii() and value.isdigit() and int(value) > 0:
        return value
    parts = value.split("/")
    if (len(parts) < 2 or value.endswith(".git")
            or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) or p in (".", "..") for p in parts)):
        raise AccessError("Use a project ID or unescaped group/project path without .git")
    return value


def read_private(path):
    """Reject symlinks and broad permissions before reading any secret."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise AccessError(f"Cannot open local file: {path}") from None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise AccessError(f"Local file must be owned by you with mode 0600: {path}")
        data = stream.read(16385)
    if len(data) > 16384:
        raise AccessError("Local configuration/credential file is too large")
    try:
        return data.decode("utf-8")
    except UnicodeError:
        raise AccessError("Local configuration/credential file must be UTF-8") from None


def write_private_new(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    check_private_directory(path.parent)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise AccessError(f"File already exists; it was not overwritten: {path}") from None
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value)


def check_private_directory(path):
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise AccessError(f"Use a private, user-owned configuration directory (0700): {path}")


def load_config(path):
    try:
        config = json.loads(read_private(path))
    except json.JSONDecodeError:
        raise AccessError("Configuration is not valid JSON") from None
    required = {"url", "allow_http"}
    if not isinstance(config, dict) or set(config) not in (required, required | {"project"}):
        raise AccessError("Configuration requires url and allow_http; only legacy project is also accepted")
    if not isinstance(config["allow_http"], bool):
        raise AccessError("allow_http must be a JSON boolean")
    config["url"] = validate_url(config["url"], config["allow_http"])
    if "project" in config:
        config.pop("project")
        print("Warning: legacy project field ignored. Back up the profile before removing it; "
              "use --project for each project query. This read did not modify the profile.", file=sys.stderr)
    return config


def validate_token(token):
    token = token.strip()
    if not token or not re.fullmatch(r"[!-~]+", token):
        raise AccessError("Token must be a single nonempty ASCII value without spaces")
    return token


def redact_assignment(match):
    # Keep quoted values quoted, including JSON embedded in a log/description.
    value = match["value"]
    quote = value[0] if value[0] in ("\"", "'") else ""
    return match["key"] + match["separator"] + quote + "[REDACTED]" + quote


def sanitize(text, token=""):
    # Strip terminal OSC and CSI sequences, then nonprinting control characters.
    # Stop at the first OSC terminator; a greedy match can erase intervening logs.
    text = re.sub(r"\x1b\][^\x07]*?(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    if token:
        text = text.replace(token, "[REDACTED]")
    text = re.sub(r"\b(?:glpat|glrt|glcbt|gldt)-[A-Za-z0-9_.-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    text = re.sub(
        r"""(?ix)
            (?P<key>(?P<key_quote>["']?)\b
                [\w-]*(?:token|password|secret|api[_-]?key|private[_-]?key)[\w-]*
                (?P=key_quote))
            (?P<separator>\s*[:=]\s*)
            (?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\[REDACTED\]|[^\s,;&}\]]+)
        """,
        redact_assignment, text,
    )
    text = re.sub(r"(?i)(Authorization\s*:\s*(?:Bearer|Basic)\s+)\S+", r"\1[REDACTED]", text)
    return text


def sanitize_value(value, token):
    """Redact strings before JSON encoding so quotes/escapes remain valid JSON."""
    if isinstance(value, str):
        return sanitize(value, token)
    if isinstance(value, list):
        return [sanitize_value(item, token) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_value(item, token) for key, item in value.items()}
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, config, token, project=None):
        self.base = validate_url(config["url"], config["allow_http"]) + "/api/v4"
        self.project = validate_project(project) if project is not None else None
        self.token = validate_token(token)
        self.opener = urllib.request.build_opener(NoRedirect())

    @property
    def prefix(self):
        if self.project is None:
            raise AccessError("This command requires --project group/project; no default project is used")
        return "/projects/" + segment(self.project)

    def get(self, path, params=None, raw=False):
        # Only explicit endpoint paths generated by the commands below are accepted.
        if not path.startswith("/") or "://" in path or "?" in path or "#" in path:
            raise AccessError("Invalid API endpoint")
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url, headers={"PRIVATE-TOKEN": self.token, "Accept": "text/plain" if raw else "application/json"},
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                payload = response.read(MAX_RESPONSE + 1)
                headers = response.headers
                if len(payload) > MAX_RESPONSE:
                    raise AccessError("Response exceeds 16 MiB; not presented as a complete result")
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            detail = {
                401: "invalid/expired token or wrong authentication type",
                403: "insufficient project permission or instance policy",
                404: "wrong project/resource path, resource not visible, or log missing",
                429: "rate limited; wait before another request",
            }.get(code, "server/proxy error")
            if 300 <= code < 400:
                detail = "redirect refused; confirm the canonical GitLab URL before sending credentials"
            raise AccessError(f"HTTP {code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            # Do not echo exceptions that may contain URLs or proxy credentials.
            raise AccessError("Network/TLS failure: check DNS, VPN, proxy, trust and sandbox access") from None
        text = payload.decode("utf-8", errors="replace")
        if raw:
            content_type = headers.get("Content-Type", "").lower()
            if "text/html" in content_type or text.lstrip().lower().startswith(("<!doctype html", "<html")):
                raise AccessError("Received HTML instead of a job trace; check login/proxy URL")
            return text, headers
        try:
            return json.loads(text), headers
        except json.JSONDecodeError:
            raise AccessError("Received non-JSON API response; check login/proxy URL") from None

    def pages(self, path, params=None):
        records = []
        for page in range(1, MAX_PAGES + 1):
            data, headers = self.get(path, dict(params or {}, page=page, per_page=100))
            if not isinstance(data, list):
                raise AccessError("Expected an API list")
            records.extend(data)
            next_page = headers.get("X-Next-Page")
            if next_page is not None:
                if not next_page:
                    return records
                if not next_page.isdigit() or int(next_page) != page + 1:
                    raise AccessError("Unexpected pagination; result is not complete")
            elif len(data) < 100:
                return records
        raise AccessError("Pagination limit reached; result is not complete")


def select(record, fields):
    if not isinstance(record, dict):
        raise AccessError("Unexpected API object")
    return {field: record.get(field) for field in fields.split()}


PIPELINE_FIELDS = "id iid project_id ref sha source status web_url created_at updated_at duration"
JOB_FIELDS = "id name stage status allow_failure failure_reason web_url duration queued_duration tag_list"
PROJECT_FIELDS = (
    "id name path_with_namespace description visibility default_branch archived "
    "web_url ssh_url_to_repo http_url_to_repo last_activity_at"
)
COMMIT_FIELDS = "id short_id title author_name authored_date committed_date web_url parent_ids"
ISSUE_FIELDS = "id iid project_id title state confidential labels web_url created_at updated_at closed_at"
MR_FIELDS = (
    "id iid project_id title state draft source_branch target_branch sha "
    "detailed_merge_status merge_status has_conflicts web_url created_at updated_at merged_at"
)
PACKAGE_FIELDS = "id name version package_type status created_at"


def segment(value):
    if not isinstance(value, str) or not value or value in (".", "..") or re.search(r"[\x00-\x1f\x7f]", value):
        raise AccessError("Resource name must be nonempty and contain no control characters")
    return urllib.parse.quote(value, safe="")


def resource_summary(kind, record, detail=False):
    if kind == "projects":
        return select(record, PROJECT_FIELDS)
    if kind == "commits":
        result = select(record, COMMIT_FIELDS + (" message status" if detail else ""))
        if detail:
            result["last_pipeline"] = (
                select(record["last_pipeline"], PIPELINE_FIELDS) if record.get("last_pipeline") else None
            )
        return result
    if kind in ("branches", "tags"):
        fields = "name protected web_url default merged" if kind == "branches" else "name protected target created_at"
        result = select(record, fields + (" message" if detail and kind == "tags" else ""))
        result["commit"] = resource_summary("commits", record["commit"]) if record.get("commit") else None
        return result
    if kind in ("issues", "mrs"):
        result = select(record, (ISSUE_FIELDS if kind == "issues" else MR_FIELDS)
                        + (" description" if detail else ""))
        result["author"] = select(record["author"], "id username") if record.get("author") else None
        if kind == "mrs" and detail:
            result["head_pipeline"] = (
                select(record["head_pipeline"], PIPELINE_FIELDS) if record.get("head_pipeline") else None
            )
        return result
    if kind == "releases":
        result = select(record, "name tag_name created_at released_at upcoming_release"
                        + (" description" if detail else ""))
        result["commit"] = resource_summary("commits", record["commit"]) if record.get("commit") else None
        result["web_url"] = (record.get("_links") or {}).get("self")
        if detail:
            result["asset_links"] = [
                select(link, "id name url direct_asset_url link_type")
                for link in (record.get("assets") or {}).get("links", [])
            ]
        return result
    if kind == "packages":
        result = select(record, PACKAGE_FIELDS)
        result["web_url"] = (record.get("_links") or {}).get("web_path")
        return result
    if kind == "package-files":
        return select(record, "id file_name size created_at file_sha256")
    if kind == "pipelines":
        return select(record, PIPELINE_FIELDS)
    raise AccessError("Unknown resource type")


def job_summary(job):
    result = select(job, JOB_FIELDS)
    result["runner"] = select(job["runner"], "id description status") if job.get("runner") else None
    result["artifacts"] = [
        select(item, "filename file_type size") for item in job.get("artifacts", [])
    ]
    if "downstream_pipeline" in job:
        child = job["downstream_pipeline"]
        result["downstream_pipeline"] = select(child, PIPELINE_FIELDS) if child else None
    return result


def positive(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def page_size(value):
    number = positive(value)
    if number > 100:
        raise argparse.ArgumentTypeError("must be between 1 and 100")
    return number


def full_sha(value):
    if not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", value):
        raise argparse.ArgumentTypeError("use the complete commit SHA (git rev-parse HEAD)")
    return value.lower()


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    cli.add_argument("--project", help="explicit project ID/path for this query only; no default")
    commands = cli.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("configure", help="write a new, non-secret local profile")
    setup.add_argument("--url", required=True)
    setup.add_argument("--allow-http", action="store_true")
    login = commands.add_parser("login", help="user-only hidden terminal prompt; no network")
    login.add_argument("--replace", action="store_true", help="replace an existing local token file")
    commands.add_parser("status", help="check API identity and server version; never query projects")
    commands.add_parser("doctor", help="check instance access; add --project to also check project/CI access")
    commands.add_parser("project", help="get project metadata; no secret settings")
    for name, help_text in (
        ("projects", "list member projects; use --search to narrow discovery"),
        ("branches", "list repository branches"), ("tags", "list repository tags"),
        ("commits", "list repository commits"), ("issues", "list project issues"),
        ("mrs", "list project merge requests"), ("releases", "list releases"),
        ("packages", "list registry package metadata"), ("package-files", "list package files and checksums"),
    ):
        listing = commands.add_parser(name, help=help_text)
        listing.add_argument("--limit", type=page_size, default=20)
        listing.add_argument("--page", type=positive, default=1)
        if name in ("projects", "branches", "tags", "issues", "mrs"):
            listing.add_argument("--search")
        if name == "projects":
            listing.add_argument("--all-accessible", action="store_true", help="also include non-member accessible projects")
        if name == "commits":
            listing.add_argument("--ref")
        if name in ("issues", "mrs"):
            states = ("opened", "closed", "all") if name == "issues" else ("opened", "closed", "merged", "all")
            listing.add_argument("--state", choices=states, default="opened")
        if name == "packages":
            listing.add_argument("--name", help="package_name filter")
        if name == "package-files":
            listing.add_argument("--id", type=positive, required=True, help="package ID")
    for name, option, converter in (
        ("branch", "--name", str), ("tag", "--name", str),
        ("commit", "--sha", full_sha), ("issue", "--iid", positive),
        ("mr", "--iid", positive), ("release", "--tag", str), ("job", "--id", positive),
    ):
        detail = commands.add_parser(name, help=f"get one {name}; issue/MR numbers are project IIDs")
        detail.add_argument(option, type=converter, required=True)
    listing = commands.add_parser("pipelines", help="find pipelines; choose exact SHA when validating CI")
    listing.add_argument("--sha", type=full_sha)
    listing.add_argument("--ref")
    listing.add_argument("--limit", type=page_size, default=10)
    listing.add_argument("--page", type=positive, default=1)
    detail = commands.add_parser("pipeline", help="get a pipeline, all jobs and bridges")
    detail.add_argument("--id", type=positive, required=True)
    trace = commands.add_parser("trace", help="print a redacted, numbered log tail")
    trace.add_argument("--job", type=positive, required=True)
    trace.add_argument("--tail", type=positive, default=200)
    return cli


def connection_status(client, check_ci=False):
    try:
        user, _ = client.get("/user")
    except AccessError as exc:
        raise AccessError(f"Authentication check failed: {exc}") from None
    if not isinstance(user, dict) or not isinstance(user.get("id"), int) or not user.get("username"):
        raise AccessError("Authentication check returned no valid user identity")
    result = {
        "authenticated": True, "user": select(user, "id username"),
        "api_url": client.base, "project": None,
        "note": "Access checks are not a full instance/Runner health check or a CI success verdict.",
    }
    if check_ci and client.project is not None:
        try:
            project, _ = client.get(client.prefix)
        except AccessError as exc:
            raise AccessError(f"Authentication passed; project lookup failed: {exc}") from None
        result["project"] = resource_summary("projects", project)
    # Do not fail otherwise useful access checks merely because version is hidden.
    try:
        version, _ = client.get("/version")
        result["server_version"] = {"available": True, **select(version, "version revision enterprise")}
    except AccessError as exc:
        result["server_version"] = {"available": False, "reason": str(exc)}
    if check_ci and client.project is not None:
        try:
            pipelines, _ = client.get(client.prefix + "/pipelines", {"per_page": 1})
        except AccessError as exc:
            raise AccessError(f"Authentication/project checks passed; pipeline listing failed: {exc}") from None
        if not isinstance(pipelines, list):
            raise AccessError("Expected a pipeline list")
        result["pipeline_list_access"] = True
        result["latest_pipeline"] = select(pipelines[0], PIPELINE_FIELDS) if pipelines else None
    return result


def query_list(client, args):
    kind = args.command
    params = {"per_page": args.limit, "page": args.page}
    if kind == "projects":
        endpoint = "/projects"
        params.update(simple="true", order_by="id", sort="desc")
        if not args.all_accessible:
            params["membership"] = "true"
    else:
        paths = {
            "branches": "/repository/branches", "tags": "/repository/tags",
            "commits": "/repository/commits", "issues": "/issues", "mrs": "/merge_requests",
            "releases": "/releases", "packages": "/packages", "pipelines": "/pipelines",
            "package-files": f"/packages/{getattr(args, 'id', 0)}/package_files",
        }
        endpoint = client.prefix + paths[kind]
    for option, parameter in (("search", "search"), ("state", "state"), ("name", "package_name")):
        if getattr(args, option, None):
            params[parameter] = getattr(args, option)
    if kind == "commits" and args.ref:
        params["ref_name"] = args.ref
    if kind == "pipelines":
        params.update(order_by="id", sort="desc")
        for key in ("sha", "ref"):
            if getattr(args, key):
                params[key] = getattr(args, key)
    data, headers = client.get(endpoint, params)
    if not isinstance(data, list):
        raise AccessError("Expected an API list")
    if kind == "pipelines" and args.sha and any(item.get("sha") != args.sha for item in data):
        raise AccessError("Server returned a pipeline for a different SHA")
    if kind == "pipelines" and args.ref and any(item.get("ref") != args.ref for item in data):
        raise AccessError("Server returned a pipeline for a different ref")
    next_page = headers.get("X-Next-Page")
    if next_page:
        if not next_page.isdigit() or int(next_page) != args.page + 1:
            raise AccessError("Unexpected pagination; result is not complete")
        next_page = int(next_page)
    else:
        next_page = None
    return {
        "kind": kind, "items": [resource_summary(kind, item) for item in data],
        "page": args.page, "per_page": args.limit, "next_page": next_page,
        "pagination_header_present": headers.get("X-Next-Page") is not None,
        "note": "One bounded page only; missing pagination headers do not prove no more results.",
    }


def query_detail(client, args):
    kind = args.command
    if kind == "project":
        endpoint, summary = "", "projects"
    elif kind in ("branch", "tag"):
        summary = "branches" if kind == "branch" else "tags"
        endpoint = f"/repository/{summary}/{segment(args.name)}"
    elif kind == "commit":
        endpoint, summary = f"/repository/commits/{args.sha}", "commits"
    elif kind in ("issue", "mr"):
        summary = "issues" if kind == "issue" else "mrs"
        path = "issues" if kind == "issue" else "merge_requests"
        endpoint = f"/{path}/{args.iid}"
    elif kind == "release":
        endpoint, summary = f"/releases/{segment(args.tag)}", "releases"
    else:
        endpoint, summary = f"/jobs/{args.id}", None
    data, _ = client.get(client.prefix + endpoint)
    if not isinstance(data, dict):
        raise AccessError("Expected an API object")
    if kind in ("issue", "mr") and data.get("iid") != args.iid:
        raise AccessError("Server returned a different project IID")
    if kind == "commit" and data.get("id") != args.sha:
        raise AccessError("Server returned a different commit SHA")
    if kind in ("branch", "tag") and data.get("name") != args.name:
        raise AccessError("Server returned a different resource name")
    if kind == "release" and data.get("tag_name") != args.tag:
        raise AccessError("Server returned a different release tag")
    if kind == "job" and data.get("id") != args.id:
        raise AccessError("Server returned a different job ID")
    return {kind: job_summary(data) if kind == "job" else resource_summary(summary, data, detail=True)}


def validate_command_scope(args):
    """Reject ambiguous or unused project arguments before any local/remote access."""
    if args.command in ("configure", "login", "status", "projects"):
        if args.project is not None:
            raise AccessError(f"{args.command} does not accept --project; "
                              "select a project only for project queries or doctor")
    elif args.command != "doctor" and args.project is None:
        raise AccessError(f"{args.command} requires --project group/project (or a numeric ID); "
                          "no default project is used")
    if args.project is not None:
        validate_project(args.project)


def run(args):
    validate_command_scope(args)
    path = args.config.expanduser().absolute()
    token_path = path.with_suffix(".token")
    if token_path == path:
        raise AccessError("Use a configuration filename ending in .json, not .token")
    if args.command == "configure":
        data = {
            "url": validate_url(args.url, args.allow_http),
            "allow_http": args.allow_http,
        }
        if token_path.exists() or token_path.is_symlink():
            raise AccessError("A credential file already exists; use a separate new profile")
        write_private_new(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print(f"Configured {path}; run login in your terminal. No network request was made.")
        return
    config = load_config(path)
    if args.command == "login":
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise AccessError("Run login yourself in an interactive terminal; do not send tokens through chat")
        check_private_directory(token_path.parent)
        if config["url"].startswith("http:"):
            print("Warning: this profile uses HTTP; API requests will transmit the token in plaintext.", file=sys.stderr)
        if token_path.exists() or token_path.is_symlink():
            if not args.replace:
                raise AccessError("Token file exists; use login --replace only to update it")
            read_private(token_path)  # Refuse links and unsafe permissions before replacement.
        token = validate_token(getpass.getpass("Read-only GitLab access token (hidden): "))
        if args.replace and token_path.exists():
            # Atomic replacement after exclusive creation, without following a token symlink.
            import tempfile
            fd, tmp = tempfile.mkstemp(prefix=".token-", dir=token_path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(token + "\n")
                os.replace(tmp, token_path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        else:
            write_private_new(token_path, token + "\n")
        print("Token saved locally with mode 0600. Run doctor to verify access.")
        return
    token = read_private(token_path)
    client = Client(config, token, args.project)
    if client.base.startswith("http:"):
        print("Warning: HTTP profile; token is sent in plaintext to the configured host.", file=sys.stderr)
    if args.command in ("status", "doctor"):
        result = connection_status(client, check_ci=args.command == "doctor")
    elif args.command in ("projects", "branches", "tags", "commits", "issues", "mrs",
                          "releases", "packages", "package-files", "pipelines"):
        result = query_list(client, args)
    elif args.command in ("project", "branch", "tag", "commit", "issue", "mr", "release", "job"):
        result = query_detail(client, args)
    elif args.command == "pipeline":
        endpoint = client.prefix + f"/pipelines/{args.id}"
        data, _ = client.get(endpoint)
        if not isinstance(data, dict) or data.get("id") != args.id:
            raise AccessError("Server returned a different pipeline ID")
        jobs = client.pages(endpoint + "/jobs", {"include_retried": "false"})
        bridges = client.pages(endpoint + "/bridges")
        result = {
            "pipeline": select(data, PIPELINE_FIELDS),
            "jobs": [job_summary(job) for job in jobs],
            "bridges": [job_summary(job) for job in bridges],
            "downstream_traversed": False,
        }
    else:
        trace, _ = client.get(client.prefix + f"/jobs/{args.job}/trace", raw=True)
        lines = sanitize(trace, client.token).splitlines()
        start = max(0, len(lines) - args.tail)
        print(f"Job {args.job}: showing lines {start + 1 if lines else 0}-{len(lines)}; basic redaction only")
        for number, line in enumerate(lines[start:], start + 1):
            print(f"{number}: {line}")
        return
    print(json.dumps(sanitize_value(result, client.token), ensure_ascii=False, indent=2))


def main(argv=None):
    try:
        run(parser().parse_args(argv))
        return 0
    except AccessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        print("error: local I/O or unexpected response/configuration format; no secret details printed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
