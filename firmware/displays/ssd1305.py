"""SSD1305-compatible OLED display backend."""

from __future__ import annotations

try:
    from machine import Pin, SPI
except ImportError:  # pragma: no cover - imported on-device
    Pin = None
    SPI = None

try:
    from displays.rendering import MonochromeSurface, _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface
except ImportError as error:  # pragma: no cover - used by CPython tests
    if getattr(error, "name", None) != "displays":
        raise
    from firmware.displays.rendering import MonochromeSurface, _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface


class SSD1305Display:
    def __init__(self, display_config, spi=None):
        if Pin is None or SPI is None:  # pragma: no cover - imported on-device
            raise RuntimeError("machine module is required on device")

        self.width = int(display_config["width_px"])
        self.height = int(display_config["height_px"])
        font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
        self.font_width = int(font.get("width_px", 8)) if isinstance(font, dict) else 8
        self.font_height = int(font.get("height_px", 8)) if isinstance(font, dict) else 8
        self.contrast = int(display_config.get("brightness", 128))
        self.failure_color = str(display_config.get("failure_color", "red"))
        pins = display_config["pins"]
        self.dc = Pin(_pin_number(pins["dc"]), Pin.OUT)
        self.rst = Pin(_pin_number(pins["rst"]), Pin.OUT)
        self.cs = Pin(_pin_number(pins["cs"]), Pin.OUT)
        self.spi = spi or SPI(
            1,
            baudrate=10_000_000,
            polarity=0,
            phase=0,
            sck=Pin(_pin_number(pins["clk"])),
            mosi=Pin(_pin_number(pins["din"])),
            miso=None,
        )
        self.buffer = bytearray((self.width * self.height) // 8)
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
            0x04,
            0x10,
            0x40,
            0x81,
            0x80,
            0xA1,
            0xA6,
            0xA8,
            0x1F,
            0xC8,
            0xD3,
            0x00,
            0xD5,
            0xF0,
            0xD8,
            0x05,
            0xD9,
            0xC2,
            0xDA,
            0x12,
            0xDB,
            0x08,
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
            self._command(0x04)
            self._command(0x10)
            start = page * self.width
            self._data(self.buffer[start : start + self.width])

    def draw_frame(self, frame):
        surface = MonochromeSurface(self.width, self.height)
        render_to_surface(
            frame,
            surface,
            self.font_width,
            self.font_height,
            self._glyph_lookup,
            failure_color=self.failure_color,
        )
        self.buffer[:] = surface.buffer
        contrast = getattr(frame, "contrast", None)
        if contrast is not None and int(contrast) != int(self.contrast):
            self.set_contrast(int(contrast))
            self.contrast = int(contrast)
        self._show()

    def show_boot_logo(self, version, glyph_builder=None):
        surface = MonochromeSurface(self.width, self.height)
        render_boot_logo_to_surface(surface, version, glyph_builder=glyph_builder)
        self.buffer[:] = surface.buffer
        self._show()