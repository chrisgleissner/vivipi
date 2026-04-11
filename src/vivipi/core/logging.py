from __future__ import annotations

from enum import IntEnum

from vivipi.core.ring_buffer import RingBuffer


DEFAULT_LOG_BUFFER_CAPACITY = 32
DEFAULT_LOG_LINE_LIMIT = 96
DEFAULT_LOG_FIELD_LIMIT = 4
DEFAULT_COMPONENT_LIMIT = 8
DEFAULT_MESSAGE_LIMIT = 20
DEFAULT_FIELD_LIMIT = 24


class LogLevel(IntEnum):
    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40


def parse_log_level(value: LogLevel | str | int) -> LogLevel:
    if isinstance(value, LogLevel):
        return value
    if isinstance(value, int):
        return LogLevel(value)
    return LogLevel[str(value).strip().upper()]


def bound_text(value: object, limit: int) -> str:
    if limit < 1:
        raise ValueError("limit must be positive")
    normalized = " ".join(str(value).split()).strip()
    if len(normalized) <= limit:
        return normalized
    if limit == 1:
        return normalized[:1]
    return normalized[: limit - 1] + "…"


def log_field(key: str, value: object, value_limit: int = DEFAULT_FIELD_LIMIT) -> str:
    normalized_key = bound_text(key, 12) or "field"
    normalized_value = bound_text(value, value_limit) or "-"
    return f"{normalized_key}={normalized_value}"


def _hard_limit(value: object, limit: int) -> str:
    normalized = " ".join(str(value).split()).strip()
    return normalized[:limit]


def format_log_line(
    level: LogLevel | str | int,
    component: str,
    message: str,
    fields: tuple[str, ...] = (),
    line_limit: int = DEFAULT_LOG_LINE_LIMIT,
    field_limit: int = DEFAULT_LOG_FIELD_LIMIT,
) -> str:
    normalized_level = parse_log_level(level)
    normalized_component = _hard_limit(component.upper(), DEFAULT_COMPONENT_LIMIT) or "CORE"
    normalized_message = bound_text(message, DEFAULT_MESSAGE_LIMIT) or "event"

    level_name = getattr(normalized_level, "_name_", str(normalized_level))
    parts = [f"[{level_name}][{normalized_component}] {normalized_message}"]
    for field in fields[:field_limit]:
        if field:
            parts.append(bound_text(field, DEFAULT_FIELD_LIMIT))

    return bound_text(" ".join(parts), line_limit)


class StructuredLogger:
    def __init__(
        self,
        level: LogLevel | str | int = LogLevel.INFO,
        buffer: RingBuffer | None = None,
        line_limit: int = DEFAULT_LOG_LINE_LIMIT,
        field_limit: int = DEFAULT_LOG_FIELD_LIMIT,
        sink=None,
    ):
        self.level = parse_log_level(level)
        self.buffer = buffer if buffer is not None else RingBuffer(capacity=DEFAULT_LOG_BUFFER_CAPACITY)
        self.line_limit = line_limit
        self.field_limit = field_limit
        self.sink = sink

    def is_enabled(self, level: LogLevel | str | int) -> bool:
        return int(parse_log_level(level)) >= int(self.level)

    def set_level(self, level: LogLevel | str | int) -> LogLevel:
        self.level = parse_log_level(level)
        return self.level

    def emit(self, level: LogLevel | str | int, component: str, message: str, fields: tuple[str, ...] = ()) -> str | None:
        if not self.is_enabled(level):
            return None
        line = format_log_line(
            level,
            component,
            message,
            fields=fields,
            line_limit=self.line_limit,
            field_limit=self.field_limit,
        )
        self.buffer.append(line)
        if self.sink is not None:
            self.sink(line)
        return line

    def debug(self, component: str, message: str, fields: tuple[str, ...] = ()) -> str | None:
        return self.emit(LogLevel.DEBUG, component, message, fields)

    def info(self, component: str, message: str, fields: tuple[str, ...] = ()) -> str | None:
        return self.emit(LogLevel.INFO, component, message, fields)

    def warn(self, component: str, message: str, fields: tuple[str, ...] = ()) -> str | None:
        return self.emit(LogLevel.WARN, component, message, fields)

    def error(self, component: str, message: str, fields: tuple[str, ...] = ()) -> str | None:
        return self.emit(LogLevel.ERROR, component, message, fields)

    def dump(self, limit: int | None = None) -> tuple[str, ...]:
        return tuple(self.buffer.items(limit=limit))

    def clear(self):
        self.buffer.clear()