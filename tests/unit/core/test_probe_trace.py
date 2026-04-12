from __future__ import annotations

import json

from vivipi.core.models import CheckDefinition, CheckType
import vivipi.core.probe_trace as probe_trace
from vivipi.core.probe_trace import ProbeTraceCollector, compare_probe_traces, load_probe_trace_records


def make_definition(identifier: str, *, target: str = "http://device.local/health") -> CheckDefinition:
    return CheckDefinition(
        identifier=identifier,
        name=identifier.upper(),
        check_type=CheckType.HTTP,
        target=target,
        interval_s=15,
        timeout_s=10,
    )


def test_probe_trace_collector_groups_transport_events_by_request_and_serializes_fields():
    definition = make_definition("alpha")
    records = []
    wall_time = iter((1.0, 1.0, 1.0, 1.0, 1.0, 1.0))
    monotonic_time = iter((10.0, 10.01, 10.02, 10.03, 10.04, 10.05))
    collector = ProbeTraceCollector(
        records.append,
        source="host",
        mode="reproduce",
        wall_time_provider=lambda: next(wall_time),
        monotonic_time_provider=lambda: next(monotonic_time),
    )

    collector.emit(definition, "probe-start", {"timeout_s": 10})
    collector.emit(definition, "dns-start", {"host": "device.local", "port": 80})
    collector.emit(definition, "dns-result", {"host": "device.local", "port": 80, "addresses": ("192.0.2.10:80",)})
    collector.emit(definition, "socket-open", {"stage": "connect", "target": "192.0.2.10:80", "socket_reused": False})
    collector.emit(definition, "socket-send", {"stage": "http-send", "bytes_sent": 48})
    collector.emit(definition, "probe-end", {"status": "OK", "detail": "HTTP 200", "latency_ms": 12.5})

    assert [record.event for record in records] == [
        "probe-start",
        "dns-start",
        "dns-result",
        "socket-open",
        "socket-send",
        "probe-end",
    ]
    assert all(record.request_id == "alpha:1" for record in records)
    assert records[2].addresses == ("192.0.2.10:80",)
    assert records[3].socket_reused is False
    assert records[4].bytes_sent == 48
    assert records[-1].status == "OK"


def test_probe_trace_load_and_compare_detects_ordering_differences(tmp_path):
    reference_path = tmp_path / "firmware.jsonl"
    candidate_path = tmp_path / "host.jsonl"
    reference_rows = [
        {"trace_kind": "probe_transport", "source": "firmware", "mode": "runtime", "wall_time": "2026-01-01T00:00:00.000000Z", "monotonic_s": 1.0, "sequence": 1, "request_index": 1, "request_id": "alpha:1", "thread_id": "main", "check_id": "alpha", "check_name": "ALPHA", "check_type": "HTTP", "target": "http://a", "probe_host_key": "a", "event": "probe-start", "raw_fields": {}},
        {"trace_kind": "probe_transport", "source": "firmware", "mode": "runtime", "wall_time": "2026-01-01T00:00:00.010000Z", "monotonic_s": 1.01, "sequence": 2, "request_index": 1, "request_id": "alpha:1", "thread_id": "main", "check_id": "alpha", "check_name": "ALPHA", "check_type": "HTTP", "target": "http://a", "probe_host_key": "a", "event": "probe-end", "raw_fields": {}},
        {"trace_kind": "probe_transport", "source": "firmware", "mode": "runtime", "wall_time": "2026-01-01T00:00:00.020000Z", "monotonic_s": 1.02, "sequence": 3, "request_index": 1, "request_id": "beta:1", "thread_id": "main", "check_id": "beta", "check_name": "BETA", "check_type": "HTTP", "target": "http://b", "probe_host_key": "b", "event": "probe-start", "raw_fields": {}},
        {"trace_kind": "probe_transport", "source": "firmware", "mode": "runtime", "wall_time": "2026-01-01T00:00:00.030000Z", "monotonic_s": 1.03, "sequence": 4, "request_index": 1, "request_id": "beta:1", "thread_id": "main", "check_id": "beta", "check_name": "BETA", "check_type": "HTTP", "target": "http://b", "probe_host_key": "b", "event": "probe-end", "raw_fields": {}},
    ]
    candidate_rows = [
        {**reference_rows[2], "source": "host", "sequence": 1, "monotonic_s": 1.0},
        {**reference_rows[3], "source": "host", "sequence": 2, "monotonic_s": 1.01},
        {**reference_rows[0], "source": "host", "sequence": 3, "monotonic_s": 1.02},
        {**reference_rows[1], "source": "host", "sequence": 4, "monotonic_s": 1.03},
    ]
    reference_path.write_text("\n".join(json.dumps(row) for row in reference_rows) + "\n", encoding="utf-8")
    candidate_path.write_text("\n".join(json.dumps(row) for row in candidate_rows) + "\n", encoding="utf-8")

    reference = load_probe_trace_records(reference_path)
    candidate = load_probe_trace_records(candidate_path)
    comparison = compare_probe_traces(reference, candidate)

    assert comparison.request_count_match is True
    assert comparison.ordering_match is False


def test_probe_trace_helper_fallbacks_and_rendering(monkeypatch, tmp_path):
    monkeypatch.setattr(probe_trace, "threading", None)
    monkeypatch.setattr(probe_trace, "_thread", None)

    assert probe_trace._allocate_lock() is None
    assert probe_trace._lock_context(None) is False
    class BrokenThreadModule:
        @staticmethod
        def get_ident():
            raise RuntimeError("no thread id")

    monkeypatch.setattr(probe_trace, "_thread", BrokenThreadModule)
    assert probe_trace._current_thread_id() == "main"
    converted = probe_trace._jsonable({"bytes": b"abc", "items": (1, object())})
    assert converted["bytes"] == "abc"
    assert converted["items"][0] == 1
    assert isinstance(converted["items"][1], str)

    invalid_path = tmp_path / "invalid.jsonl"
    invalid_path.write_text("not-json\n[]\n{}\n", encoding="utf-8")
    assert load_probe_trace_records(invalid_path) == ()
    assert probe_trace.render_parity_summary(None) == "Parity comparison was not run for this invocation.\n"


def test_probe_trace_compare_detects_lifecycle_and_timing_differences():
    reference = (
        probe_trace.ProbeTraceRecord(
            source="firmware",
            mode="runtime",
            wall_time="2026-01-01T00:00:00.000000Z",
            monotonic_s=1.0,
            sequence=1,
            request_index=1,
            request_id="alpha:1",
            thread_id="main",
            check_id="alpha",
            check_name="ALPHA",
            check_type="HTTP",
            target="http://a",
            probe_host_key="a",
            event="probe-start",
        ),
        probe_trace.ProbeTraceRecord(
            source="firmware",
            mode="runtime",
            wall_time="2026-01-01T00:00:00.100000Z",
            monotonic_s=1.1,
            sequence=2,
            request_index=1,
            request_id="alpha:1",
            thread_id="main",
            check_id="alpha",
            check_name="ALPHA",
            check_type="HTTP",
            target="http://a",
            probe_host_key="a",
            event="probe-end",
        ),
        probe_trace.ProbeTraceRecord(
            source="firmware",
            mode="runtime",
            wall_time="2026-01-01T00:00:00.200000Z",
            monotonic_s=1.2,
            sequence=3,
            request_index=1,
            request_id="beta:1",
            thread_id="main",
            check_id="beta",
            check_name="BETA",
            check_type="HTTP",
            target="http://b",
            probe_host_key="b",
            event="probe-start",
        ),
        probe_trace.ProbeTraceRecord(
            source="firmware",
            mode="runtime",
            wall_time="2026-01-01T00:00:00.300000Z",
            monotonic_s=1.3,
            sequence=4,
            request_index=1,
            request_id="beta:1",
            thread_id="main",
            check_id="beta",
            check_name="BETA",
            check_type="HTTP",
            target="http://b",
            probe_host_key="b",
            event="probe-end",
        ),
    )
    candidate = (
        probe_trace.ProbeTraceRecord(
            source="host",
            mode="local",
            wall_time="2026-01-01T00:00:00.000000Z",
            monotonic_s=1.0,
            sequence=1,
            request_index=1,
            request_id="alpha:1",
            thread_id="main",
            check_id="alpha",
            check_name="ALPHA",
            check_type="HTTP",
            target="http://a",
            probe_host_key="a",
            event="probe-start",
        ),
        probe_trace.ProbeTraceRecord(
            source="host",
            mode="local",
            wall_time="2026-01-01T00:00:00.300000Z",
            monotonic_s=1.3,
            sequence=2,
            request_index=1,
            request_id="alpha:1",
            thread_id="main",
            check_id="alpha",
            check_name="ALPHA",
            check_type="HTTP",
            target="http://a",
            probe_host_key="a",
            event="probe-error",
        ),
        probe_trace.ProbeTraceRecord(
            source="host",
            mode="local",
            wall_time="2026-01-01T00:00:00.500000Z",
            monotonic_s=1.5,
            sequence=3,
            request_index=1,
            request_id="beta:1",
            thread_id="main",
            check_id="beta",
            check_name="BETA",
            check_type="HTTP",
            target="http://b",
            probe_host_key="b",
            event="probe-start",
        ),
        probe_trace.ProbeTraceRecord(
            source="host",
            mode="local",
            wall_time="2026-01-01T00:00:00.600000Z",
            monotonic_s=1.6,
            sequence=4,
            request_index=1,
            request_id="beta:1",
            thread_id="main",
            check_id="beta",
            check_name="BETA",
            check_type="HTTP",
            target="http://b",
            probe_host_key="b",
            event="probe-end",
        ),
    )

    comparison = compare_probe_traces(reference, candidate, timing_tolerance_ratio=0.05)
    summary = probe_trace.render_parity_summary(comparison)

    assert comparison.lifecycle_match is False
    assert comparison.timing_within_tolerance is False
    assert comparison.lifecycle_differences
    assert "Lifecycle differences:" in summary


def test_probe_trace_thread_and_sequence_helpers_cover_remaining_paths(monkeypatch, tmp_path):
    class FakeLock:
        def __init__(self):
            self.acquired = 0
            self.released = 0

        def acquire(self):
            self.acquired += 1

        def release(self):
            self.released += 1

    fake_lock = FakeLock()

    class FakeThreadModule:
        @staticmethod
        def allocate_lock():
            return fake_lock

        @staticmethod
        def get_ident():
            raise RuntimeError("broken thread id")

    class FakeThreading:
        @staticmethod
        def get_ident():
            return 77

    monkeypatch.setattr(probe_trace, "threading", None)
    monkeypatch.setattr(probe_trace, "_thread", FakeThreadModule)
    assert probe_trace._allocate_lock() is fake_lock
    assert probe_trace._lock_context(fake_lock) is True
    assert fake_lock.acquired == 1

    monkeypatch.setattr(probe_trace, "threading", FakeThreading)
    assert probe_trace._current_thread_id() == "77"
    assert probe_trace._jsonable([b"abc", {1: "x"}]) == ["abc", {"1": "x"}]
    monkeypatch.setattr(probe_trace, "threading", None)

    definition = make_definition("alpha")
    records = []
    collector = ProbeTraceCollector(
        records.append,
        source="host",
        mode="local",
        wall_time_provider=lambda: 1.0,
        monotonic_time_provider=lambda: 10.0,
    )
    collector.lock = fake_lock

    collector.emit(definition, "probe-start", {"timeout_s": 10})
    collector.emit(definition, "socket-send", {"stage": "http-send", "bytes_sent": 10})
    collector.emit(definition, "probe-error", {"detail": "boom"})

    assert probe_trace._sequence_for(tuple(records), "socket-send") == ("alpha",)
    assert collector.request_context_by_thread == {}
    assert fake_lock.released >= 2

    path = tmp_path / "trace.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"trace_kind": "probe_transport", "request_id": "", "check_id": "alpha", "event": "probe-error", "raw_fields": []}),
                "{invalid}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_probe_trace_records(path)
    assert loaded[0].raw_fields == {}
    assert probe_trace._request_traces(()) == ()


def test_probe_trace_compare_skips_timing_when_probe_start_is_missing():
    reference = (
        probe_trace.ProbeTraceRecord(
            source="firmware",
            mode="runtime",
            wall_time="2026-01-01T00:00:00.000000Z",
            monotonic_s=1.0,
            sequence=1,
            request_index=1,
            request_id="alpha:1",
            thread_id="main",
            check_id="alpha",
            check_name="ALPHA",
            check_type="HTTP",
            target="http://a",
            probe_host_key="a",
            event="probe-error",
        ),
    )
    candidate = (
        probe_trace.ProbeTraceRecord(
            source="host",
            mode="local",
            wall_time="2026-01-01T00:00:00.000000Z",
            monotonic_s=2.0,
            sequence=1,
            request_index=1,
            request_id="alpha:1",
            thread_id="main",
            check_id="alpha",
            check_name="ALPHA",
            check_type="HTTP",
            target="http://a",
            probe_host_key="a",
            event="probe-error",
        ),
    )

    comparison = compare_probe_traces(reference, candidate)

    assert comparison.request_count_match is True
    assert comparison.max_timing_delta_ratio is None
    assert comparison.timing_within_tolerance is True
