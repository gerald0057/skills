from __future__ import annotations

import math
import json
import re
import struct
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator

from .errors import ToolError
from .limits import Limits


BLOCK_BYTES = 2 * 1024 * 1024
BLOCK_SAMPLES = BLOCK_BYTES * 8
_BLOCK_RE = re.compile(r"^L-(\d+)/(\d+)$")
_PROBE_RE = re.compile(r"^probe(\d+)$")


@dataclass(frozen=True)
class ChannelInfo:
    physical_id: int
    name: str
    blocks: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"physical_id": self.physical_id, "name": self.name}


@dataclass(frozen=True)
class DslMetadata:
    path: str
    file_size: int
    file_version: int
    driver: str
    device_mode: int
    samplerate_hz: int
    sample_count: int
    block_count: int
    trigger_position: int
    channels: tuple[ChannelInfo, ...]
    compressed_bytes: int
    uncompressed_bytes: int
    compression_ratio: float

    def as_dict(self) -> dict[str, object]:
        return {
            "format": "dsview-dsl",
            "path": self.path,
            "file_size": self.file_size,
            "file_version": self.file_version,
            "driver": self.driver,
            "device_mode": self.device_mode,
            "samplerate_hz": self.samplerate_hz,
            "sample_count": self.sample_count,
            "duration_seconds": self.sample_count / self.samplerate_hz,
            "block_count": self.block_count,
            "trigger_position": self.trigger_position,
            "channels": [channel.as_dict() for channel in self.channels],
            "archive": {
                "compressed_bytes": self.compressed_bytes,
                "uncompressed_bytes": self.uncompressed_bytes,
                "compression_ratio": self.compression_ratio,
            },
        }


def _fail(code: str, message: str, **details: object) -> ToolError:
    return ToolError(code, message, "container_preflight", details, exit_code=3)


def _parse_int(fields: dict[str, str], key: str) -> int:
    raw = fields.get(key)
    if raw is None:
        raise _fail("DSL_HEADER_MISSING_FIELD", f"Missing DSL header field: {key}", field=key)
    try:
        return int(raw, 10)
    except ValueError as exc:
        raise _fail("DSL_HEADER_INVALID_FIELD", f"Invalid integer in DSL header: {key}", field=key, value=raw) from exc


def _parse_rate(raw: str) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmMgG]?)(?:[hH][zZ])?\s*", raw)
    if not match:
        raise _fail("DSL_HEADER_INVALID_FIELD", "Invalid DSL samplerate", field="samplerate", value=raw)
    multiplier = {"": 1, "k": 1000, "m": 1000_000, "g": 1000_000_000}[match.group(2).lower()]
    value = float(match.group(1)) * multiplier
    if not math.isfinite(value) or value <= 0 or not value.is_integer():
        raise _fail("DSL_HEADER_INVALID_FIELD", "DSL samplerate must be a positive integer Hz value", value=raw)
    return int(value)


def _parse_header(text: str) -> tuple[int, dict[str, str], dict[int, str]]:
    version = 0
    section = ""
    fields: dict[str, str] = {}
    probes: dict[int, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        key_lower = key.lower()
        if section == "version" and key_lower == "version":
            try:
                version = int(value)
            except ValueError as exc:
                raise _fail("DSL_HEADER_INVALID_FIELD", "Invalid DSL version", value=value) from exc
        elif section in ("", "header"):
            probe_match = _PROBE_RE.fullmatch(key_lower)
            if probe_match:
                physical_id = int(probe_match.group(1))
                if physical_id in probes:
                    raise _fail("DSL_CHANNEL_DUPLICATE", "Duplicate physical channel in DSL header", channel=physical_id)
                probes[physical_id] = value
            else:
                fields[key_lower] = value
    return version, fields, probes


def _safe_entry_name(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and "" not in path.parts


def _precheck_central_directory(path: Path, file_size: int, limits: Limits) -> None:
    tail_size = min(file_size, 65_557)
    try:
        with path.open("rb") as stream:
            stream.seek(file_size - tail_size)
            tail = stream.read(tail_size)
    except OSError as exc:
        raise _fail("INVALID_ZIP", "Cannot read DSL ZIP end record", reason=str(exc)) from exc
    position = tail.rfind(b"PK\x05\x06")
    if position < 0 or position + 22 > len(tail):
        raise _fail("INVALID_ZIP", "DSL file has no valid ZIP end record")
    try:
        _, disk, directory_disk, entries_on_disk, total_entries, directory_size, directory_offset, comment_size = struct.unpack_from(
            "<4s4H2LH", tail, position
        )
    except struct.error as exc:
        raise _fail("INVALID_ZIP", "DSL ZIP end record is truncated") from exc
    if disk != 0 or directory_disk != 0 or entries_on_disk != total_entries:
        raise _fail("ZIP_MULTIDISK_UNSUPPORTED", "Multi-disk DSL archives are unsupported")
    if total_entries == 0xFFFF:
        raise _fail("ZIP_ENTRY_LIMIT", "ZIP64 entry counts are unsupported by the bounded preflight", limit=limits.max_zip_entries)
    if total_entries > limits.max_zip_entries:
        raise _fail("ZIP_ENTRY_LIMIT", "DSL archive contains too many entries", observed=total_entries, limit=limits.max_zip_entries)
    if directory_size > limits.max_central_directory_bytes:
        raise _fail(
            "ZIP_DIRECTORY_LIMIT",
            "DSL central directory exceeds the configured limit",
            observed=directory_size,
            limit=limits.max_central_directory_bytes,
        )
    absolute_eocd = file_size - tail_size + position
    if directory_offset + directory_size > absolute_eocd or position + 22 + comment_size > len(tail):
        raise _fail("INVALID_ZIP", "DSL ZIP directory offsets are inconsistent")


def preflight_dsl(path: Path, limits: Limits) -> DslMetadata:
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

    _precheck_central_directory(path, file_size, limits)

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise _fail("INVALID_ZIP", "DSL file is not a readable ZIP container", reason=str(exc)) from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > limits.max_zip_entries:
            raise _fail("ZIP_ENTRY_LIMIT", "DSL archive contains too many entries", observed=len(infos), limit=limits.max_zip_entries)
        names: set[str] = set()
        total_uncompressed = 0
        total_compressed = 0
        for info in infos:
            if info.filename in names:
                raise _fail("ZIP_DUPLICATE_ENTRY", "DSL archive contains duplicate entry names", entry=info.filename)
            names.add(info.filename)
            if not _safe_entry_name(info.filename):
                raise _fail("ZIP_UNSAFE_ENTRY", "DSL archive contains an unsafe entry name", entry=info.filename)
            if info.flag_bits & 0x1:
                raise _fail("ZIP_ENCRYPTED_ENTRY", "Encrypted DSL archive entries are unsupported", entry=info.filename)
            total_uncompressed += info.file_size
            total_compressed += info.compress_size
            if total_uncompressed > limits.max_uncompressed_bytes:
                raise _fail("ZIP_UNCOMPRESSED_LIMIT", "DSL archive exceeds the uncompressed byte limit", observed=total_uncompressed, limit=limits.max_uncompressed_bytes)
        ratio = total_uncompressed / max(total_compressed, 1)
        if ratio > limits.max_compression_ratio:
            raise _fail("ZIP_BOMB_SUSPECTED", "DSL archive exceeds the configured compression ratio", observed=ratio, limit=limits.max_compression_ratio)

        required = {"header", "session", "decoders"}
        missing = sorted(required - names)
        if missing:
            raise _fail("DSL_STRUCTURE_INVALID", "DSL archive is missing required metadata entries", missing=missing)
        header_info = archive.getinfo("header")
        if header_info.file_size > limits.max_header_bytes:
            raise _fail("DSL_HEADER_TOO_LARGE", "DSL header exceeds the configured limit", observed=header_info.file_size, limit=limits.max_header_bytes)
        for metadata_name in ("session", "decoders"):
            if archive.getinfo(metadata_name).file_size > limits.max_metadata_bytes:
                raise _fail("DSL_METADATA_TOO_LARGE", "DSL metadata entry exceeds the configured limit", entry=metadata_name)
        try:
            session_root = json.loads(archive.read("session"))
            decoders_root = json.loads(archive.read("decoders"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise _fail("DSL_METADATA_INVALID", "DSL session or decoder metadata is not valid JSON", reason=str(exc)) from exc
        if not isinstance(session_root, dict) or not isinstance(decoders_root, list):
            raise _fail(
                "DSL_METADATA_INVALID",
                "DSL session must be an object and decoders must be an array",
                session_type=type(session_root).__name__,
                decoders_type=type(decoders_root).__name__,
            )
        try:
            header_text = archive.read("header").decode("utf-8")
        except (UnicodeDecodeError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise _fail("DSL_HEADER_INVALID", "DSL header cannot be decoded", reason=str(exc)) from exc

        version, fields, probes = _parse_header(header_text)
        if version not in (2, 3):
            raise _fail("DSL_UNSUPPORTED_VERSION", "Only DSView DSL v2/v3 files are supported", version=version)
        device_mode = _parse_int(fields, "device mode")
        if device_mode != 0:
            raise _fail("DSL_UNSUPPORTED_MODE", "Only digital Logic captures are supported", device_mode=device_mode)
        sample_count = _parse_int(fields, "total samples")
        total_probes = _parse_int(fields, "total probes")
        block_count = _parse_int(fields, "total blocks")
        trigger_position = _parse_int(fields, "trigger pos")
        rate_raw = fields.get("samplerate")
        if rate_raw is None:
            raise _fail("DSL_HEADER_MISSING_FIELD", "Missing DSL samplerate", field="samplerate")
        samplerate_hz = _parse_rate(rate_raw)
        if sample_count <= 0 or sample_count % 64:
            raise _fail("DSL_STRUCTURE_INVALID", "DSL sample count must be positive and 64-sample aligned", sample_count=sample_count)
        if total_probes <= 0 or total_probes != len(probes):
            raise _fail("DSL_CHANNEL_MISMATCH", "DSL channel count does not match physical channel declarations", declared=total_probes, observed=len(probes))
        if block_count <= 0:
            raise _fail("DSL_STRUCTURE_INVALID", "DSL block count must be positive", block_count=block_count)
        if trigger_position < 0 or trigger_position > sample_count:
            raise _fail("DSL_STRUCTURE_INVALID", "DSL trigger position is outside the capture", trigger_position=trigger_position)

        blocks_by_channel: dict[int, dict[int, zipfile.ZipInfo]] = {}
        for info in infos:
            match = _BLOCK_RE.fullmatch(info.filename)
            if not match:
                continue
            channel_id, block_id = (int(value) for value in match.groups())
            channel_blocks = blocks_by_channel.setdefault(channel_id, {})
            if block_id in channel_blocks:
                raise _fail("DSL_BLOCK_DUPLICATE", "Duplicate DSL waveform block", channel=channel_id, block=block_id)
            channel_blocks[block_id] = info

        if set(blocks_by_channel) != set(probes):
            raise _fail("DSL_CHANNEL_MISMATCH", "DSL waveform entries do not match declared physical channels", declared=sorted(probes), observed=sorted(blocks_by_channel))

        expected_total_bytes = sample_count // 8
        expected_last_bytes = expected_total_bytes - BLOCK_BYTES * (block_count - 1)
        if expected_last_bytes <= 0 or expected_last_bytes > BLOCK_BYTES:
            raise _fail("DSL_BLOCK_SIZE_MISMATCH", "DSL block count is inconsistent with sample count", sample_count=sample_count, block_count=block_count)
        channels: list[ChannelInfo] = []
        waveform_uncompressed = 0
        for channel_id in sorted(probes):
            channel_blocks = blocks_by_channel[channel_id]
            expected_ids = list(range(block_count))
            if sorted(channel_blocks) != expected_ids:
                raise _fail("DSL_BLOCK_MISSING", "DSL waveform block sequence is incomplete", channel=channel_id, expected=expected_ids, observed=sorted(channel_blocks))
            entry_names: list[str] = []
            for block_id in expected_ids:
                info = channel_blocks[block_id]
                expected_size = BLOCK_BYTES if block_id < block_count - 1 else expected_last_bytes
                if info.file_size != expected_size:
                    raise _fail("DSL_BLOCK_SIZE_MISMATCH", "DSL waveform block has an unexpected size", channel=channel_id, block=block_id, observed=info.file_size, expected=expected_size)
                waveform_uncompressed += info.file_size
                entry_names.append(info.filename)
            channels.append(ChannelInfo(channel_id, probes[channel_id], tuple(entry_names)))

        if waveform_uncompressed != expected_total_bytes * total_probes:
            raise _fail("DSL_BLOCK_SIZE_MISMATCH", "DSL waveform byte total is inconsistent", observed=waveform_uncompressed, expected=expected_total_bytes * total_probes)

        return DslMetadata(
            path=str(path),
            file_size=file_size,
            file_version=version,
            driver=fields.get("driver", ""),
            device_mode=device_mode,
            samplerate_hz=samplerate_hz,
            sample_count=sample_count,
            block_count=block_count,
            trigger_position=trigger_position,
            channels=tuple(channels),
            compressed_bytes=total_compressed,
            uncompressed_bytes=total_uncompressed,
            compression_ratio=ratio,
        )


def _read_sample(archive: zipfile.ZipFile, channel: ChannelInfo, sample: int) -> int:
    block_id = sample // BLOCK_SAMPLES
    within = sample % BLOCK_SAMPLES
    byte_offset = within // 8
    bit_offset = within % 8
    try:
        with archive.open(channel.blocks[block_id]) as stream:
            try:
                stream.seek(byte_offset)
            except (AttributeError, OSError):
                remaining = byte_offset
                while remaining:
                    chunk = stream.read(min(remaining, 64 * 1024))
                    if not chunk:
                        raise ToolError("DSL_READ_FAILED", "Unexpected end of DSL waveform block", "stream_analyze", exit_code=8)
                    remaining -= len(chunk)
            value = stream.read(1)
    except ToolError:
        raise
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
        raise ToolError("DSL_READ_FAILED", "Cannot read DSL waveform block", "stream_analyze", {"reason": str(exc)}, exit_code=8) from exc
    if len(value) != 1:
        raise ToolError("DSL_READ_FAILED", "Cannot read requested DSL sample", "stream_analyze", {"sample": sample}, exit_code=8)
    return (value[0] >> bit_offset) & 1


def iter_transitions(
    metadata: DslMetadata,
    channel: ChannelInfo,
    start_sample: int,
    end_sample: int,
    deadline: float,
) -> tuple[int, Iterator[tuple[int, int]]]:
    """Return initial level and an iterator of (sample_index, new_level)."""

    archive = zipfile.ZipFile(metadata.path)
    try:
        initial_level = _read_sample(archive, channel, start_sample)
    except Exception:
        archive.close()
        raise

    def generate() -> Iterator[tuple[int, int]]:
        previous = _read_sample(archive, channel, start_sample - 1) if start_sample > 0 else initial_level
        first_byte = start_sample // 8
        last_byte_exclusive = (end_sample + 7) // 8
        try:
            for block_id, entry_name in enumerate(channel.blocks):
                block_first_byte = block_id * BLOCK_BYTES
                block_last_byte = block_first_byte + (metadata.sample_count // 8 - block_first_byte if block_id == metadata.block_count - 1 else BLOCK_BYTES)
                wanted_first = max(first_byte, block_first_byte)
                wanted_last = min(last_byte_exclusive, block_last_byte)
                if wanted_first >= wanted_last:
                    continue
                if time.monotonic() > deadline:
                    raise ToolError("ANALYSIS_TIMEOUT", "Waveform analysis exceeded its deadline", "stream_analyze", retryable=True, exit_code=7)
                try:
                    data = archive.read(entry_name)
                except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
                    raise ToolError(
                        "DSL_READ_FAILED",
                        "Cannot decompress DSL waveform block",
                        "stream_analyze",
                        {"entry": entry_name, "reason": str(exc)},
                        exit_code=8,
                    ) from exc
                local_first = wanted_first - block_first_byte
                local_last = wanted_last - block_first_byte
                for local_index in range(local_first, local_last):
                    byte_value = data[local_index]
                    global_byte = block_first_byte + local_index
                    sample_base = global_byte * 8
                    lo = max(0, start_sample - sample_base)
                    hi = min(8, end_sample - sample_base)
                    if lo == 0 and hi == 8:
                        transition_mask = (byte_value ^ ((byte_value << 1) & 0xFF)) & 0xFE
                        if (byte_value & 1) != previous:
                            transition_mask |= 1
                        while transition_mask:
                            lowest = transition_mask & -transition_mask
                            bit = lowest.bit_length() - 1
                            new_level = (byte_value >> bit) & 1
                            yield sample_base + bit, new_level
                            transition_mask ^= lowest
                        previous = (byte_value >> 7) & 1
                    else:
                        for bit in range(lo, hi):
                            level = (byte_value >> bit) & 1
                            if level != previous:
                                yield sample_base + bit, level
                            previous = level
        finally:
            archive.close()

    return initial_level, generate()
