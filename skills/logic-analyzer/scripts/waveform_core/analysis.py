from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .csv_reader import CsvMetadata, analyze_csv, preflight_csv
from .dsl_reader import DslMetadata, iter_transitions, preflight_dsl
from .errors import ToolError, invalid_request
from .gx_dsview import inspect_and_validate, resolve_cli
from .json_output import JsonlEventWriter
from .limits import Limits
from .measurements import ChannelMeasurement, EventSink


def _resolve_window(sample_count: int, start: int | None, end: int | None) -> tuple[int, int]:
    actual_start = 0 if start is None else start
    actual_end = sample_count if end is None else end
    if actual_start < 0 or actual_end <= actual_start or actual_end > sample_count:
        raise invalid_request(
            "Analysis window is outside the capture",
            start_sample=actual_start,
            end_sample=actual_end,
            sample_count=sample_count,
        )
    return actual_start, actual_end


def _selected_dsl_channels(metadata: DslMetadata, selectors: list[str]) -> list:
    if not selectors:
        return list(metadata.channels)
    by_id = {str(channel.physical_id): channel for channel in metadata.channels}
    by_name: dict[str, list] = {}
    for channel in metadata.channels:
        by_name.setdefault(channel.name, []).append(channel)
    result = []
    for selector in selectors:
        normalized = selector[1:] if selector.lower().startswith("d") and selector[1:].isdigit() else selector
        channel = by_id.get(normalized)
        if channel is None:
            matches = by_name.get(selector, [])
            if len(matches) > 1:
                raise invalid_request("Channel name is ambiguous; select by physical id", channel=selector)
            channel = matches[0] if matches else None
        if channel is None:
            raise ToolError("CHANNEL_NOT_FOUND", f"Channel not found: {selector}", "work_plan", {"available": [item.as_dict() for item in metadata.channels]}, exit_code=2)
        if channel not in result:
            result.append(channel)
    return result


def _selected_csv_indices(metadata: CsvMetadata, selectors: list[str]) -> list[int]:
    if not selectors:
        return list(range(len(metadata.channel_names)))
    result: list[int] = []
    for selector in selectors:
        if selector.isdigit():
            index = int(selector)
        else:
            try:
                index = metadata.channel_names.index(selector)
            except ValueError as exc:
                raise ToolError("CHANNEL_NOT_FOUND", f"Channel not found: {selector}", "work_plan", {"available": list(metadata.channel_names)}, exit_code=2) from exc
        if index < 0 or index >= len(metadata.channel_names):
            raise ToolError("CHANNEL_NOT_FOUND", f"Channel index is outside the CSV: {index}", "work_plan", exit_code=2)
        if index not in result:
            result.append(index)
    return result


def check_gx_consistency(metadata: DslMetadata, inspect: dict) -> None:
    expected_channels = [channel.physical_id for channel in metadata.channels]
    observed_channels = inspect.get("channels")
    checks = {
        "samplerate_hz": (metadata.samplerate_hz, inspect.get("samplerate_hz")),
        "sample_count": (metadata.sample_count, inspect.get("sample_count")),
        "channels": (expected_channels, observed_channels),
        "block_count": (metadata.block_count, inspect.get("block_count")),
        "trigger_position": (metadata.trigger_position, inspect.get("trigger_position")),
    }
    mismatches = {key: {"local": values[0], "gx": values[1]} for key, values in checks.items() if values[0] != values[1]}
    if mismatches:
        raise ToolError("METADATA_MISMATCH", "Local DSL preflight disagrees with gx-dsview-cli", "metadata_check", mismatches, exit_code=5)


def analyze(
    input_path: Path,
    selectors: list[str],
    start_sample: int | None,
    end_sample: int | None,
    limits: Limits,
    gx_cli: str | None,
    events_output: Path | None,
) -> tuple[dict[str, object], JsonlEventWriter | None]:
    input_path = input_path.expanduser().resolve()
    suffix = input_path.suffix.lower()
    event_writer = JsonlEventWriter(events_output, limits.max_detail_bytes) if events_output else None

    def sink_factory(channel_id: int, channel_name: str) -> EventSink | None:
        if event_writer is None:
            return None
        def write_event(event: dict[str, object]) -> None:
            event_writer.write(event)
        return write_event

    started = time.monotonic()
    try:
        if suffix == ".dsl":
            metadata = preflight_dsl(input_path, limits)
            executable = resolve_cli(gx_cli)
            gx_inspect, gx_validate = inspect_and_validate(input_path, executable, limits)
            check_gx_consistency(metadata, gx_inspect)
            selected = _selected_dsl_channels(metadata, selectors)
            window = _resolve_window(metadata.sample_count, start_sample, end_sample)
            packed_bytes = ((window[1] - window[0] + 7) // 8) * len(selected)
            deadline = time.monotonic() + limits.max_analysis_seconds
            results = []
            for channel in selected:
                initial, transitions = iter_transitions(metadata, channel, window[0], window[1], deadline)
                measurement = ChannelMeasurement(
                    channel.physical_id,
                    channel.name,
                    metadata.samplerate_hz,
                    window[0],
                    window[1],
                    initial,
                    limits.event_examples,
                    sink_factory(channel.physical_id, channel.name),
                )
                measurement.consume(transitions)
                results.append(measurement.result())
            source = metadata.as_dict()
            validation: dict[str, object] = {
                "local_preflight": "success",
                "gx_inspect": gx_inspect,
                "gx_validate": gx_validate,
                "gx_cli": str(executable),
            }
        elif suffix == ".csv":
            csv_metadata = preflight_csv(input_path, limits)
            selected_indices = _selected_csv_indices(csv_metadata, selectors)
            window = _resolve_window(csv_metadata.sample_count, start_sample, end_sample)
            packed_bytes = csv_metadata.file_size
            results = analyze_csv(csv_metadata, selected_indices, window[0], window[1], limits, sink_factory)
            source = csv_metadata.as_dict()
            validation = {"local_preflight": "success", "gx_inspect": None, "gx_validate": None}
        else:
            raise ToolError("UNSUPPORTED_FORMAT", "Only .dsl and .csv waveform files are supported", "file_preflight", {"suffix": suffix}, exit_code=4)

        warnings = [
            "Boundary intervals are excluded from complete pulse-width statistics.",
            "Percentiles are not computed by the bounded-memory statistics engine.",
        ]
        if suffix == ".csv":
            warnings.append(
                "CSV sample_count comes from a human-formatted DSView comment; provide --end-sample when the exact capture boundary matters."
            )
        payload: dict[str, object] = {
            "schema_version": 1,
            "status": "success",
            "source": source,
            "validation": validation,
            "request": {
                "channels": selectors or "all",
                "window": {"start_sample": window[0], "end_sample": window[1]},
                "events_output": str(events_output.resolve()) if events_output else None,
            },
            "work_plan": {
                "channels_scanned": len(results),
                "estimated_input_bytes_scanned": packed_bytes,
                "limits": limits.as_dict(),
            },
            "measurements": results,
            "warnings": warnings,
            "runtime": {"elapsed_seconds": time.monotonic() - started},
        }
        return payload, event_writer
    except Exception:
        if event_writer:
            event_writer.abort()
        raise


def compact_summary(payload: dict[str, object], result_path: Path, result_bytes: int, event_info: dict[str, object] | None) -> dict[str, object]:
    measurements = payload.get("measurements", [])
    summaries = []
    if isinstance(measurements, list):
        for item in measurements:
            if not isinstance(item, dict):
                continue
            edges = item.get("edges", {})
            summaries.append({
                "channel": item.get("physical_id"),
                "name": item.get("name"),
                "edges": edges.get("total") if isinstance(edges, dict) else None,
                "high_ratio": item.get("levels", {}).get("high_ratio") if isinstance(item.get("levels"), dict) else None,
            })
    result = {
        "status": "success",
        "summary": summaries,
        "result_file": str(result_path.resolve()),
        "result_bytes": result_bytes,
        "warnings": len(payload.get("warnings", [])) if isinstance(payload.get("warnings"), list) else 0,
    }
    if event_info:
        result["events"] = event_info
    return result
