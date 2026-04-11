"""Waveshare Pico ePaper 2.13 B V4 backend."""

from __future__ import annotations

try:
    import utime as time  # type: ignore[import-not-found]
    from machine import Pin, SPI  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - imported on-device
    import time

    Pin = None
    SPI = None

try:
    from displays.rendering import _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface
except ImportError:  # pragma: no cover - used by CPython tests
    from firmware.displays.rendering import _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface


def _sleep_ms(value):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value)
        return
    time.sleep(value / 1000.0)


class WaveshareEPaper213BV4Surface:
    def __init__(self, width, height, background_color="white", foreground_color="black"):
        self.logical_width = width
        self.logical_height = height
        self.width = width
        self.height = height
        self.background_color = background_color
        self.foreground_color = foreground_color
        self.supported_colors = ("white", "black", "red")
        self.padded_height = ((height + 7) // 8) * 8
        self.row_bytes = self.padded_height // 8
        self.black_buffer = bytearray(width * self.row_bytes)
        self.accent_buffer = bytearray(width * self.row_bytes)
        self.clear(background_color)

    def can_render_color(self, color_name):
        return color_name in self.supported_colors

    def clear(self, color_name):
        if color_name == "black":
            fill_black = 0x00
            fill_accent = 0xFF
        elif color_name == "red":
            fill_black = 0xFF
            fill_accent = 0x00
        else:
            fill_black = 0xFF
            fill_accent = 0xFF

        for index in range(len(self.black_buffer)):
            self.black_buffer[index] = fill_black
            self.accent_buffer[index] = fill_accent

    def _index_and_mask(self, x, y):
        byte_index = (x * self.row_bytes) + (y // 8)
        bit_mask = 1 << (y % 8)
        return byte_index, bit_mask

    def set_pixel(self, x, y, color_name):
        if not (0 <= x < self.logical_width and 0 <= y < self.logical_height):
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
        self.font_width = int(font.get("width_px", 15)) if isinstance(font, dict) else 15
        self.font_height = int(font.get("height_px", 15)) if isinstance(font, dict) else 15
        self.failure_color = str(display_config.get("failure_color", "red"))
        pins = display_config["pins"]
        self.dc = Pin(_pin_number(pins["dc"]), Pin.OUT)
        self.rst = Pin(_pin_number(pins["rst"]), Pin.OUT)
        self.cs = Pin(_pin_number(pins["cs"]), Pin.OUT)
        self.busy = Pin(_pin_number(pins["busy"]), Pin.IN)
        self.spi = spi or SPI(
            1,
            baudrate=4_000_000,
            polarity=0,
            phase=0,
            sck=Pin(_pin_number(pins["clk"])),
            mosi=Pin(_pin_number(pins["din"])),
        )
        self._glyph_lookup = _build_glyph_lookup(self.font_width, self.font_height)
        self.surface_height = ((self.height + 7) // 8) * 8

    def _command(self, value):
        self.cs(1)
        self.dc(0)
        self.cs(0)
        self.spi.write(bytearray([value]))
        self.cs(1)

    def _data(self, values):
        if isinstance(values, int):
            values = bytearray([values])
        self.cs(1)
        self.dc(1)
        self.cs(0)
        self.spi.write(values)
        self.cs(1)

    def _wait_until_idle(self, timeout_ms=20_000):
        waited_ms = 0
        while self.busy.value() == 1 and waited_ms < timeout_ms:
            _sleep_ms(20)
            waited_ms += 20

    def _reset(self):
        self.rst(1)
        _sleep_ms(20)
        self.rst(0)
        _sleep_ms(2)
        self.rst(1)
        _sleep_ms(20)

    def _initialize(self):
        self._reset()
        self._wait_until_idle()
        self._command(0x12)
        self._wait_until_idle()

        self._command(0x01)
        self._data(bytearray([0xF9, 0x00, 0x00]))
        self._command(0x11)
        self._data(0x07)

        self._command(0x44)
        self._data(bytearray([0x00, self.row_bytes - 1]))
        self._command(0x45)
        self._data(bytearray([0x00, 0x00, (self.width - 1) & 0xFF, ((self.width - 1) >> 8) & 0xFF]))

        self._command(0x3C)
        self._data(0x05)
        self._command(0x18)
        self._data(0x80)
        self._command(0x21)
        self._data(bytearray([0x80, 0x80]))
        self._command(0x4E)
        self._data(0x00)
        self._command(0x4F)
        self._data(bytearray([0x00, 0x00]))
        self._wait_until_idle()

    @property
    def row_bytes(self):
        return self.surface_height // 8

    def _refresh(self):
        self._command(0x20)
        self._wait_until_idle()

    def _sleep(self):
        self._command(0x10)
        self._data(0x01)
        _sleep_ms(2000)
        self.rst(0)

    def _show_buffers(self, black_buffer, accent_buffer):
        self._initialize()
        self._command(0x24)
        self._data(black_buffer)
        self._command(0x26)
        self._data(accent_buffer)
        self._refresh()
        self._sleep()

    def draw_frame(self, frame):
        surface = WaveshareEPaper213BV4Surface(self.width, self.height)
        render_to_surface(
            frame,
            surface,
            self.font_width,
            self.font_height,
            self._glyph_lookup,
            failure_color=self.failure_color,
        )
        self._show_buffers(surface.black_buffer, surface.accent_buffer)

    def show_boot_logo(self, version, glyph_builder=None):
        surface = WaveshareEPaper213BV4Surface(self.width, self.height)
        render_boot_logo_to_surface(surface, version, glyph_builder=glyph_builder)
        self._show_buffers(surface.black_buffer, surface.accent_buffer)