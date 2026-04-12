"""SH1107 OLED display backend."""

from __future__ import annotations

try:
    import framebuf
    from machine import Pin, SPI
except ImportError:  # pragma: no cover - imported on-device
    framebuf = None
    Pin = None
    SPI = None

try:
    import utime as time
except ImportError:  # pragma: no cover - used by CPython tests
    import time

try:
    from displays.rendering import MonochromeSurface, _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface
except ImportError as error:  # pragma: no cover - used by CPython tests
    if getattr(error, "name", None) != "displays":
        raise
    from firmware.displays.rendering import MonochromeSurface, _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface


class SH1107Display:
    def __init__(self, display_config, spi=None):
        if framebuf is None or Pin is None or SPI is None:  # pragma: no cover - imported on-device
            raise RuntimeError("machine and framebuf modules are required on device")

        self.width = int(display_config["width_px"])
        self.height = int(display_config["height_px"])
        font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
        self.font_width = int(font.get("width_px", 8)) if isinstance(font, dict) else 8
        self.font_height = int(font.get("height_px", 8)) if isinstance(font, dict) else 8
        self.contrast = int(display_config.get("brightness", 128))
        self.failure_color = str(display_config.get("failure_color", "red"))
        self.column_offset = int(display_config.get("column_offset", 0)) if isinstance(display_config, dict) else 0
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
            miso=None,
        )
        self.buffer = bytearray((self.width * self.height) // 8)
        self.framebuffer = framebuf.FrameBuffer(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self._glyph_lookup = _build_glyph_lookup(self.font_width, self.font_height)
        self._initialize()

    @property
    def native_width(self):
        return self.height

    @property
    def native_height(self):
        return self.width

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
        self.cs(1)
        self.dc(0)
        self.rst(1)
        _sleep_ms(20)
        self.rst(0)
        _sleep_ms(20)
        self.rst(1)
        _sleep_ms(20)
        for command in (
            0xAE,
            0xDC,
            0x00,
            0x20,
            0xA0,
            0xC0,
            0xA8,
            self.native_height - 1,
            0xD3,
            0x00,
            0xD5,
            0x50,
            0xD9,
            0x22,
            0xDB,
            0x35,
            0xAD,
            0x8B,
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
        transport = _rotate_buffer_clockwise(self.buffer, self.width, self.height)
        pages = self.native_height // 8
        column_start = self.column_offset
        for page in range(pages):
            self._command(0xB0 + page)
            self._command(column_start & 0x0F)
            self._command(0x10 | ((column_start >> 4) & 0x0F))
            start = page * self.native_width
            self._data(transport[start : start + self.native_width])

    def draw_frame(self, frame):
        surface = MonochromeSurface(self.width, self.height)
        render_to_surface(
            frame,
            surface,
            self.font_width,
            self.font_height,
            self._glyph_lookup,
            failure_color=getattr(self, "failure_color", "red"),
        )
        self.buffer[:] = surface.buffer
        self._show()

    def show_boot_logo(self, version, glyph_builder=None):
        surface = MonochromeSurface(self.width, self.height)
        render_boot_logo_to_surface(surface, version, glyph_builder=glyph_builder)
        self.buffer[:] = surface.buffer
        self._show()


def _sleep_ms(value):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value)
        return
    time.sleep(value / 1000.0)


def _rotate_buffer_clockwise(buffer, width, height):
    native_width = height
    native_height = width
    rotated = bytearray((native_width * native_height) // 8)
    pages = height // 8
    for page in range(pages):
        base_y = page * 8
        for x in range(width):
            byte_value = buffer[x + (page * width)]
            if not byte_value:
                continue
            for bit in range(8):
                if not ((byte_value >> bit) & 1):
                    continue
                y = base_y + bit
                native_x = native_width - 1 - y
                native_y = x
                rotated[native_x + ((native_y // 8) * native_width)] |= 1 << (native_y % 8)
    return rotated