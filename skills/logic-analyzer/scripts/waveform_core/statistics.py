from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class OnlineStats:
    count: int = 0
    minimum: int | None = None
    maximum: int | None = None
    mean: float = 0.0
    m2: float = 0.0

    def add(self, value: int) -> None:
        self.count += 1
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)

    def as_dict(self, samplerate_hz: int) -> dict[str, object]:
        if self.count == 0:
            return {
                "count": 0,
                "samples": {"min": None, "max": None, "mean": None, "stddev": None},
                "seconds": {"min": None, "max": None, "mean": None, "stddev": None},
            }
        stddev = math.sqrt(self.m2 / self.count)
        assert self.minimum is not None and self.maximum is not None
        return {
            "count": self.count,
            "samples": {
                "min": self.minimum,
                "max": self.maximum,
                "mean": self.mean,
                "stddev": stddev,
            },
            "seconds": {
                "min": self.minimum / samplerate_hz,
                "max": self.maximum / samplerate_hz,
                "mean": self.mean / samplerate_hz,
                "stddev": stddev / samplerate_hz,
            },
        }
