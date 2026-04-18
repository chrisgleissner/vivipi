from __future__ import annotations

from dataclasses import dataclass

from vivipi.core.models import AppMode, AppState, CheckRuntime, DisplayMode, Status
from vivipi.core.state import selected_check, visible_checks
from vivipi.core.text import center_text, column_widths, compact_overview_cell, overview_row, truncate_text


def _enum_text(value) -> str:
    return str(getattr(value, "value", value))


@dataclass(frozen=True)
class TextSpan:
    row_index: int
    start_column: int
    end_column: int


InvertedSpan = TextSpan


@dataclass(frozen=True)
class Frame:
    rows: tuple[str, ...]
    inverted_row: int | None = None
    shift_offset: tuple[int, int] = (0, 0)
    inverted_spans: tuple[InvertedSpan, ...] = ()
    failure_spans: tuple[TextSpan, ...] = ()


def _blank_rows(row_width: int, page_size: int) -> list[str]:
    return [" " * row_width for _ in range(page_size)]


def _pad_right(value: str, width: int) -> str:
    if width <= len(value):
        return value[:width]
    return value + (" " * (width - len(value)))


def _fixed_width_row(value: str, row_width: int) -> str:
    return _pad_right(truncate_text(value, row_width), row_width)


def _detail_rows(check: CheckRuntime | None, now_s: float | None, row_width: int, page_size: int) -> tuple[str, ...]:
    if check is None:
        return tuple(_blank_rows(row_width, page_size))

    rows = [_fixed_width_row(check.name, row_width)]
    rows.append(_fixed_width_row(f"STATUS: {_enum_text(check.status)}", row_width))

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


def _status_span(row_index: int, row_width: int, status_value: str) -> TextSpan:
    end_column = row_width
    start_column = max(0, end_column - len(status_value))
    return TextSpan(row_index=row_index, start_column=start_column, end_column=end_column)


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


def _legacy_overview_frame(state: AppState, checks: tuple[CheckRuntime, ...], highlight_selection: bool) -> Frame:
    rows = _blank_rows(state.row_width, state.page_size)
    failure_spans: list[TextSpan] = []
    inverted_row = None
    for row_index, check in enumerate(checks):
        status_text = _enum_text(check.status)
        rows[row_index] = overview_row(check.name, status_text, state.row_width)
        if highlight_selection and check.identifier == state.selected_id:
            inverted_row = row_index
        if check.status == Status.FAIL:
            failure_spans.append(_status_span(row_index, state.row_width, status_text))
    return Frame(
        rows=tuple(rows),
        inverted_row=inverted_row,
        shift_offset=state.shift_offset,
        failure_spans=tuple(failure_spans),
    )


def _compact_overview_frame(state: AppState, checks: tuple[CheckRuntime, ...], highlight_selection: bool) -> Frame:
    rows = _blank_rows(state.row_width, state.page_size)
    inverted_spans: list[InvertedSpan] = []
    separator = state.column_separator
    widths = column_widths(state.row_width, state.overview_columns, separator_width=len(separator))
    failure_spans: list[TextSpan] = []

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
                display_text = compact_overview_cell(check.name, _enum_text(check.status), width)
                cell = _pad_right(display_text, width)
                if highlight_selection and check.identifier == state.selected_id:
                    inverted_spans.append(
                        InvertedSpan(
                            row_index=row_index,
                            start_column=cursor,
                            end_column=cursor + width,
                        )
                    )
                if display_text and check.status == Status.FAIL:
                    failure_spans.append(
                        TextSpan(
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

    return Frame(
        rows=tuple(rows),
        shift_offset=state.shift_offset,
        inverted_spans=tuple(inverted_spans),
        failure_spans=tuple(failure_spans),
    )


def _overview_frame(state: AppState, highlight_selection: bool) -> Frame:
    checks = visible_checks(state)
    if not checks:
        rows = _blank_rows(state.row_width, state.page_size)
        idle_row = (state.page_size - 1) // 2
        rows[idle_row] = center_text("IDLE", state.row_width)
        return Frame(rows=tuple(rows), shift_offset=state.shift_offset)

    if state.display_mode == DisplayMode.STANDARD and state.overview_columns == 1:
        return _legacy_overview_frame(state, checks, highlight_selection)

    return _compact_overview_frame(state, checks, highlight_selection)


def render_frame(state: AppState, now_s: float | None = None, highlight_selection: bool = True) -> Frame:
    if state.mode == AppMode.DETAIL:
        selected = selected_check(state)
        failure_spans = ()
        if selected is not None and selected.status == Status.FAIL:
            failure_spans = (
                TextSpan(row_index=1, start_column=len("STATUS: "), end_column=len(f"STATUS: {_enum_text(selected.status)}")),
            )
        return Frame(
            rows=_detail_rows(selected, now_s, state.row_width, state.page_size),
            shift_offset=state.shift_offset,
            failure_spans=failure_spans,
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
    return _overview_frame(state, highlight_selection)
