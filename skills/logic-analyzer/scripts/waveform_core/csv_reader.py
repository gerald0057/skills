from __future__ import annotations

import csv
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Callable

from .errors import ToolError
from .limits import Limits
from .measurements import ChannelMeasurement, EventSink


_RATE_RE = re.compile(r"^;\s*Sample rate:\s*(\d+(?:\.\d+)?)\s*([kKmMgG]?)(?:[hH][zZ])?\s*$")
_COUNT_RE = re.compile(
    r"^;\s*Sample count:\s*([0-9]+(?:\.[0-9]+)?)(?:\s*([kKmMgG]))?(?:\s*(?:samples?|s))?\s*$",
    re.IGNORECASE,
)


def _scaled_number(value: str, suffix: str | None) -> int:
    multiplier = {None: 1, "": 1, "k": 1000, "m": 1000_000, "g": 1000_000_000}[suffix.lower() if suffix else None]
    number = Decimal(value) * multiplier
    if number <= 0 or number != number.to_integral_value():
        raise ValueError(value)
    return int(number)


@dataclass(frozen=True)
class CsvMetadata:
    path: str
    file_size: int
    samplerate_hz: int
    sample_count: int
    channel_names: tuple[str, ...]
    header_line: int

    def as_dict(self) -> dict[str, object]:
        return {
            "format": "dsview-transition-csv",
            "path": self.path,
            "file_size": self.file_size,
            "samplerate_hz": self.samplerate_hz,
            "sample_count": self.sample_count,
            "sample_count_source": "formatted_csv_comment",
            "duration_seconds": self.sample_count / self.samplerate_hz,
            "channels": [
                {"physical_id": index, "name": name, "source_column": index + 1}
                for index, name in enumerate(self.channel_names)
            ],
        }


def _read_bounded_line(stream, limit: int, line_number: int) -> str:
    line = stream.readline(limit + 1)
    if len(line.encode("utf-8")) > limit:
        raise ToolError("CSV_LINE_TOO_LARGE", "CSV line exceeds the configured limit", "file_preflight", {"line": line_number, "limit": limit}, exit_code=3)
    return line


def preflight_csv(path: Path, limits: Limits) -> CsvMetadata:
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise ToolError("FILE_NOT_FOUND", f"Cannot stat input file: {path}", "file_preflight", {"reason": str(exc)}, exit_code=3) from exc
    if not path.is_file():
        raise ToolError("NOT_REGULAR_FILE", f"Input is not a regular file: {path}", "file_preflight", exit_code=3)
    if file_size <= 0:
        raise ToolError("EMPTY_INPUT", "Input file is empty", "file_preflight", exit_code=3)
    if file_size > limits.max_input_bytes:
        raise ToolError("INPUT_TOO_LARGE", "Input file exceeds configured size limit", "file_preflight", {"observed": file_size, "limit": limits.max_input_bytes}, exit_code=3)

    rate: int | None = None
    count: int | None = None
    header: list[str] | None = None
    header_line = 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for line_number in range(1, 1026):
                line = _read_bounded_line(stream, limits.max_csv_line_bytes, line_number)
                if line == "":
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                rate_match = _RATE_RE.fullmatch(stripped)
                if rate_match:
                    rate = _scaled_number(rate_match.group(1), rate_match.group(2))
                    continue
                count_match = _COUNT_RE.fullmatch(stripped)
                if count_match:
                    count = _scaled_number(count_match.group(1), count_match.group(2))
                    continue
                if stripped.startswith(";"):
                    continue
                header = next(csv.reader([line], skipinitialspace=True))
                header_line = line_number
                break
    except (OSError, UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise ToolError("CSV_UNSUPPORTED_DIALECT", "Cannot read DSView waveform CSV metadata", "file_preflight", {"reason": str(exc)}, exit_code=4) from exc

    if header is None or not header or header[0].strip().lower() != "time(s)":
        raise ToolError("CSV_UNSUPPORTED_DIALECT", "CSV must use a Time(s) first column", "file_preflight", exit_code=4)
    channel_names = tuple(value.strip() for value in header[1:])
    if not channel_names or any(not value for value in channel_names):
        raise ToolError("CSV_UNSUPPORTED_DIALECT", "CSV must contain named digital channels", "file_preflight", exit_code=4)
    if len(channel_names) > limits.max_csv_columns:
        raise ToolError("CSV_COLUMN_LIMIT", "CSV contains too many channels", "file_preflight", {"observed": len(channel_names), "limit": limits.max_csv_columns}, exit_code=3)
    if len(set(channel_names)) != len(channel_names):
        raise ToolError("CSV_DUPLICATE_CHANNEL", "CSV channel names must be unique", "file_preflight", exit_code=4)
    if rate is None or count is None:
        missing = [name for name, value in (("Sample rate", rate), ("Sample count", count)) if value is None]
        raise ToolError("CSV_METADATA_MISSING", "CSV is missing required DSView metadata", "file_preflight", {"missing": missing}, hint="Export the CSV with DSView metadata comments.", exit_code=4)
    return CsvMetadata(str(path), file_size, rate, count, channel_names, header_line)


def _time_to_sample(raw: str, samplerate_hz: int, line_number: int) -> int:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise ToolError("CSV_ROW_INVALID", "CSV time is not numeric", "stream_analyze", {"line": line_number, "value": raw}, exit_code=8) from exc
    if not value.is_finite() or value < 0:
        raise ToolError("CSV_ROW_INVALID", "CSV time must be finite and non-negative", "stream_analyze", {"line": line_number, "value": raw}, exit_code=8)
    exact = value * samplerate_hz
    rounded = int(exact.to_integral_value(rounding=ROUND_HALF_EVEN))
    if abs(exact - rounded) > Decimal("0.5000001"):
        raise ToolError("CSV_TIME_OFF_GRID", "CSV time cannot be mapped to the declared sample rate", "stream_analyze", {"line": line_number, "value": raw}, exit_code=8)
    return rounded


def analyze_csv(
    metadata: CsvMetadata,
    selected_indices: list[int],
    start_sample: int,
    end_sample: int,
    limits: Limits,
    event_sink_factory: Callable[[int, str], EventSink | None],
) -> list[dict[str, object]]:
    deadline = time.monotonic() + limits.max_analysis_seconds
    states: list[int] | None = None
    measurements: dict[int, ChannelMeasurement] = {}
    last_sample = -1
    with Path(metadata.path).open("r", encoding="utf-8-sig", newline="") as stream:
        for _ in range(metadata.header_line):
            _read_bounded_line(stream, limits.max_csv_line_bytes, _ + 1)
        line_number = metadata.header_line
        while True:
            line_number += 1
            line = _read_bounded_line(stream, limits.max_csv_line_bytes, line_number)
            if line == "":
                break
            if time.monotonic() > deadline:
                raise ToolError("ANALYSIS_TIMEOUT", "CSV analysis exceeded its deadline", "stream_analyze", retryable=True, exit_code=7)
            if not line.strip() or line.lstrip().startswith(";"):
                continue
            try:
                row = next(csv.reader([line], skipinitialspace=True))
            except csv.Error as exc:
                raise ToolError("CSV_ROW_INVALID", "Cannot parse CSV row", "stream_analyze", {"line": line_number, "reason": str(exc)}, exit_code=8) from exc
            if len(row) != len(metadata.channel_names) + 1:
                raise ToolError("CSV_ROW_INVALID", "CSV row has an unexpected column count", "stream_analyze", {"line": line_number, "observed": len(row), "expected": len(metadata.channel_names) + 1}, exit_code=8)
            sample = _time_to_sample(row[0], metadata.samplerate_hz, line_number)
            if sample < last_sample:
                raise ToolError("CSV_TIME_NOT_MONOTONIC", "CSV timestamps are not monotonic", "stream_analyze", {"line": line_number, "sample": sample, "previous": last_sample}, exit_code=8)
            last_sample = sample
            try:
                new_states = [int(value.strip()) for value in row[1:]]
            except ValueError as exc:
                raise ToolError("CSV_ROW_INVALID", "CSV digital levels must be 0 or 1", "stream_analyze", {"line": line_number}, exit_code=8) from exc
            if any(value not in (0, 1) for value in new_states):
                raise ToolError("CSV_ROW_INVALID", "CSV digital levels must be 0 or 1", "stream_analyze", {"line": line_number}, exit_code=8)
            if states is None:
                states = new_states
                if sample > start_sample:
                    raise ToolError("CSV_INITIAL_STATE_MISSING", "CSV has no channel state at or before the requested window", "stream_analyze", {"first_sample": sample, "start_sample": start_sample}, exit_code=8)
                continue
            if sample < start_sample:
                states = new_states
                continue
            if not measurements:
                for index in selected_indices:
                    measurements[index] = ChannelMeasurement(
                        index,
                        metadata.channel_names[index],
                        metadata.samplerate_hz,
                        start_sample,
                        end_sample,
                        states[index],
                        limits.event_examples,
                        event_sink_factory(index, metadata.channel_names[index]),
                    )
            if sample >= end_sample:
                break
            for index in selected_indices:
                if new_states[index] != states[index]:
                    measurements[index].add_transition(sample, new_states[index])
            states = new_states
    if states is None:
        raise ToolError("CSV_NO_DATA", "CSV contains no waveform rows", "stream_analyze", exit_code=8)
    if not measurements:
        for index in selected_indices:
            measurements[index] = ChannelMeasurement(
                index,
                metadata.channel_names[index],
                metadata.samplerate_hz,
                start_sample,
                end_sample,
                states[index],
                limits.event_examples,
                event_sink_factory(index, metadata.channel_names[index]),
            )
    for measurement in measurements.values():
        measurement.finish()
    return [measurements[index].result() for index in selected_indices]
