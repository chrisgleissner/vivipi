from types import SimpleNamespace

from firmware.display import SH1107Display, _pin_number, _sample_source_coordinates, render, render_framebuffer
from vivipi.core.models import CheckRuntime, Status
from vivipi.core.render import InvertedSpan


def fake_glyph_lookup(character):
    if character == " ":
        return ()
    return ((0, 0),)


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
        )
    )

    assert list(display.buffer) == [0x01, 0x01, 0x00, 0x00]
    assert marker["shown"] is True


def test_set_contrast_emits_the_sh1107_contrast_commands():
    display = SH1107Display.__new__(SH1107Display)
    commands = []
    display._command = lambda value: commands.append(value)

    display.set_contrast(200)

    assert commands == [0x81, 200]


def test_scaled_sampling_spreads_pixels_across_the_target_width():
    assert _sample_source_coordinates(6) == (0, 2, 3, 4, 6, 7)


def test_render_framebuffer_inverts_only_the_requested_text_span():
    frame = SimpleNamespace(
        rows=("A|B ",),
        inverted_row=None,
        shift_offset=(0, 0),
        inverted_spans=(InvertedSpan(row_index=0, start_column=2, end_column=3),),
    )

    buffer = render_framebuffer(frame, width=4, height=8, font_width=1, font_height=8, glyph_lookup=fake_glyph_lookup)

    assert list(buffer) == [0x01, 0x01, 0xFE, 0x00]


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