from dataclasses import dataclass


ELLIPSIS = "…"


@dataclass(frozen=True)
class OverviewRowLayout:
    text: str
    status_start_column: int
    status_end_column: int
    freshness_column: int | None


def _pad_right(value: str, width: int) -> str:
    if width <= len(value):
        return value[:width]
    return value + (" " * (width - len(value)))


def _pad_left(value: str, width: int) -> str:
    if width <= len(value):
        return value[-width:]
    return (" " * (width - len(value))) + value


def truncate_text(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width == 1:
        return ELLIPSIS
    return value[: width - 1] + ELLIPSIS


def center_text(value: str, width: int = 16) -> str:
    clipped = truncate_text(value, width)
    padding = max(width - len(clipped), 0)
    left_padding = padding // 2
    right_padding = padding - left_padding
    return (" " * left_padding) + clipped + (" " * right_padding)


def hard_truncate_text(value: str, width: int) -> str:
    if width <= 0:
        return ""
    return value[:width]


def compact_status_suffix(status: str) -> str:
    normalized = status.strip().upper()
    if normalized == "OK":
        return ""
    if normalized == "DEG":
        return "!"
    if normalized == "FAIL":
        return "X"
    return "?"


def column_widths(total_width: int, columns: int, separator_width: int = 1) -> tuple[int, ...]:
    if columns < 1 or columns > 4:
        raise ValueError("columns must be between 1 and 4")
    if separator_width < 0:
        raise ValueError("separator_width must not be negative")

    available_chars = total_width - ((columns - 1) * separator_width)
    if available_chars < columns:
        raise ValueError("total_width is too small for the requested column count")

    base_width = available_chars // columns
    remainder = available_chars % columns
    return tuple(base_width + 1 if index < remainder else base_width for index in range(columns))


def compact_overview_cell(name: str, status: str, column_width: int) -> str:
    if column_width <= 0:
        return ""

    suffix = compact_status_suffix(status)
    max_name_len = column_width - 1 if suffix else column_width
    display_name = hard_truncate_text(name, max_name_len)
    return hard_truncate_text(display_name + suffix, column_width)


def overview_row_layout(
    name: str,
    status: str,
    total_width: int = 16,
    status_width: int = 4,
    freshness_width: int = 1,
    separator: str = " ",
) -> OverviewRowLayout:
    if total_width <= 0:
        return OverviewRowLayout(text="", status_start_column=0, status_end_column=0, freshness_column=None)

    reserved_freshness_width = max(0, min(freshness_width, total_width))
    body_width = max(0, total_width - reserved_freshness_width)
    separator_text = separator if body_width > status_width else ""
    display_status_width = min(status_width, body_width)
    display_status = _pad_left(truncate_text(status, display_status_width), display_status_width)
    name_width = max(body_width - len(separator_text) - display_status_width, 0)
    display_name = _pad_right(truncate_text(name, name_width), name_width)
    body_text = _pad_right(display_name + separator_text + display_status, body_width)
    status_start_column = min(len(display_name + separator_text), body_width)
    status_end_column = min(status_start_column + display_status_width, body_width)
    freshness_column = body_width if reserved_freshness_width else None
    return OverviewRowLayout(
        text=_pad_right(body_text, body_width) + (" " * reserved_freshness_width),
        status_start_column=status_start_column,
        status_end_column=status_end_column,
        freshness_column=freshness_column,
    )


def overview_row(name: str, status: str, total_width: int = 16, status_width: int = 4) -> str:
    return overview_row_layout(name, status, total_width=total_width, status_width=status_width).text
