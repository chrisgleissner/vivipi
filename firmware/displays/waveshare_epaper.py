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
        self.rst = Pin(_pin_number(pins["rst"]), Pin.OUT)
        self.busy = _busy_pin(_pin_number(pins["busy"]))
        self.cs = Pin(_pin_number(pins["cs"]), Pin.OUT)
        if spi is None:
            spi_instance = SPI(1)
            spi_instance.init(baudrate=4_000_000)
            self.spi = spi_instance
        else:
            self.spi = spi
        # Configure DC pin AFTER SPI init so it isn't shadowed by the
        # SPI peripheral's default MISO assignment on the same GPIO.
        self.dc = Pin(_pin_number(pins["dc"]), Pin.OUT)
        self._glyph_builder = lambda width, height: _build_glyph_lookup(width, height)
        self._glyph_lookup = self._glyph_builder(self.font_width, self.font_height)
        self.surface_height = ((self.height + 7) // 8) * 8
        self._surface = WaveshareEPaper213BV4Surface(self.width, self.height)
        self._watchdog_feed = None
        self._initialize()

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
        self.cs(1)
        self.dc(0)
        self.cs(0)
        self.spi.write(bytearray([value]))
        self.cs(1)

    def _data(self, values):
        payload = bytearray([values]) if isinstance(values, int) else bytearray(values)
        self.cs(1)
        self.dc(1)
        self.cs(0)
        self.spi.write(payload)
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
        self._data((x_start >> 3) & 0xFF)
        self._data((x_end >> 3) & 0xFF)
        self._command(0x45)
        self._data(y_start & 0xFF)
        self._data((y_start >> 8) & 0xFF)
        self._data(y_end & 0xFF)
        self._data((y_end >> 8) & 0xFF)

    def _set_cursor(self, x_start, y_start):
        self._command(0x4E)
        self._data(x_start & 0xFF)
        self._command(0x4F)
        self._data(y_start & 0xFF)
        self._data((y_start >> 8) & 0xFF)

    def _initialize(self):
        self._reset()
        self._wait_until_idle()
        self._command(0x12)
        self._wait_until_idle()
        self._command(0x01)
        self._data(0xF9)
        self._data(0x00)
        self._data(0x00)
        self._command(0x11)
        self._data(0x07)
        self._set_windows(0, 0, self.transport_width_px - 1, self.transport_height_px - 1)
        self._set_cursor(0, 0)
        self._command(0x3C)
        self._data(0x05)
        self._command(0x18)
        self._data(0x80)
        self._command(0x21)
        self._data(0x80)
        self._data(0x80)
        self._wait_until_idle()

    @property
    def row_bytes(self):
        return self.surface_height_px // 8

    def _send_landscape_plane(self, command, buffer):
        """Stream a surface plane to the panel in vendor landscape order.

        Mirrors the Waveshare 2.13-B-V4 landscape reference: with data entry
        mode 0x07 (Y-first, X and Y incrementing), the panel scans 250
        consecutive Y rows for one X byte before advancing X. Iterating
        ``column_group`` from ``row_bytes-1`` down to 0 and ``row`` from 0 to
        ``width`` walks the MONO_VLSB surface buffer in exactly that order.
        Each byte is sent as its own SPI transaction with CS toggled,
        mirroring the vendor reference which is the only known-working
        pattern for this panel's RAM data write path.
        """
        self._command(command)
        surface_width = self.width
        for column_group in range(self.row_bytes - 1, -1, -1):
            self._feed_watchdog()
            base_index = column_group * surface_width
            for row in range(surface_width):
                self._data(buffer[base_index + row])
        self._feed_watchdog()

    def _refresh(self):
        self._feed_watchdog()
        self._command(0x22)
        self._data(0xF7)
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
        self._initialize()
        self._send_landscape_plane(0x24, black_buffer)
        self._send_landscape_plane(0x26, accent_buffer)
        self._refresh()
        self._sleep()

    def _render_surface(self):
        surface = getattr(self, "_surface", None)
        if surface is None:
            surface = WaveshareEPaper213BV4Surface(self.width, self.height)
            self._surface = surface
        surface.clear(surface.background_color)
        return surface

    def draw_frame(self, frame):
        surface = self._render_surface()
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
        render_boot_logo_to_surface(
            surface,
            version,
            glyph_builder=glyph_builder or self._glyph_builder,
            rotation=getattr(self, "rotation", 0),
        )
        self._show_buffers(surface.black_buffer, surface.accent_buffer)
