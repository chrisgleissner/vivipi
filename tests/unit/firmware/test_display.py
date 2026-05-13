from types import SimpleNamespace

import firmware.displays.sh1107 as sh1107_module
import firmware.displays.waveshare_epaper as waveshare_epaper_module
from firmware.display import SH1107Display, SSD1305Display, ST77xxDisplay, WaveshareEPaperMonoDisplay, WaveshareEPaperTriColorDisplay, _pin_number, _sample_source_coordinates, boot_logo_font_sizes, render, render_boot_logo, render_boot_logo_to_surface, render_framebuffer
from firmware.displays import BACKENDS, create_display
from firmware.displays.rendering import HorizontalMonochromeSurface, MonochromeSurface, RGB565Surface, _scale_glyph_pixels, render_to_surface
from firmware.displays.waveshare_epaper import WaveshareEPaper213BV4Display, WaveshareEPaper213BV4Surface
from vivipi.core.models import CheckRuntime, Status
from vivipi.core.render import InvertedSpan


def fake_glyph_lookup(character):
    if character == " ":
        return ()
    return ((0, 0),)


def lit_pixels(buffer, width, height):
    pixels = set()
    for y in range(height):
        for x in range(width):
            byte_index = x + ((y // 8) * width)
            if buffer[byte_index] & (1 << (y % 8)):
                pixels.add((x, y))
    return pixels


def test_pin_number_parses_gpio_names():
    assert _pin_number("GP14") == 14


def test_draw_frame_writes_the_rendered_buffer_and_shows_it():
    display = SH1107Display.__new__(SH1107Display)
    display.width = 4
    display.height = 8
    display.font_width = 1
    display.font_height = 8
    display.buffer = bytearray(4)
    display._glyph_lookup = fake_glyph_lookup
    marker = {"shown": False}
    display._show = lambda: marker.__setitem__("shown", True)

    display.draw_frame(
        SimpleNamespace(
            rows=("AB  ",),
            inverted_row=None,
            shift_offset=(0, 0),
            inverted_spans=(),
            failure_spans=(),
        )
    )

    assert list(display.buffer) == [0x01, 0x01, 0x00, 0x00]
    assert marker["shown"] is True


def test_draw_frame_applies_frame_contrast_when_requested():
    display = SH1107Display.__new__(SH1107Display)
    display.width = 4
    display.height = 8
    display.font_width = 1
    display.font_height = 8
    display.contrast = 128
    display.buffer = bytearray(4)
    display._glyph_lookup = fake_glyph_lookup
    display._show = lambda: None
    applied = []
    display.set_contrast = lambda value: applied.append(value)

    display.draw_frame(
        SimpleNamespace(
            rows=("A   ",),
            inverted_row=None,
            shift_offset=(0, 0),
            inverted_spans=(),
            failure_spans=(),
            contrast=132,
        )
    )

    assert applied == [132]
    assert display.contrast == 132


def test_set_contrast_emits_the_sh1107_contrast_commands():
    display = SH1107Display.__new__(SH1107Display)
    commands = []
    display._command = lambda value: commands.append(value)

    display.set_contrast(200)

    assert commands == [0x81, 200]


def test_sh1107_initialize_waits_for_reset_and_enables_dc_dc(monkeypatch):
    display = SH1107Display.__new__(SH1107Display)
    transitions = []
    commands = []
    sleeps = []

    display.width = 128
    display.height = 64
    display.cs = lambda value: transitions.append(("cs", value))
    display.dc = lambda value: transitions.append(("dc", value))
    display.rst = lambda value: transitions.append(("rst", value))
    display._command = lambda value: commands.append(value)
    display.set_contrast = lambda value: commands.extend((0x81, value))
    display.contrast = 128

    monkeypatch.setattr(sh1107_module, "_sleep_ms", lambda value: sleeps.append(value))

    display._initialize()

    assert transitions[:5] == [
        ("cs", 1),
        ("dc", 0),
        ("rst", 1),
        ("rst", 0),
        ("rst", 1),
    ]
    assert sleeps == [20, 20, 20]
    assert (0xA8, 127) == tuple(commands[commands.index(0xA8) : commands.index(0xA8) + 2])
    assert (0xD3, 0x00) == tuple(commands[commands.index(0xD3) : commands.index(0xD3) + 2])
    assert (0xAD, 0x8B) == tuple(commands[commands.index(0xAD) : commands.index(0xAD) + 2])
    assert commands[-1] == 0xAF


def test_sh1107_constructor_uses_write_only_spi_and_leaves_reset_pin_free(monkeypatch):
    class FakePin:
        OUT = "out"

        def __init__(self, number, mode=None):
            self.number = number
            self.mode = mode

        def __call__(self, value):
            return None

    class FakeSPI:
        def __init__(self, bus, **kwargs):
            self.bus = bus
            self.kwargs = kwargs

        def write(self, values):
            return None

    class FakeFrameBuffer:
        def __init__(self, buffer, width, height, mode):
            self.buffer = buffer
            self.width = width
            self.height = height
            self.mode = mode

        def fill(self, value):
            return None

        def text(self, character, x, y, color):
            return None

        def pixel(self, x, y):
            return 0

    monkeypatch.setattr(sh1107_module, "Pin", FakePin)
    monkeypatch.setattr(sh1107_module, "SPI", FakeSPI)
    monkeypatch.setattr(sh1107_module, "framebuf", SimpleNamespace(FrameBuffer=FakeFrameBuffer, MONO_VLSB="mono"))
    monkeypatch.setattr(sh1107_module, "_build_glyph_lookup", lambda width, height: fake_glyph_lookup)
    monkeypatch.setattr(sh1107_module.SH1107Display, "_initialize", lambda self: None)

    display = sh1107_module.SH1107Display(
        {
            "width_px": 128,
            "height_px": 64,
            "brightness": 128,
            "column_offset": 32,
            "pins": {"dc": "GP8", "rst": "GP12", "cs": "GP9", "clk": "GP10", "din": "GP11"},
            "font": {"width_px": 8, "height_px": 8},
        }
    )

    assert display.rst.number == 12
    assert display.spi.bus == 1
    assert display.column_offset == 32
    assert display.spi.kwargs["miso"] is None
    assert display.spi.kwargs["polarity"] == 1
    assert display.spi.kwargs["phase"] == 1
    assert display.spi.kwargs["sck"].number == 10
    assert display.spi.kwargs["mosi"].number == 11


def test_sh1107_rotate_buffer_clockwise_targets_native_64x128_layout():
    rotated = sh1107_module._rotate_buffer_clockwise(bytes([0b00000001, 0b00000010] + [0] * 14), width=16, height=8)

    assert len(rotated) == 16
    assert rotated[7] == 0b00000001
    assert rotated[6] == 0b00000010


def test_sh1107_show_writes_rotated_native_pages():
    display = SH1107Display.__new__(SH1107Display)
    display.width = 16
    display.height = 8
    display.column_offset = 0
    display.buffer = bytearray([0b00000001, 0b00000010] + [0] * 14)
    commands = []
    payloads = []
    display._command = lambda value: commands.append(value)
    display._data = lambda values: payloads.append(bytes(values))

    display._show()

    assert commands == [0xB0, 0x00, 0x10, 0xB1, 0x00, 0x10]
    assert payloads == [bytes([0, 0, 0, 0, 0, 0, 0b00000010, 0b00000001]), bytes([0] * 8)]


def test_sh1107_show_applies_column_offset_to_native_pages():
    display = SH1107Display.__new__(SH1107Display)
    display.width = 16
    display.height = 8
    display.column_offset = 4
    display.buffer = bytearray([0b00000001] + [0] * 15)

    commands = []
    payloads = []
    display._command = lambda value: commands.append(value)
    display._data = lambda values: payloads.append(bytes(values))

    display._show()

    assert commands[:3] == [0xB0, 0x04, 0x10]
    assert payloads[0][-1] == 0b00000001


def test_scaled_sampling_spreads_pixels_across_the_target_width():
    assert _sample_source_coordinates(6) == (0, 2, 3, 4, 6, 7)


def test_render_framebuffer_inverts_only_the_requested_text_span():
    frame = SimpleNamespace(
        rows=("A|B ",),
        inverted_row=None,
        shift_offset=(0, 0),
        inverted_spans=(InvertedSpan(row_index=0, start_column=2, end_column=3),),
        failure_spans=(),
    )

    buffer = render_framebuffer(frame, width=4, height=8, font_width=1, font_height=8, glyph_lookup=fake_glyph_lookup)

    assert list(buffer) == [0x01, 0x01, 0xFE, 0x00]


def test_render_framebuffer_draws_bottom_heartbeat():
    frame = SimpleNamespace(
        rows=("        ",),
        inverted_row=None,
        shift_offset=(0, 0),
        inverted_spans=(),
        failure_spans=(),
        bottom_pixels=(5, 6, 7),
    )

    buffer = render_framebuffer(frame, width=8, height=8, font_width=8, font_height=8, glyph_lookup=fake_glyph_lookup)

    assert list(buffer) == [0x00, 0x00, 0x00, 0x00, 0x00, 0x80, 0x80, 0x80]


def test_render_to_surface_supports_180_degree_rotation():
    frame = SimpleNamespace(
        rows=("A ",),
        inverted_row=None,
        shift_offset=(0, 0),
        inverted_spans=(),
        failure_spans=(),
        bottom_pixels=(),
    )
    surface = MonochromeSurface(2, 8)

    render_to_surface(frame, surface, 1, 1, fake_glyph_lookup, rotation=180)

    assert lit_pixels(surface.buffer, 2, 8) == {(1, 7)}


def test_scale_glyph_pixels_balances_horizontal_and_vertical_strokes():
    source_rows = (
        0b00011000,
        0b00011000,
        0b00011000,
        0b11111111,
        0b11111111,
        0b00011000,
        0b00011000,
        0b00011000,
    )

    scaled = set(_scale_glyph_pixels(source_rows, 15, 15))
    vertical_width = sum(1 for x in range(15) if (x, 0) in scaled)
    horizontal_height = sum(1 for y in range(15) if (0, y) in scaled)

    assert vertical_width > 0
    assert horizontal_height > 0
    assert abs(vertical_width - horizontal_height) <= 1


def test_render_boot_logo_to_surface_supports_180_degree_rotation():
    normal = MonochromeSurface(64, 32)
    rotated = MonochromeSurface(64, 32)

    render_boot_logo_to_surface(normal, "1", glyph_builder=lambda w, h: fake_glyph_lookup)
    render_boot_logo_to_surface(rotated, "1", glyph_builder=lambda w, h: fake_glyph_lookup, rotation=180)

    normal_pixels = lit_pixels(normal.buffer, normal.width, normal.height)
    rotated_pixels = lit_pixels(rotated.buffer, rotated.width, rotated.height)

    assert normal_pixels
    assert rotated_pixels == {(rotated.width - 1 - x, rotated.height - 1 - y) for x, y in normal_pixels}


def test_render_returns_a_deterministic_buffer_for_compact_failed_columns():
    checks = (
        CheckRuntime(identifier="alpha", name="Alpha", status=Status.OK),
        CheckRuntime(identifier="bravo", name="Bravo", status=Status.FAIL),
    )
    config = {
        "width_px": 16,
        "height_px": 8,
        "mode": "compact",
        "columns": 2,
        "column_separator": "|",
        "font": {"width_px": 1, "height_px": 8},
    }

    first = render(checks, config, glyph_lookup=fake_glyph_lookup)
    second = render(checks, config, glyph_lookup=fake_glyph_lookup)

    assert first == second
    assert list(first[:9]) == [0xFE, 0xFE, 0xFE, 0xFE, 0xFE, 0xFE, 0x00, 0x00, 0x01]


def test_render_defaults_to_standard_mode_when_omitted():
    buffer = render(
        (CheckRuntime(identifier="alpha", name="Alpha", status=Status.OK),),
        {
            "width_px": 8,
            "height_px": 8,
            "font": {"width_px": 1, "height_px": 8},
        },
        glyph_lookup=fake_glyph_lookup,
    )

    assert len(buffer) == 8


def test_render_returns_epaper_planes_and_uses_red_for_failures_when_supported():
    checks = (CheckRuntime(identifier="bravo", name="Bravo", status=Status.FAIL),)
    config = {
        "type": "waveshare-pico-epaper-2.13-b-v4",
        "mode": "compact",
        "columns": 1,
        "font": {"width_px": 6, "height_px": 6},
    }

    rendered = render(checks, config, glyph_lookup=fake_glyph_lookup)

    assert set(rendered) == {"black", "accent"}
    assert any(byte != 0xFF for byte in rendered["accent"])


def test_render_returns_tri_color_epaper_planes_for_large_tricolor_panels():
    checks = (CheckRuntime(identifier="bravo", name="Bravo", status=Status.FAIL),)
    config = {
        "type": "waveshare-pico-epaper-7.5-b-v2",
        "mode": "compact",
        "columns": 1,
        "font": {"width_px": 6, "height_px": 6},
    }

    rendered = render(checks, config, glyph_lookup=fake_glyph_lookup)

    assert set(rendered) == {"black", "accent"}
    assert rendered["black"][0] == 0xFF
    assert rendered["accent"][0] != 0xFF


def test_render_returns_rgb565_buffer_for_raw_lcd_config():
    checks = (CheckRuntime(identifier="bravo", name="Bravo", status=Status.FAIL),)
    config = {
        "type": "waveshare-pico-lcd-0.96",
        "mode": "compact",
        "columns": 1,
        "font": {"width_px": 6, "height_px": 8},
    }

    rendered = render(checks, config, glyph_lookup=fake_glyph_lookup)

    assert isinstance(rendered, bytearray)
    assert len(rendered) == 160 * 80 * 2
    assert any(byte != 0 for byte in rendered)


def test_create_display_selects_backend_from_display_type(monkeypatch):
    created = []

    class FakeOLED:
        def __init__(self, config, spi=None):
            created.append(("oled", config["backend"], spi))

    class FakeEPaper:
        def __init__(self, config, spi=None):
            created.append(("epaper", config["backend"], spi))

    class FakeLCD:
        def __init__(self, config, spi=None):
            created.append(("lcd", config["backend"], spi))

    class FakeOLED23:
        def __init__(self, config, spi=None):
            created.append(("oled23", config["backend"], spi))

    class FakeMonoEPaper:
        def __init__(self, config, spi=None):
            created.append(("mono-epaper", config["backend"], spi))

    class FakeTriColorEPaper:
        def __init__(self, config, spi=None):
            created.append(("tri-epaper", config["backend"], spi))

    monkeypatch.setitem(BACKENDS, "sh1107", FakeOLED)
    monkeypatch.setitem(BACKENDS, "ssd1305", FakeOLED23)
    monkeypatch.setitem(BACKENDS, "st77xx", FakeLCD)
    monkeypatch.setitem(BACKENDS, "waveshare-epaper-2.13-b-v4", FakeEPaper)
    monkeypatch.setitem(BACKENDS, "waveshare-epaper-mono", FakeMonoEPaper)
    monkeypatch.setitem(BACKENDS, "waveshare-epaper-tricolor", FakeTriColorEPaper)

    create_display({"type": "waveshare-pico-oled-1.3"}, spi="oled-spi")
    create_display({"type": "waveshare-pico-oled-2.23"}, spi="oled23-spi")
    create_display({"type": "waveshare-pico-lcd-1.3"}, spi="lcd-spi")
    create_display({"type": "waveshare-pico-epaper-2.13-b-v4"}, spi="epaper-spi")
    create_display({"type": "waveshare-pico-epaper-2.9"}, spi="mono-epaper-spi")
    create_display({"type": "waveshare-pico-epaper-7.5-b-v2"}, spi="tri-epaper-spi")

    assert created == [
        ("oled", "sh1107", "oled-spi"),
        ("oled23", "ssd1305", "oled23-spi"),
        ("lcd", "st77xx", "lcd-spi"),
        ("epaper", "waveshare-epaper-2.13-b-v4", "epaper-spi"),
        ("mono-epaper", "waveshare-epaper-mono", "mono-epaper-spi"),
        ("tri-epaper", "waveshare-epaper-tricolor", "tri-epaper-spi"),
    ]


def test_epaper_surface_uses_vendor_landscape_vlsb_layout():
    surface = WaveshareEPaper213BV4Surface(width=250, height=122)

    surface.clear("white")
    surface.set_pixel(0, 0, "red")
    surface.set_pixel(8, 0, "black")

    assert len(surface.black_buffer) == 16 * 250
    assert len(surface.accent_buffer) == 16 * 250
    assert surface.black_buffer[0] == 0xFF
    assert surface.accent_buffer[0] == 0xFE
    assert surface.black_buffer[8] == 0xFE
    assert surface.accent_buffer[8] == 0xFF


def test_epaper_v4_initialize_matches_vendor_landscape_profile():
    display = WaveshareEPaper213BV4Display.__new__(WaveshareEPaper213BV4Display)
    display.width = 250
    display.height = 122

    resets = []
    waits = []
    commands = []
    payloads = []

    display._reset = lambda: resets.append(True)
    display._wait_until_idle = lambda timeout_ms=20_000: waits.append(timeout_ms)
    display._command = lambda value: commands.append(value)
    display._data = lambda values: payloads.append(bytes([values]) if isinstance(values, int) else bytes(values))

    display._initialize()

    assert resets == [True]
    assert waits == [20_000, 20_000, 20_000]
    assert commands == [0x12, 0x01, 0x11, 0x44, 0x45, 0x4E, 0x4F, 0x3C, 0x18, 0x21]
    assert payloads == [
        b"\xF9\x00\x00",
        b"\x07",
        b"\x00\x0F",
        b"\x00\x00\xF9\x00",
        b"\x00",
        b"\x00\x00",
        b"\x05",
        b"\x80",
        b"\x80\x80",
    ]


def test_epaper_v4_show_buffers_streams_vendor_landscape_order():
    display = WaveshareEPaper213BV4Display.__new__(WaveshareEPaper213BV4Display)
    display.width = 4
    display.height = 9

    commands = []
    payloads = []
    phases = []

    display._initialize = lambda: phases.append("init")
    display._command = lambda value: commands.append(value)
    display._data = lambda values: payloads.append(int(values))
    display._refresh = lambda: phases.append("refresh")

    display._show_buffers(bytearray(range(8)), bytearray(range(20, 28)))

    assert commands == [0x24, 0x26]
    assert payloads == [4, 5, 6, 7, 0, 1, 2, 3, 24, 25, 26, 27, 20, 21, 22, 23]
    assert phases == ["refresh"]


def test_epaper_v4_wait_until_idle_matches_vendor_polling(monkeypatch):
    display = WaveshareEPaper213BV4Display.__new__(WaveshareEPaper213BV4Display)

    class FakeBusy:
        def __init__(self):
            self.values = [1, 1, 0]

        def value(self):
            return self.values.pop(0)

    sleeps = []
    display.busy = FakeBusy()
    monkeypatch.setattr(waveshare_epaper_module, "_sleep_ms", lambda value: sleeps.append(value))

    display._wait_until_idle()

    assert sleeps == [10, 10, 20]


def test_epaper_v4_constructor_uses_busy_pull_up_when_available(monkeypatch):
    class FakePin:
        OUT = "out"
        IN = "in"
        PULL_UP = "pull_up"

        def __init__(self, number, mode=None, pull=None):
            self.number = number
            self.mode = mode
            self.pull = pull

        def __call__(self, value):
            return None

        def value(self):
            return 0

    class FakeSPI:
        def __init__(self, bus):
            self.bus = bus
            self.init_calls = []

        def init(self, **kwargs):
            self.init_calls.append(kwargs)

        def write(self, values):
            return None

    monkeypatch.setattr(waveshare_epaper_module, "Pin", FakePin)
    monkeypatch.setattr(waveshare_epaper_module, "SPI", FakeSPI)
    monkeypatch.setattr(waveshare_epaper_module, "_build_glyph_lookup", lambda width, height: fake_glyph_lookup)
    init_calls = []
    monkeypatch.setattr(waveshare_epaper_module.WaveshareEPaper213BV4Display, "_initialize", lambda self: init_calls.append(True))

    display = WaveshareEPaper213BV4Display(
        {
            "width_px": 250,
            "height_px": 122,
            "pins": {"dc": "GP8", "rst": "GP12", "cs": "GP9", "busy": "GP13", "clk": "GP10", "din": "GP11"},
            "font": {"width_px": 10, "height_px": 10},
            "failure_color": "red",
        }
    )

    assert display.busy.number == 13
    assert display.busy.mode == FakePin.IN
    assert display.busy.pull == FakePin.PULL_UP
    assert display.spi.init_calls == [{"baudrate": 4_000_000}]
    assert init_calls == [True]


def test_boot_logo_font_sizes_scale_to_screen_dimensions():
    title_font, version_font = boot_logo_font_sizes(128, 64, "0.1.0")

    assert 6 <= title_font <= 32
    assert 6 <= version_font <= 32
    assert title_font > version_font


def test_boot_logo_font_sizes_clamp_to_minimum_for_tiny_screen():
    title_font, version_font = boot_logo_font_sizes(36, 12, "0.1.0-abcdef12")

    assert title_font == 6
    assert version_font == 6


def test_boot_logo_font_sizes_return_zero_version_font_when_no_version():
    title_font, version_font = boot_logo_font_sizes(128, 64, "")

    assert title_font > 0
    assert version_font == 0


def test_boot_logo_font_sizes_leave_horizontal_headroom_on_128x64_panel():
    title_font, version_font = boot_logo_font_sizes(128, 64, "0.1.0")

    assert 6 <= title_font < 21
    assert 0 < version_font < title_font


def test_render_boot_logo_centers_title_with_visible_side_margins():
    surface = MonochromeSurface(128, 64)

    def block_builder(width, height):
        pixels = tuple((x, y) for y in range(height) for x in range(width))
        return lambda character: () if character == " " else pixels

    render_boot_logo_to_surface(surface, "", glyph_builder=block_builder)

    lit_columns = [index for index in range(surface.width) if any(surface.buffer[index + (page * surface.width)] for page in range(surface.height // 8))]

    assert lit_columns[0] >= 6
    assert lit_columns[-1] <= 121


def test_render_boot_logo_produces_correct_buffer_size():
    buffer = render_boot_logo(128, 64, "0.1.0", glyph_builder=lambda w, h: fake_glyph_lookup)

    assert len(buffer) == (128 * 64) // 8


def test_render_boot_logo_has_lit_pixels():
    buffer = render_boot_logo(128, 64, "0.1.0", glyph_builder=lambda w, h: fake_glyph_lookup)

    assert any(byte != 0 for byte in buffer)


def test_render_boot_logo_without_version_still_renders_title():
    buffer = render_boot_logo(128, 64, "", glyph_builder=lambda w, h: fake_glyph_lookup)

    assert any(byte != 0 for byte in buffer)


def test_show_boot_logo_writes_buffer_and_shows():
    display = SH1107Display.__new__(SH1107Display)
    display.width = 128
    display.height = 64
    display.buffer = bytearray((128 * 64) // 8)
    shown = {"called": False}
    display._show = lambda: shown.__setitem__("called", True)

    display.show_boot_logo("0.1.0", glyph_builder=lambda w, h: fake_glyph_lookup)

    assert shown["called"] is True
    assert len(display.buffer) == (128 * 64) // 8


def test_ssd1305_draw_frame_writes_buffer_and_shows():
    display = SSD1305Display.__new__(SSD1305Display)
    display.width = 4
    display.height = 8
    display.font_width = 1
    display.font_height = 8
    display.failure_color = "red"
    display.buffer = bytearray(4)
    display._glyph_lookup = fake_glyph_lookup
    shown = {"called": False}
    display._show = lambda: shown.__setitem__("called", True)

    display.draw_frame(
        SimpleNamespace(
            rows=("AB  ",),
            inverted_row=None,
            shift_offset=(0, 0),
            inverted_spans=(),
            failure_spans=(),
        )
    )

    assert list(display.buffer) == [0x01, 0x01, 0x00, 0x00]
    assert shown["called"] is True


def test_st77xx_set_brightness_maps_to_pwm_range():
    display = ST77xxDisplay.__new__(ST77xxDisplay)
    duty = {"value": None}
    display.backlight = SimpleNamespace(duty_u16=lambda value: duty.__setitem__("value", value))

    display.set_brightness(128)

    assert duty["value"] == (128 * 65535) // 255


def test_st77xx_draw_frame_writes_rgb565_buffer_and_shows():
    display = ST77xxDisplay.__new__(ST77xxDisplay)
    display.width = 2
    display.height = 1
    display.font_width = 1
    display.font_height = 1
    display.failure_color = "red"
    display.color_values = {"black": 0x0000, "white": 0xFFFF, "red": 0xF800}
    display.buffer = bytearray(4)
    display._glyph_lookup = fake_glyph_lookup
    shown = {"called": False}
    display._show = lambda: shown.__setitem__("called", True)

    display.draw_frame(
        SimpleNamespace(
            rows=("A ",),
            inverted_row=None,
            shift_offset=(0, 0),
            inverted_spans=(),
            failure_spans=(),
        )
    )

    assert shown["called"] is True
    assert display.buffer == bytearray([0x00, 0x00, 0xFF, 0xFF])


def test_waveshare_epaper_mono_renders_vertical_surface_for_landscape_profiles():
    display = WaveshareEPaperMonoDisplay.__new__(WaveshareEPaperMonoDisplay)
    display.width = 8
    display.height = 8
    display.font_width = 1
    display.font_height = 8
    display.failure_color = "red"
    display.profile = {"surface_kind": "vertical"}
    display._glyph_lookup = fake_glyph_lookup
    display._initialize = lambda: None
    sent = {"buffer": None}
    display._send_vertical_buffer = lambda buffer: sent.__setitem__("buffer", buffer)
    display._send_horizontal_buffer = lambda buffer: (_ for _ in ()).throw(AssertionError("horizontal transport should not run"))

    display.draw_frame(
        SimpleNamespace(
            rows=("A       ",),
            inverted_row=None,
            shift_offset=(0, 0),
            inverted_spans=(),
            failure_spans=(),
        )
    )

    assert isinstance(display._render_surface(), MonochromeSurface)
    assert sent["buffer"] is not None
    assert len(sent["buffer"]) == 8


def test_waveshare_epaper_mono_renders_horizontal_surface_for_large_profile():
    display = WaveshareEPaperMonoDisplay.__new__(WaveshareEPaperMonoDisplay)
    display.width = 8
    display.height = 8
    display.font_width = 1
    display.font_height = 8
    display.failure_color = "red"
    display.profile = {"surface_kind": "horizontal"}
    display._glyph_lookup = fake_glyph_lookup
    display._initialize = lambda: None
    sent = {"buffer": None}
    display._send_horizontal_buffer = lambda buffer: sent.__setitem__("buffer", buffer)
    display._send_vertical_buffer = lambda buffer: (_ for _ in ()).throw(AssertionError("vertical transport should not run"))

    display.draw_frame(
        SimpleNamespace(
            rows=("A       ",),
            inverted_row=None,
            shift_offset=(0, 0),
            inverted_spans=(),
            failure_spans=(),
        )
    )

    assert isinstance(display._render_surface(), HorizontalMonochromeSurface)
    assert sent["buffer"] is not None
    assert len(sent["buffer"]) == 8


def test_waveshare_epaper_tricolor_draw_frame_emits_both_planes():
    display = WaveshareEPaperTriColorDisplay.__new__(WaveshareEPaperTriColorDisplay)
    display.width = 8
    display.height = 1
    display.font_width = 1
    display.font_height = 1
    display.failure_color = "red"
    display._glyph_lookup = fake_glyph_lookup
    sent = {"black": None, "accent": None}
    display._show_buffers = lambda black, accent: (sent.__setitem__("black", black), sent.__setitem__("accent", accent))

    display.draw_frame(
        SimpleNamespace(
            rows=("A       ",),
            inverted_row=None,
            shift_offset=(0, 0),
            inverted_spans=(),
            failure_spans=(),
        )
    )

    assert sent["black"] is not None
    assert sent["accent"] is not None
    assert len(sent["black"]) == 1
    assert len(sent["accent"]) == 1


def test_waveshare_epaper_v4_reuses_surface_between_frames():
    display = WaveshareEPaper213BV4Display.__new__(WaveshareEPaper213BV4Display)
    display.width = 8
    display.height = 8
    display.font_width = 1
    display.font_height = 8
    display.failure_color = "red"
    display.rotation = 0
    display._glyph_lookup = fake_glyph_lookup
    display._surface = WaveshareEPaper213BV4Surface(8, 8)
    sent = {"calls": 0, "black": None, "accent": None}
    display._show_buffers = lambda black, accent: (
        sent.__setitem__("calls", sent["calls"] + 1),
        sent.__setitem__("black", bytes(black)),
        sent.__setitem__("accent", bytes(accent)),
    )

    display.draw_frame(
        SimpleNamespace(
            rows=("A       ",),
            inverted_row=None,
            shift_offset=(0, 0),
            inverted_spans=(),
            failure_spans=(),
        )
    )
    first_surface_id = id(display._surface)

    display.draw_frame(
        SimpleNamespace(
            rows=("        ",),
            inverted_row=None,
            shift_offset=(0, 0),
            inverted_spans=(),
            failure_spans=(),
        )
    )

    assert id(display._surface) == first_surface_id
    assert sent["calls"] == 2
    assert sent["black"] == bytes([0xFF] * len(sent["black"]))
    assert sent["accent"] == bytes([0xFF] * len(sent["accent"]))


def test_waveshare_epaper_v4_feeds_watchdog_while_waiting_for_busy_pin(monkeypatch):
    display = WaveshareEPaper213BV4Display.__new__(WaveshareEPaper213BV4Display)
    busy_values = iter([1, 1, 0])
    display.busy = SimpleNamespace(value=lambda: next(busy_values))
    feed_calls = []
    sleep_calls = []
    display._watchdog_feed = lambda: feed_calls.append(True)

    monkeypatch.setattr(waveshare_epaper_module, "_sleep_ms", lambda value: sleep_calls.append(value))

    display._wait_until_idle(timeout_ms=40)

    assert len(feed_calls) == 3
    assert sleep_calls == [10, 10, 20]


def test_rgb565_surface_encodes_little_endian_pixels():
    surface = RGB565Surface(1, 1, color_values={"black": 0x0000, "white": 0xFFFF, "red": 0xF800})

    surface.clear("black")
    surface.set_pixel(0, 0, "red")

    assert surface.buffer == bytearray([0x00, 0xF8])
