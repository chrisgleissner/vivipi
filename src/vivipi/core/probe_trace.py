from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
try:
    import threading
except ImportError:  # pragma: no cover - MicroPython fallback
    threading = None
try:
    import _thread
except ImportError:  # pragma: no cover - CPython fallback
    _thread = None
import time

from vivipi.core.models import CheckDefinition
from vivipi.core.scheduler import probe_host_key


def _isoformat_utc(value_s: float) -> str:
    return datetime.fromtimestamp(value_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _allocate_lock():
    if threading is not None:
        return threading.Lock()
    if _thread is not None:
        return _thread.allocate_lock()
    return None


def _lock_context(lock):
    if lock is None:
        return False
    lock.acquire()
    return True


def _current_thread_id() -> str:
    if _thread is not None and hasattr(_thread, "get_ident"):
        try:
            return str(_thread.get_ident())
        except Exception:
            pass
    if threading is not None:
        try:
            return str(threading.get_ident())
        except Exception:
            pass
    return "main"


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _sequence_for(records: tuple["ProbeTraceRecord", ...], event_name: str) -> tuple[str, ...]:
    return tuple(record.check_id for record in records if record.event == event_name)


@dataclass(frozen=True)
class ProbeTraceRecord:
    source: str
    mode: str
    wall_time: str
    monotonic_s: float
    sequence: int
    request_index: int
    request_id: str
    thread_id: str
    check_id: str
    check_name: str
    check_type: str
    target: str
    probe_host_key: str | None
    event: str
    stage: str | None = None
    status: str | None = None
    detail: str | None = None
    latency_ms: float | None = None
    timeout_s: int | None = None
    socket_target: str | None = None
    dns_host: str | None = None
    dns_port: int | None = None
    bytes_sent: int | None = None
    bytes_received: int | None = None
    remain_ms: int | None = None
    socket_reused: bool | None = None
    addresses: tuple[str, ...] = field(default_factory=tuple)
    raw_fields: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "trace_kind": "probe_transport",
            "source": self.source,
            "mode": self.mode,
            "wall_time": self.wall_time,
            "monotonic_s": round(self.monotonic_s, 6),
            "sequence": self.sequence,
            "request_index": self.request_index,
            "request_id": self.request_id,
            "thread_id": self.thread_id,
            "check_id": self.check_id,
            "check_name": self.check_name,
            "check_type": self.check_type,
            "target": self.target,
            "probe_host_key": self.probe_host_key,
            "event": self.event,
            "stage": self.stage,
            "status": self.status,
            "detail": self.detail,
            "latency_ms": round(self.latency_ms, 3) if self.latency_ms is not None else None,
            "timeout_s": self.timeout_s,
            "socket_target": self.socket_target,
            "dns_host": self.dns_host,
            "dns_port": self.dns_port,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "remain_ms": self.remain_ms,
            "socket_reused": self.socket_reused,
            "addresses": list(self.addresses),
            "raw_fields": _jsonable(self.raw_fields),
        }


@dataclass(frozen=True)
class RequestTrace:
    request_id: str
    check_id: str
    start_offset_s: float | None
    lifecycle: tuple[str, ...]


@dataclass(frozen=True)
class ParityComparison:
    ordering_match: bool
    lifecycle_match: bool
    timing_within_tolerance: bool
    request_count_match: bool
    timing_tolerance_ratio: float
    max_timing_delta_ratio: float | None
    reference_request_count: int
    candidate_request_count: int
    reference_order: tuple[str, ...]
    candidate_order: tuple[str, ...]
    lifecycle_differences: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "ordering_match": self.ordering_match,
            "lifecycle_match": self.lifecycle_match,
            "timing_within_tolerance": self.timing_within_tolerance,
            "request_count_match": self.request_count_match,
            "timing_tolerance_ratio": self.timing_tolerance_ratio,
            "max_timing_delta_ratio": self.max_timing_delta_ratio,
            "reference_request_count": self.reference_request_count,
            "candidate_request_count": self.candidate_request_count,
            "reference_order": list(self.reference_order),
            "candidate_order": list(self.candidate_order),
            "lifecycle_differences": list(self.lifecycle_differences),
        }


class ProbeTraceCollector:
    def __init__(
        self,
        sink,
        *,
        source: str,
        mode: str,
        wall_time_provider=None,
        monotonic_time_provider=None,
    ):
        self.sink = sink
        self.source = source
        self.mode = mode
        self.wall_time_provider = wall_time_provider or time.time
        self.monotonic_time_provider = monotonic_time_provider or time.perf_counter
        self.lock = _allocate_lock()
        self.sequence = 0
        self.request_index_by_check: dict[str, int] = defaultdict(int)
        self.request_context_by_thread: dict[str, tuple[str, int]] = {}
        self.records: list[ProbeTraceRecord] = []

    def emit(self, definition: CheckDefinition, event: str, fields: dict[str, object] | None = None):
        payload = dict(fields or {})
        thread_id = _current_thread_id()
        lock_acquired = _lock_context(self.lock)
        try:
            request_id, request_index = self.request_context_by_thread.get(thread_id, ("", 0))
            if event == "probe-start" or not request_id:
                self.request_index_by_check[definition.identifier] += 1
                request_index = self.request_index_by_check[definition.identifier]
                request_id = f"{definition.identifier}:{request_index}"
                self.request_context_by_thread[thread_id] = (request_id, request_index)
            self.sequence += 1
            sequence = self.sequence
        finally:
            if lock_acquired:
                self.lock.release()

        record = ProbeTraceRecord(
            source=self.source,
            mode=self.mode,
            wall_time=_isoformat_utc(float(self.wall_time_provider())),
            monotonic_s=float(self.monotonic_time_provider()),
            sequence=sequence,
            request_index=request_index,
            request_id=request_id,
            thread_id=thread_id,
            check_id=definition.identifier,
            check_name=definition.name,
            check_type=getattr(definition.check_type, "value", definition.check_type),
            target=definition.target,
            probe_host_key=probe_host_key(definition),
            event=event,
            stage=str(payload.get("stage")) if payload.get("stage") is not None else None,
            status=str(payload.get("status")) if payload.get("status") is not None else None,
            detail=str(payload.get("detail")) if payload.get("detail") is not None else None,
            latency_ms=float(payload["latency_ms"]) if payload.get("latency_ms") is not None else None,
            timeout_s=int(payload["timeout_s"]) if payload.get("timeout_s") is not None else None,
            socket_target=str(payload.get("target")) if payload.get("target") is not None else None,
            dns_host=str(payload.get("host")) if payload.get("host") is not None else None,
            dns_port=int(payload["port"]) if payload.get("port") is not None else None,
            bytes_sent=int(payload["bytes_sent"]) if payload.get("bytes_sent") is not None else None,
            bytes_received=int(payload["bytes_received"]) if payload.get("bytes_received") is not None else None,
            remain_ms=int(payload["remain_ms"]) if payload.get("remain_ms") is not None else None,
            socket_reused=bool(payload["socket_reused"]) if payload.get("socket_reused") is not None else None,
            addresses=tuple(str(item) for item in payload.get("addresses", ()) or ()),
            raw_fields={key: _jsonable(value) for key, value in payload.items()},
        )

        lock_acquired = _lock_context(self.lock)
        try:
            self.records.append(record)
            if event in {"probe-end", "probe-error"}:
                self.request_context_by_thread.pop(thread_id, None)
        finally:
            if lock_acquired:
                self.lock.release()

        self.sink(record)


class ProbeTraceJsonlWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("w", encoding="utf-8")

    def write(self, record: ProbeTraceRecord):
        self.handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
        self.handle.flush()

    def close(self):
        self.handle.flush()
        self.handle.close()


def load_probe_trace_records(path: str | Path) -> tuple[ProbeTraceRecord, ...]:
    records: list[ProbeTraceRecord] = []
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if not isinstance(payload, dict) or payload.get("trace_kind") != "probe_transport":
                continue
            records.append(
                ProbeTraceRecord(
                    source=str(payload.get("source", "unknown")),
                    mode=str(payload.get("mode", "")),
                    wall_time=str(payload.get("wall_time", "")),
                    monotonic_s=float(payload.get("monotonic_s", 0.0)),
                    sequence=int(payload.get("sequence", 0)),
                    request_index=int(payload.get("request_index", 0)),
                    request_id=str(payload.get("request_id", "")),
                    thread_id=str(payload.get("thread_id", "")),
                    check_id=str(payload.get("check_id", "")),
                    check_name=str(payload.get("check_name", "")),
                    check_type=str(payload.get("check_type", "")),
                    target=str(payload.get("target", "")),
                    probe_host_key=str(payload.get("probe_host_key")) if payload.get("probe_host_key") is not None else None,
                    event=str(payload.get("event", "")),
                    stage=str(payload.get("stage")) if payload.get("stage") is not None else None,
                    status=str(payload.get("status")) if payload.get("status") is not None else None,
                    detail=str(payload.get("detail")) if payload.get("detail") is not None else None,
                    latency_ms=float(payload["latency_ms"]) if payload.get("latency_ms") is not None else None,
                    timeout_s=int(payload["timeout_s"]) if payload.get("timeout_s") is not None else None,
                    socket_target=str(payload.get("socket_target")) if payload.get("socket_target") is not None else None,
                    dns_host=str(payload.get("dns_host")) if payload.get("dns_host") is not None else None,
                    dns_port=int(payload["dns_port"]) if payload.get("dns_port") is not None else None,
                    bytes_sent=int(payload["bytes_sent"]) if payload.get("bytes_sent") is not None else None,
                    bytes_received=int(payload["bytes_received"]) if payload.get("bytes_received") is not None else None,
                    remain_ms=int(payload["remain_ms"]) if payload.get("remain_ms") is not None else None,
                    socket_reused=bool(payload["socket_reused"]) if payload.get("socket_reused") is not None else None,
                    addresses=tuple(str(item) for item in payload.get("addresses", ()) or ()),
                    raw_fields=dict(payload.get("raw_fields", {})) if isinstance(payload.get("raw_fields"), dict) else {},
                )
            )
    return tuple(records)


def _request_traces(records: tuple[ProbeTraceRecord, ...]) -> tuple[RequestTrace, ...]:
    grouped: dict[str, list[ProbeTraceRecord]] = defaultdict(list)
    for record in records:
        grouped[record.request_id].append(record)
    ordered_groups = sorted(grouped.values(), key=lambda items: min(item.sequence for item in items))
    if not ordered_groups:
        return ()
    base_time = min(items[0].monotonic_s for items in ordered_groups)
    requests: list[RequestTrace] = []
    for items in ordered_groups:
        ordered = tuple(sorted(items, key=lambda item: item.sequence))
        start_offset_s = None
        for item in ordered:
            if item.event == "probe-start":
                start_offset_s = item.monotonic_s - base_time
                break
        requests.append(
            RequestTrace(
                request_id=ordered[0].request_id,
                check_id=ordered[0].check_id,
                start_offset_s=start_offset_s,
                lifecycle=tuple(item.event for item in ordered),
            )
        )
    return tuple(requests)


def compare_probe_traces(
    reference: tuple[ProbeTraceRecord, ...],
    candidate: tuple[ProbeTraceRecord, ...],
    *,
    timing_tolerance_ratio: float = 0.05,
) -> ParityComparison:
    reference_requests = _request_traces(reference)
    candidate_requests = _request_traces(candidate)
    reference_order = tuple(item.check_id for item in reference_requests)
    candidate_order = tuple(item.check_id for item in candidate_requests)
    ordering_match = reference_order == candidate_order
    request_count_match = len(reference_requests) == len(candidate_requests)

    lifecycle_differences: list[str] = []
    max_timing_delta_ratio = None
    lifecycle_match = True
    timing_within_tolerance = True
    for index, (reference_request, candidate_request) in enumerate(zip(reference_requests, candidate_requests), start=1):
        if reference_request.lifecycle != candidate_request.lifecycle:
            lifecycle_match = False
            lifecycle_differences.append(
                f"request {index} {reference_request.check_id}: {reference_request.lifecycle} != {candidate_request.lifecycle}"
            )
        if reference_request.start_offset_s is None or candidate_request.start_offset_s is None:
            continue
        baseline = max(reference_request.start_offset_s, 0.001)
        delta_ratio = abs(candidate_request.start_offset_s - reference_request.start_offset_s) / baseline
        if max_timing_delta_ratio is None or delta_ratio > max_timing_delta_ratio:
            max_timing_delta_ratio = delta_ratio
        if delta_ratio > timing_tolerance_ratio:
            timing_within_tolerance = False

    return ParityComparison(
        ordering_match=ordering_match,
        lifecycle_match=lifecycle_match,
        timing_within_tolerance=timing_within_tolerance,
        request_count_match=request_count_match,
        timing_tolerance_ratio=timing_tolerance_ratio,
        max_timing_delta_ratio=max_timing_delta_ratio,
        reference_request_count=len(reference_requests),
        candidate_request_count=len(candidate_requests),
        reference_order=reference_order,
        candidate_order=candidate_order,
        lifecycle_differences=tuple(lifecycle_differences),
    )


def render_parity_summary(comparison: ParityComparison | None) -> str:
    if comparison is None:
        return "Parity comparison was not run for this invocation.\n"
    lines = [
        f"Request count match: {comparison.request_count_match}",
        f"Ordering match: {comparison.ordering_match}",
        f"Lifecycle match: {comparison.lifecycle_match}",
        f"Timing within tolerance: {comparison.timing_within_tolerance}",
        f"Timing tolerance ratio: {comparison.timing_tolerance_ratio:.3f}",
        f"Max timing delta ratio: {comparison.max_timing_delta_ratio if comparison.max_timing_delta_ratio is not None else '-'}",
    ]
    if comparison.lifecycle_differences:
        lines.append("Lifecycle differences:")
        lines.extend(f"- {item}" for item in comparison.lifecycle_differences)
    return "\n".join(lines) + "\n"
