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


class DisplayMode(str, Enum):
    STANDARD = "standard"
    COMPACT = "compact"


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
    display_mode: DisplayMode = DisplayMode.STANDARD
    overview_columns: int = 1
    column_separator: str = " "
    row_width: int = 16
    page_size: int = 8
    page_index: int = 0
    shift_offset: tuple[int, int] = (0, 0)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if self.row_width < 1:
            raise ValueError("row_width must be positive")
        if self.overview_columns < 1 or self.overview_columns > 4:
            raise ValueError("overview_columns must be between 1 and 4")
        if len(self.column_separator) != 1:
            raise ValueError("column_separator must be exactly one character")
        if self.page_size < 1:
            raise ValueError("page_size must be positive")
        if self.page_index < 0:
            raise ValueError("page_index must not be negative")
        minimum_width = self.overview_columns + ((self.overview_columns - 1) * len(self.column_separator))
        if self.row_width < minimum_width:
            raise ValueError("row_width is too small for the configured overview columns")
