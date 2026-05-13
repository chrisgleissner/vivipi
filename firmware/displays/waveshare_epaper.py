"""Waveshare Pico ePaper 2.13 B V4 backend."""

from __future__ import annotations

try:
    import framebuf  # type: ignore[import-not-found]
    import utime as time  # type: ignore[import-not-found]
    from machine import Pin, SPI  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - imported on-device
    framebuf = None
    import time

    Pin = None
    SPI = None

try:
    from displays.rendering import _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface
except ImportError as error:  # pragma: no cover - used by CPython tests
    if getattr(error, "name", None) != "displays":
        raise
    from firmware.displays.rendering import _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface


def _sleep_ms(value):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value)
        return
    time.sleep(value / 1000.0)


def _busy_pin(pin_number):
    pull_up = getattr(Pin, "PULL_UP", None)
    if pull_up is None:
        return Pin(pin_number, Pin.IN)
    return Pin(pin_number, Pin.IN, pull_up)


class WaveshareEPaper213BV4Surface:
    def __init__(self, width, height, background_color="white", foreground_color="black"):
        self.logical_width = width
        self.logical_height = height
        self.width = width
        self.height = height
        self.background_color = background_color
        self.foreground_color = foreground_color
        self.supported_colors = ("white", "black", "red")
        self.surface_height = ((height + 7) // 8) * 8
        self.row_bytes = self.surface_height // 8
        self.black_buffer = bytearray(width * self.row_bytes)
        self.accent_buffer = bytearray(width * self.row_bytes)
        self._black_framebuffer = None
        self._accent_framebuffer = None
        if framebuf is not None:
            self._black_framebuffer = framebuf.FrameBuffer(
                self.black_buffer,
                self.logical_width,
                self.surface_height,
                framebuf.MONO_VLSB,
            )
            self._accent_framebuffer = framebuf.FrameBuffer(
                self.accent_buffer,
                self.logical_width,
                self.surface_height,
                framebuf.MONO_VLSB,
            )
        self.clear(background_color)

    def can_render_color(self, color_name):
        return color_name in self.supported_colors

    def _encoded_bytes(self, color_name):
        if color_name == "black":
            return 0x00, 0xFF
        if color_name == "red":
            return 0xFF, 0x00
        return 0xFF, 0xFF

    def clear(self, color_name):
        black_framebuffer = getattr(self, "_black_framebuffer", None)
        accent_framebuffer = getattr(self, "_accent_framebuffer", None)
        if black_framebuffer is not None and accent_framebuffer is not None:
            if color_name == "black":
                black_framebuffer.fill(0)
                accent_framebuffer.fill(1)
                return
            if color_name == "red":
                black_framebuffer.fill(1)
                accent_framebuffer.fill(0)
                return
            black_framebuffer.fill(1)
            accent_framebuffer.fill(1)
            return

        fill_black, fill_accent = self._encoded_bytes(color_name)
        for index in range(len(self.black_buffer)):
            self.black_buffer[index] = fill_black
            self.accent_buffer[index] = fill_accent

    def _index_and_mask(self, x, y):
        byte_index = x + ((y // 8) * self.logical_width)
        bit_mask = 1 << (y % 8)
        return byte_index, bit_mask

    def set_pixel(self, x, y, color_name):
        if not (0 <= x < self.logical_width and 0 <= y < self.logical_height):
            return

        black_framebuffer = getattr(self, "_black_framebuffer", None)
        accent_framebuffer = getattr(self, "_accent_framebuffer", None)
        if black_framebuffer is not None and accent_framebuffer is not None:
            if color_name == "black":
                black_framebuffer.pixel(x, y, 0)
                accent_framebuffer.pixel(x, y, 1)
                return
            if color_name == "red":
                black_framebuffer.pixel(x, y, 1)
                accent_framebuffer.pixel(x, y, 0)
                return
            black_framebuffer.pixel(x, y, 1)
            accent_framebuffer.pixel(x, y, 1)
            return

        byte_index, bit_mask = self._index_and_mask(x, y)
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

        black_framebuffer = getattr(self, "_black_framebuffer", None)
        accent_framebuffer = getattr(self, "_accent_framebuffer", None)
        if black_framebuffer is not None and accent_framebuffer is not None:
            if color_name == "black":
                black_framebuffer.fill_rect(x, y, rect_width, rect_height, 0)
                accent_framebuffer.fill_rect(x, y, rect_width, rect_height, 1)
                return
            if color_name == "red":
                black_framebuffer.fill_rect(x, y, rect_width, rect_height, 1)
                accent_framebuffer.fill_rect(x, y, rect_width, rect_height, 0)
                return
            black_framebuffer.fill_rect(x, y, rect_width, rect_height, 1)
            accent_framebuffer.fill_rect(x, y, rect_width, rect_height, 1)
            return

        for delta_y in range(rect_height):
            pixel_y = y + delta_y
            if not (0 <= pixel_y < self.logical_height):
                continue
            for delta_x in range(rect_width):
                pixel_x = x + delta_x
                if 0 <= pixel_x < self.logical_width:
                    self.set_pixel(pixel_x, pixel_y, color_name)


class WaveshareEPaper213BV4Display:
    def __init__(self, display_config, spi=None):
        if Pin is None or SPI is None:  # pragma: no cover - imported on-device
            raise RuntimeError("machine module is required on device")

        self.width = int(display_config["width_px"])
        self.height = int(display_config["height_px"])
        font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
        configured_font_width = int(font.get("width_px", 15)) if isinstance(font, dict) else 15
        configured_font_height = int(font.get("height_px", 15)) if isinstance(font, dict) else 15
        self.font_width = max(16, configured_font_width)
        self.font_height = max(16, configured_font_height)
        self.failure_color = str(display_config.get("failure_color", "red"))
        self.rotation = int(display_config.get("rotation", 0))
        pins = display_config["pins"]
        self.dc = Pin(_pin_number(pins["dc"]), Pin.OUT)
        self.rst = Pin(_pin_number(pins["rst"]), Pin.OUT)
        self.cs = Pin(_pin_number(pins["cs"]), Pin.OUT)
        self.busy = _busy_pin(_pin_number(pins["busy"]))
        self.spi = spi or SPI(1)
        if spi is None and hasattr(self.spi, "init"):
            self.spi.init(baudrate=4_000_000)
        self._glyph_builder = lambda width, height: _build_glyph_lookup(width, height)
        self._glyph_lookup = self._glyph_builder(self.font_width, self.font_height)
        self.surface_height = ((self.height + 7) // 8) * 8
        self._surface = WaveshareEPaper213BV4Surface(self.width, self.height)
        self._watchdog_feed = None
        self._panel_primed = False
        self._initialize()

    def _surface_supports_framebuf_text(self, surface):
        return (
            framebuf is not None
            and getattr(surface, "_black_framebuffer", None) is not None
            and getattr(surface, "_accent_framebuffer", None) is not None
        )

    def _row_failure_columns(self, frame):
        failure_by_row = {}
        for span in getattr(frame, "failure_spans", ()):
            failure_by_row.setdefault(int(span.row_index), []).append((int(span.start_column), int(span.end_column)))
        return failure_by_row

    def _draw_rotated_glyph(self, surface, glyph_pixels, origin_x, origin_y, color_name):
        rotation = int(getattr(self, "rotation", 0))

        for index, (delta_x, delta_y) in enumerate(glyph_pixels):
            pixel_x = origin_x + delta_x
            pixel_y = origin_y + delta_y
            if rotation == 180:
                pixel_x = self.width - 1 - pixel_x
                pixel_y = self.height - 1 - pixel_y
            surface.set_pixel(pixel_x, pixel_y, color_name)
            if (index & 0x3F) == 0x3F:
                self._feed_watchdog()

    def _render_text_with_framebuf(self, surface, frame):
        surface.clear(surface.background_color)
        self._feed_watchdog()
        x_offset, y_offset = getattr(frame, "shift_offset", (0, 0))
        failure_by_row = self._row_failure_columns(frame)
        glyph_width = max(1, int(getattr(self, "font_width", 16)))
        glyph_height = max(1, int(getattr(self, "font_height", 16)))
        glyph_lookup = getattr(self, "_glyph_lookup", None)
        if glyph_lookup is None:
            glyph_lookup = self._glyph_builder(glyph_width, glyph_height)
            self._glyph_lookup = glyph_lookup

        for row_index, row in enumerate(getattr(frame, "rows", ())):
            self._feed_watchdog()
            y = y_offset + (row_index * glyph_height)
            if y >= self.height:
                break
            for column_index, character in enumerate(str(row)):
                x = x_offset + (column_index * glyph_width)
                if x >= self.width:
                    break
                is_failure = False
                for start_column, end_column in failure_by_row.get(row_index, ()):  # keep failure text red on-device.
                    if start_column <= column_index < end_column:
                        is_failure = True
                        break
                self._draw_rotated_glyph(
                    surface,
                    glyph_lookup(character),
                    x,
                    y,
                    "red" if is_failure else surface.foreground_color,
                )
                self._feed_watchdog()

        bottom_pixel_width = max(1, int(getattr(frame, "bottom_pixel_width_px", 1)))
        bottom_pixel_height = max(1, int(getattr(frame, "bottom_pixel_height_px", 1)))
        bottom_pixel_y = max(0, self.height - bottom_pixel_height)
        for pixel_x in getattr(frame, "bottom_pixels", ()):
            surface.fill_rect(int(pixel_x), bottom_pixel_y, bottom_pixel_width, bottom_pixel_height, surface.foreground_color)
            self._feed_watchdog()

    @property
    def surface_height_px(self):
        return int(getattr(self, "surface_height", ((self.height + 7) // 8) * 8))

    @property
    def transport_width_px(self):
        return int(self.surface_height_px)

    @property
    def transport_height_px(self):
        return int(self.width)

    def _command(self, value):
        self.dc(0)
        self.cs(0)
        self.spi.write(bytearray([value]))
        self.cs(1)

    def _data(self, values):
        if isinstance(values, int):
            values = (values,)
        self.dc(1)
        self.cs(0)
        self.spi.write(values if isinstance(values, bytearray) else bytearray(values))
        self.cs(1)

    def _wait_until_idle(self, timeout_ms=20_000):
        waited_ms = 0
        while self.busy.value() == 1 and waited_ms < timeout_ms:
            self._feed_watchdog()
            _sleep_ms(10)
            waited_ms += 10
        self._feed_watchdog()
        _sleep_ms(20)

    def _reset(self):
        self.rst(1)
        _sleep_ms(50)
        self.rst(0)
        _sleep_ms(2)
        self.rst(1)
        _sleep_ms(50)

    def _set_windows(self, x_start, y_start, x_end, y_end):
        self._command(0x44)
        self._data(bytearray([(x_start >> 3) & 0xFF, (x_end >> 3) & 0xFF]))
        self._command(0x45)
        self._data(
            bytearray(
                [
                    y_start & 0xFF,
                    (y_start >> 8) & 0xFF,
                    y_end & 0xFF,
                    (y_end >> 8) & 0xFF,
                ]
            )
        )

    def _set_cursor(self, x_start, y_start):
        self._command(0x4E)
        self._data(x_start & 0xFF)
        self._command(0x4F)
        self._data(bytearray([y_start & 0xFF, (y_start >> 8) & 0xFF]))

    def _initialize(self):
        self._reset()
        self._wait_until_idle()
        self._command(0x12)
        self._wait_until_idle()
        self._command(0x01)
        self._data(bytearray([0xF9, 0x00, 0x00]))
        self._command(0x11)
        self._data(0x07)
        self._set_windows(0, 0, self.transport_width_px - 1, self.transport_height_px - 1)
        self._set_cursor(0, 0)
        self._command(0x3C)
        self._data(0x05)
        self._command(0x18)
        self._data(0x80)
        self._command(0x21)
        self._data(bytearray([0x80, 0x80]))
        self._wait_until_idle()

    @property
    def row_bytes(self):
        return self.surface_height_px // 8

    def _send_landscape_plane(self, command, buffer):
        self._command(command)
        for column_group in range(self.row_bytes - 1, -1, -1):
            self._feed_watchdog()
            base_index = column_group * self.width
            for row in range(self.width):
                self._data(buffer[base_index + row])
                if (row & 0x3F) == 0x3F:
                    self._feed_watchdog()

    def _send_solid_plane(self, command, value):
        self._command(command)
        for column_group in range(self.row_bytes - 1, -1, -1):
            self._feed_watchdog()
            del column_group
            for row in range(self.width):
                self._data(value)
                if (row & 0x3F) == 0x3F:
                    self._feed_watchdog()

    def _clear_panel(self):
        self._send_solid_plane(0x24, 0xFF)
        self._send_solid_plane(0x26, 0xFF)
        self._refresh()

    def _refresh(self):
        self._feed_watchdog()
        self._command(0x20)
        self._wait_until_idle()

    def _feed_watchdog(self):
        callback = getattr(self, "_watchdog_feed", None)
        if callback is None:
            return
        callback()

    def _sleep(self):
        self._command(0x10)
        self._data(0x01)
        _sleep_ms(2000)
        self.rst(0)

    def _show_buffers(self, black_buffer, accent_buffer):
        if not getattr(self, "_panel_primed", False):
            self._clear_panel()
            self._panel_primed = True
        self._send_landscape_plane(0x24, black_buffer)
        self._send_landscape_plane(0x26, accent_buffer)
        self._refresh()

    def _render_surface(self):
        surface = getattr(self, "_surface", None)
        if surface is None:
            surface = WaveshareEPaper213BV4Surface(self.width, self.height)
            self._surface = surface
        surface.clear(surface.background_color)
        return surface

    def draw_frame(self, frame):
        surface = self._render_surface()
        if self._surface_supports_framebuf_text(surface):
            self._render_text_with_framebuf(surface, frame)
            self._show_buffers(surface.black_buffer, surface.accent_buffer)
            return
        render_to_surface(
            frame,
            surface,
            self.font_width,
            self.font_height,
            self._glyph_lookup,
            failure_color=self.failure_color,
            rotation=getattr(self, "rotation", 0),
        )
        self._show_buffers(surface.black_buffer, surface.accent_buffer)

    def show_boot_logo(self, version, glyph_builder=None):
        surface = self._render_surface()
        if self._surface_supports_framebuf_text(surface):
            glyph_height = max(1, int(getattr(self, "font_height", 16)))
            rows = (
                "ViviPi",
                str(version or ""),
            )
            self._render_text_with_framebuf(
                surface,
                type("BootFrame", (), {
                    "rows": rows,
                    "shift_offset": (0, max(0, (self.height - (len(rows) * glyph_height)) // 2)),
                    "failure_spans": (),
                    "bottom_pixels": (),
                    "bottom_pixel_width_px": 1,
                    "bottom_pixel_height_px": 1,
                })(),
            )
            self._show_buffers(surface.black_buffer, surface.accent_buffer)
            return
        render_boot_logo_to_surface(
            surface,
            version,
            glyph_builder=glyph_builder or self._glyph_builder,
            rotation=getattr(self, "rotation", 0),
        )
        self._show_buffers(surface.black_buffer, surface.accent_buffer)