from __future__ import annotations

import sys

try:
    import traceback
except ImportError:  # pragma: no cover - MicroPython fallback
    traceback = None

from vivipi.core.logging import bound_text


_BOUND_APP = None


class _TraceWriter:
    def __init__(self):
        self._chunks: list[str] = []

    def write(self, value: str):
        self._chunks.append(str(value))

    def lines(self, line_limit: int, max_lines: int) -> tuple[str, ...]:
        text = "".join(self._chunks).strip()
        if not text:
            return ()
        return tuple(bound_text(line, line_limit) for line in text.splitlines()[:max_lines])


def format_exception_trace(exception: BaseException, line_limit: int = 96, max_lines: int = 6) -> tuple[str, ...]:
    writer = _TraceWriter()
    if hasattr(sys, "print_exception"):
        sys.print_exception(exception, writer)
        return writer.lines(line_limit, max_lines)

    if traceback is None:
        return writer.lines(line_limit, max_lines)

    traceback.print_exception(type(exception), exception, exception.__traceback__, file=writer)
    return writer.lines(line_limit, max_lines)


def make_error_record(
    scope: str,
    exception: BaseException,
    observed_at_s: float | None = None,
    identifier: str | None = None,
) -> dict[str, object]:
    return {
        "scope": scope,
        "identifier": identifier,
        "type": type(exception).__name__,
        "message": bound_text(str(exception) or type(exception).__name__, 96),
        "observed_at_s": observed_at_s,
        "trace": format_exception_trace(exception),
    }


def bind_app(app):
    global _BOUND_APP
    _BOUND_APP = app
    return app


def clear_bound_app():
    global _BOUND_APP
    _BOUND_APP = None


def get_app():
    if _BOUND_APP is None:
        raise RuntimeError("runtime app is not bound")
    return _BOUND_APP


def get_registered_checks() -> tuple[dict[str, object], ...]:
    return get_app().get_registered_checks()


def get_checks() -> tuple[dict[str, object], ...]:
    return get_app().get_checks_snapshot()


def get_failures() -> tuple[dict[str, object], ...]:
    return get_app().get_failures_snapshot()


def get_metrics() -> dict[str, object]:
    return get_app().get_metrics_snapshot()


def get_network_state() -> dict[str, object]:
    return get_app().get_network_state_snapshot()


def get_logs(limit: int | None = None) -> tuple[str, ...]:
    return get_app().get_logs(limit=limit)


def get_errors(limit: int | None = None) -> tuple[dict[str, object], ...]:
    return get_app().get_errors(limit=limit)


def snapshot() -> dict[str, object]:
    return get_app().snapshot()