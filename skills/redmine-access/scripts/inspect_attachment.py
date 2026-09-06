#!/usr/bin/env python3
"""Safely classify downloaded attachments and extract bounded log evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import deque
from pathlib import Path
from typing import Any

import redmine_client as client


MAX_ANALYSIS_BYTES = 50_000_000
MAX_IMAGE_PIXELS = 40_000_000
MAX_LINE_CHARS = 1000
DEFAULT_PATTERNS = (
    "error",
    "fail",
    "timeout",
    "assert",
    "exception",
    "panic",
    "fatal",
)


class InspectionError(RuntimeError):
    """Expected failure while examining an untrusted attachment."""


def secure_file(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_symlink():
        raise InspectionError("拒绝读取符号链接附件")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise InspectionError(f"附件文件不存在：{path}") from exc
    if not resolved.is_file():
        raise InspectionError("附件路径不是普通文件")
    if resolved.stat().st_size > MAX_ANALYSIS_BYTES:
        raise InspectionError(
            f"附件超过本地分析上限 {MAX_ANALYSIS_BYTES} 字节；请先缩小范围"
        )
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    while offset + 9 < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            return None
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            return None
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        } and length >= 7:
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height
        offset += length
    return None


def detect_type(data: bytes) -> dict[str, Any]:
    result: dict[str, Any]
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        result = {"kind": "image", "format": "png", "width": width, "height": height}
    elif data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        result = {"kind": "image", "format": "gif", "width": width, "height": height}
    elif data.startswith(b"\xff\xd8"):
        result = {"kind": "image", "format": "jpeg"}
        dimensions = jpeg_dimensions(data)
        if dimensions:
            result.update({"width": dimensions[0], "height": dimensions[1]})
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        result = {"kind": "image", "format": "webp"}
        if len(data) >= 30 and data[12:16] == b"VP8X":
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
            result.update({"width": width, "height": height})
    elif data.startswith(b"%PDF-"):
        result = {"kind": "document", "format": "pdf"}
    elif data.startswith(b"PK\x03\x04"):
        result = {"kind": "archive", "format": "zip"}
    elif data.startswith(b"\x1f\x8b"):
        result = {"kind": "archive", "format": "gzip"}
    elif data.startswith(b"\x7fELF") or data.startswith(b"MZ"):
        result = {"kind": "executable", "format": "binary"}
    elif b"\x00" not in data:
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            result = {"kind": "binary", "format": "unknown"}
        else:
            result = {"kind": "text", "format": "utf-8"}
    else:
        result = {"kind": "binary", "format": "unknown"}
    width = result.get("width")
    height = result.get("height")
    if isinstance(width, int) and isinstance(height, int):
        pixels = width * height
        result["pixels"] = pixels
        result["safe_to_view"] = 0 < pixels <= MAX_IMAGE_PIXELS
    elif result["kind"] == "image":
        result["safe_to_view"] = False
        result["warning"] = "无法在有界头部中验证图片尺寸"
    return result


def metadata(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        header = stream.read(1024 * 1024)
    detected = detect_type(header)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "detected": detected,
        "untrusted": True,
    }


def compact_line(line: str) -> str:
    line = line.rstrip("\r\n")
    if len(line) > MAX_LINE_CHARS:
        return line[:MAX_LINE_CHARS] + "…"
    return line


def inspect_log(
    path: Path,
    patterns: list[str],
    max_matches: int,
    head_lines: int,
    tail_lines: int,
) -> dict[str, Any]:
    info = metadata(path)
    if info["detected"]["kind"] != "text":
        raise InspectionError("附件未被识别为 UTF-8 文本，拒绝按日志读取")
    lowered = [pattern.casefold() for pattern in patterns if pattern]
    if not lowered:
        raise InspectionError("至少需要一个非空搜索词")
    head: list[dict[str, Any]] = []
    tail: deque[dict[str, Any]] = deque(maxlen=tail_lines)
    matches: list[dict[str, Any]] = []
    scanned_bytes = 0
    scanned_lines = 0
    truncated = False
    with path.open("rb") as stream:
        for line_number, raw in enumerate(stream, 1):
            scanned_bytes += len(raw)
            if scanned_bytes > MAX_ANALYSIS_BYTES:
                truncated = True
                break
            try:
                text = raw.decode("utf-8-sig" if line_number == 1 else "utf-8")
            except UnicodeDecodeError as exc:
                raise InspectionError(
                    f"日志第 {line_number} 行不是有效 UTF-8，停止分析"
                ) from exc
            scanned_lines = line_number
            item = {"line": line_number, "text": compact_line(text)}
            if len(head) < head_lines:
                head.append(item)
            tail.append(item)
            folded = text.casefold()
            if any(pattern in folded for pattern in lowered):
                matches.append(item)
                if len(matches) >= max_matches:
                    truncated = stream.read(1) != b""
                    break
    return {
        **info,
        "patterns": patterns,
        "scanned_bytes": scanned_bytes,
        "scanned_lines": scanned_lines,
        "truncated": truncated,
        "head": head,
        "tail": list(tail),
        "matches": matches,
        "matches_returned": len(matches),
    }


def bounded_count(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 200:
        raise argparse.ArgumentTypeError("数量必须在 1 到 200 之间")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读检查已下载的 Redmine 附件")
    parser.add_argument("--profile", help="用于输出脱敏的 Redmine profile")
    parser.add_argument("--pretty", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    metadata_parser = subparsers.add_parser("metadata", help="检测附件类型和摘要")
    metadata_parser.add_argument("path")
    log_parser = subparsers.add_parser("log", help="有界读取 UTF-8 日志")
    log_parser.add_argument("path")
    log_parser.add_argument("--pattern", action="append", default=[])
    log_parser.add_argument("--max-matches", type=bounded_count, default=100)
    log_parser.add_argument("--head-lines", type=bounded_count, default=20)
    log_parser.add_argument("--tail-lines", type=bounded_count, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    secrets_to_redact: list[str] = []
    try:
        _, profile, _ = client.load_context(args.profile)
        secrets_to_redact = [profile["api_key"]]
        path = secure_file(args.path)
        if args.command == "metadata":
            value = metadata(path)
        else:
            value = inspect_log(
                path,
                args.pattern or list(DEFAULT_PATTERNS),
                args.max_matches,
                args.head_lines,
                args.tail_lines,
            )
        client.print_json(value, args.pretty, secrets_to_redact)
        return 0
    except (InspectionError, client.RedmineAccessError, OSError, ValueError) as exc:
        client.print_json(
            {"code": "INSPECTION_ERROR", "error": str(exc)},
            args.pretty,
            secrets_to_redact,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
