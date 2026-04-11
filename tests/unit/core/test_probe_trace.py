from __future__ import annotations

import json

from vivipi.core.models import CheckDefinition, CheckType
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
