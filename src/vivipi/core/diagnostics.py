from __future__ import annotations

from vivipi.core.models import DiagnosticEvent
from vivipi.core.text import truncate_text


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).strip()


def format_diagnostic_line(event: DiagnosticEvent, width: int = 16) -> str:
    message = _normalize_text(event.message)
    return truncate_text(f"{event.code.strip().upper()} {message}".strip(), width)


def append_diagnostic_lines(
    lines: tuple[str, ...],
    events: tuple[DiagnosticEvent, ...],
    limit: int = 8,
    width: int = 16,
) -> tuple[str, ...]:
    updated = list(lines)
    for event in events:
        line = format_diagnostic_line(event, width=width)
        if line in updated:
            updated.remove(line)
        updated.append(line)
    return tuple(updated[-limit:])