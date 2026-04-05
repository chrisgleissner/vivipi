from types import SimpleNamespace

from firmware.display import SH1107Display, _pin_number, _sample_source_coordinates, boot_logo_font_sizes, render, render_boot_logo, render_framebuffer
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