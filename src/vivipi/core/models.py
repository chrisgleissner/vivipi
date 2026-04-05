from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CheckType(str, Enum):
    PING = "PING"
    REST = "REST"
    SERVICE = "SERVICE"


class Status(str, Enum):
    OK = "OK"
    DEG = "DEG"
    FAIL = "FAIL"
    UNKNOWN = "?"


class AppMode(str, Enum):
    OVERVIEW = "overview"
    DETAIL = "detail"
    DIAGNOSTICS = "diagnostics"


@dataclass(frozen=True)
class TransitionThresholds:
    failures_to_degraded: int = 1
    failures_to_failed: int = 2
    successes_to_recover: int = 1

    def __post_init__(self):
        if self.failures_to_degraded < 1:
            raise ValueError("failures_to_degraded must be at least 1")
        if self.failures_to_failed < self.failures_to_degraded:
            raise ValueError("failures_to_failed must not be less than failures_to_degraded")
        if self.successes_to_recover < 1:
            raise ValueError("successes_to_recover must be at least 1")


@dataclass(frozen=True)
class CheckDefinition:
    identifier: str
    name: str
    check_type: CheckType
    target: str
    interval_s: int = 15
    timeout_s: int = 10
    method: str = "GET"
    service_prefix: str | None = None


@dataclass(frozen=True)
class CheckObservation:
    identifier: str
    name: str
    status: Status
    details: str = ""
    latency_ms: float | None = None
    observed_at_s: float | None = None
    source_identifier: str | None = None


@dataclass(frozen=True)
class CheckRuntime:
    identifier: str
    name: str
    status: Status = Status.UNKNOWN
    details: str = ""
    latency_ms: float | None = None
    last_update_s: float | None = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    source_identifier: str | None = None


@dataclass(frozen=True)
class DiagnosticEvent:
    code: str
    message: str
    observed_at_s: float | None = None
    source_identifier: str | None = None

    def __post_init__(self):
        if not self.code.strip():
            raise ValueError("diagnostic code must be a non-empty string")
        if not self.message.strip():
            raise ValueError("diagnostic message must be a non-empty string")


@dataclass(frozen=True)
class AppState:
    checks: tuple[CheckRuntime, ...] = field(default_factory=tuple)
    selected_id: str | None = None
    mode: AppMode = AppMode.OVERVIEW
    page_size: int = 8
    shift_offset: tuple[int, int] = (0, 0)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
