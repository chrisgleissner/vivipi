"""Thin SH1107 text display adapter for ViviPi."""

try:
    import framebuf
    from machine import Pin, SPI
except ImportError:  # pragma: no cover - imported on-device
    framebuf = None
    Pin = None
    SPI = None


def _pin_number(value):
    return int(str(value).replace("GP", ""))


class SH1107Display:
    def __init__(self, display_config, spi=None):
        if framebuf is None or Pin is None or SPI is None:  # pragma: no cover - imported on-device
            raise RuntimeError("machine and framebuf modules are required on device")

        self.width = int(display_config.get("width_px", 128))
        self.height = int(display_config.get("height_px", 64))
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
            0x81,
            0x80,
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
            0xAF,
        ):
            self._command(command)

    def _show(self):
        pages = self.height // 8
        for page in range(pages):
            self._command(0xB0 + page)
            self._command(0x02)
            self._command(0x10)
            start = page * self.width
            self._data(self.buffer[start : start + self.width])

    def draw_frame(self, frame):
        self.framebuffer.fill(0)
        x_offset, y_offset = frame.shift_offset
        for row_index, row in enumerate(frame.rows):
            y = (row_index * 8) + y_offset
            inverted = frame.inverted_row == row_index
            if inverted:
                self.framebuffer.fill_rect(0, y, self.width, 8, 1)
            self.framebuffer.text(row, x_offset, y, 0 if inverted else 1)
        self._show()