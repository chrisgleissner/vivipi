"""ST7735S and ST7789 LCD backends for Waveshare Pico display modules."""

from __future__ import annotations

try:
    from machine import PWM, Pin, SPI
except ImportError:  # pragma: no cover - imported on-device
    PWM = None
    Pin = None
    SPI = None

from firmware.displays.rendering import RGB565Surface, _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface


def _sequence(command, data=(), delay_ms=0):
    return {"command": command, "data": tuple(data), "delay_ms": delay_ms}


ST7789_SEQUENCE = (
    _sequence(0x36, (0x70,)),
    _sequence(0x3A, (0x05,)),
    _sequence(0xB2, (0x0C, 0x0C, 0x00, 0x33, 0x33)),
    _sequence(0xB7, (0x35,)),
    _sequence(0xBB, (0x19,)),
    _sequence(0xC0, (0x2C,)),
    _sequence(0xC2, (0x01,)),
    _sequence(0xC3, (0x12,)),
    _sequence(0xC4, (0x20,)),
    _sequence(0xC6, (0x0F,)),
    _sequence(0xD0, (0xA4, 0xA1)),
    _sequence(0xE0, (0xD0, 0x04, 0x0D, 0x11, 0x13, 0x2B, 0x3F, 0x54, 0x4C, 0x18, 0x0D, 0x0B, 0x1F, 0x23)),
    _sequence(0xE1, (0xD0, 0x04, 0x0C, 0x11, 0x13, 0x2C, 0x3F, 0x44, 0x51, 0x2F, 0x1F, 0x1F, 0x20, 0x23)),
    _sequence(0x21),
    _sequence(0x11, delay_ms=120),
    _sequence(0x29),
)

ST7735R_SEQUENCE = (
    _sequence(0x36, (0x70,)),
    _sequence(0x3A, (0x05,)),
    _sequence(0xB1, (0x01, 0x2C, 0x2D)),
    _sequence(0xB2, (0x01, 0x2C, 0x2D)),
    _sequence(0xB3, (0x01, 0x2C, 0x2D, 0x01, 0x2C, 0x2D)),
    _sequence(0xB4, (0x07,)),
    _sequence(0xC0, (0xA2, 0x02, 0x84)),
    _sequence(0xC1, (0xC5,)),
    _sequence(0xC2, (0x0A, 0x00)),
    _sequence(0xC3, (0x8A, 0x2A)),
    _sequence(0xC4, (0x8A, 0xEE)),
    _sequence(0xC5, (0x0E,)),
    _sequence(0xE0, (0x0F, 0x1A, 0x0F, 0x18, 0x2F, 0x28, 0x20, 0x22, 0x1F, 0x1B, 0x23, 0x37, 0x00, 0x07, 0x02, 0x10)),
    _sequence(0xE1, (0x0F, 0x1B, 0x0F, 0x17, 0x33, 0x2C, 0x29, 0x2E, 0x30, 0x30, 0x39, 0x3F, 0x00, 0x07, 0x03, 0x10)),
    _sequence(0xF0, (0x01,)),
    _sequence(0xF6, (0x00,)),
    _sequence(0x11, delay_ms=120),
    _sequence(0x29),
)

ST7735S_096_SEQUENCE = (
    _sequence(0x11, delay_ms=120),
    _sequence(0x21),
    _sequence(0x21),
    _sequence(0xB1, (0x05, 0x3A, 0x3A)),
    _sequence(0xB2, (0x05, 0x3A, 0x3A)),
    _sequence(0xB3, (0x05, 0x3A, 0x3A, 0x05, 0x3A, 0x3A)),
    _sequence(0xB4, (0x03,)),
    _sequence(0xC0, (0x62, 0x02, 0x04)),
    _sequence(0xC1, (0xC0,)),
    _sequence(0xC2, (0x0D, 0x00)),
    _sequence(0xC3, (0x8D, 0x6A)),
    _sequence(0xC4, (0x8D, 0xEE)),
    _sequence(0xC5, (0x0E,)),
    _sequence(0xE0, (0x10, 0x0E, 0x02, 0x03, 0x0E, 0x07, 0x02, 0x07, 0x0A, 0x12, 0x27, 0x37, 0x00, 0x0D, 0x0E, 0x10)),
    _sequence(0xE1, (0x10, 0x0E, 0x03, 0x03, 0x0F, 0x06, 0x02, 0x08, 0x0A, 0x13, 0x26, 0x36, 0x00, 0x0D, 0x0E, 0x10)),
    _sequence(0x3A, (0x05,)),
    _sequence(0x36, (0xA8,)),
    _sequence(0x29),
)

ST77XX_PROFILES = {
    "waveshare-pico-lcd-0.96": {
        "baudrate": 10_000_000,
        "color_values": {"black": 0x0000, "white": 0xFFFF, "red": 0x00F8},
        "window": (1, 26),
        "sequence": ST7735S_096_SEQUENCE,
    },
    "waveshare-pico-lcd-1.14": {
        "baudrate": 10_000_000,
        "color_values": {"black": 0x0000, "white": 0xFFFF, "red": 0x07E0},
        "window": (40, 53),
        "sequence": ST7789_SEQUENCE,
    },
    "waveshare-pico-lcd-1.14-v2": {
        "baudrate": 10_000_000,
        "color_values": {"black": 0x0000, "white": 0xFFFF, "red": 0x07E0},
        "window": (40, 53),
        "sequence": ST7789_SEQUENCE,
    },
    "waveshare-pico-lcd-1.3": {
        "baudrate": 40_000_000,
        "color_values": {"black": 0x0000, "white": 0xFFFF, "red": 0x07E0},
        "window": (0, 0),
        "sequence": ST7789_SEQUENCE,
    },
    "waveshare-pico-lcd-1.44": {
        "baudrate": 10_000_000,
        "color_values": {"black": 0x0000, "white": 0xFFFF, "red": 0xF800},
        "window": (1, 2),
        "sequence": ST7735R_SEQUENCE,
    },
    "waveshare-pico-lcd-1.8": {
        "baudrate": 10_000_000,
        "color_values": {"black": 0x0000, "white": 0xFFFF, "red": 0x07E0},
        "window": (1, 2),
        "sequence": ST7735R_SEQUENCE,
    },
    "waveshare-pico-lcd-2.0": {
        "baudrate": 40_000_000,
        "color_values": {"black": 0x0000, "white": 0xFFFF, "red": 0x07E0},
        "window": (0, 0),
        "sequence": ST7789_SEQUENCE,
    },
}


class ST77xxDisplay:
    def __init__(self, display_config, spi=None):
        if Pin is None or SPI is None or PWM is None:  # pragma: no cover - imported on-device
            raise RuntimeError("machine module is required on device")

        self.display_type = str(display_config["type"])
        self.profile = ST77XX_PROFILES[self.display_type]
        self.width = int(display_config["width_px"])
        self.height = int(display_config["height_px"])
        font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
        self.font_width = int(font.get("width_px", 8)) if isinstance(font, dict) else 8
        self.font_height = int(font.get("height_px", 8)) if isinstance(font, dict) else 8
        self.failure_color = str(display_config.get("failure_color", "red"))
        self.brightness = int(display_config.get("brightness", 192))
        self.color_values = dict(self.profile["color_values"])
        pins = display_config["pins"]
        self.dc = Pin(_pin_number(pins["dc"]), Pin.OUT)
        self.rst = Pin(_pin_number(pins["rst"]), Pin.OUT)
        self.cs = Pin(_pin_number(pins["cs"]), Pin.OUT)
        self.backlight = PWM(Pin(_pin_number(pins["bl"])))
        self.backlight.freq(1000)
        self.spi = spi or SPI(
            1,
            baudrate=int(self.profile["baudrate"]),
            polarity=0,
            phase=0,
            sck=Pin(_pin_number(pins["clk"])),
            mosi=Pin(_pin_number(pins["din"])),
        )
        self.buffer = bytearray(self.width * self.height * 2)
        self._glyph_lookup = _build_glyph_lookup(self.font_width, self.font_height)
        self._initialize()

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

    def _delay(self, delay_ms):
        try:
            import utime as time  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover - imported on-device
            import time

        if delay_ms <= 0:
            return
        if hasattr(time, "sleep_ms"):
            time.sleep_ms(delay_ms)
            return
        time.sleep(delay_ms / 1000.0)

    def _initialize(self):
        self.rst(1)
        self.rst(0)
        self.rst(1)
        for step in self.profile["sequence"]:
            self._command(int(step["command"]))
            data = tuple(step["data"])
            if data:
                self._data(data)
            self._delay(int(step["delay_ms"]))
        self.set_brightness(self.brightness)

    def set_brightness(self, value):
        brightness = max(0, min(255, int(value)))
        self.backlight.duty_u16((brightness * 65535) // 255)

    def _show(self):
        x_start, y_start = self.profile["window"]
        x_end = x_start + self.width - 1
        y_end = y_start + self.height - 1
        self._command(0x2A)
        self._data(((x_start >> 8) & 0xFF, x_start & 0xFF, (x_end >> 8) & 0xFF, x_end & 0xFF))
        self._command(0x2B)
        self._data(((y_start >> 8) & 0xFF, y_start & 0xFF, (y_end >> 8) & 0xFF, y_end & 0xFF))
        self._command(0x2C)
        self.cs(1)
        self.dc(1)
        self.cs(0)
        self.spi.write(self.buffer)
        self.cs(1)

    def draw_frame(self, frame):
        surface = RGB565Surface(self.width, self.height, color_values=self.color_values)
        render_to_surface(
            frame,
            surface,
            self.font_width,
            self.font_height,
            self._glyph_lookup,
            failure_color=self.failure_color,
        )
        self.buffer[:] = surface.buffer
        self._show()

    def show_boot_logo(self, version, glyph_builder=None):
        surface = RGB565Surface(self.width, self.height, color_values=self.color_values)
        render_boot_logo_to_surface(surface, version, glyph_builder=glyph_builder)
        self.buffer[:] = surface.buffer
        self._show()