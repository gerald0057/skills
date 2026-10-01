from __future__ import annotations

import json
import io
import os
import sys
import tempfile
import unittest
import warnings
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills/logic-analyzer/scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from waveform_core.csv_reader import analyze_csv, preflight_csv
from waveform_core.dsl_reader import iter_transitions, preflight_dsl
from waveform_core.errors import ToolError
from waveform_core.gx_dsview import run_json
from waveform_core.limits import Limits
from waveform_core.measurements import ChannelMeasurement
from waveform_tool import _acquire, build_parser, cmd_acquire, cmd_capture


def pack_bits(samples: list[int]) -> bytes:
    result = bytearray()
    for offset in range(0, len(samples), 8):
        value = 0
        for bit, sample in enumerate(samples[offset : offset + 8]):
            value |= sample << bit
        result.append(value)
    return bytes(result)


def write_dsl(path: Path, samples: list[int], *, channel: int = 4, block_override: bytes | None = None) -> None:
    assert len(samples) == 64
    header = (
        "[version]\nversion = 3\n[header]\n"
        "driver = DSLogic\ndevice mode = 0\ncapturefile = data\n"
        "total samples = 64\ntotal probes = 1\ntotal blocks = 1\n"
        "samplerate = 10 Hz\ntrigger time = 0\ntrigger pos = 0\n"
        f"probe{channel} = signal\n"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("header", header)
        archive.writestr("session", "{}")
        archive.writestr("decoders", "[]")
        archive.writestr(f"L-{channel}/0", block_override if block_override is not None else pack_bits(samples))


class DslTests(unittest.TestCase):
    def test_sparse_physical_channel_and_measurement(self) -> None:
        samples = [0] * 8 + [1] * 8 + [0] * 16 + [1] * 16 + [0] * 16
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "capture.dsl"
            write_dsl(path, samples)
            metadata = preflight_dsl(path, Limits())
            self.assertEqual([channel.physical_id for channel in metadata.channels], [4])
            initial, transitions = iter_transitions(
                metadata,
                metadata.channels[0],
                0,
                64,
                float("inf"),
            )
            measurement = ChannelMeasurement(4, "signal", 10, 0, 64, initial, 4)
            measurement.consume(transitions)
            result = measurement.result()
            self.assertEqual(result["edges"]["rising"], 2)
            self.assertEqual(result["edges"]["falling"], 2)
            self.assertEqual(result["levels"]["high_samples"], 24)
            self.assertEqual(result["complete_pulse_widths"]["high"]["count"], 2)
            self.assertEqual(result["complete_pulse_widths"]["high"]["samples"]["min"], 8)
            self.assertEqual(result["periods"]["rising_to_rising"]["samples"]["mean"], 24)

    def test_block_size_mismatch_is_rejected_before_analysis(self) -> None:
        samples = [0] * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.dsl"
            write_dsl(path, samples, block_override=b"\x00")
            with self.assertRaises(ToolError) as caught:
                preflight_dsl(path, Limits())
            self.assertEqual(caught.exception.code, "DSL_BLOCK_SIZE_MISMATCH")

    def test_edge_at_window_start_is_counted(self) -> None:
        samples = [0] * 8 + [1] * 8 + [0] * 48
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "capture.dsl"
            write_dsl(path, samples)
            metadata = preflight_dsl(path, Limits())
            initial, transitions = iter_transitions(metadata, metadata.channels[0], 8, 32, float("inf"))
            measurement = ChannelMeasurement(4, "signal", 10, 8, 32, initial, 4)
            measurement.consume(transitions)
            result = measurement.result()
            self.assertEqual(result["edges"]["rising"], 1)
            self.assertEqual(result["edges"]["falling"], 1)
            self.assertEqual(result["complete_pulse_widths"]["high"]["count"], 1)

    def test_duplicate_zip_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "duplicate.dsl"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("header", "one")
                    archive.writestr("header", "two")
            with self.assertRaises(ToolError) as caught:
                preflight_dsl(path, Limits())
            self.assertEqual(caught.exception.code, "ZIP_DUPLICATE_ENTRY")


class CsvTests(unittest.TestCase):
    def test_dsview_transition_csv_streams_measurements(self) -> None:
        content = (
            "; CSV, generated for test\n"
            "; Channels (2/2)\n"
            "; Sample rate: 10 Hz\n"
            "; Sample count: 10\n"
            "Time(s), SOF, Motion\n"
            "0,0,0\n"
            "0.2,1,0\n"
            "0.4,0,1\n"
            "0.6,1,1\n"
            "0.8,0,0\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "capture.csv"
            path.write_text(content, encoding="utf-8")
            metadata = preflight_csv(path, Limits())
            result = analyze_csv(metadata, [0], 0, 10, Limits(), lambda _i, _n: None)[0]
            self.assertEqual(result["edges"]["rising"], 2)
            self.assertEqual(result["edges"]["falling"], 2)
            self.assertEqual(result["levels"]["high_samples"], 4)

    def test_non_monotonic_csv_is_rejected(self) -> None:
        content = (
            "; Sample rate: 10 Hz\n; Sample count: 10\n"
            "Time(s), D0\n0,0\n0.5,1\n0.4,0\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.csv"
            path.write_text(content, encoding="utf-8")
            metadata = preflight_csv(path, Limits())
            with self.assertRaises(ToolError) as caught:
                analyze_csv(metadata, [0], 0, 10, Limits(), lambda _i, _n: None)
            self.assertEqual(caught.exception.code, "CSV_TIME_NOT_MONOTONIC")


class GxRunnerTests(unittest.TestCase):
    def test_acquire_command_has_no_analysis_output(self) -> None:
        args = build_parser().parse_args([
            "acquire",
            "--output", "/tmp/capture.dsl",
            "--samplerate", "25000000",
            "--channels", "4,7",
            "--samples", "65536",
        ])
        self.assertIs(args.func, cmd_acquire)
        self.assertEqual(args.capture_output, "/tmp/capture.dsl")
        self.assertFalse(hasattr(args, "events_output"))

    def test_acquire_emits_capture_summary_without_analysis(self) -> None:
        args = build_parser().parse_args([
            "acquire",
            "--output", "/tmp/capture.dsl",
            "--samplerate", "25000000",
            "--channels", "4,7",
            "--samples", "65536",
        ])
        summary = {
            "status": "success",
            "capture_file": "/tmp/capture.dsl",
            "samplerate_hz": 25000000,
            "requested_samples": 65536,
            "actual_samples": 65536,
            "channels": [4, 7],
            "trigger_position": 0,
        }
        stdout = io.StringIO()
        with mock.patch("waveform_tool._acquire", return_value=(Path("/tmp/capture.dsl"), {}, summary)), \
             mock.patch("waveform_tool._analyze_and_publish") as analyze, \
             redirect_stdout(stdout):
            self.assertEqual(cmd_acquire(args), 0)
        analyze.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue()), summary)

    def test_capture_reuses_acquisition_and_runs_analysis(self) -> None:
        args = build_parser().parse_args([
            "capture",
            "--capture-output", "/tmp/capture.dsl",
            "--output", "/tmp/result.json",
            "--samplerate", "25000000",
            "--channels", "4,7",
            "--samples", "65536",
        ])
        provenance = {"result": {"status": "success"}}
        with mock.patch(
            "waveform_tool._acquire",
            return_value=(Path("/tmp/capture.dsl"), provenance, {}),
        ) as acquire, mock.patch("waveform_tool._analyze_and_publish", return_value=0) as analyze:
            self.assertEqual(cmd_capture(args), 0)
        acquire.assert_called_once_with(args)
        analyze.assert_called_once_with(args, Path("/tmp/capture.dsl"), provenance)

    def test_acquire_rejects_existing_output_before_device_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "capture.dsl"
            output.write_bytes(b"existing")
            args = build_parser().parse_args([
                "acquire",
                "--output", str(output),
                "--samplerate", "25000000",
                "--channels", "4,7",
                "--samples", "65536",
            ])
            with mock.patch("waveform_tool.resolve_cli") as resolve:
                with self.assertRaises(ToolError) as caught:
                    _acquire(args)
            self.assertEqual(caught.exception.code, "OUTPUT_EXISTS")
            resolve.assert_not_called()

    def test_json_runner_success(self) -> None:
        result = run_json(
            Path(sys.executable),
            ["-c", "import json; print(json.dumps({'status':'success','value':1}))"],
            timeout=2,
            limits=Limits(),
            stage="test",
        )
        self.assertEqual(result["value"], 1)

    def test_json_runner_bounds_output(self) -> None:
        limits = Limits(max_process_output_bytes=64)
        with self.assertRaises(ToolError) as caught:
            run_json(
                Path(sys.executable),
                ["-c", "print('x' * 1000)"],
                timeout=2,
                limits=limits,
                stage="test",
            )
        self.assertEqual(caught.exception.code, "GX_OUTPUT_LIMIT")

    def test_json_runner_timeout(self) -> None:
        with self.assertRaises(ToolError) as caught:
            run_json(
                Path(sys.executable),
                ["-c", "import time; time.sleep(1)"],
                timeout=0.05,
                limits=Limits(),
                stage="test",
            )
        self.assertEqual(caught.exception.code, "GX_PROCESS_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
