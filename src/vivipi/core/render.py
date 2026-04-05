from __future__ import annotations

from dataclasses import dataclass

from vivipi.core.models import AppMode, AppState, CheckRuntime, DisplayMode, Status
from vivipi.core.state import normalize_selection, overview_checks, selected_check, visible_checks
from vivipi.core.text import center_text, column_widths, compact_overview_cell, overview_row, truncate_text


@dataclass(frozen=True)
class InvertedSpan:
    row_index: int
    start_column: int
    end_column: int


@dataclass(frozen=True)
class Frame:
    rows: tuple[str, ...]
    inverted_row: int | None = None
    shift_offset: tuple[int, int] = (0, 0)
    inverted_spans: tuple[InvertedSpan, ...] = ()


def _blank_rows(row_width: int, page_size: int) -> list[str]:
    return [" " * row_width for _ in range(page_size)]


def _fixed_width_row(value: str, row_width: int) -> str:
    return truncate_text(value, row_width).ljust(row_width)


def _detail_rows(check: CheckRuntime | None, now_s: float | None, row_width: int, page_size: int) -> tuple[str, ...]:
    if check is None:
        return tuple(_blank_rows(row_width, page_size))

    rows = [_fixed_width_row(check.name, row_width)]
    rows.append(_fixed_width_row(f"STATUS: {check.status.value}", row_width))

    if check.latency_ms is not None:
        rows.append(_fixed_width_row(f"LAT: {int(check.latency_ms)}ms", row_width))

    if check.last_update_s is not None and now_s is not None:
        age_s = max(0, int(now_s - check.last_update_s))
        rows.append(_fixed_width_row(f"AGE: {age_s}s", row_width))

    if check.details:
        rows.append(_fixed_width_row(check.details, row_width))

    while len(rows) < page_size:
        rows.append(" " * row_width)

    return tuple(rows[:page_size])


def _diagnostic_rows(lines: tuple[str, ...], row_width: int, page_size: int) -> tuple[str, ...]:
    rows = [_fixed_width_row(line, row_width) for line in lines[:page_size]]
    while len(rows) < page_size:
        rows.append(" " * row_width)
    return tuple(rows)


def _about_rows(state: AppState, row_width: int, page_size: int) -> tuple[str, ...]:
    rows: list[str] = []
    rows.append(center_text("ViviPi", row_width))
    if state.version:
        rows.append(_fixed_width_row(f"VER: {state.version}", row_width))
    if state.build_time:
        rows.append(_fixed_width_row(f"BLD: {state.build_time}", row_width))
    while len(rows) < page_size:
        rows.append(" " * row_width)
    return tuple(rows[:page_size])


def _legacy_overview_frame(state: AppState, checks: tuple[CheckRuntime, ...]) -> Frame:
    rows = _blank_rows(state.row_width, state.page_size)
    selected_index = None
    selected_id = normalize_selection(state.checks, state.selected_id, overview_checks(state))
    for row_index, check in enumerate(checks):
        rows[row_index] = overview_row(check.name, check.status.value, state.row_width)
        if check.identifier == selected_id:
            selected_index = row_index
    return Frame(rows=tuple(rows), inverted_row=selected_index, shift_offset=state.shift_offset)


def _compact_overview_frame(state: AppState, checks: tuple[CheckRuntime, ...]) -> Frame:
    rows = _blank_rows(state.row_width, state.page_size)
    separator = state.column_separator
    widths = column_widths(state.row_width, state.overview_columns, separator_width=len(separator))
    spans: list[InvertedSpan] = []

    for row_index in range(state.page_size):
        start = row_index * state.overview_columns
        row_checks = checks[start : start + state.overview_columns]
        parts: list[str] = []
        cursor = 0

        for column_index, width in enumerate(widths):
            check = row_checks[column_index] if column_index < len(row_checks) else None
            if check is None:
                display_text = ""
                cell = " " * width
            else:
                display_text = compact_overview_cell(check.name, check.status.value, width)
                cell = display_text.ljust(width)
                if display_text and check.status == Status.FAIL:
                    spans.append(
                        InvertedSpan(
                            row_index=row_index,
                            start_column=cursor,
                            end_column=cursor + len(display_text),
                        )
                    )

            parts.append(cell)
            cursor += width
            if column_index < state.overview_columns - 1:
                parts.append(separator)
                cursor += len(separator)

        rows[row_index] = "".join(parts)

    return Frame(rows=tuple(rows), shift_offset=state.shift_offset, inverted_spans=tuple(spans))


def _overview_frame(state: AppState) -> Frame:
    checks = visible_checks(state)
    if not checks:
        rows = _blank_rows(state.row_width, state.page_size)
        idle_row = (state.page_size - 1) // 2
        rows[idle_row] = center_text("IDLE", state.row_width)
        return Frame(rows=tuple(rows), shift_offset=state.shift_offset)

    if state.display_mode == DisplayMode.STANDARD and state.overview_columns == 1:
        return _legacy_overview_frame(state, checks)

    return _compact_overview_frame(state, checks)


def render_frame(state: AppState, now_s: float | None = None) -> Frame:
    if state.mode == AppMode.DETAIL:
        return Frame(
            rows=_detail_rows(selected_check(state), now_s, state.row_width, state.page_size),
            shift_offset=state.shift_offset,
        )
    if state.mode == AppMode.DIAGNOSTICS:
        return Frame(
            rows=_diagnostic_rows(state.diagnostics, state.row_width, state.page_size),
            shift_offset=state.shift_offset,
        )
    if state.mode == AppMode.ABOUT:
        return Frame(
            rows=_about_rows(state, state.row_width, state.page_size),
            shift_offset=state.shift_offset,
        )
    return _overview_frame(state)
