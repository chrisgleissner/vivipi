from types import SimpleNamespace

import vivipi.runtime.metrics as runtime_metrics
from vivipi.runtime.metrics import MetricSeries, MetricsStore, elapsed_ms, start_timer


def test_timer_helpers_cover_ticks_us_ticks_ms_and_perf_counter(monkeypatch):
    fake_ticks_us = SimpleNamespace(
        ticks_us=lambda: 1250,
        ticks_ms=lambda: 12,
        ticks_diff=lambda current, started: current - started,
        perf_counter=lambda: 0.0,
    )
    monkeypatch.setattr(runtime_metrics, "time", fake_ticks_us)

    started_at, timer_kind = start_timer()

    assert (started_at, timer_kind) == (1250, "ticks_us")
    assert elapsed_ms(250, "ticks_us") == 1.0

    fake_ticks_ms = SimpleNamespace(
        ticks_ms=lambda: 30,
        ticks_diff=lambda current, started: current - started,
        perf_counter=lambda: 0.0,
    )
    monkeypatch.setattr(runtime_metrics, "time", fake_ticks_ms)

    started_at, timer_kind = start_timer()

    assert (started_at, timer_kind) == (30, "ticks_ms")
    assert elapsed_ms(10, "ticks_ms") == 20.0

    fake_perf = SimpleNamespace(perf_counter=lambda: 2.5)
    monkeypatch.setattr(runtime_metrics, "time", fake_perf)

    started_at, timer_kind = start_timer()

    assert (started_at, timer_kind) == (2.5, "perf_counter")
    assert elapsed_ms(2.0, "perf_counter") == 500.0


def test_metric_series_and_store_cover_none_unknown_id_and_reset_paths():
    series = MetricSeries()

    series.record(None)
    assert series.snapshot()["avg_ms"] is None

    series.record(10.0)
    series.record(20.0)
    assert series.snapshot() == {"count": 2, "last_ms": 20.0, "min_ms": 10.0, "max_ms": 20.0, "avg_ms": 15.0}

    store = MetricsStore(("router",), memory_snapshot_capacity=2)
    store.record_network(None)
    store.record_gc(None)
    store.record_check("dynamic", 11.0, 7.0)
    store.record_memory({"label": "one"})
    store.record_memory({"label": "two"})
    store.record_memory({"label": "three"})

    snapshot = store.snapshot()

    assert snapshot["gc_collections"] == 0
    assert snapshot["checks"]["dynamic"]["duration_ms"]["last_ms"] == 11.0
    assert snapshot["memory"] == ({"label": "two"}, {"label": "three"})

    store.reset()

    assert store.snapshot()["checks"]["router"]["duration_ms"]["count"] == 0
    assert store.snapshot()["memory"] == ()