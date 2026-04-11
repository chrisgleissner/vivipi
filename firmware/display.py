"""Display facade and compatibility exports for ViviPi firmware."""

from __future__ import annotations

from vivipi.core.display import infer_default_font, normalize_display_config, supported_font_sizes
from vivipi.core.models import AppState, DisplayMode
from vivipi.core.render import render_frame

try:
    from displays import SH1107Display, SSD1305Display, ST77xxDisplay, WaveshareEPaperMonoDisplay, create_display
    from displays.rendering import (
        HorizontalMonochromeSurface,
        MonochromeSurface,
        RGB565Surface,
        TriColorSurface,
        _build_glyph_lookup,
        _pin_number,
        _sample_source_coordinates,
        boot_logo_font_sizes,
        render_boot_logo,
        render_boot_logo_to_surface,
        render_framebuffer,
        render_to_surface,
    )
    from displays.waveshare_epaper import WaveshareEPaper213BV4Display as _WaveshareEPaper213BV4Display, WaveshareEPaper213BV4Surface
    from displays.waveshare_epaper_tricolor import WaveshareEPaperTriColorDisplay
except ImportError:  # pragma: no cover - used by CPython tests
    from firmware.displays import SH1107Display, SSD1305Display, ST77xxDisplay, WaveshareEPaperMonoDisplay, create_display
    from firmware.displays.rendering import (
        HorizontalMonochromeSurface,
        MonochromeSurface,
        RGB565Surface,
        TriColorSurface,
        _build_glyph_lookup,
        _pin_number,
        _sample_source_coordinates,
        boot_logo_font_sizes,
        render_boot_logo,
        render_boot_logo_to_surface,
        render_framebuffer,
        render_to_surface,
    )
    from firmware.displays.waveshare_epaper import WaveshareEPaper213BV4Display as _WaveshareEPaper213BV4Display, WaveshareEPaper213BV4Surface
    from firmware.displays.waveshare_epaper_tricolor import WaveshareEPaperTriColorDisplay


WaveshareEPaper213BV4Display = _WaveshareEPaper213BV4Display


def _render_display_config(config):
    display_config = config.get("display", config) if isinstance(config, dict) else {}
    if not isinstance(display_config, dict):
        return normalize_display_config({})

    if any(key in display_config for key in ("type", "family", "backend", "controller")):
        return normalize_display_config(display_config)

    width = int(display_config.get("width_px", 128))
    height = int(display_config.get("height_px", 64))
    font_value = display_config.get("font")
    if isinstance(font_value, str) and font_value.strip().casefold() in supported_font_sizes():
        font = infer_default_font(width, height, None, font_value.strip().casefold())
    else:
        font_config = font_value if isinstance(font_value, dict) else {}
        font = {
            "width_px": int(font_config.get("width_px", 8)),
            "height_px": int(font_config.get("height_px", 8)),
        }

    return {
        "family": str(display_config.get("family", "oled")),
        "width_px": width,
        "height_px": height,
        "mode": str(display_config.get("mode", DisplayMode.STANDARD.value)),
        "columns": int(display_config.get("columns", 1)),
        "column_separator": str(display_config.get("column_separator", " ")),
        "failure_color": str(display_config.get("failure_color", "red")),
        "font": font,
    }


def render_display_buffers(checks, config, selected_id=None, page_index=0, shift_offset=(0, 0), glyph_lookup=None):
    display_config = _render_display_config(config)
    width = int(display_config["width_px"])
    height = int(display_config["height_px"])
    font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
    font_width = int(font.get("width_px", 8)) if isinstance(font, dict) else 8
    font_height = int(font.get("height_px", 8)) if isinstance(font, dict) else 8
    glyph_lookup = glyph_lookup or _build_glyph_lookup(font_width, font_height)
    state = AppState(
        checks=tuple(checks),
        selected_id=selected_id,
        display_mode=DisplayMode(str(display_config.get("mode", DisplayMode.STANDARD.value))),
        overview_columns=int(display_config.get("columns", 1)),
        column_separator=str(display_config.get("column_separator", " ")),
        row_width=max(1, width // font_width),
        page_size=max(1, height // font_height),
        page_index=page_index,
        shift_offset=shift_offset,
    )
    frame = render_frame(state)
    failure_color = str(display_config.get("failure_color", "red"))
    if display_config.get("family") == "eink":
        if "red" in tuple(display_config.get("colors", ())):
            if str(display_config.get("type", "")) == "waveshare-pico-epaper-2.13-b-v4":
                surface = WaveshareEPaper213BV4Surface(width, height)
            else:
                surface = TriColorSurface(width, height)
            render_to_surface(frame, surface, font_width, font_height, glyph_lookup, failure_color=failure_color)
            return {"black": surface.black_buffer, "accent": surface.accent_buffer}
        surface = HorizontalMonochromeSurface(width, height)
        render_to_surface(frame, surface, font_width, font_height, glyph_lookup, failure_color=failure_color)
        return surface.buffer

    if display_config.get("family") == "lcd":
        surface = RGB565Surface(width, height)
        render_to_surface(frame, surface, font_width, font_height, glyph_lookup, failure_color=failure_color)
        return surface.buffer

    surface = MonochromeSurface(width, height)
    render_to_surface(frame, surface, font_width, font_height, glyph_lookup, failure_color=failure_color)
    return surface.buffer


def render(checks, config, selected_id=None, page_index=0, shift_offset=(0, 0), glyph_lookup=None):
    return render_display_buffers(
        checks,
        config,
        selected_id=selected_id,
        page_index=page_index,
        shift_offset=shift_offset,
        glyph_lookup=glyph_lookup,
    )


__all__ = [
    "SH1107Display",
    "SSD1305Display",
    "ST77xxDisplay",
    "WaveshareEPaper213BV4Display",
    "WaveshareEPaperMonoDisplay",
    "WaveshareEPaperTriColorDisplay",
    "_pin_number",
    "_sample_source_coordinates",
    "boot_logo_font_sizes",
    "create_display",
    "render",
    "render_boot_logo",
    "render_boot_logo_to_surface",
    "render_display_buffers",
    "render_framebuffer",
]