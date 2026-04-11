"""Profile-driven tri-color Waveshare Pico e-paper backends."""

from __future__ import annotations

try:
    import utime as time  # type: ignore[import-not-found]
    from machine import Pin, SPI  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - imported on-device
    import time

    Pin = None
    SPI = None

try:
    from displays.rendering import TriColorSurface, _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface
except ImportError as error:  # pragma: no cover - used by CPython tests
    if getattr(error, "name", None) != "displays":
        raise
    from firmware.displays.rendering import TriColorSurface, _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface


TRICOLOR_EPAPER_PROFILES = {
    "waveshare-pico-epaper-7.5-b-v2": {
        "baudrate": 4_000_000,
        "busy_active": 0,
        "init_id": "7in5b-v2",
        "sleep": ((0x02, ()), (0x07, (0xA5,))),
    },
}


def _sleep_ms(value):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value)
        return
    time.sleep(value / 1000.0)


class WaveshareEPaperTriColorDisplay:
    def __init__(self, display_config, spi=None):
        if Pin is None or SPI is None:  # pragma: no cover - imported on-device
            raise RuntimeError("machine module is required on device")

        self.display_type = str(display_config["type"])
        self.profile = TRICOLOR_EPAPER_PROFILES[self.display_type]
        self.width = int(display_config["width_px"])
        self.height = int(display_config["height_px"])
        font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
        self.font_width = int(font.get("width_px", 8)) if isinstance(font, dict) else 8
        self.font_height = int(font.get("height_px", 8)) if isinstance(font, dict) else 8
        self.failure_color = str(display_config.get("failure_color", "red"))
        pins = display_config["pins"]
        self.dc = Pin(_pin_number(pins["dc"]), Pin.OUT)
        self.rst = Pin(_pin_number(pins["rst"]), Pin.OUT)
        self.cs = Pin(_pin_number(pins["cs"]), Pin.OUT)
        self.busy = Pin(_pin_number(pins["busy"]), Pin.IN)
        self.spi = spi or SPI(
            1,
            baudrate=int(self.profile["baudrate"]),
            polarity=0,
            phase=0,
            sck=Pin(_pin_number(pins["clk"])),
            mosi=Pin(_pin_number(pins["din"])),
        )
        self._glyph_lookup = _build_glyph_lookup(self.font_width, self.font_height)

    def _command(self, value):
        self.cs(1)
        self.dc(0)
        self.cs(0)
        self.spi.write(bytearray([value]))
        self.cs(1)

    def _data(self, values):
        payload = bytearray(values)
        self.cs(1)
        self.dc(1)
        self.cs(0)
        self.spi.write(payload)
        self.cs(1)

    def _wait_until_idle(self, timeout_ms=120_000):
        waited_ms = 0
        busy_active = int(self.profile["busy_active"])
        while self.busy.value() == busy_active and waited_ms < timeout_ms:
            _sleep_ms(100)
            waited_ms += 100

    def _reset(self):
        self.rst(1)
        _sleep_ms(20)
        self.rst(0)
        _sleep_ms(5)
        self.rst(1)
        _sleep_ms(20)

    def _initialize(self):
        if str(self.profile["init_id"]) != "7in5b-v2":
            raise ValueError(f"unsupported tri-color init profile: {self.profile['init_id']}")
        self._reset()
        self._command(0x06)
        self._data((0x17, 0x17, 0x28, 0x17))
        self._command(0x04)
        _sleep_ms(100)
        self._wait_until_idle()
        self._command(0x00)
        self._data((0x0F,))
        self._command(0x61)
        self._data((0x03, 0x20, 0x01, 0xE0))
        self._command(0x15)
        self._data((0x00,))
        self._command(0x50)
        self._data((0x11, 0x07))
        self._command(0x60)
        self._data((0x22,))
        self._command(0x65)
        self._data((0x00, 0x00, 0x00, 0x00))

    def _send_plane(self, command, buffer, invert=False):
        self._command(command)
        payload = bytearray(1)
        self.dc(1)
        self.cs(0)
        for value in buffer:
            payload[0] = (value ^ 0xFF) if invert else value
            self.spi.write(payload)
        self.cs(1)

    def _refresh(self):
        self._command(0x12)
        _sleep_ms(100)
        self._wait_until_idle()

    def _sleep(self):
        for command, values in self.profile["sleep"]:
            self._command(int(command))
            if values:
                self._data(values)
        self.rst(0)

    def _show_buffers(self, black_buffer, accent_buffer):
        self._initialize()
        self._send_plane(0x10, black_buffer, invert=True)
        self._send_plane(0x13, accent_buffer, invert=False)
        self._refresh()
        self._sleep()

    def draw_frame(self, frame):
        surface = TriColorSurface(self.width, self.height)
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
        surface = TriColorSurface(self.width, self.height)
        render_boot_logo_to_surface(surface, version, glyph_builder=glyph_builder)
        self._show_buffers(surface.black_buffer, surface.accent_buffer)