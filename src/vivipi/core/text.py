ELLIPSIS = "…"


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


def overview_row(name: str, status: str, total_width: int = 16, status_width: int = 4) -> str:
    if total_width <= 0:
        return ""
    display_status_width = min(status_width, total_width)
    display_status = truncate_text(status, display_status_width).rjust(display_status_width)
    name_width = max(total_width - display_status_width, 0)
    display_name = truncate_text(name, name_width).ljust(name_width)
    return (display_name + display_status).ljust(total_width)
