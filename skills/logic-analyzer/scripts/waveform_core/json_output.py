from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import ToolError


def atomic_write_json(path: Path, payload: dict[str, Any], max_bytes: int) -> int:
    path = path.resolve()
    if path.exists():
        raise ToolError("OUTPUT_EXISTS", f"Output already exists: {path}", "atomic_publish", exit_code=2)
    if not path.parent.is_dir():
        raise ToolError("OUTPUT_PARENT_MISSING", f"Output parent does not exist: {path.parent}", "atomic_publish", exit_code=2)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            size = stream.tell()
        if size > max_bytes:
            raise ToolError("RESULT_TOO_LARGE", "Result JSON exceeds the configured limit", "atomic_publish", {"observed": size, "limit": max_bytes}, retryable=True, exit_code=6)
        os.replace(temp_name, path)
        return size
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


class JsonlEventWriter:
    def __init__(self, path: Path, max_bytes: int) -> None:
        self.path = path.resolve()
        if self.path.exists():
            raise ToolError("OUTPUT_EXISTS", f"Event output already exists: {self.path}", "request", exit_code=2)
        if not self.path.parent.is_dir():
            raise ToolError("OUTPUT_PARENT_MISSING", f"Event output parent does not exist: {self.path.parent}", "request", exit_code=2)
        self.max_bytes = max_bytes
        self.count = 0
        self.digest = hashlib.sha256()
        fd, self.temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".part", dir=self.path.parent)
        self.stream = os.fdopen(fd, "w", encoding="utf-8")

    def write(self, event: dict[str, object]) -> None:
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        self.stream.write(line)
        self.digest.update(line.encode("utf-8"))
        self.count += 1
        if self.stream.tell() > self.max_bytes:
            raise ToolError("RESOURCE_LIMIT", "Event detail output exceeds the configured limit", "stream_analyze", {"limit": self.max_bytes}, retryable=True, hint="Narrow the analysis window or omit --events-output.", exit_code=6)

    def commit(self) -> int:
        self.stream.flush()
        os.fsync(self.stream.fileno())
        size = self.stream.tell()
        self.stream.close()
        os.replace(self.temp_name, self.path)
        return size

    @property
    def sha256(self) -> str:
        return self.digest.hexdigest()

    def abort(self) -> None:
        try:
            self.stream.close()
        finally:
            try:
                os.unlink(self.temp_name)
            except FileNotFoundError:
                pass
