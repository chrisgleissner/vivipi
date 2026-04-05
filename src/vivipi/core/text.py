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


def overview_row(name: str, status: str, total_width: int = 16, status_width: int = 4) -> str:
    display_status = truncate_text(status, status_width).rjust(status_width)
    name_width = total_width - status_width
    display_name = truncate_text(name, name_width).ljust(name_width)
    return display_name + display_status
