from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Limits:
    max_input_bytes: int = 4 * 1024**3
    max_zip_entries: int = 4096
    max_central_directory_bytes: int = 16 * 1024 * 1024
    max_header_bytes: int = 64 * 1024
    max_metadata_bytes: int = 8 * 1024 * 1024
    max_uncompressed_bytes: int = 8 * 1024**3
    max_compression_ratio: float = 2000.0
    max_analysis_seconds: float = 120.0
    max_process_output_bytes: int = 1024 * 1024
    max_result_bytes: int = 64 * 1024 * 1024
    max_detail_bytes: int = 1024**3
    max_csv_line_bytes: int = 1024 * 1024
    max_csv_columns: int = 256
    event_examples: int = 20

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)
