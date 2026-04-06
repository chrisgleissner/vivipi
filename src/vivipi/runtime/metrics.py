from __future__ import annotations

from dataclasses import dataclass

from vivipi.core.ring_buffer import RingBuffer

try:
    import time
except ImportError:  # pragma: no cover - imported on-device
    time = None


def start_timer() -> tuple[int | float, str]:
    if time is not None and hasattr(time, "ticks_us"):
        return int(time.ticks_us()), "ticks_us"
    if time is not None and hasattr(time, "ticks_ms"):
        return int(time.ticks_ms()), "ticks_ms"
    return float(time.perf_counter()), "perf_counter"


def elapsed_ms(started_at: int | float, timer_kind: str) -> float:
    if time is not None and timer_kind == "ticks_us":
        return float(time.ticks_diff(time.ticks_us(), int(started_at))) / 1000.0
    if time is not None and timer_kind == "ticks_ms":
        return float(time.ticks_diff(time.ticks_ms(), int(started_at)))
    return (float(time.perf_counter()) - float(started_at)) * 1000.0


@dataclass
class MetricSeries:
    count: int = 0
    total_ms: float = 0.0
    last_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None

    def record(self, duration_ms: float | None):
        if duration_ms is None:
            return
        value = float(duration_ms)
        self.count += 1
        self.total_ms += value
        self.last_ms = value
        self.min_ms = value if self.min_ms is None else min(self.min_ms, value)
        self.max_ms = value if self.max_ms is None else max(self.max_ms, value)

    def reset(self):
        self.count = 0
        self.total_ms = 0.0
        self.last_ms = None
        self.min_ms = None
        self.max_ms = None

    def snapshot(self) -> dict[str, float | int | None]:
        average_ms = None
        if self.count:
            average_ms = self.total_ms / self.count
        return {
            "count": self.count,
            "last_ms": self.last_ms,
            "min_ms": self.min_ms,
            "max_ms": self.max_ms,
            "avg_ms": average_ms,
        }


class MetricsStore:
    def __init__(self, check_ids: tuple[str, ...], memory_snapshot_capacity: int = 8):
        self.cycle = MetricSeries()
        self.network = MetricSeries()
        self.gc_pause = MetricSeries()
        self.check_duration = {check_id: MetricSeries() for check_id in check_ids}
        self.check_latency = {check_id: MetricSeries() for check_id in check_ids}
        self.memory_snapshots = RingBuffer(memory_snapshot_capacity)
        self.gc_collections = 0

    def record_cycle(self, duration_ms: float):
        self.cycle.record(duration_ms)

    def record_network(self, duration_ms: float | None):
        self.network.record(duration_ms)

    def record_gc(self, duration_ms: float | None):
        self.gc_pause.record(duration_ms)
        if duration_ms is not None:
            self.gc_collections += 1

    def record_check(self, check_id: str, duration_ms: float | None, latency_ms: float | None = None):
        if check_id not in self.check_duration:
            self.check_duration[check_id] = MetricSeries()
            self.check_latency[check_id] = MetricSeries()
        self.check_duration[check_id].record(duration_ms)
        self.check_latency[check_id].record(latency_ms)

    def record_memory(self, snapshot: dict[str, object]):
        self.memory_snapshots.append(dict(snapshot))

    def reset(self):
        self.cycle.reset()
        self.network.reset()
        self.gc_pause.reset()
        self.gc_collections = 0
        self.memory_snapshots.clear()
        for series in self.check_duration.values():
            series.reset()
        for series in self.check_latency.values():
            series.reset()

    def snapshot(self) -> dict[str, object]:
        return {
            "cycle_ms": self.cycle.snapshot(),
            "network_connect_ms": self.network.snapshot(),
            "gc_pause_ms": self.gc_pause.snapshot(),
            "gc_collections": self.gc_collections,
            "checks": {
                check_id: {
                    "duration_ms": self.check_duration[check_id].snapshot(),
                    "latency_ms": self.check_latency[check_id].snapshot(),
                }
                for check_id in sorted(self.check_duration)
            },
            "memory": tuple(self.memory_snapshots.items()),
        }