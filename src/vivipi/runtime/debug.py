from __future__ import annotations

import gc

from vivipi.runtime import state as runtime_state
from vivipi.runtime.metrics import elapsed_ms, start_timer


def capture_memory_snapshot(label: str = "manual", observed_at_s: float | None = None) -> dict[str, object]:
    free_bytes = int(gc.mem_free()) if hasattr(gc, "mem_free") else None
    allocated_bytes = int(gc.mem_alloc()) if hasattr(gc, "mem_alloc") else None
    gc_counts = tuple(gc.get_count()) if hasattr(gc, "get_count") else ()
    return {
        "label": label,
        "observed_at_s": observed_at_s,
        "free_bytes": free_bytes,
        "allocated_bytes": allocated_bytes,
        "gc_counts": gc_counts,
    }


def mem() -> dict[str, object]:
    app = runtime_state.get_app()
    snapshot = capture_memory_snapshot(label="manual", observed_at_s=app.current_time_s())
    app.metrics.record_memory(snapshot)
    return snapshot


def collect() -> dict[str, object]:
    app = runtime_state.get_app()
    started_at, timer_kind = start_timer()
    gc.collect()
    duration_ms = elapsed_ms(started_at, timer_kind)
    app.metrics.record_gc(duration_ms)
    snapshot = capture_memory_snapshot(label="gc", observed_at_s=app.current_time_s())
    app.metrics.record_memory(snapshot)
    return {"duration_ms": duration_ms, "snapshot": snapshot}