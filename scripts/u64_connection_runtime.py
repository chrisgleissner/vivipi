from __future__ import annotations

import enum
import http.client
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable


class ProbeCorrectness(enum.StrEnum):
    CORRECT = "correct"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


class ProbeSurface(enum.StrEnum):
    SMOKE = "smoke"
    READ = "read"
    READWRITE = "readwrite"


@dataclass(frozen=True)
class RuntimeSettings:
    host: str
    http_path: str
    http_port: int
    telnet_port: int
    ftp_port: int
    ftp_user: str
    ftp_pass: str
    delay_ms: int
    log_every: int
    verbose: bool


@dataclass(frozen=True)
class ProbeOutcome:
    result: str
    detail: str
    elapsed_ms: float


@dataclass(frozen=True)
class ProbeExecutionContext:
    protocol: str
    runner_id: int
    iteration: int
    surface: ProbeSurface
    state: Any | None = None


Operation = Callable[[RuntimeSettings], str]
SURFACE_OPERATION_RETRY_DELAYS_S = (0.05, 0.10, 0.20)


def first_non_empty_line(text: str, fallback: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), fallback)


def surface_detail(surface: ProbeSurface, op_name: str, detail: str) -> str:
    if detail:
        return f"surface={surface.value} op={op_name} {detail}"
    return f"surface={surface.value} op={op_name}"


def select_operation_index(context: ProbeExecutionContext | None, operation_count: int) -> int:
    if operation_count < 1:
        raise ValueError("operation_count must be >= 1")
    if context is None or context.state is None:
        return 0
    return context.state.next_probe_operation_index(
        context.protocol,
        context.runner_id,
        context.surface,
        operation_count,
    )


def is_retryable_surface_error(error: Exception) -> bool:
    if isinstance(error, (ConnectionResetError, BrokenPipeError, TimeoutError, socket.timeout)):
        return True
    if isinstance(error, (http.client.IncompleteRead, http.client.RemoteDisconnected, http.client.ResponseNotReady)):
        return True
    if isinstance(error, OSError) and getattr(error, "errno", None) in {104, 110, 111}:
        return True
    if isinstance(error, RuntimeError):
        detail = str(error).lower()
        return (
            "empty telnet text" in detail
            or "timed out" in detail
            or "missing audio mixer write value" in detail
            or "missing telnet text" in detail
            or "verification mismatch" in detail
        )
    return False


def is_expected_incomplete_disconnect(error: Exception) -> bool:
    if isinstance(error, (ConnectionResetError, BrokenPipeError)):
        return True
    if isinstance(error, OSError) and getattr(error, "errno", None) == 104:
        return True
    detail = str(error).lower()
    return "connection reset by peer" in detail or "broken pipe" in detail


def run_surface_operation(
    protocol: str,
    operation: Operation,
    settings: RuntimeSettings,
    *,
    on_error: Callable[[Exception], None] | None = None,
) -> str:
    attempts = len(SURFACE_OPERATION_RETRY_DELAYS_S) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation(settings)
        except Exception as error:
            last_error = error
            if on_error is not None:
                on_error(error)
            if not is_retryable_surface_error(error) or attempt + 1 >= attempts:
                raise
            time.sleep(SURFACE_OPERATION_RETRY_DELAYS_S[attempt])
    raise RuntimeError(f"{protocol} surface operation failed without error") from last_error


def run_incomplete_surface_operation(
    protocol: str,
    surface: ProbeSurface,
    op_name: str,
    operation: Operation,
    settings: RuntimeSettings,
) -> ProbeOutcome:
    started_at = time.perf_counter_ns()
    try:
        detail = run_surface_operation(protocol, operation, settings)
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("OK", surface_detail(surface, op_name, detail), elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        if is_expected_incomplete_disconnect(error):
            return ProbeOutcome("OK", surface_detail(surface, op_name, "expected_disconnect_after_abort"), elapsed_ms)
        return ProbeOutcome("FAIL", surface_detail(surface, op_name, str(error)), elapsed_ms)
