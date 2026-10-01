from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from .errors import ToolError
from .limits import Limits


DEFAULT_CANDIDATES = (
    Path("/home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli"),
    Path.home() / "workspace/projects/ideas/gx-dsview/build/gx-dsview-cli",
)


def resolve_cli(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    on_path = shutil.which("gx-dsview-cli")
    if on_path:
        candidates.append(Path(on_path))
    candidates.extend(DEFAULT_CANDIDATES)
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    raise ToolError(
        "GX_CLI_NOT_FOUND",
        "Cannot locate an executable gx-dsview-cli",
        "gx_cli",
        {"checked": [str(candidate) for candidate in candidates]},
        hint="Pass --gx-cli with the absolute executable path.",
        exit_code=5,
    )


def run_json(
    executable: Path,
    arguments: Sequence[str],
    *,
    timeout: float,
    limits: Limits,
    stage: str,
) -> dict[str, Any]:
    process = subprocess.Popen(
        [str(executable), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout

    def stop_process() -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stop_process()
                raise ToolError(
                    "GX_PROCESS_TIMEOUT",
                    "gx-dsview-cli exceeded its process deadline",
                    stage,
                    {"timeout_seconds": timeout, "arguments": list(arguments)},
                    retryable=True,
                    exit_code=7,
                )
            for key, _ in selector.select(min(remaining, 0.25)):
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = buffers[key.data]
                target.extend(chunk)
                if len(target) > limits.max_process_output_bytes:
                    stop_process()
                    raise ToolError(
                        "GX_OUTPUT_LIMIT",
                        "gx-dsview-cli produced excessive output",
                        stage,
                        {"stream": key.data, "observed": len(target), "limit": limits.max_process_output_bytes},
                        exit_code=5,
                    )
        return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()

    stdout_text = buffers["stdout"].decode("utf-8", errors="replace")
    stderr_text = buffers["stderr"].decode("utf-8", errors="replace")
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise ToolError(
            "GX_INVALID_JSON",
            "gx-dsview-cli did not return valid JSON",
            stage,
            {"exit_code": return_code, "stderr_tail": stderr_text[-2000:]},
            exit_code=5,
        ) from exc
    if not isinstance(payload, dict):
        raise ToolError("GX_INVALID_JSON", "gx-dsview-cli JSON root must be an object", stage, exit_code=5)
    if return_code != 0 or payload.get("status") not in ("success", "partial"):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        raise ToolError(
            str(error.get("code", "GX_COMMAND_FAILED")),
            str(error.get("message", "gx-dsview-cli command failed")),
            stage,
            {"exit_code": return_code, "stderr_tail": stderr_text[-2000:]},
            retryable=return_code in (3, 4, 7, 8, 9),
            exit_code=5,
        )
    return payload


def inspect_and_validate(path: Path, executable: Path, limits: Limits) -> tuple[dict[str, Any], dict[str, Any]]:
    inspect = run_json(
        executable,
        ["inspect", str(path), "--json"],
        timeout=min(limits.max_analysis_seconds, 60.0),
        limits=limits,
        stage="gx_inspect",
    )
    validate = run_json(
        executable,
        ["validate", str(path), "--json"],
        timeout=limits.max_analysis_seconds,
        limits=limits,
        stage="gx_validate",
    )
    if validate.get("valid") is not True:
        raise ToolError("GX_VALIDATE_FAILED", "gx-dsview-cli did not validate the DSL file", "gx_validate", {"payload": validate}, exit_code=5)
    return inspect, validate
