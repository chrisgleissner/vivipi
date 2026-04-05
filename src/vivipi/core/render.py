from __future__ import annotations

from dataclasses import dataclass

from vivipi.core.models import AppMode, AppState, CheckRuntime
from vivipi.core.state import normalize_selection, selected_check, visible_checks
from vivipi.core.text import center_text, overview_row, truncate_text


DISPLAY_HEIGHT = 8
DISPLAY_WIDTH = 16


@dataclass(frozen=True)
class Frame:
    rows: tuple[str, ...]
    inverted_row: int | None = None
    shift_offset: tuple[int, int] = (0, 0)


def _blank_rows() -> list[str]:
    return [" " * DISPLAY_WIDTH for _ in range(DISPLAY_HEIGHT)]


def _fixed_width_row(value: str) -> str:
    return truncate_text(value, DISPLAY_WIDTH).ljust(DISPLAY_WIDTH)


def _detail_rows(check: CheckRuntime | None, now_s: float | None) -> tuple[str, ...]:
    if check is None:
        return tuple(_blank_rows())

    rows = [_fixed_width_row(check.name)]
    rows.append(_fixed_width_row(f"STATUS: {check.status.value}"))

    if check.latency_ms is not None:
        rows.append(_fixed_width_row(f"LAT: {int(check.latency_ms)}ms"))

    if check.last_update_s is not None and now_s is not None:
        age_s = max(0, int(now_s - check.last_update_s))
        rows.append(_fixed_width_row(f"AGE: {age_s}s"))

    if check.details:
        rows.append(_fixed_width_row(check.details))

    while len(rows) < DISPLAY_HEIGHT:
        rows.append(" " * DISPLAY_WIDTH)

    return tuple(rows[:DISPLAY_HEIGHT])


def _diagnostic_rows(lines: tuple[str, ...]) -> tuple[str, ...]:
    rows = [_fixed_width_row(line) for line in lines[:DISPLAY_HEIGHT]]
    while len(rows) < DISPLAY_HEIGHT:
        rows.append(" " * DISPLAY_WIDTH)
    return tuple(rows)


def _overview_frame(state: AppState) -> Frame:
    rows = _blank_rows()
    checks = visible_checks(state)
    if not checks:
        rows[3] = center_text("IDLE", DISPLAY_WIDTH)
        return Frame(rows=tuple(rows), shift_offset=state.shift_offset)

    selected_index = None
    selected_id = normalize_selection(state.checks, state.selected_id)
    for row_index, check in enumerate(checks):
        rows[row_index] = overview_row(check.name, check.status.value, DISPLAY_WIDTH)
        if check.identifier == selected_id:
            selected_index = row_index

    return Frame(rows=tuple(rows), inverted_row=selected_index, shift_offset=state.shift_offset)


def render_frame(state: AppState, now_s: float | None = None) -> Frame:
    if state.mode == AppMode.DETAIL:
        return Frame(rows=_detail_rows(selected_check(state), now_s), shift_offset=state.shift_offset)
    if state.mode == AppMode.DIAGNOSTICS:
        return Frame(rows=_diagnostic_rows(state.diagnostics), shift_offset=state.shift_offset)
    return _overview_frame(state)
