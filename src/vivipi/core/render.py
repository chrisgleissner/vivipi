from __future__ import annotations

from dataclasses import dataclass

from vivipi.core.models import AppMode, AppState, CheckRuntime, DisplayMode, Status
from vivipi.core.state import overview_checks, selected_check, visible_checks
from vivipi.core.text import center_text, column_widths, compact_overview_cell, overview_row_layout, truncate_text


# --- Matrix overview -------------------------------------------------------
# Targets are rows, probe types are columns. Each check name is
# "<TARGET> <PROBE>"; its last word selects the column. This lets the whole fleet
# (many targets x several probes) fit on one static screen: a calm dot grid when
# healthy, with failed probes rendered as a loud inverted block at a fixed
# (target, probe) position -- glanceable from across the room, no paging, no
# reordering, so positional memory holds.
MATRIX_DEFAULT_COLUMNS = ("P", "R", "F", "T", "I", "D")
MATRIX_COLUMN_KEYWORDS = {
    "P": ("PING", "ADB"),  # ADB reachability is treated as a ping-class probe
    "R": ("REST", "HTTP"),
    "F": ("FTP",),
    "T": ("TELNET",),
    "I": ("IDENT",),
    "D": ("DMA",),
}
_MATRIX_KEYWORD_TO_COLUMN = {
    keyword: column for column, keywords in MATRIX_COLUMN_KEYWORDS.items() for keyword in keywords
}
MATRIX_GLYPH_NONE = " "  # no such (target, probe) check is configured
MATRIX_GLYPH_OK = "."
MATRIX_GLYPH_DEG = "!"
MATRIX_GLYPH_FAIL = "X"
MATRIX_GLYPH_UNKNOWN = "?"
_MATRIX_COLUMN_CELL_WIDTH = 2  # one leading space + one status glyph per column


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
    bottom_pixels: tuple[int, ...] = ()
    bottom_pixel_width_px: int = 1
    bottom_pixel_height_px: int = 1
    bottom_pixel_gap_px: int = 0
    contrast: int | None = None
    # Optional per-row pixel layout. When set, each entry is
    # (y_origin_px, glyph_height_px) for the row at that index, overriding the
    # default "row_index * font_height" placement and uniform glyph height. Used
    # by the matrix view to spread a small number of configured rows across the
    # full display height with a larger glyph. None keeps the legacy layout.
    row_layout: tuple[tuple[int, int], ...] | None = None


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


def _status_span(row_index: int, start_column: int, end_column: int) -> TextSpan:
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
        layout = overview_row_layout(check.name, status_text, state.row_width)
        rows[row_index] = layout.text
        if highlight_selection and check.identifier == state.selected_id:
            inverted_row = row_index
        if check.status == Status.FAIL:
            failure_spans.append(_status_span(row_index, layout.status_start_column, layout.status_end_column))
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


def _matrix_parse_check(name: str) -> tuple[str | None, str | None]:
    tokens = str(name).split()
    if len(tokens) < 2:
        return None, None
    column = _MATRIX_KEYWORD_TO_COLUMN.get(tokens[-1].upper())
    if column is None:
        return None, None
    return " ".join(tokens[:-1]), column


def _matrix_cell_glyph(check: CheckRuntime | None) -> str:
    if check is None:
        return MATRIX_GLYPH_NONE
    if check.status == Status.FAIL:
        return MATRIX_GLYPH_FAIL
    if check.status == Status.DEG:
        return MATRIX_GLYPH_DEG
    if check.status == Status.OK:
        return MATRIX_GLYPH_OK
    return MATRIX_GLYPH_UNKNOWN


def matrix_row_layout(
    content_row_count: int,
    display_height_px: int,
    reserved_bottom_px: int,
    base_font_height_px: int,
) -> tuple[tuple[int, int], ...] | None:
    # Spread the configured matrix rows across the full display height (minus the
    # reserved bottom liveness band) and enlarge each glyph to fill its row, so a
    # small fleet uses the whole screen instead of a thin strip at the top.
    if content_row_count < 1 or display_height_px < 1 or base_font_height_px < 1:
        return None
    usable = max(base_font_height_px, int(display_height_px) - max(0, int(reserved_bottom_px)))
    pitch = usable // content_row_count
    # Only apply the enlarged layout when it actually grows the rows; otherwise
    # keep the standard uniform spacing so dense fleets do not shrink text.
    if pitch <= base_font_height_px:
        return None
    return tuple((row_index * pitch, pitch) for row_index in range(content_row_count))


def _matrix_overview_frame(
    state: AppState,
    checks: tuple[CheckRuntime, ...],
    geometry: dict[str, int] | None = None,
) -> Frame | None:
    columns = MATRIX_DEFAULT_COLUMNS
    label_width = state.row_width - (_MATRIX_COLUMN_CELL_WIDTH * len(columns))
    if label_width < 1:
        return None  # not enough width for this many columns; caller falls back

    grid: dict[str, dict[str, CheckRuntime]] = {}
    target_order: list[str] = []
    for check in checks:
        target, column = _matrix_parse_check(check.name)
        if target is None or column not in columns:
            continue
        if target not in grid:
            grid[target] = {}
            target_order.append(target)
        # First check wins per (target, column) so a stable name sort is honoured.
        grid[target].setdefault(column, check)

    if not grid:
        return None  # nothing parsed into the matrix; caller falls back

    # Multi-probe targets first (by name), single-probe targets (e.g. the phone)
    # last -- deterministic and stable, so each row keeps a fixed home.
    target_order.sort(key=lambda target: (len(grid[target]) <= 1, target))

    header = _pad_right(
        (" " * label_width) + "".join(" " + column for column in columns),
        state.row_width,
    )
    rows = [header]
    failure_spans: list[TextSpan] = []

    for target in target_order[: state.page_size - 1]:
        row_index = len(rows)
        parts = [_pad_right(target[:label_width], label_width)]
        cursor = label_width
        for column in columns:
            check = grid[target].get(column)
            parts.append(" " + _matrix_cell_glyph(check))
            if check is not None and check.status == Status.FAIL:
                failure_spans.append(_status_span(row_index, cursor, cursor + _MATRIX_COLUMN_CELL_WIDTH))
            cursor += _MATRIX_COLUMN_CELL_WIDTH
        rows.append(_pad_right("".join(parts), state.row_width))

    row_layout = _resolve_matrix_row_layout(rows, geometry)
    if row_layout is None:
        while len(rows) < state.page_size:
            rows.append(" " * state.row_width)

    return Frame(
        rows=tuple(rows[: state.page_size]),
        shift_offset=state.shift_offset,
        failure_spans=tuple(failure_spans),
        row_layout=row_layout,
    )


def _resolve_matrix_row_layout(rows: list[str], geometry: dict[str, int] | None) -> tuple[tuple[int, int], ...] | None:
    if not geometry:
        return None
    display_height_px = geometry.get("display_height_px")
    font_height_px = geometry.get("font_height_px")
    if display_height_px is None or font_height_px is None:
        return None
    reserved_bottom_px = geometry.get("reserved_bottom_px", 0)
    return matrix_row_layout(len(rows), int(display_height_px), int(reserved_bottom_px), int(font_height_px))


def _overview_frame(state: AppState, highlight_selection: bool, geometry: dict[str, int] | None = None) -> Frame:
    if state.display_mode == DisplayMode.MATRIX:
        # The matrix packs every check onto one static screen, so it draws from
        # the full check set rather than a single paged slice.
        matrix = _matrix_overview_frame(state, overview_checks(state), geometry)
        if matrix is not None:
            return matrix
        # Fall through to the paged views if the checks do not fit the matrix
        # model (e.g. names that are not "<TARGET> <PROBE>").

    checks = visible_checks(state)
    if not checks:
        rows = _blank_rows(state.row_width, state.page_size)
        idle_row = (state.page_size - 1) // 2
        rows[idle_row] = center_text("IDLE", state.row_width)
        return Frame(rows=tuple(rows), shift_offset=state.shift_offset)

    if state.display_mode in (DisplayMode.STANDARD, DisplayMode.MATRIX) and state.overview_columns == 1:
        return _legacy_overview_frame(state, checks, highlight_selection)

    return _compact_overview_frame(state, checks, highlight_selection)


def render_frame(
    state: AppState,
    now_s: float | None = None,
    highlight_selection: bool = True,
    display_height_px: int | None = None,
    font_height_px: int | None = None,
    reserved_bottom_px: int | None = None,
) -> Frame:
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
    # Display pixel geometry lets the matrix view spread its rows across the
    # full screen with a larger glyph; other overview modes ignore it.
    geometry = (
        {
            "display_height_px": int(display_height_px),
            "font_height_px": int(font_height_px),
            "reserved_bottom_px": int(reserved_bottom_px or 0),
        }
        if display_height_px is not None and font_height_px is not None
        else None
    )
    return _overview_frame(state, highlight_selection, geometry)
