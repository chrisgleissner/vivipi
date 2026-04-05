"""Thin SH1107 text display adapter for ViviPi."""

from vivipi.core.models import AppState, DisplayMode
from vivipi.core.render import render_frame

try:
    import framebuf
    from machine import Pin, SPI
except ImportError:  # pragma: no cover - imported on-device
    framebuf = None
    Pin = None
    SPI = None


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


def _set_buffer_pixel(buffer, width, height, x, y, color):
    if not (0 <= x < width and 0 <= y < height):
        return
    byte_index = x + ((y // 8) * width)
    bit_mask = 1 << (y % 8)
    if color:
        buffer[byte_index] |= bit_mask
    else:
        buffer[byte_index] &= ~bit_mask


def _fill_buffer(buffer, color):
    fill_value = 0xFF if color else 0x00
    for index in range(len(buffer)):
        buffer[index] = fill_value


def _fill_rect_buffer(buffer, width, height, x, y, rect_width, rect_height, color):
    if rect_width <= 0 or rect_height <= 0:
        return
    for delta_y in range(rect_height):
        pixel_y = y + delta_y
        if not (0 <= pixel_y < height):
            continue
        for delta_x in range(rect_width):
            pixel_x = x + delta_x
            _set_buffer_pixel(buffer, width, height, pixel_x, pixel_y, color)


def _column_is_inverted(column_index, inverted_ranges):
    for start, end in inverted_ranges:
        if start <= column_index < end:
            return True
    return False


def _draw_text_buffer(
    buffer,
    width,
    height,
    value,
    origin_x,
    origin_y,
    font_width,
    default_color,
    glyph_lookup,
    inverted_ranges=(),
):
    for column_index, character in enumerate(value):
        glyph = glyph_lookup(character)
        if not glyph:
            continue

        color = 0 if _column_is_inverted(column_index, inverted_ranges) else default_color
        cell_x = origin_x + (column_index * font_width)
        for delta_x, delta_y in glyph:
            _set_buffer_pixel(buffer, width, height, cell_x + delta_x, origin_y + delta_y, color)


def render_framebuffer(frame, width, height, font_width, font_height, glyph_lookup):
    buffer = bytearray((width * height) // 8)
    _fill_buffer(buffer, 0)
    x_offset, y_offset = frame.shift_offset
    spans_by_row = {}
    for span in frame.inverted_spans:
        row_spans = spans_by_row.setdefault(span.row_index, [])
        row_spans.append((span.start_column, span.end_column))

    for row_index, row in enumerate(frame.rows):
        y = (row_index * font_height) + y_offset
        inverted_ranges = tuple(spans_by_row.get(row_index, ()))
        row_inverted = frame.inverted_row == row_index
        if row_inverted:
            _fill_rect_buffer(buffer, width, height, 0, y, width, font_height, 1)
        for start, end in inverted_ranges:
            _fill_rect_buffer(
                buffer,
                width,
                height,
                x_offset + (start * font_width),
                y,
                (end - start) * font_width,
                font_height,
                1,
            )
        _draw_text_buffer(
            buffer,
            width,
            height,
            row,
            x_offset,
            y,
            font_width,
            0 if row_inverted else 1,
            glyph_lookup,
            inverted_ranges=inverted_ranges,
        )

    return buffer


def render(checks, config, selected_id=None, page_index=0, shift_offset=(0, 0), glyph_lookup=None):
    display_config = config.get("display", config) if isinstance(config, dict) else {}
    width = int(display_config.get("width_px", 128))
    height = int(display_config.get("height_px", 64))
    font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
    font_width = int(font.get("width_px", 8)) if isinstance(font, dict) else 8
    font_height = int(font.get("height_px", 8)) if isinstance(font, dict) else 8
    glyph_lookup = glyph_lookup or _build_glyph_lookup(font_width, font_height)
    state = AppState(
        checks=tuple(checks),
        selected_id=selected_id,
        display_mode=DisplayMode(str(display_config.get("mode", DisplayMode.STANDARD.value))),
        overview_columns=int(display_config.get("columns", 1)),
        column_separator=str(display_config.get("column_separator", " ")),
        row_width=max(1, width // font_width),
        page_size=max(1, height // font_height),
        page_index=page_index,
        shift_offset=shift_offset,
    )
    frame = render_frame(state)
    return render_framebuffer(frame, width, height, font_width, font_height, glyph_lookup)


class SH1107Display:
    def __init__(self, display_config, spi=None):
        if framebuf is None or Pin is None or SPI is None:  # pragma: no cover - imported on-device
            raise RuntimeError("machine and framebuf modules are required on device")

        self.width = int(display_config.get("width_px", 128))
        self.height = int(display_config.get("height_px", 64))
        font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
        self.font_width = int(font.get("width_px", 8)) if isinstance(font, dict) else 8
        self.font_height = int(font.get("height_px", 8)) if isinstance(font, dict) else 8
        self.contrast = int(display_config.get("brightness", 128))
        pins = display_config["pins"]
        self.dc = Pin(_pin_number(pins["dc"]), Pin.OUT)
        self.rst = Pin(_pin_number(pins["rst"]), Pin.OUT)
        self.cs = Pin(_pin_number(pins["cs"]), Pin.OUT)
        self.spi = spi or SPI(
            1,
            baudrate=10_000_000,
            polarity=1,
            phase=1,
            sck=Pin(_pin_number(pins["clk"])),
            mosi=Pin(_pin_number(pins["din"])),
        )
        self.buffer = bytearray((self.width * self.height) // 8)
        self.framebuffer = framebuf.FrameBuffer(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self._glyph_lookup = _build_glyph_lookup(self.font_width, self.font_height)
        self._initialize()

    def _command(self, value):
        self.cs(1)
        self.dc(0)
        self.cs(0)
        self.spi.write(bytearray([value]))
        self.cs(1)

    def _data(self, values):
        self.cs(1)
        self.dc(1)
        self.cs(0)
        self.spi.write(values)
        self.cs(1)

    def _initialize(self):
        self.rst(1)
        self.rst(0)
        self.rst(1)
        for command in (
            0xAE,
            0xDC,
            0x00,
            0x20,
            0xA0,
            0xC0,
            0xA8,
            self.height - 1,
            0xD3,
            0x60,
            0xD5,
            0x50,
            0xD9,
            0x22,
            0xDB,
            0x35,
            0xAD,
            0x8A,
            0xA4,
            0xA6,
        ):
            self._command(command)
        self.set_contrast(self.contrast)
        self._command(0xAF)

    def set_contrast(self, value):
        contrast = max(0, min(255, int(value)))
        self._command(0x81)
        self._command(contrast)

    def _show(self):
        pages = self.height // 8
        for page in range(pages):
            self._command(0xB0 + page)
            self._command(0x02)
            self._command(0x10)
            start = page * self.width
            self._data(self.buffer[start : start + self.width])

    def draw_frame(self, frame):
        self.buffer[:] = render_framebuffer(
            frame,
            self.width,
            self.height,
            self.font_width,
            self.font_height,
            self._glyph_lookup,
        )
        self._show()