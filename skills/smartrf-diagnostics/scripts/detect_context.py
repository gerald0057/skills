#!/usr/bin/env python3
"""Detect SmartRF diagnostic version, layout, library variant and source match."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


STACK_PATTERNS = (
    re.compile(r"\[srf_core\].*?\bstack=(\d+\.\d+\.\d+)\b"),
    re.compile(r"\[srf_core\].*?\bsrf_init ok\b.*?\bversion=(\d+\.\d+\.\d+)\b"),
)
LINK_PATTERN = re.compile(
    r"\blink=([A-Za-z0-9_+-]+)/([0-9]+(?:\.[0-9]+){1,2})-(debug|release)\b"
)
VERSION_DEFINE = re.compile(
    r"^\s*#\s*define\s+SRF_VERSION_(MAJOR|MINOR|PATCH)\s+([0-9]+)[UuLl]*\b",
    re.MULTILINE,
)
SOURCE_SUFFIXES = (
    Path("subsys/wireless/smartrf_v4"),
    Path("subsys/wireless/smartrf/v4"),
)


def unique_in_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def versions_from_log(text: str) -> list[str]:
    hits: list[tuple[int, str]] = []
    for pattern in STACK_PATTERNS:
        hits.extend((match.start(), match.group(1)) for match in pattern.finditer(text))
    return unique_in_order([version for _, version in sorted(hits)])


def links_from_log(text: str) -> list[dict[str, str]]:
    encoded = unique_in_order(["\0".join(match.groups()) for match in LINK_PATTERN.finditer(text)])
    return [
        {"name": parts[0], "version": parts[1], "variant": parts[2]}
        for value in encoded
        for parts in [value.split("\0")]
    ]


def detect_schema(text: str, version: str | None) -> tuple[str, str, list[str]]:
    markers = [
        marker
        for marker in ("[conn_state]", "[channel_quality]", "[conn_transport]", "[conn_timing]")
        if marker in text
    ]
    if version and version.startswith("4.2."):
        return "v4.2", "exact_version", markers
    if markers:
        return "v4.2", "layout_inference", markers
    return "legacy", "fallback", ["[connected_impl]"] if "[connected_impl]" in text else []


def source_version(root: Path) -> str | None:
    header = root / "inc/smartrf.h"
    if not header.is_file():
        return None
    values = {name: value for name, value in VERSION_DEFINE.findall(header.read_text(errors="replace"))}
    if not all(key in values for key in ("MAJOR", "MINOR", "PATCH")):
        return None
    return ".".join(values[key] for key in ("MAJOR", "MINOR", "PATCH"))


def normalize_source_root(path: Path) -> Path | None:
    path = path.expanduser().resolve()
    if (path / "inc/smartrf.h").is_file():
        return path
    for suffix in SOURCE_SUFFIXES:
        candidate = path / suffix
        if (candidate / "inc/smartrf.h").is_file():
            return candidate
    return None


def discover_source(explicit: str | None) -> Path | None:
    if explicit:
        return normalize_source_root(Path(explicit))
    current = Path.cwd().resolve()
    for parent in (current, *current.parents):
        found = normalize_source_root(parent)
        if found:
            return found
    return None


def analysis_mode(source: Path | None, matches: bool | None, variant: str) -> str:
    if source is None:
        return "log_only_limited" if variant == "release" else "log_only"
    if matches is False:
        return "source_mismatch"
    if variant == "release":
        return "source_matched_release"
    if variant == "debug":
        return "source_matched_debug"
    return "source_matched_variant_unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", nargs="?", help="diagnostic log; read stdin when omitted")
    parser.add_argument("--source-root", help="repository root or SmartRF subsystem root")
    parser.add_argument("--version", help="user-specified SmartRF stack version")
    args = parser.parse_args()

    if args.log:
        text = Path(args.log).read_text(errors="replace")
    else:
        text = sys.stdin.read()

    detected_versions = versions_from_log(text)
    selected_version = args.version or (detected_versions[0] if len(detected_versions) == 1 else None)
    conflicts: list[str] = []
    if len(detected_versions) > 1:
        conflicts.append("multiple_stack_versions_in_log")
    if args.version and detected_versions and args.version not in detected_versions:
        conflicts.append("user_version_differs_from_log")

    links = links_from_log(text)
    variants = unique_in_order([entry["variant"] for entry in links])
    variant = variants[0] if len(variants) == 1 else "unknown"
    if len(variants) > 1:
        conflicts.append("multiple_link_variants_in_log")

    schema_version = selected_version
    if schema_version is None and detected_versions and all(
        version.startswith("4.2.") for version in detected_versions
    ):
        schema_version = detected_versions[0]
    schema, schema_confidence, markers = detect_schema(text, schema_version)
    if selected_version is None and schema_confidence == "exact_version":
        schema_confidence = "version_family"
    source = discover_source(args.source_root)
    if args.source_root and source is None:
        conflicts.append("source_root_not_found")
    local_version = source_version(source) if source else None
    matches = None if source is None or selected_version is None else local_version == selected_version

    result = {
        "log": {
            "detected_stack_versions": detected_versions,
            "selected_stack_version": selected_version,
            "selection_source": "user" if args.version else ("log" if selected_version else "unknown"),
            "link_layers": links,
            "link_variant": variant,
            "schema": schema,
            "schema_confidence": schema_confidence,
            "layout_markers": markers,
        },
        "source": {
            "root": str(source) if source else None,
            "stack_version": local_version,
            "matches_log": matches,
        },
        "analysis_mode": analysis_mode(source, matches, variant),
        "conflicts": conflicts,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
