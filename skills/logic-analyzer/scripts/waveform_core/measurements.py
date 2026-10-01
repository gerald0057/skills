from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable

from .statistics import OnlineStats


EventSink = Callable[[dict[str, object]], None]


@dataclass
class ExtremeInterval:
    start_sample: int
    end_sample: int
    level: int

    @property
    def width(self) -> int:
        return self.end_sample - self.start_sample

    def as_dict(self, samplerate_hz: int) -> dict[str, object]:
        return {
            "start_sample": self.start_sample,
            "end_sample": self.end_sample,
            "level": self.level,
            "duration_samples": self.width,
            "duration_seconds": self.width / samplerate_hz,
        }


class ChannelMeasurement:
    def __init__(
        self,
        physical_id: int,
        name: str,
        samplerate_hz: int,
        start_sample: int,
        end_sample: int,
        initial_level: int,
        example_limit: int,
        event_sink: EventSink | None = None,
    ) -> None:
        self.physical_id = physical_id
        self.name = name
        self.rate = samplerate_hz
        self.start = start_sample
        self.end = end_sample
        self.initial_level = initial_level
        self.final_level = initial_level
        self.current_level = initial_level
        self.segment_start = start_sample
        self.rising_count = 0
        self.falling_count = 0
        self.high_samples = 0
        self.low_samples = 0
        self.high_widths = OnlineStats()
        self.low_widths = OnlineStats()
        self.rising_periods = OnlineStats()
        self.falling_periods = OnlineStats()
        self.last_rising: int | None = None
        self.last_falling: int | None = None
        self.first_events: list[dict[str, object]] = []
        self.last_events: deque[dict[str, object]] = deque(maxlen=example_limit)
        self.example_limit = example_limit
        self.event_sink = event_sink
        self.shortest: dict[int, ExtremeInterval | None] = {0: None, 1: None}
        self.longest: dict[int, ExtremeInterval | None] = {0: None, 1: None}

    def _event_dict(self, sample: int, new_level: int) -> dict[str, object]:
        return {
            "channel": self.physical_id,
            "sample": sample,
            "time_seconds": sample / self.rate,
            "edge": "rising" if new_level else "falling",
            "new_level": new_level,
        }

    def _close_interval(self, end_sample: int, *, complete: bool) -> None:
        width = end_sample - self.segment_start
        if width < 0:
            raise ValueError("transition order is not monotonic")
        if self.current_level:
            self.high_samples += width
            stats = self.high_widths
        else:
            self.low_samples += width
            stats = self.low_widths
        if complete and width > 0:
            stats.add(width)
            interval = ExtremeInterval(self.segment_start, end_sample, self.current_level)
            current_shortest = self.shortest[self.current_level]
            current_longest = self.longest[self.current_level]
            if current_shortest is None or interval.width < current_shortest.width:
                self.shortest[self.current_level] = interval
            if current_longest is None or interval.width > current_longest.width:
                self.longest[self.current_level] = interval

    def consume(self, transitions: Iterable[tuple[int, int]]) -> None:
        for sample, new_level in transitions:
            self.add_transition(sample, new_level, force=(sample == self.start))
        self.finish()

    def add_transition(self, sample: int, new_level: int, *, force: bool = False) -> None:
        if sample < self.start or sample >= self.end or (new_level == self.current_level and not force):
            return
        if new_level != self.current_level:
            self._close_interval(sample, complete=(self.rising_count + self.falling_count) > 0)
        event = self._event_dict(sample, new_level)
        if len(self.first_events) < self.example_limit:
            self.first_events.append(event)
        self.last_events.append(event)
        if self.event_sink:
            self.event_sink(event)
        if new_level:
            self.rising_count += 1
            if self.last_rising is not None:
                self.rising_periods.add(sample - self.last_rising)
            self.last_rising = sample
        else:
            self.falling_count += 1
            if self.last_falling is not None:
                self.falling_periods.add(sample - self.last_falling)
            self.last_falling = sample
        if new_level != self.current_level:
            self.current_level = new_level
            self.final_level = new_level
            self.segment_start = sample

    def finish(self) -> None:
        self._close_interval(self.end, complete=False)

    def result(self) -> dict[str, object]:
        total_samples = self.end - self.start
        first_ids = {(item["sample"], item["edge"]) for item in self.first_events}
        last_only = [item for item in self.last_events if (item["sample"], item["edge"]) not in first_ids]
        extrema: dict[str, object] = {}
        for level, label in ((0, "low"), (1, "high")):
            extrema[label] = {
                "shortest_complete": self.shortest[level].as_dict(self.rate) if self.shortest[level] else None,
                "longest_complete": self.longest[level].as_dict(self.rate) if self.longest[level] else None,
            }
        return {
            "physical_id": self.physical_id,
            "name": self.name,
            "window": {"start_sample": self.start, "end_sample": self.end},
            "initial_level": self.initial_level,
            "final_level": self.final_level,
            "edges": {
                "total": self.rising_count + self.falling_count,
                "rising": self.rising_count,
                "falling": self.falling_count,
                "first": self.first_events,
                "last": last_only,
            },
            "levels": {
                "high_samples": self.high_samples,
                "low_samples": self.low_samples,
                "high_ratio": self.high_samples / total_samples if total_samples else None,
                "low_ratio": self.low_samples / total_samples if total_samples else None,
            },
            "complete_pulse_widths": {
                "high": self.high_widths.as_dict(self.rate),
                "low": self.low_widths.as_dict(self.rate),
            },
            "periods": {
                "rising_to_rising": self.rising_periods.as_dict(self.rate),
                "falling_to_falling": self.falling_periods.as_dict(self.rate),
            },
            "extrema": extrema,
            "boundary_intervals_excluded_from_width_stats": True,
        }
