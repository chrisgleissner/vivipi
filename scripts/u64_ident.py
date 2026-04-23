from __future__ import annotations

import json
import os
import socket
import time

from u64_connection_runtime import (
    ProbeExecutionContext,
    ProbeOutcome,
    ProbeSurface,
    RuntimeSettings,
    run_surface_operation,
    select_operation_index,
    surface_detail,
)


IDENT_PORT = 64
IDENT_TIMEOUT_S = 1.0


def ident_nonce() -> str:
    return f"vivipi-{os.getpid()}-{time.monotonic_ns()}"


def identify_json(settings: RuntimeSettings) -> str:
    nonce = ident_nonce()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(IDENT_TIMEOUT_S)
        sock.sendto(f"json{nonce}".encode("utf-8"), (settings.host, IDENT_PORT))
        payload, _address = sock.recvfrom(4096)
    finally:
        sock.close()
    try:
        response = json.loads(payload.decode("utf-8"))
    except Exception as error:
        raise RuntimeError(f"invalid ident JSON: {error}") from error
    if not isinstance(response, dict):
        raise RuntimeError("invalid ident payload")
    for key in ("product", "firmware_version", "hostname", "your_string"):
        value = response.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"missing ident field: {key}")
    if response["your_string"] != nonce:
        raise RuntimeError("ident echo mismatch")
    return f"product={response['product']} hostname={response['hostname']}"


def surface_operations(surface: ProbeSurface) -> tuple[tuple[str, callable], ...]:
    del surface
    return (("ident_json", identify_json),)


def run_probe(settings: RuntimeSettings, correctness, *, context: ProbeExecutionContext | None = None) -> ProbeOutcome:
    del correctness
    if context is not None:
        operations = surface_operations(context.surface)
        index = select_operation_index(context, len(operations))
        op_name, operation = operations[index]
        started_at = time.perf_counter_ns()
        try:
            detail = run_surface_operation("ident", operation, settings)
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("OK", surface_detail(context.surface, op_name, detail), elapsed_ms)
        except Exception as error:
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("FAIL", surface_detail(context.surface, op_name, str(error)), elapsed_ms)

    started_at = time.perf_counter_ns()
    try:
        detail = identify_json(settings)
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("OK", detail, elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("FAIL", f"ident failed: {error}", elapsed_ms)