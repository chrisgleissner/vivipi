"""Shared text rendering helpers for ViviPi display backends."""

from __future__ import annotations

try:
    import framebuf
except ImportError:  # pragma: no cover - imported on-device
    framebuf = None


BOOT_LOGO_TITLE = "ViviPi"
BOOT_LOGO_MIN_FONT = 6
BOOT_LOGO_MAX_FONT = 32

BASE_GLYPH_SIZE = 8
ELLIPSIS = "…"
ELLIPSIS_ROWS = (
    0b00000000,
    0b00000000,
    0b00000000,
    0b00000000,
    0b00000000,
    0b00101010,
    0b00000000,
    0b00000000,
)


def _pin_number(value):
    return int(str(value).replace("GP", ""))


def _sample_source_coordinates(target_size):
    return tuple(
        min(BASE_GLYPH_SIZE - 1, ((index * 2 + 1) * BASE_GLYPH_SIZE) // (target_size * 2))
        for index in range(target_size)
    )


def _normalize_character(value):
    if value == ELLIPSIS:
        return value
    if not value:
        return " "
    codepoint = ord(value[0])
    if 32 <= codepoint <= 126:
        return value[0]
    return "?"


def _build_glyph_lookup(font_width, font_height):
    if framebuf is None:
        raise RuntimeError("framebuf is required when no glyph_lookup is provided")

    glyph_buffer = bytearray(BASE_GLYPH_SIZE)
    glyph_framebuffer = framebuf.FrameBuffer(
        glyph_buffer,
        BASE_GLYPH_SIZE,
        BASE_GLYPH_SIZE,
        framebuf.MONO_VLSB,
    )
    glyph_rows_cache = {}
    scaled_glyph_cache = {}
    scaled_x = _sample_source_coordinates(font_width)
    scaled_y = _sample_source_coordinates(font_height)

    def glyph_rows(value):
        character = _normalize_character(value)
        if character == " ":
            return (0,) * BASE_GLYPH_SIZE
        cached = glyph_rows_cache.get(character)
        if cached is not None:
            return cached
        if character == ELLIPSIS:
            glyph_rows_cache[character] = ELLIPSIS_ROWS
            return ELLIPSIS_ROWS

        glyph_framebuffer.fill(0)
        glyph_framebuffer.text(character, 0, 0, 1)
        rows = []
        for y in range(BASE_GLYPH_SIZE):
            bits = 0
            for x in range(BASE_GLYPH_SIZE):
                if glyph_framebuffer.pixel(x, y):
                    bits |= 1 << x
            rows.append(bits)
        rendered_rows = tuple(rows)
        glyph_rows_cache[character] = rendered_rows
        return rendered_rows

    def glyph_lookup(value):
        character = _normalize_character(value)
        cached = scaled_glyph_cache.get(character)
        if cached is not None:
            return cached

        source_rows = glyph_rows(character)
        pixels = []
        for y, source_y in enumerate(scaled_y):
            row_bits = source_rows[source_y]
            for x, source_x in enumerate(scaled_x):
                if row_bits & (1 << source_x):
                    pixels.append((x, y))
        scaled = tuple(pixels)
        scaled_glyph_cache[character] = scaled
        return scaled

    return glyph_lookup


def _column_matches(column_index, ranges):
    for start, end in ranges:
        if start <= column_index < end:
            return True
    return False


class MonochromeSurface:
    def __init__(self, width, height, background_color="black", foreground_color="white"):
        self.width = width
        self.height = height
        self.background_color = background_color
        self.foreground_color = foreground_color
        self.supported_colors = ("black", "white")
        self.buffer = bytearray((width * height) // 8)

    def can_render_color(self, color_name):
        return color_name in self.supported_colors

    def clear(self, color_name):
        fill_value = 0xFF if color_name == "white" else 0x00
        for index in range(len(self.buffer)):
            self.buffer[index] = fill_value

    def set_pixel(self, x, y, color_name):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        byte_index = x + ((y // 8) * self.width)
        bit_mask = 1 << (y % 8)
        if color_name == "white":
            self.buffer[byte_index] |= bit_mask
        else:
            self.buffer[byte_index] &= ~bit_mask

    def fill_rect(self, x, y, rect_width, rect_height, color_name):
        if rect_width <= 0 or rect_height <= 0:
            return
        for delta_y in range(rect_height):
            pixel_y = y + delta_y
            if not (0 <= pixel_y < self.height):
                continue
            for delta_x in range(rect_width):
                self.set_pixel(x + delta_x, pixel_y, color_name)


class HorizontalMonochromeSurface:
    def __init__(self, width, height, background_color="white", foreground_color="black"):
        self.width = width
        self.height = height
        self.background_color = background_color
        self.foreground_color = foreground_color
        self.supported_colors = ("white", "black")
        self.bytes_per_row = (width + 7) // 8
        self.buffer = bytearray(self.bytes_per_row * height)

    def can_render_color(self, color_name):
        return color_name in self.supported_colors

    def clear(self, color_name):
        fill_value = 0x00 if color_name == "black" else 0xFF
        for index in range(len(self.buffer)):
            self.buffer[index] = fill_value

    def set_pixel(self, x, y, color_name):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        byte_index = (y * self.bytes_per_row) + (x // 8)
        bit_mask = 0x80 >> (x % 8)
        if color_name == "black":
            self.buffer[byte_index] &= ~bit_mask
        else:
            self.buffer[byte_index] |= bit_mask

    def fill_rect(self, x, y, rect_width, rect_height, color_name):
        if rect_width <= 0 or rect_height <= 0:
            return
        for delta_y in range(rect_height):
            pixel_y = y + delta_y
            if not (0 <= pixel_y < self.height):
                continue
            for delta_x in range(rect_width):
                pixel_x = x + delta_x
                if 0 <= pixel_x < self.width:
                    self.set_pixel(pixel_x, pixel_y, color_name)


class RGB565Surface:
    def __init__(self, width, height, color_values=None, background_color="white", foreground_color="black"):
        self.width = width
        self.height = height
        self.background_color = background_color
        self.foreground_color = foreground_color
        self.color_values = color_values or {
            "black": 0x0000,
            "white": 0xFFFF,
            "red": 0xF800,
        }
        self.supported_colors = tuple(self.color_values)
        self.buffer = bytearray(width * height * 2)

    def can_render_color(self, color_name):
        return color_name in self.supported_colors

    def clear(self, color_name):
        color_value = self.color_values.get(color_name, self.color_values[self.background_color])
        low_byte = color_value & 0xFF
        high_byte = (color_value >> 8) & 0xFF
        for index in range(0, len(self.buffer), 2):
            self.buffer[index] = low_byte
            self.buffer[index + 1] = high_byte

    def set_pixel(self, x, y, color_name):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        color_value = self.color_values.get(color_name, self.color_values[self.foreground_color])
        index = ((y * self.width) + x) * 2
        self.buffer[index] = color_value & 0xFF
        self.buffer[index + 1] = (color_value >> 8) & 0xFF

    def fill_rect(self, x, y, rect_width, rect_height, color_name):
        if rect_width <= 0 or rect_height <= 0:
            return
        for delta_y in range(rect_height):
            pixel_y = y + delta_y
            if not (0 <= pixel_y < self.height):
                continue
            for delta_x in range(rect_width):
                pixel_x = x + delta_x
                if 0 <= pixel_x < self.width:
                    self.set_pixel(pixel_x, pixel_y, color_name)


class TriColorSurface:
    def __init__(self, width, height, background_color="white", foreground_color="black"):
        self.width = width
        self.height = height
        self.background_color = background_color
        self.foreground_color = foreground_color
        self.supported_colors = ("white", "black", "red")
        self.bytes_per_row = (width + 7) // 8
        self.black_buffer = bytearray(self.bytes_per_row * height)
        self.accent_buffer = bytearray(self.bytes_per_row * height)

    def can_render_color(self, color_name):
        return color_name in self.supported_colors

    def _encoded_bytes(self, color_name):
        if color_name == "black":
            return 0x00, 0xFF
        if color_name == "red":
            return 0xFF, 0x00
        return 0xFF, 0xFF

    def clear(self, color_name):
        fill_black, fill_accent = self._encoded_bytes(color_name)
        for index in range(len(self.black_buffer)):
            self.black_buffer[index] = fill_black
            self.accent_buffer[index] = fill_accent

    def set_pixel(self, x, y, color_name):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        byte_index = (x // 8) + (y * self.bytes_per_row)
        bit_mask = 0x80 >> (x % 8)

        if color_name == "black":
            self.black_buffer[byte_index] &= ~bit_mask
            self.accent_buffer[byte_index] |= bit_mask
            return
        if color_name == "red":
            self.black_buffer[byte_index] |= bit_mask
            self.accent_buffer[byte_index] &= ~bit_mask
            return

        self.black_buffer[byte_index] |= bit_mask
        self.accent_buffer[byte_index] |= bit_mask

    def fill_rect(self, x, y, rect_width, rect_height, color_name):
        if rect_width <= 0 or rect_height <= 0:
            return
        for delta_y in range(rect_height):
            pixel_y = y + delta_y
            if not (0 <= pixel_y < self.height):
                continue
            for delta_x in range(rect_width):
                self.set_pixel(x + delta_x, pixel_y, color_name)


def _draw_text(surface, value, origin_x, origin_y, font_width, text_color, glyph_lookup):
    for column_index, character in enumerate(value):
        glyph = glyph_lookup(character)
        if not glyph:
            continue

        cell_x = origin_x + (column_index * font_width)
        for delta_x, delta_y in glyph:
            surface.set_pixel(cell_x + delta_x, origin_y + delta_y, text_color(column_index))


def _scaled_indicator_width(width_px: int, font_width: int) -> int:
    if width_px <= 0:
        return 0
    return max(1, min(font_width, int(((int(width_px) * font_width) + 7) // 8)))


def _draw_freshness_indicator(surface, indicator, x_offset, y_offset, font_width, font_height, color_name):
    cell_x = x_offset + (indicator.column_index * font_width)
    cell_y = y_offset + (indicator.row_index * font_height)
    rendered_width = _scaled_indicator_width(getattr(indicator, "width_px", 0), font_width)
    if rendered_width > 0:
        surface.fill_rect(cell_x, cell_y, rendered_width, font_height, color_name)
        return

    sentinel_y = cell_y + max(0, min(font_height - 1, font_height // 2))
    surface.set_pixel(cell_x, sentinel_y, color_name)


def render_to_surface(frame, surface, font_width, font_height, glyph_lookup, failure_color="red"):
    surface.clear(surface.background_color)
    x_offset, y_offset = frame.shift_offset
    inverted_by_row = {}
    failure_by_row = {}
    for span in frame.inverted_spans:
        inverted_by_row.setdefault(span.row_index, []).append((span.start_column, span.end_column))
    for span in frame.failure_spans:
        failure_by_row.setdefault(span.row_index, []).append((span.start_column, span.end_column))

    failure_color_supported = surface.can_render_color(failure_color)
    freshness_indicators_by_row = {}
    for indicator in getattr(frame, "freshness_indicators", ()):
        freshness_indicators_by_row.setdefault(indicator.row_index, []).append(indicator)

    # Hot path: rendering stays deterministic and free of logging or dynamic state growth.
    for row_index, row in enumerate(frame.rows):
        y = (row_index * font_height) + y_offset
        row_inverted = frame.inverted_row == row_index
        inverted_ranges = tuple(inverted_by_row.get(row_index, ()))
        failure_ranges = tuple(failure_by_row.get(row_index, ()))
        effective_inverted = inverted_ranges
        if not failure_color_supported and not row_inverted:
            effective_inverted = effective_inverted + failure_ranges

        if row_inverted:
            surface.fill_rect(0, y, surface.width, font_height, surface.foreground_color)
        for start, end in effective_inverted:
            surface.fill_rect(
                x_offset + (start * font_width),
                y,
                (end - start) * font_width,
                font_height,
                surface.foreground_color,
            )

        def text_color(column_index):
            if row_inverted or _column_matches(column_index, effective_inverted):
                return surface.background_color
            if failure_color_supported and _column_matches(column_index, failure_ranges):
                return failure_color
            return surface.foreground_color

        _draw_text(surface, row, x_offset, y, font_width, text_color, glyph_lookup)
        indicator_color = surface.background_color if row_inverted else surface.foreground_color
        for indicator in freshness_indicators_by_row.get(row_index, ()):
            _draw_freshness_indicator(
                surface,
                indicator,
                x_offset,
                y_offset,
                font_width,
                font_height,
                indicator_color,
            )

    return surface


def _clamp_font(size):
    return max(BOOT_LOGO_MIN_FONT, min(BOOT_LOGO_MAX_FONT, size))


def _boot_logo_padding(width):
    return max(4, width // 16)


def _boot_logo_letter_spacing(font_size):
    return max(1, font_size // 8)


def _boot_logo_text_width(text, font_size):
    if not text:
        return 0
    spacing = _boot_logo_letter_spacing(font_size)
    return (len(text) * font_size) + ((len(text) - 1) * spacing)


def _fit_boot_logo_font(text, width, height_limit):
    if not text:
        return 0

    padding = _boot_logo_padding(width)
    width_limit = max(BOOT_LOGO_MIN_FONT, width - (padding * 2))
    candidate = _clamp_font(min(width_limit // len(text), height_limit))
    while candidate > BOOT_LOGO_MIN_FONT and _boot_logo_text_width(text, candidate) > width_limit:
        candidate -= 1
    return _clamp_font(candidate)


def boot_logo_font_sizes(width, height, version):
    title_font = _fit_boot_logo_font(BOOT_LOGO_TITLE, width, (height * 55) // 100)

    version_len = len(version) if version else 0
    if version_len > 0:
        remaining = height - title_font
        version_font = _fit_boot_logo_font(version, width, min((remaining * 60) // 100, (title_font * 2) // 3))
    else:
        version_font = 0

    return title_font, version_font


def render_boot_logo_to_surface(surface, version, glyph_builder=None):
    builder = glyph_builder or _build_glyph_lookup
    surface.clear(surface.background_color)

    title_font, version_font = boot_logo_font_sizes(surface.width, surface.height, version)
    gap = max(2, surface.height // 16)
    total_h = title_font + (gap + version_font if version else 0)
    y_start = max(0, (surface.height - total_h) // 2)

    title_glyph = builder(title_font, title_font)
    title_spacing = _boot_logo_letter_spacing(title_font)
    title_px = _boot_logo_text_width(BOOT_LOGO_TITLE, title_font)
    title_x = max(0, (surface.width - title_px) // 2)
    for col_index, character in enumerate(BOOT_LOGO_TITLE):
        for delta_x, delta_y in title_glyph(character):
            cell_x = title_x + col_index * (title_font + title_spacing)
            surface.set_pixel(cell_x + delta_x, y_start + delta_y, surface.foreground_color)

    if version:
        ver_glyph = builder(version_font, version_font)
        ver_spacing = _boot_logo_letter_spacing(version_font)
        ver_px = _boot_logo_text_width(version, version_font)
        ver_x = max(0, (surface.width - ver_px) // 2)
        ver_y = y_start + title_font + gap
        for col_index, character in enumerate(version):
            for delta_x, delta_y in ver_glyph(character):
                cell_x = ver_x + col_index * (version_font + ver_spacing)
                surface.set_pixel(cell_x + delta_x, ver_y + delta_y, surface.foreground_color)

    return surface


def render_framebuffer(frame, width, height, font_width, font_height, glyph_lookup, failure_color="red"):
    surface = MonochromeSurface(width, height)
    render_to_surface(frame, surface, font_width, font_height, glyph_lookup, failure_color=failure_color)
    return surface.buffer


def render_boot_logo(width, height, version, glyph_builder=None):
    surface = MonochromeSurface(width, height)
    render_boot_logo_to_surface(surface, version, glyph_builder=glyph_builder)
    return surface.buffer