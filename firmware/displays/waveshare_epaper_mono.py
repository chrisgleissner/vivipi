"""Profile-driven monochrome Waveshare Pico e-paper backends."""

from __future__ import annotations

try:
    import utime as time  # type: ignore[import-not-found]
    from machine import Pin, SPI  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - imported on-device
    import time

    Pin = None
    SPI = None

try:
    from displays.rendering import HorizontalMonochromeSurface, MonochromeSurface, _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface
except ImportError as error:  # pragma: no cover - used by CPython tests
    if getattr(error, "name", None) != "displays":
        raise
    from firmware.displays.rendering import HorizontalMonochromeSurface, MonochromeSurface, _build_glyph_lookup, _pin_number, render_boot_logo_to_surface, render_to_surface


WS_20_30_2IN13_V3 = bytes.fromhex(
    "804A40000000000000000000"
    "404A80000000000000000000"
    "804A40000000000000000000"
    "404A80000000000000000000"
    "000000000000000000000000"
    "0F000000000000"
    "0F00000F000002"
    "0F000000000000"
    "01000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "222222222222000000"
)

PROFILE_2IN9_PARTIAL_LUT = bytes.fromhex(
    "004000000000000000000000"
    "808000000000000000000000"
    "404000000000000000000000"
    "008000000000000000000000"
    "000000000000000000000000"
    "0A000000000001"
    "01000000000000"
    "01000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "00000000000000"
    "222222222222000000"
    "221741B03236"
)

EPD_LUT_VCOM0_4IN2 = bytes.fromhex(
    "000808000002000F0F0000010008080000020000000000000000000000000000000000000000000000"
)
EPD_LUT_WW_4IN2 = bytes.fromhex(
    "500808000002900F0F000001A008080000020000000000000000000000000000000000000000000000"
)
EPD_LUT_BW_4IN2 = bytes.fromhex(
    "500808000002900F0F000001A008080000020000000000000000000000000000000000000000000000"
)
EPD_LUT_WB_4IN2 = bytes.fromhex(
    "A00808000002900F0F0000015008080000020000000000000000000000000000000000000000000000"
)
EPD_LUT_BB_4IN2 = bytes.fromhex(
    "200808000002900F0F0000011008080000020000000000000000000000000000000000000000000000"
)
EPD_3IN7_LUT_1GRAY_GC = bytes.fromhex(
    "2A050000000000000000"
    "052A0000000000000000"
    "2A150000000000000000"
    "050A0000000000000000"
    "00000000000000000000"
    "0002030A0002060A0500"
    "00000000000000000000"
    "00000000000000000000"
    "00000000000000000000"
    "00000000000000000000"
    "2222222222"
)

MONO_EPAPER_PROFILES = {
    "waveshare-pico-epaper-2.13-v3": {
        "baudrate": 4_000_000,
        "busy_active": 1,
        "surface_kind": "vertical",
        "init_id": "2in13v3",
        "refresh": ((0x22, (0xC7,)), (0x20, ())),
        "sleep": ((0x10, (0x01,)),),
    },
    "waveshare-pico-epaper-2.13-v4": {
        "baudrate": 4_000_000,
        "busy_active": 1,
        "surface_kind": "vertical",
        "init_id": "2in13v4",
        "refresh": ((0x22, (0xF7,)), (0x20, ())),
        "sleep": ((0x10, (0x01,)),),
    },
    "waveshare-pico-epaper-2.7-v2": {
        "baudrate": 4_000_000,
        "busy_active": 1,
        "surface_kind": "vertical",
        "init_id": "2in7v2",
        "refresh": ((0x22, (0xF7,)), (0x20, ())),
        "sleep": ((0x10, (0x01,)),),
    },
    "waveshare-pico-epaper-2.7": {
        "baudrate": 4_000_000,
        "busy_active": 0,
        "surface_kind": "vertical",
        "init_id": "2in7",
        "refresh": ((0x12, ()),),
        "sleep": ((0x02, ()), (0x07, (0xA5,))),
    },
    "waveshare-pico-epaper-2.9": {
        "baudrate": 4_000_000,
        "busy_active": 1,
        "surface_kind": "vertical",
        "init_id": "2in9",
        "refresh": ((0x22, (0xF7,)), (0x20, ())),
        "sleep": ((0x10, (0x01,)),),
    },
    "waveshare-pico-epaper-3.7": {
        "baudrate": 4_000_000,
        "busy_active": 1,
        "surface_kind": "vertical",
        "init_id": "3in7",
        "refresh": ((0x20, ()),),
        "sleep": ((0x10, (0x01,)),),
    },
    "waveshare-pico-epaper-4.2": {
        "baudrate": 4_000_000,
        "busy_active": 0,
        "surface_kind": "horizontal",
        "init_id": "4in2",
        "refresh": (),
        "sleep": ((0x07, (0xA5,)),),
    },
    "waveshare-pico-epaper-4.2-v2": {
        "baudrate": 4_000_000,
        "busy_active": 0,
        "surface_kind": "horizontal",
        "init_id": "4in2v2",
        "refresh": (),
        "sleep": ((0x10, (0x01,)),),
    },
}


def _sleep_ms(value):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value)
        return
    time.sleep(value / 1000.0)


class WaveshareEPaperMonoDisplay:
    def __init__(self, display_config, spi=None):
        if Pin is None or SPI is None:  # pragma: no cover - imported on-device
            raise RuntimeError("machine module is required on device")

        self.display_type = str(display_config["type"])
        self.profile = MONO_EPAPER_PROFILES[self.display_type]
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

    def _wait_until_idle(self, timeout_ms=60_000):
        waited_ms = 0
        busy_active = int(self.profile["busy_active"])
        while self.busy.value() == busy_active and waited_ms < timeout_ms:
            _sleep_ms(100)
            waited_ms += 100

    def _reset(self, high_ms=20, low_ms=20, settle_ms=20):
        self.rst(1)
        _sleep_ms(high_ms)
        self.rst(0)
        _sleep_ms(low_ms)
        self.rst(1)
        _sleep_ms(settle_ms)

    def _initialize(self):
        init_id = str(self.profile["init_id"])
        if init_id == "2in13v3":
            self._initialize_2in13v3()
            return
        if init_id == "2in13v4":
            self._initialize_2in13v4()
            return
        if init_id == "2in7v2":
            self._initialize_2in7v2()
            return
        if init_id == "2in7":
            self._initialize_2in7()
            return
        if init_id == "2in9":
            self._initialize_2in9()
            return
        if init_id == "3in7":
            self._initialize_3in7()
            return
        if init_id == "4in2":
            self._initialize_4in2()
            return
        if init_id == "4in2v2":
            self._initialize_4in2v2()
            return
        raise ValueError(f"unsupported e-paper init profile: {init_id}")

    def _initialize_2in13v3(self):
        self._reset(20, 5, 20)
        self._wait_until_idle()
        self._command(0x12)
        self._wait_until_idle()
        self._command(0x01)
        self._data((0xF9, 0x00, 0x00))
        self._command(0x11)
        self._data((0x03,))
        self._command(0x44)
        self._data((0x00, 0x0F))
        self._command(0x45)
        self._data((0x00, 0x00, 0xF9, 0x00))
        self._command(0x4E)
        self._data((0x00,))
        self._command(0x4F)
        self._data((0x00, 0x00))
        self._command(0x3C)
        self._data((0x05,))
        self._command(0x21)
        self._data((0x00, 0x80))
        self._command(0x18)
        self._data((0x80,))
        self._command(0x32)
        self._data(WS_20_30_2IN13_V3)
        self._wait_until_idle()
        self._command(0x3F)
        self._data((0x22,))
        self._command(0x03)
        self._data((0x17,))
        self._command(0x04)
        self._data((0x41, 0x00, 0x32))
        self._command(0x2C)
        self._data((0x36,))

    def _initialize_2in13v4(self):
        self._reset(20, 5, 20)
        self._wait_until_idle()
        self._command(0x12)
        self._wait_until_idle()
        self._command(0x01)
        self._data((0xF9, 0x00, 0x00))
        self._command(0x11)
        self._data((0x03,))
        self._command(0x44)
        self._data((0x00, 0x0F))
        self._command(0x45)
        self._data((0x00, 0x00, 0xF9, 0x00))
        self._command(0x4E)
        self._data((0x00,))
        self._command(0x4F)
        self._data((0x00, 0x00))
        self._command(0x3C)
        self._data((0x05,))
        self._command(0x21)
        self._data((0x00, 0x80))
        self._command(0x18)
        self._data((0x80,))
        self._wait_until_idle()

    def _initialize_2in7v2(self):
        self._reset(200, 20, 200)
        self._command(0x12)
        self._wait_until_idle()
        self._command(0x45)
        self._data((0x00, 0x00, 0x07, 0x01))
        self._command(0x4F)
        self._data((0x00, 0x00))
        self._command(0x11)
        self._data((0x03,))
        self._wait_until_idle()

    def _initialize_2in7(self):
        self._reset(200, 200, 200)
        self._command(0x01)
        self._data((0x03, 0x00, 0x2B, 0x2B, 0x09))
        self._command(0x06)
        self._data((0x07, 0x07, 0x17))
        for command, values in (
            (0xF8, (0x60, 0xA5)),
            (0xF8, (0x89, 0xA5)),
            (0xF8, (0x90, 0x00)),
            (0xF8, (0x93, 0x2A)),
            (0xF8, (0xA0, 0xA5)),
            (0xF8, (0xA1, 0x00)),
            (0xF8, (0x73, 0x41)),
        ):
            self._command(command)
            self._data(values)
        self._command(0x16)
        self._data((0x00,))
        self._command(0x04)
        self._wait_until_idle()
        self._command(0x00)
        self._data((0xAF,))
        self._command(0x30)
        self._data((0x3A,))
        self._command(0x50)
        self._data((0x57,))
        self._command(0x82)
        self._data((0x12,))
        _sleep_ms(2)
        lut_vcom_dc = bytes.fromhex(
            "0000000800000002602828000001001400000001001212000001000000000000000000000000000000"
        )
        lut_ww = bytes.fromhex(
            "400800000002902828000001401400000001A012120000010000000000000000000000000000000000"
        )
        lut_bb = bytes.fromhex(
            "800800000002902828000001801400000001501212000001000000000000000000000000000000000000"
        )
        self._command(0x20)
        self._data(lut_vcom_dc)
        self._command(0x21)
        self._data(lut_ww)
        self._command(0x22)
        self._data(lut_ww)
        self._command(0x23)
        self._data(lut_bb)
        self._command(0x24)
        self._data(lut_bb)

    def _initialize_2in9(self):
        self._reset(200, 20, 200)
        self._wait_until_idle()
        self._command(0x12)
        self._wait_until_idle()
        self._command(0x01)
        self._data((0x27, 0x01, 0x00))
        self._command(0x11)
        self._data((0x03,))
        self._command(0x21)
        self._data((0x00, 0x80))
        self._command(0x44)
        self._data((0x00, 0x0F))
        self._command(0x45)
        self._data((0x00, 0x00, 0x27, 0x01))
        self._command(0x4E)
        self._data((0x00,))
        self._command(0x4F)
        self._data((0x00, 0x00))
        self._wait_until_idle()

    def _initialize_3in7(self):
        self._reset(20, 5, 20)
        self._command(0x12)
        _sleep_ms(300)
        self._command(0x46)
        self._data((0xF7,))
        self._wait_until_idle()
        self._command(0x47)
        self._data((0xF7,))
        self._wait_until_idle()
        self._command(0x01)
        self._data((0xDF, 0x01, 0x00))
        self._command(0x03)
        self._data((0x00,))
        self._command(0x04)
        self._data((0x41, 0xA8, 0x32))
        self._command(0x11)
        self._data((0x03,))
        self._command(0x3C)
        self._data((0x03,))
        self._command(0x0C)
        self._data((0xAE, 0xC7, 0xC3, 0xC0, 0xC0))
        self._command(0x18)
        self._data((0x80,))
        self._command(0x2C)
        self._data((0x44,))
        self._command(0x37)
        self._data((0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0x4F, 0xFF, 0xFF, 0xFF, 0xFF))
        self._command(0x44)
        self._data((0x00, 0x00, 0x17, 0x01))
        self._command(0x45)
        self._data((0x00, 0x00, 0xDF, 0x01))
        self._command(0x22)
        self._data((0xCF,))
        self._command(0x32)
        self._data(EPD_3IN7_LUT_1GRAY_GC)

    def _initialize_4in2(self):
        self._reset(20, 20, 20)
        self._command(0x01)
        self._data((0x03, 0x00, 0x2B, 0x2B))
        self._command(0x06)
        self._data((0x17, 0x17, 0x17))
        self._command(0x04)
        self._wait_until_idle()
        self._command(0x00)
        self._data((0xBF, 0x0D))
        self._command(0x30)
        self._data((0x3C,))
        self._command(0x61)
        self._data((0x01, 0x90, 0x01, 0x2C))
        self._command(0x82)
        self._data((0x28,))
        self._command(0x50)
        self._data((0x97,))
        self._command(0x20)
        self._data(EPD_LUT_VCOM0_4IN2)
        self._command(0x21)
        self._data(EPD_LUT_WW_4IN2)
        self._command(0x22)
        self._data(EPD_LUT_BW_4IN2)
        self._command(0x23)
        self._data(EPD_LUT_WB_4IN2)
        self._command(0x24)
        self._data(EPD_LUT_BB_4IN2)

    def _initialize_4in2v2(self):
        self._reset(20, 20, 20)
        self._wait_until_idle()
        self._command(0x12)
        self._wait_until_idle()
        self._command(0x21)
        self._data((0x40, 0x00))
        self._command(0x3C)
        self._data((0x05,))
        self._command(0x11)
        self._data((0x03,))
        self._command(0x44)
        self._data((0x00, 0x31))
        self._command(0x45)
        self._data((0x00, 0x00, 0x2B, 0x01))
        self._command(0x4E)
        self._data((0x00,))
        self._command(0x4F)
        self._data((0x00, 0x00))
        self._wait_until_idle()

    def _refresh(self):
        for command, values in self.profile["refresh"]:
            self._command(int(command))
            if values:
                self._data(values)
        if self.profile["refresh"]:
            self._wait_until_idle()

    def _sleep(self):
        for command, values in self.profile["sleep"]:
            self._command(int(command))
            if values:
                self._data(values)
        self.rst(0)

    def _send_vertical_buffer(self, buffer):
        self._command(0x24)
        self.dc(1)
        self.cs(0)
        column_count = self.width
        vertical_byte_count = max(1, self.height // 8)
        initial_index = column_count * (vertical_byte_count - 1)
        index = initial_index
        vertical_count = 0
        horizontal_count = 0
        payload = bytearray(1)
        for _ in range(len(buffer)):
            payload[0] = (~buffer[index]) & 0xFF
            self.spi.write(payload)
            index -= column_count
            vertical_count += 1
            vertical_count %= vertical_byte_count
            if not vertical_count:
                horizontal_count += 1
                index = initial_index + horizontal_count
        self.cs(1)
        self._refresh()
        self._sleep()

    def _send_horizontal_buffer(self, buffer):
        self._command(0x13)
        bytes_per_row = self.width // 8
        payload = bytearray(bytes_per_row)
        for row_index in range(self.height):
            start = row_index * bytes_per_row
            for offset, value in enumerate(buffer[start : start + bytes_per_row]):
                payload[offset] = value ^ 0xFF
            self._data(payload)
        self._command(0x12)
        _sleep_ms(10)
        self._wait_until_idle()
        self._sleep()

    def _render_surface(self):
        if self.profile["surface_kind"] == "horizontal":
            return HorizontalMonochromeSurface(self.width, self.height)
        return MonochromeSurface(self.width, self.height)

    def draw_frame(self, frame):
        self._initialize()
        surface = self._render_surface()
        render_to_surface(
            frame,
            surface,
            self.font_width,
            self.font_height,
            self._glyph_lookup,
            failure_color=self.failure_color,
        )
        if self.profile["surface_kind"] == "horizontal":
            self._send_horizontal_buffer(surface.buffer)
            return
        self._send_vertical_buffer(surface.buffer)

    def show_boot_logo(self, version, glyph_builder=None):
        self._initialize()
        surface = self._render_surface()
        render_boot_logo_to_surface(surface, version, glyph_builder=glyph_builder)
        if self.profile["surface_kind"] == "horizontal":
            self._send_horizontal_buffer(surface.buffer)
            return
        self._send_vertical_buffer(surface.buffer)