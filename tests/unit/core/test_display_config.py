import pytest

import vivipi.core.display as core_display
from vivipi.core.display import get_display_definition, infer_default_font, infer_display_type, normalize_display_config, normalize_display_type, supported_display_types, supported_font_sizes


def test_supported_display_types_include_oled_and_epaper():
    assert "waveshare-pico-oled-1.3" in supported_display_types()
    assert "waveshare-pico-oled-2.23" in supported_display_types()
    assert "waveshare-pico-lcd-1.3" in supported_display_types()
    assert "waveshare-pico-epaper-2.13-b-v4" in supported_display_types()
    assert "waveshare-pico-epaper-2.7" in supported_display_types()
    assert "waveshare-pico-epaper-3.7" in supported_display_types()
    assert "waveshare-pico-epaper-4.2-v2" in supported_display_types()
    assert "waveshare-pico-epaper-7.5-b-v2" in supported_display_types()
    assert "waveshare-pico-epaper-4.2" in supported_display_types()


def test_normalize_display_type_accepts_aliases():
    assert normalize_display_type("pico-oled-1.3") == "waveshare-pico-oled-1.3"
    assert normalize_display_type("waveshare-pico-epaper-2.13-b") == "waveshare-pico-epaper-2.13-b-v4"
    assert normalize_display_type("pico-epaper-7.5-b-v2-old") == "waveshare-pico-epaper-7.5-b-v2"


def test_normalize_display_type_defaults_and_rejects_invalid_values():
    assert normalize_display_type(None) == "waveshare-pico-oled-1.3"

    with pytest.raises(ValueError, match="device.display.type"):
        normalize_display_type(123)

    with pytest.raises(ValueError, match="device.display.type"):
        normalize_display_type("unknown-panel")


def test_normalize_display_config_defaults_to_inferred_oled_geometry_and_font():
    config = normalize_display_config({})

    assert config["type"] == "waveshare-pico-oled-1.3"
    assert config["width_px"] == 128
    assert config["height_px"] == 64
    assert config["column_offset"] == 32
    assert config["font"] == {"width_px": 8, "height_px": 8}
    assert config["page_interval_s"] == 20
    assert config["boot_logo_duration_s"] == 4
    assert config["liveness"]["contrast_breathing"]["enabled"] is False
    assert config["liveness"]["per_row_micro"]["enabled"] is False
    assert config["liveness"]["bottom_heartbeat"]["enabled"] is False
    assert config["pins"]["dc"] == "GP8"


def test_normalize_display_config_accepts_column_offset_override_for_subwindowed_oleds():
    config = normalize_display_config({"type": "waveshare-pico-oled-1.3", "column_offset": 29})

    assert config["column_offset"] == 29


def test_normalize_display_config_accepts_boot_logo_duration_override():
    config = normalize_display_config({"type": "waveshare-pico-oled-1.3", "boot_logo_duration": "7s"})

    assert config["boot_logo_duration_s"] == 4


def test_normalize_display_config_accepts_liveness_configuration():
    config = normalize_display_config(
        {
            "type": "waveshare-pico-oled-1.3",
            "liveness": {
                "contrast_breathing": {"enabled": True, "period_s": 45, "amplitude": 8},
                "per_row_micro": {"enabled": True, "period_s": 15, "stagger": False},
                "bottom_heartbeat": {"enabled": True, "period_s": 20, "pixel_count": 3, "position": "center"},
            },
        }
    )

    assert config["liveness"]["contrast_breathing"] == {"enabled": True, "period_s": 45, "amplitude": 8}
    assert config["liveness"]["per_row_micro"] == {"enabled": True, "period_s": 15, "stagger": False}
    assert config["liveness"]["bottom_heartbeat"] == {
        "enabled": True,
        "period_s": 20,
        "pixel_count": 3,
        "position": "center",
    }


def test_normalize_display_config_parses_liveness_string_values_and_rejects_invalid_shapes():
    config = normalize_display_config(
        {
            "type": "waveshare-pico-oled-1.3",
            "liveness": {
                "contrast_breathing": {"enabled": "yes", "period_s": "1s", "amplitude": "16"},
                "per_row_micro": {"enabled": "off", "period_s": "2s", "stagger": "no"},
                "bottom_heartbeat": {"enabled": "1", "period_s": "3s", "pixel_count": "2", "position": "LEFT"},
            },
        }
    )

    assert config["liveness"]["contrast_breathing"] == {"enabled": True, "period_s": 1, "amplitude": 16}
    assert config["liveness"]["per_row_micro"] == {"enabled": False, "period_s": 2, "stagger": False}
    assert config["liveness"]["bottom_heartbeat"] == {
        "enabled": True,
        "period_s": 3,
        "pixel_count": 2,
        "position": "left",
    }

    with pytest.raises(ValueError, match="device.display.liveness must be a mapping"):
        normalize_display_config({"type": "waveshare-pico-oled-1.3", "liveness": []})

    with pytest.raises(ValueError, match="device.display.liveness.contrast_breathing must be a mapping"):
        normalize_display_config({"type": "waveshare-pico-oled-1.3", "liveness": {"contrast_breathing": []}})

    with pytest.raises(ValueError, match="device.display.liveness.per_row_micro.enabled must be a boolean"):
        normalize_display_config(
            {
                "type": "waveshare-pico-oled-1.3",
                "liveness": {"per_row_micro": {"enabled": "maybe"}},
            }
        )

    with pytest.raises(ValueError, match="device.display.liveness.bottom_heartbeat.period_s must be at least 1 second"):
        normalize_display_config(
            {
                "type": "waveshare-pico-oled-1.3",
                "liveness": {"bottom_heartbeat": {"period_s": 0}},
            }
        )

    with pytest.raises(ValueError, match="device.display.liveness.contrast_breathing.amplitude must be between 0 and 255"):
        normalize_display_config(
            {
                "type": "waveshare-pico-oled-1.3",
                "liveness": {"contrast_breathing": {"amplitude": 256}},
            }
        )


def test_display_parser_helpers_cover_numeric_and_error_branches():
    assert core_display._parse_positive_int(7.0, "device.display.width_px") == 7
    assert core_display._parse_positive_int("8", "device.display.width_px") == 8
    assert core_display._parse_non_negative_int(3.0, "device.display.column_offset", 0) == 3
    assert core_display._parse_non_negative_int("4", "device.display.column_offset", 0) == 4

    with pytest.raises(ValueError, match="positive integer"):
        core_display._parse_positive_int(0, "device.display.width_px")

    with pytest.raises(ValueError, match="non-negative integer"):
        core_display._parse_non_negative_int(-1, "device.display.column_offset", 0)

    with pytest.raises(ValueError, match="must be one of"):
        core_display._parse_font_size_name("mega")


def test_infer_default_font_uses_legacy_grid_fallback_without_diagonal():
    assert infer_default_font(160, 80, None, size_name="medium") == {"width_px": 10, "height_px": 10}


def test_normalize_display_config_infers_epaper_defaults_from_type_only():
    config = normalize_display_config({"type": "waveshare-pico-epaper-2.13-b-v4"})

    assert config["family"] == "eink"
    assert config["width_px"] == 250
    assert config["height_px"] == 122
    assert config["font_size"] == "medium"
    assert config["font"] == {"width_px": 10, "height_px": 10}
    assert config["page_interval_s"] == 180
    assert config["pins"]["busy"] == "GP13"
    assert config["failure_color"] == "red"


def test_supported_font_sizes_expose_symbolic_presets():
    assert supported_font_sizes() == ("extrasmall", "small", "medium", "large", "extralarge")


def test_normalize_display_config_accepts_symbolic_font_size_strings():
    compact = normalize_display_config({"type": "waveshare-pico-lcd-1.3", "font": "small"})
    expanded = normalize_display_config({"type": "waveshare-pico-lcd-1.3", "font": "extralarge"})

    assert compact["font_size"] == "small"
    assert expanded["font_size"] == "extralarge"
    assert compact["font"]["width_px"] < expanded["font"]["width_px"]
    assert compact["font"]["height_px"] < expanded["font"]["height_px"]


def test_normalize_display_config_keeps_pixel_font_overrides_for_backwards_compatibility():
    config = normalize_display_config(
        {
            "type": "waveshare-pico-lcd-1.14",
            "font": {"size": "large", "width_px": 9, "height_px": 12},
        }
    )

    assert config["font_size"] == "large"
    assert config["font"] == {"width_px": 9, "height_px": 12}


def test_infer_display_type_uses_controller_geometry_and_busy_pin_signatures():
    assert infer_display_type({"controller": "ssd1503", "width_px": 128, "height_px": 32}) == "waveshare-pico-oled-2.23"
    assert infer_display_type({"controller": "st7789v", "width_px": 240, "height_px": 135}) == "waveshare-pico-lcd-1.14"
    assert infer_display_type({"width_px": 296, "height_px": 128, "pins": {"busy": "GP13"}}) == "waveshare-pico-epaper-2.9"
    assert infer_display_type({"width_px": 480, "height_px": 280, "pins": {"busy": "GP13"}}) == "waveshare-pico-epaper-3.7"
    assert infer_display_type({"width_px": 400, "height_px": 300, "pins": {"busy": "GP13"}}) == "waveshare-pico-epaper-4.2"
    assert infer_display_type({"width_px": 800, "height_px": 480, "pins": {"busy": "GP13"}}) == "waveshare-pico-epaper-7.5-b-v2"
    assert infer_display_type({"width_px": 250, "height_px": 122, "pins": {"busy": "GP13"}}) == "waveshare-pico-oled-1.3"


def test_normalize_display_config_accepts_inference_without_explicit_type():
    config = normalize_display_config({"controller": "st7789v", "width_px": 320, "height_px": 240})

    assert config["type"] == "waveshare-pico-lcd-2.0"
    assert config["family"] == "lcd"
    assert config["brightness"] == 192


def test_normalize_display_config_validates_top_level_display_shape_and_nested_shapes():
    with pytest.raises(ValueError, match="device.display must be a mapping"):
        normalize_display_config(["not", "a", "mapping"])

    with pytest.raises(ValueError, match="device.display.font"):
        normalize_display_config({"type": "waveshare-pico-oled-1.3", "font": 7})

    with pytest.raises(ValueError, match="device.display.font"):
        normalize_display_config({"type": "waveshare-pico-oled-1.3", "font": {"size": 7}})

    with pytest.raises(ValueError, match="device.display.pins must be a mapping"):
        normalize_display_config({"type": "waveshare-pico-oled-1.3", "pins": []})

    with pytest.raises(ValueError, match="device.display.pins.dc"):
        normalize_display_config({"type": "waveshare-pico-oled-1.3", "pins": {"dc": "   "}})


def test_normalize_display_config_validates_failure_color_and_brightness_ranges():
    with pytest.raises(ValueError, match="device.display.failure_color"):
        normalize_display_config({"type": "waveshare-pico-lcd-1.3", "failure_color": "   "})

    with pytest.raises(ValueError, match="device.display.failure_color"):
        normalize_display_config({"type": "waveshare-pico-lcd-1.3", "failure_color": 5})

    with pytest.raises(ValueError, match="device.display.brightness"):
        normalize_display_config({"type": "waveshare-pico-lcd-1.3", "brightness": "blinding"})

    with pytest.raises(ValueError, match="device.display.brightness"):
        normalize_display_config({"type": "waveshare-pico-lcd-1.3", "brightness": 999})

    with pytest.raises(ValueError, match="device.display.liveness.bottom_heartbeat.pixel_count"):
        normalize_display_config(
            {
                "type": "waveshare-pico-oled-1.3",
                "liveness": {"bottom_heartbeat": {"pixel_count": 4}},
            }
        )

    with pytest.raises(ValueError, match="device.display.liveness.bottom_heartbeat.position"):
        normalize_display_config(
            {
                "type": "waveshare-pico-oled-1.3",
                "liveness": {"bottom_heartbeat": {"position": "far-right"}},
            }
        )


def test_normalize_display_config_rejects_standard_multi_column_layouts():
    with pytest.raises(ValueError, match="use 'compact' for multiple columns"):
        normalize_display_config({"type": "waveshare-pico-oled-1.3", "mode": "standard", "columns": 2})


def test_normalize_display_config_validates_inferred_string_and_numeric_fields():
    with pytest.raises(ValueError, match="device.display.controller"):
        normalize_display_config({"type": "waveshare-pico-lcd-1.3", "controller": 3})

    with pytest.raises(ValueError, match="device.display.interface"):
        normalize_display_config({"type": "waveshare-pico-lcd-1.3", "interface": "i2c"})

    with pytest.raises(ValueError, match="device.display.spi_mode"):
        normalize_display_config({"type": "waveshare-pico-lcd-1.3", "spi_mode": 3})

    with pytest.raises(ValueError, match="device.display.width_px"):
        normalize_display_config({"type": "waveshare-pico-lcd-1.3", "width_px": "wide"})

    with pytest.raises(ValueError, match="device.display.font.width_px"):
        normalize_display_config({"type": "waveshare-pico-lcd-1.3", "font": {"width_px": 33}})

    with pytest.raises(ValueError, match="device.display.page_interval"):
        normalize_display_config({"type": "waveshare-pico-lcd-1.3", "page_interval": object()})

    with pytest.raises(ValueError, match="device.display.column_offset"):
        normalize_display_config({"type": "waveshare-pico-oled-1.3", "column_offset": -1})


def test_normalize_display_config_rejects_mismatched_inferred_values():
    with pytest.raises(ValueError, match="device.display.width_px"):
        normalize_display_config({"type": "waveshare-pico-oled-1.3", "width_px": 64})


def test_normalize_display_config_rejects_brightness_for_epaper():
    with pytest.raises(ValueError, match="brightness is not supported"):
        normalize_display_config({"type": "waveshare-pico-epaper-2.13-b-v4", "brightness": "high"})


def test_get_display_definition_returns_json_friendly_metadata():
    definition = get_display_definition("waveshare-pico-oled-1.3")

    assert definition["backend"] == "sh1107"
    assert definition["colors"] == ["white", "black"]
    assert definition["default_column_offset"] == 32


def test_get_display_definition_includes_screen_diagonal_for_readme_matrix_generation():
    definition = get_display_definition("waveshare-pico-lcd-2.0")

    assert definition["diagonal_in"] == pytest.approx(2.0)
