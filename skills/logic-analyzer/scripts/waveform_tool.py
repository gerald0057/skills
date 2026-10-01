#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import replace
from pathlib import Path

from waveform_core.analysis import analyze, check_gx_consistency, compact_summary
from waveform_core.csv_reader import preflight_csv
from waveform_core.dsl_reader import preflight_dsl
from waveform_core.errors import ToolError, invalid_request
from waveform_core.gx_dsview import inspect_and_validate, resolve_cli, run_json
from waveform_core.json_output import atomic_write_json
from waveform_core.limits import Limits


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def _limits(args: argparse.Namespace) -> Limits:
    limits = Limits()
    updates = {}
    for field, argument in (
        ("max_input_bytes", "max_input_bytes"),
        ("max_compression_ratio", "max_compression_ratio"),
        ("max_analysis_seconds", "max_analysis_seconds"),
        ("max_result_bytes", "max_result_bytes"),
        ("max_detail_bytes", "max_detail_bytes"),
        ("event_examples", "event_examples"),
    ):
        value = getattr(args, argument, None)
        if value is not None:
            updates[field] = value
    return replace(limits, **updates)


def _add_limits(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-input-bytes", type=int)
    parser.add_argument("--max-compression-ratio", type=float)
    parser.add_argument("--max-analysis-seconds", type=float)
    parser.add_argument("--max-result-bytes", type=int)
    parser.add_argument("--max-detail-bytes", type=int)
    parser.add_argument("--event-examples", type=int)


def _validate_limits(limits: Limits) -> None:
    numeric = limits.as_dict()
    invalid = {key: value for key, value in numeric.items() if isinstance(value, (int, float)) and value <= 0}
    if invalid:
        raise invalid_request("Resource limits must be positive", invalid=invalid)


def cmd_preflight(args: argparse.Namespace) -> int:
    path = Path(args.input).expanduser().resolve()
    limits = _limits(args)
    _validate_limits(limits)
    if path.suffix.lower() == ".dsl":
        metadata = preflight_dsl(path, limits)
    elif path.suffix.lower() == ".csv":
        metadata = preflight_csv(path, limits)
    else:
        raise ToolError("UNSUPPORTED_FORMAT", "Only .dsl and .csv waveform files are supported", "file_preflight", exit_code=4)
    _emit({"status": "success", "preflight": metadata.as_dict(), "limits": limits.as_dict()})
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    path = Path(args.input).expanduser().resolve()
    limits = _limits(args)
    _validate_limits(limits)
    if path.suffix.lower() == ".csv":
        metadata = preflight_csv(path, limits)
        _emit({"status": "success", "source": metadata.as_dict(), "validation": {"local_preflight": "success"}})
        return 0
    if path.suffix.lower() != ".dsl":
        raise ToolError("UNSUPPORTED_FORMAT", "Only .dsl and .csv waveform files are supported", "file_preflight", exit_code=4)
    metadata = preflight_dsl(path, limits)
    executable = resolve_cli(args.gx_cli)
    inspect, validate = inspect_and_validate(path, executable, limits)
    check_gx_consistency(metadata, inspect)
    _emit({
        "status": "success",
        "source": metadata.as_dict(),
        "validation": {"local_preflight": "success", "gx_inspect": inspect, "gx_validate": validate},
        "gx_cli": str(executable),
    })
    return 0


def _analyze_and_publish(
    args: argparse.Namespace,
    input_path: Path,
    provenance: dict[str, object] | None = None,
) -> int:
    limits = _limits(args)
    _validate_limits(limits)
    output = Path(args.output).expanduser().resolve()
    events_output = Path(args.events_output).expanduser().resolve() if args.events_output else None
    if output == input_path.resolve() or (events_output and events_output in (input_path.resolve(), output)):
        raise invalid_request("Input, result, and event-detail paths must be distinct")
    if output.exists():
        raise ToolError("OUTPUT_EXISTS", f"Output already exists: {output}", "request", exit_code=2)
    if not output.parent.is_dir():
        raise ToolError("OUTPUT_PARENT_MISSING", f"Output parent does not exist: {output.parent}", "request", exit_code=2)
    payload, event_writer = analyze(
        input_path,
        args.channel or [],
        args.start_sample,
        args.end_sample,
        limits,
        args.gx_cli,
        events_output,
    )
    if provenance:
        payload["capture"] = provenance
    event_info = None
    committed_event = False
    try:
        if event_writer and events_output:
            event_bytes = event_writer.commit()
            committed_event = True
            event_info = {
                "file": str(events_output),
                "count": event_writer.count,
                "bytes": event_bytes,
                "sha256": event_writer.sha256,
                "format": "jsonl",
            }
            payload["event_detail"] = event_info
        result_bytes = atomic_write_json(output, payload, limits.max_result_bytes)
    except Exception:
        if committed_event and events_output:
            try:
                events_output.unlink()
            except FileNotFoundError:
                pass
        raise
    _emit(compact_summary(payload, output, result_bytes, event_info))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    return _analyze_and_publish(args, Path(args.input).expanduser().resolve())


def _acquire(args: argparse.Namespace) -> tuple[Path, dict[str, object], dict[str, object]]:
    limits = _limits(args)
    _validate_limits(limits)
    capture_output = Path(args.capture_output).expanduser().resolve()
    if capture_output.exists():
        raise ToolError("OUTPUT_EXISTS", f"Capture output already exists: {capture_output}", "request", exit_code=2)
    if not capture_output.parent.is_dir():
        raise ToolError("OUTPUT_PARENT_MISSING", f"Capture output parent does not exist: {capture_output.parent}", "request", exit_code=2)
    if capture_output.suffix.lower() != ".dsl":
        raise invalid_request("Capture output must use the .dsl extension")
    if args.device < 0 or args.samplerate <= 0 or args.samples <= 0 or args.timeout <= 0:
        raise invalid_request("Samplerate, samples, and timeout must be positive")
    if args.pretrigger < 0 or args.pretrigger > 90:
        raise invalid_request("Pretrigger must be between 0 and 90 percent")
    if args.trigger != "none" and not re.fullmatch(r"D\d+:(?:rising|falling)", args.trigger):
        raise invalid_request("Trigger must be none, D<channel>:rising, or D<channel>:falling")
    channels = []
    for value in args.channels.split(","):
        try:
            channel = int(value.strip())
        except ValueError as exc:
            raise invalid_request("Capture channels must be comma-separated integers", value=value) from exc
        if channel < 0 or channel in channels:
            raise invalid_request("Capture channels must be unique non-negative integers", channel=channel)
        channels.append(channel)
    executable = resolve_cli(args.gx_cli)
    devices = run_json(executable, ["devices", "--json"], timeout=15, limits=limits, stage="device_discovery")
    capabilities = run_json(
        executable,
        ["capabilities", "--device", str(args.device), "--json"],
        timeout=30,
        limits=limits,
        stage="device_capabilities",
    )
    command = [
        "capture",
        "--device", str(args.device),
        "--samplerate", str(args.samplerate),
        "--channels", ",".join(str(value) for value in channels),
        "--samples", str(args.samples),
        "--trigger", args.trigger,
        "--pretrigger", str(args.pretrigger),
        "--timeout", str(args.timeout),
        "--output", str(capture_output),
        "--no-private-decode",
        "--json",
    ]
    capture = run_json(
        executable,
        command,
        timeout=args.timeout + 60,
        limits=limits,
        stage="capture",
    )
    provenance: dict[str, object] = {
        "devices": devices,
        "capabilities": capabilities,
        "command": [str(executable), *command],
        "result": capture,
    }
    capture_details = capture.get("capture") if isinstance(capture.get("capture"), dict) else {}
    summary: dict[str, object] = {
        "status": "success",
        "capture_file": str(capture_output),
        "samplerate_hz": capture_details.get("samplerate_hz", args.samplerate),
        "requested_samples": capture_details.get("requested_sample_count", args.samples),
        "actual_samples": capture_details.get("sample_count"),
        "channels": capture_details.get("channels", channels),
        "trigger_position": capture_details.get("trigger_position"),
    }
    return capture_output, provenance, summary


def cmd_acquire(args: argparse.Namespace) -> int:
    _, _, summary = _acquire(args)
    _emit(summary)
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    capture_output, provenance, _ = _acquire(args)
    return _analyze_and_publish(args, capture_output, provenance)


def _add_acquisition_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--samplerate", type=int, required=True)
    parser.add_argument("--channels", required=True)
    parser.add_argument("--samples", type=int, required=True)
    parser.add_argument("--trigger", default="none")
    parser.add_argument("--pretrigger", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--gx-cli")
    _add_limits(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="waveform_tool", description="Safely inspect, capture, and measure DSView digital waveforms")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="Perform bounded local file/container checks")
    preflight.add_argument("input")
    _add_limits(preflight)
    preflight.set_defaults(func=cmd_preflight)

    inspect = subparsers.add_parser("inspect", help="Preflight and validate waveform metadata")
    inspect.add_argument("input")
    inspect.add_argument("--gx-cli")
    _add_limits(inspect)
    inspect.set_defaults(func=cmd_inspect)

    analyze_parser = subparsers.add_parser("analyze", help="Stream and measure a DSL or CSV waveform")
    analyze_parser.add_argument("input")
    analyze_parser.add_argument("--output", required=True)
    analyze_parser.add_argument("--events-output")
    analyze_parser.add_argument("--channel", action="append", help="Physical DSL channel id/name or CSV column index/name; repeatable")
    analyze_parser.add_argument("--start-sample", type=int)
    analyze_parser.add_argument("--end-sample", type=int)
    analyze_parser.add_argument("--gx-cli")
    _add_limits(analyze_parser)
    analyze_parser.set_defaults(func=cmd_analyze)

    acquire = subparsers.add_parser("acquire", help="Acquire a waveform to a DSL file without analyzing it")
    acquire.add_argument("--output", dest="capture_output", required=True, help="Captured DSL file")
    _add_acquisition_arguments(acquire)
    acquire.set_defaults(func=cmd_acquire)

    capture = subparsers.add_parser("capture", help="Capture, validate, and analyze a waveform")
    capture.add_argument("--capture-output", required=True)
    capture.add_argument("--output", required=True, help="Analysis result JSON")
    capture.add_argument("--events-output")
    capture.add_argument("--channel", action="append", help="Channels to analyze after capture; defaults to all captured channels")
    capture.add_argument("--start-sample", type=int)
    capture.add_argument("--end-sample", type=int)
    _add_acquisition_arguments(capture)
    capture.set_defaults(func=cmd_capture)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return args.func(args)
    except ToolError as exc:
        _emit({"status": "error", "error": exc.as_dict()})
        return exc.exit_code
    except KeyboardInterrupt:
        error = ToolError("CANCELLED", "Operation cancelled", "runtime", retryable=True, exit_code=130)
        _emit({"status": "error", "error": error.as_dict()})
        return error.exit_code
    except Exception as exc:
        error = ToolError("INTERNAL_ERROR", "Unexpected internal error", "runtime", {"type": type(exc).__name__}, exit_code=9)
        _emit({"status": "error", "error": error.as_dict()})
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
