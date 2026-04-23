from __future__ import annotations

from math import sqrt

try:
    from collections.abc import Mapping
except ImportError:  # pragma: no cover - MicroPython fallback
    Mapping = dict


DEFAULT_DISPLAY_TYPE = "waveshare-pico-oled-1.3"
DEFAULT_GRID_COLUMNS = 16
DEFAULT_GRID_ROWS = 8
MIN_FONT_SIZE_PX = 6
MAX_FONT_SIZE_PX = 32
DEFAULT_FAILURE_COLOR = "red"
DEFAULT_FONT_SIZE = "medium"
DEFAULT_BOOT_LOGO_DURATION_S = 4
DISPLAY_MODES = frozenset({"standard", "compact"})
LIVENESS_POSITIONS = frozenset({"left", "center", "right"})
BRIGHTNESS_PRESETS = {
    "low": 64,
    "medium": 128,
    "high": 192,
    "max": 255,
}
FONT_SIZE_PRESETS_MM = {
    "extrasmall": 1.35,
    "small": 1.60,
    "medium": 1.85,
    "large": 2.20,
    "extralarge": 2.60,
}

DEFAULT_SPI_PINS = {
    "vcc": "VSYS",
    "gnd": "GND",
    "din": "GP11",
    "clk": "GP10",
    "cs": "GP9",
    "dc": "GP8",
    "rst": "GP12",
}
LCD_SPI_PINS = dict(DEFAULT_SPI_PINS)
LCD_SPI_PINS["bl"] = "GP13"
EPAPER_SPI_PINS = dict(DEFAULT_SPI_PINS)
EPAPER_SPI_PINS["busy"] = "GP13"


def _fold(value: object) -> str:
    return str(value).strip().lower()


DISPLAY_TYPES = {
    "waveshare-pico-oled-1.3": {
        "family": "oled",
        "backend": "sh1107",
        "controller": "sh1107",
        "interface": "spi",
        "spi_mode": 3,
        "width_px": 128,
        "height_px": 64,
        "diagonal_in": 1.3,
        "colors": ("white", "black"),
        "supports_brightness": True,
        "default_brightness": 128,
        "default_page_interval_s": 20,
        "default_column_offset": 32,
        "pins": DEFAULT_SPI_PINS,
    },
    "waveshare-pico-oled-2.23": {
        "family": "oled",
        "backend": "ssd1305",
        "controller": "ssd1305",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 128,
        "height_px": 32,
        "diagonal_in": 2.23,
        "colors": ("white", "black"),
        "supports_brightness": True,
        "default_brightness": 128,
        "default_page_interval_s": 20,
        "pins": DEFAULT_SPI_PINS,
    },
    "waveshare-pico-lcd-0.96": {
        "family": "lcd",
        "backend": "st77xx",
        "controller": "st7735s",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 160,
        "height_px": 80,
        "diagonal_in": 0.96,
        "colors": ("white", "black", "red"),
        "supports_brightness": True,
        "default_brightness": 192,
        "default_page_interval_s": 20,
        "pins": LCD_SPI_PINS,
    },
    "waveshare-pico-lcd-1.14": {
        "family": "lcd",
        "backend": "st77xx",
        "controller": "st7789",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 240,
        "height_px": 135,
        "diagonal_in": 1.14,
        "colors": ("white", "black", "red"),
        "supports_brightness": True,
        "default_brightness": 192,
        "default_page_interval_s": 20,
        "pins": LCD_SPI_PINS,
    },
    "waveshare-pico-lcd-1.14-v2": {
        "family": "lcd",
        "backend": "st77xx",
        "controller": "st7789",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 240,
        "height_px": 135,
        "diagonal_in": 1.14,
        "colors": ("white", "black", "red"),
        "supports_brightness": True,
        "default_brightness": 192,
        "default_page_interval_s": 20,
        "pins": LCD_SPI_PINS,
    },
    "waveshare-pico-lcd-1.3": {
        "family": "lcd",
        "backend": "st77xx",
        "controller": "st7789",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 240,
        "height_px": 240,
        "diagonal_in": 1.3,
        "colors": ("white", "black", "red"),
        "supports_brightness": True,
        "default_brightness": 192,
        "default_page_interval_s": 20,
        "pins": LCD_SPI_PINS,
    },
    "waveshare-pico-lcd-1.44": {
        "family": "lcd",
        "backend": "st77xx",
        "controller": "st7735s",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 128,
        "height_px": 128,
        "diagonal_in": 1.44,
        "colors": ("white", "black", "red"),
        "supports_brightness": True,
        "default_brightness": 192,
        "default_page_interval_s": 20,
        "pins": LCD_SPI_PINS,
    },
    "waveshare-pico-lcd-1.8": {
        "family": "lcd",
        "backend": "st77xx",
        "controller": "st7735s",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 160,
        "height_px": 128,
        "diagonal_in": 1.8,
        "colors": ("white", "black", "red"),
        "supports_brightness": True,
        "default_brightness": 192,
        "default_page_interval_s": 20,
        "pins": LCD_SPI_PINS,
    },
    "waveshare-pico-lcd-2.0": {
        "family": "lcd",
        "backend": "st77xx",
        "controller": "st7789",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 320,
        "height_px": 240,
        "diagonal_in": 2.0,
        "colors": ("white", "black", "red"),
        "supports_brightness": True,
        "default_brightness": 192,
        "default_page_interval_s": 20,
        "pins": LCD_SPI_PINS,
    },
    "waveshare-pico-epaper-2.13-v3": {
        "family": "eink",
        "backend": "waveshare-epaper-mono",
        "controller": "waveshare-epaper-2.13-v3",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 250,
        "height_px": 122,
        "diagonal_in": 2.13,
        "colors": ("white", "black"),
        "supports_brightness": False,
        "default_page_interval_s": 180,
        "pins": EPAPER_SPI_PINS,
    },
    "waveshare-pico-epaper-2.13-v4": {
        "family": "eink",
        "backend": "waveshare-epaper-mono",
        "controller": "waveshare-epaper-2.13-v4",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 250,
        "height_px": 122,
        "diagonal_in": 2.13,
        "colors": ("white", "black"),
        "supports_brightness": False,
        "default_page_interval_s": 180,
        "pins": EPAPER_SPI_PINS,
    },
    "waveshare-pico-epaper-2.13-v2": {
        "family": "eink",
        "backend": "waveshare-epaper-mono",
        "controller": "waveshare-epaper-2.13-v2",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 250,
        "height_px": 122,
        "diagonal_in": 2.13,
        "colors": ("white", "black"),
        "supports_brightness": False,
        "default_page_interval_s": 180,
        "pins": EPAPER_SPI_PINS,
    },
    "waveshare-pico-epaper-2.13-b-v4": {
        "family": "eink",
        "backend": "waveshare-epaper-2.13-b-v4",
        "controller": "waveshare-epaper-2.13-b-v4",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 250,
        "height_px": 122,
        "diagonal_in": 2.13,
        "colors": ("white", "black", "red"),
        "supports_brightness": False,
        "default_page_interval_s": 180,
        "pins": EPAPER_SPI_PINS,
    },
    "waveshare-pico-epaper-2.7": {
        "family": "eink",
        "backend": "waveshare-epaper-mono",
        "controller": "waveshare-epaper-2.7",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 264,
        "height_px": 176,
        "diagonal_in": 2.7,
        "colors": ("white", "black"),
        "supports_brightness": False,
        "default_page_interval_s": 240,
        "pins": EPAPER_SPI_PINS,
    },
    "waveshare-pico-epaper-2.7-v2": {
        "family": "eink",
        "backend": "waveshare-epaper-mono",
        "controller": "waveshare-epaper-2.7-v2",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 264,
        "height_px": 176,
        "diagonal_in": 2.7,
        "colors": ("white", "black"),
        "supports_brightness": False,
        "default_page_interval_s": 240,
        "pins": EPAPER_SPI_PINS,
    },
    "waveshare-pico-epaper-2.9": {
        "family": "eink",
        "backend": "waveshare-epaper-mono",
        "controller": "waveshare-epaper-2.9",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 296,
        "height_px": 128,
        "diagonal_in": 2.9,
        "colors": ("white", "black"),
        "supports_brightness": False,
        "default_page_interval_s": 240,
        "pins": EPAPER_SPI_PINS,
    },
    "waveshare-pico-epaper-3.7": {
        "family": "eink",
        "backend": "waveshare-epaper-mono",
        "controller": "waveshare-epaper-3.7",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 480,
        "height_px": 280,
        "diagonal_in": 3.7,
        "colors": ("white", "black"),
        "supports_brightness": False,
        "default_page_interval_s": 300,
        "pins": EPAPER_SPI_PINS,
    },
    "waveshare-pico-epaper-4.2": {
        "family": "eink",
        "backend": "waveshare-epaper-mono",
        "controller": "waveshare-epaper-4.2",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 400,
        "height_px": 300,
        "diagonal_in": 4.2,
        "colors": ("white", "black"),
        "supports_brightness": False,
        "default_page_interval_s": 300,
        "pins": EPAPER_SPI_PINS,
    },
    "waveshare-pico-epaper-4.2-v2": {
        "family": "eink",
        "backend": "waveshare-epaper-mono",
        "controller": "waveshare-epaper-4.2-v2",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 400,
        "height_px": 300,
        "diagonal_in": 4.2,
        "colors": ("white", "black"),
        "supports_brightness": False,
        "default_page_interval_s": 300,
        "pins": EPAPER_SPI_PINS,
    },
    "waveshare-pico-epaper-7.5-b-v2": {
        "family": "eink",
        "backend": "waveshare-epaper-tricolor",
        "controller": "waveshare-epaper-7.5-b-v2",
        "interface": "spi",
        "spi_mode": 0,
        "width_px": 800,
        "height_px": 480,
        "diagonal_in": 7.5,
        "colors": ("white", "black", "red"),
        "supports_brightness": False,
        "default_page_interval_s": 600,
        "pins": EPAPER_SPI_PINS,
    },
}

DISPLAY_TYPE_ALIASES = {
    "pico-oled-1.3": "waveshare-pico-oled-1.3",
    "waveshare-pico-oled": "waveshare-pico-oled-1.3",
    "sh1107-128x64": "waveshare-pico-oled-1.3",
    "pico-oled-2.23": "waveshare-pico-oled-2.23",
    "ssd1305-128x32": "waveshare-pico-oled-2.23",
    "pico-lcd-0.96": "waveshare-pico-lcd-0.96",
    "pico-lcd-1.14": "waveshare-pico-lcd-1.14",
    "pico-lcd-1.14-v2": "waveshare-pico-lcd-1.14-v2",
    "pico-lcd-1.3": "waveshare-pico-lcd-1.3",
    "pico-lcd-1.44": "waveshare-pico-lcd-1.44",
    "pico-lcd-1.8": "waveshare-pico-lcd-1.8",
    "pico-lcd-2": "waveshare-pico-lcd-2.0",
    "pico-lcd-2.0": "waveshare-pico-lcd-2.0",
    "pico-epaper-2.13-b-v4": "waveshare-pico-epaper-2.13-b-v4",
    "waveshare-pico-epaper-2.13-b": "waveshare-pico-epaper-2.13-b-v4",
    "pico-epaper-2.13-v2": "waveshare-pico-epaper-2.13-v2",
    "pico-epaper-2.13-v3": "waveshare-pico-epaper-2.13-v3",
    "pico-epaper-2.13-v4": "waveshare-pico-epaper-2.13-v4",
    "pico-epaper-2.7": "waveshare-pico-epaper-2.7",
    "pico-epaper-2.7-v2": "waveshare-pico-epaper-2.7-v2",
    "pico-epaper-2.9": "waveshare-pico-epaper-2.9",
    "pico-epaper-3.7": "waveshare-pico-epaper-3.7",
    "pico-epaper-4.2": "waveshare-pico-epaper-4.2",
    "pico-epaper-4.2-v2": "waveshare-pico-epaper-4.2-v2",
    "pico-epaper-7.5-b-v2": "waveshare-pico-epaper-7.5-b-v2",
    "pico-epaper-7.5-b-v2-old": "waveshare-pico-epaper-7.5-b-v2",
}

CONTROLLER_ALIASES = {
    "ssd1503": "ssd1305",
    "st7789v": "st7789",
}

DISPLAY_SIGNATURES = {
    ("sh1107", 128, 64): "waveshare-pico-oled-1.3",
    ("ssd1305", 128, 32): "waveshare-pico-oled-2.23",
    ("st7735s", 160, 80): "waveshare-pico-lcd-0.96",
    ("st7735s", 128, 128): "waveshare-pico-lcd-1.44",
    ("st7735s", 160, 128): "waveshare-pico-lcd-1.8",
    ("st7789", 240, 135): "waveshare-pico-lcd-1.14",
    ("st7789", 240, 240): "waveshare-pico-lcd-1.3",
    ("st7789", 320, 240): "waveshare-pico-lcd-2.0",
    ("waveshare-epaper-2.13-v3", 250, 122): "waveshare-pico-epaper-2.13-v3",
    ("waveshare-epaper-2.13-v4", 250, 122): "waveshare-pico-epaper-2.13-v4",
    ("waveshare-epaper-2.13-v2", 250, 122): "waveshare-pico-epaper-2.13-v2",
    ("waveshare-epaper-2.13-b-v4", 250, 122): "waveshare-pico-epaper-2.13-b-v4",
    ("waveshare-epaper-2.7", 264, 176): "waveshare-pico-epaper-2.7",
    ("waveshare-epaper-2.7-v2", 264, 176): "waveshare-pico-epaper-2.7-v2",
    ("waveshare-epaper-2.9", 296, 128): "waveshare-pico-epaper-2.9",
    ("waveshare-epaper-3.7", 480, 280): "waveshare-pico-epaper-3.7",
    ("waveshare-epaper-4.2", 400, 300): "waveshare-pico-epaper-4.2",
    ("waveshare-epaper-4.2-v2", 400, 300): "waveshare-pico-epaper-4.2-v2",
    ("waveshare-epaper-7.5-b-v2", 800, 480): "waveshare-pico-epaper-7.5-b-v2",
}


def supported_display_types() -> tuple[str, ...]:
    return tuple(sorted(DISPLAY_TYPES))


def supported_font_sizes() -> tuple[str, ...]:
    return tuple(FONT_SIZE_PRESETS_MM)


def _display_type_choices() -> str:
    return ", ".join(supported_display_types())


def _font_size_choices() -> str:
    return ", ".join(supported_font_sizes())


def _parse_positive_int(value: object, context: str) -> int:
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise ValueError(f"{context} must be a positive integer")

    if parsed < 1:
        raise ValueError(f"{context} must be a positive integer")
    return parsed


def _parse_non_negative_int(value: object, context: str, default: int) -> int:
    if value is None:
        parsed = default
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise ValueError(f"{context} must be a non-negative integer")

    if parsed < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return parsed


def _parse_duration_s(value: object, context: str) -> int:
    if isinstance(value, int):
        seconds = value
    elif isinstance(value, float) and value.is_integer():
        seconds = int(value)
    elif isinstance(value, str):
        normalized = _fold(value)
        if normalized.endswith("s"):
            normalized = normalized[:-1].strip()
        if not normalized.isdigit():
            raise ValueError(f"{context} must be an integer number of seconds or use the '<seconds>s' format")
        seconds = int(normalized)
    else:
        raise ValueError(f"{context} must be an integer number of seconds")

    if seconds < 0:
        raise ValueError(f"{context} must not be negative")
    return seconds


def _parse_font_size_px(value: object, context: str, default: int) -> int:
    if value is None:
        size = default
    elif isinstance(value, int):
        size = value
    elif isinstance(value, float) and value.is_integer():
        size = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        size = int(value.strip())
    else:
        raise ValueError(f"{context} must be an integer number of pixels")

    if size < MIN_FONT_SIZE_PX or size > MAX_FONT_SIZE_PX:
        raise ValueError(f"{context} must be between {MIN_FONT_SIZE_PX} and {MAX_FONT_SIZE_PX} pixels")
    return size


def _parse_font_size_name(value: object, context: str = "device.display.font") -> str:
    if value is None:
        return DEFAULT_FONT_SIZE
    if not isinstance(value, str):
        raise ValueError(f"{context} must be one of: {_font_size_choices()}")
    normalized = _fold(value)
    if normalized not in FONT_SIZE_PRESETS_MM:
        raise ValueError(f"{context} must be one of: {_font_size_choices()}")
    return normalized


def _parse_brightness(value: object, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = _fold(value)
        if normalized in BRIGHTNESS_PRESETS:
            return BRIGHTNESS_PRESETS[normalized]
        if normalized.isdigit():
            value = int(normalized)
        else:
            raise ValueError("device.display.brightness must be 0-255 or one of low, medium, high, max")

    if isinstance(value, int):
        brightness = value
    elif isinstance(value, float) and value.is_integer():
        brightness = int(value)
    else:
        raise ValueError("device.display.brightness must be 0-255 or one of low, medium, high, max")

    if brightness < 0 or brightness > 255:
        raise ValueError("device.display.brightness must be between 0 and 255")
    return brightness


def _parse_display_mode(value: object) -> str:
    if value is None:
        return "standard"
    if not isinstance(value, str):
        raise ValueError("device.display.mode must be 'standard' or 'compact'")

    normalized = _fold(value)
    if normalized not in DISPLAY_MODES:
        raise ValueError("device.display.mode must be 'standard' or 'compact'")
    return normalized


def _parse_columns(value: object) -> int:
    if value is None:
        return 1
    if isinstance(value, int):
        columns = value
    elif isinstance(value, float) and value.is_integer():
        columns = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        columns = int(value.strip())
    else:
        raise ValueError("device.display.columns must be an integer from 1 to 4")

    if columns < 1 or columns > 4:
        raise ValueError("device.display.columns must be an integer from 1 to 4")
    return columns


def _parse_column_separator(value: object) -> str:
    if value is None:
        return " "
    if not isinstance(value, str):
        raise ValueError("device.display.column_separator must be exactly one character")
    if len(value) != 1:
        raise ValueError("device.display.column_separator must be exactly one character")
    return value


def _parse_failure_color(value: object) -> str:
    if value is None:
        return DEFAULT_FAILURE_COLOR
    if not isinstance(value, str):
        raise ValueError("device.display.failure_color must be a color name")
    normalized = _fold(value)
    if not normalized:
        raise ValueError("device.display.failure_color must be a color name")
    return normalized


def _parse_bool(value: object, context: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = _fold(value)
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    raise ValueError(f"{context} must be a boolean")


def _parse_liveness_period_s(value: object, context: str, default: int) -> int:
    seconds = _parse_duration_s(default if value is None else value, context)
    if seconds < 1:
        raise ValueError(f"{context} must be at least 1 second")
    return seconds


def _parse_liveness_amplitude(value: object, context: str, default: int) -> int:
    amplitude = _parse_non_negative_int(value, context, default)
    if amplitude > 255:
        raise ValueError(f"{context} must be between 0 and 255")
    return amplitude


def _parse_bottom_pixel_count(value: object, context: str, default: int) -> int:
    count = _parse_positive_int(default if value is None else value, context)
    if count < 1 or count > 3:
        raise ValueError(f"{context} must be between 1 and 3")
    return count


def _parse_liveness_position(value: object, context: str, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{context} must be one of: left, center, right")
    normalized = _fold(value)
    if normalized not in LIVENESS_POSITIONS:
        raise ValueError(f"{context} must be one of: left, center, right")
    return normalized


def _parse_display_liveness(value: object) -> dict[str, object]:
    if value is None:
        raw = {}
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        raise ValueError("device.display.liveness must be a mapping")

    contrast_raw = raw.get("contrast_breathing")
    if contrast_raw is None:
        contrast = {}
    elif isinstance(contrast_raw, Mapping):
        contrast = dict(contrast_raw)
    else:
        raise ValueError("device.display.liveness.contrast_breathing must be a mapping")

    per_row_raw = raw.get("per_row_micro")
    if per_row_raw is None:
        per_row = {}
    elif isinstance(per_row_raw, Mapping):
        per_row = dict(per_row_raw)
    else:
        raise ValueError("device.display.liveness.per_row_micro must be a mapping")

    heartbeat_raw = raw.get("bottom_heartbeat")
    if heartbeat_raw is None:
        heartbeat = {}
    elif isinstance(heartbeat_raw, Mapping):
        heartbeat = dict(heartbeat_raw)
    else:
        raise ValueError("device.display.liveness.bottom_heartbeat must be a mapping")

    return {
        "contrast_breathing": {
            "enabled": _parse_bool(
                contrast.get("enabled"),
                "device.display.liveness.contrast_breathing.enabled",
                False,
            ),
            "period_s": _parse_liveness_period_s(
                contrast.get("period_s"),
                "device.display.liveness.contrast_breathing.period_s",
                45,
            ),
            "amplitude": _parse_liveness_amplitude(
                contrast.get("amplitude"),
                "device.display.liveness.contrast_breathing.amplitude",
                8,
            ),
        },
        "per_row_micro": {
            "enabled": _parse_bool(
                per_row.get("enabled"),
                "device.display.liveness.per_row_micro.enabled",
                False,
            ),
            "period_s": _parse_liveness_period_s(
                per_row.get("period_s"),
                "device.display.liveness.per_row_micro.period_s",
                15,
            ),
            "stagger": _parse_bool(
                per_row.get("stagger"),
                "device.display.liveness.per_row_micro.stagger",
                True,
            ),
        },
        "bottom_heartbeat": {
            "enabled": _parse_bool(
                heartbeat.get("enabled"),
                "device.display.liveness.bottom_heartbeat.enabled",
                False,
            ),
            "period_s": _parse_liveness_period_s(
                heartbeat.get("period_s"),
                "device.display.liveness.bottom_heartbeat.period_s",
                20,
            ),
            "pixel_count": _parse_bottom_pixel_count(
                heartbeat.get("pixel_count"),
                "device.display.liveness.bottom_heartbeat.pixel_count",
                1,
            ),
            "position": _parse_liveness_position(
                heartbeat.get("position"),
                "device.display.liveness.bottom_heartbeat.position",
                "right",
            ),
        },
    }


def _canonical_display_type(value: str) -> str:
    normalized = _fold(value)
    return DISPLAY_TYPE_ALIASES.get(normalized, normalized)


def _normalize_controller_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = _fold(value)
    return CONTROLLER_ALIASES.get(normalized, normalized)


def normalize_display_type(value: object | None) -> str:
    if value is None:
        return DEFAULT_DISPLAY_TYPE
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"device.display.type must be one of: {_display_type_choices()}")

    normalized = _canonical_display_type(value)
    if normalized not in DISPLAY_TYPES:
        raise ValueError(f"device.display.type must be one of: {_display_type_choices()}")
    return normalized


def infer_display_type(display: Mapping[str, object] | None) -> str:
    if display is None:
        return DEFAULT_DISPLAY_TYPE

    explicit_type = display.get("type")
    if explicit_type is not None:
        return normalize_display_type(explicit_type)

    controller = _normalize_controller_name(display.get("controller"))
    width_px = display.get("width_px")
    height_px = display.get("height_px")
    pins = display.get("pins")

    if controller:
        signature = DISPLAY_SIGNATURES.get(
            (
                controller,
                _parse_positive_int(width_px, "device.display.width_px") if width_px is not None else -1,
                _parse_positive_int(height_px, "device.display.height_px") if height_px is not None else -1,
            )
        )
        if signature is not None:
            return signature

    if isinstance(pins, Mapping) and "busy" in pins and width_px is not None and height_px is not None:
        width_value = _parse_positive_int(width_px, "device.display.width_px")
        height_value = _parse_positive_int(height_px, "device.display.height_px")
        if (width_value, height_value) == (264, 176):
            return "waveshare-pico-epaper-2.7-v2"
        if (width_value, height_value) == (296, 128):
            return "waveshare-pico-epaper-2.9"
        if (width_value, height_value) == (480, 280):
            return "waveshare-pico-epaper-3.7"
        if (width_value, height_value) == (400, 300):
            return "waveshare-pico-epaper-4.2"
        if (width_value, height_value) == (800, 480):
            return "waveshare-pico-epaper-7.5-b-v2"

    return DEFAULT_DISPLAY_TYPE


def get_display_definition(display_type: str) -> dict[str, object]:
    normalized = normalize_display_type(display_type)
    definition = DISPLAY_TYPES[normalized]
    return {
        "type": normalized,
        "family": definition["family"],
        "backend": definition["backend"],
        "controller": definition["controller"],
        "interface": definition["interface"],
        "spi_mode": definition["spi_mode"],
        "width_px": definition["width_px"],
        "height_px": definition["height_px"],
        "diagonal_in": definition["diagonal_in"],
        "colors": list(definition["colors"]),
        "supports_brightness": definition["supports_brightness"],
        "default_brightness": definition.get("default_brightness"),
        "default_page_interval_s": definition["default_page_interval_s"],
        "default_column_offset": definition.get("default_column_offset", 0),
        "pins": dict(definition["pins"]),
    }


def infer_default_font(width_px: int, height_px: int, diagonal_in: float | None = None, size_name: str = DEFAULT_FONT_SIZE) -> dict[str, int]:
    if diagonal_in is None or diagonal_in <= 0:
        target_width = width_px // DEFAULT_GRID_COLUMNS
        target_height = height_px // DEFAULT_GRID_ROWS
    else:
        diagonal_px = sqrt((width_px * width_px) + (height_px * height_px))
        pixels_per_mm = diagonal_px / (float(diagonal_in) * 25.4)
        target_size_px = round(FONT_SIZE_PRESETS_MM[size_name] * pixels_per_mm)
        target_width = target_size_px
        target_height = target_size_px

    return {
        "width_px": max(MIN_FONT_SIZE_PX, min(MAX_FONT_SIZE_PX, int(target_width))),
        "height_px": max(MIN_FONT_SIZE_PX, min(MAX_FONT_SIZE_PX, int(target_height))),
    }


def _validate_inferred_value(value: object, expected: object, context: str):
    if value is None:
        return
    if isinstance(expected, int):
        parsed = _parse_positive_int(value, context)
    elif isinstance(expected, str):
        if not isinstance(value, str):
            raise ValueError(f"{context} is inferred from device.display.type")
        if context == "device.display.controller":
            parsed = _normalize_controller_name(value)
            expected = _normalize_controller_name(expected)
        else:
            parsed = _fold(value)
            expected = _fold(expected)
    else:
        parsed = value

    if parsed != expected:
        raise ValueError(f"{context} is inferred from device.display.type and must match the selected display")


def normalize_display_config(raw_display: object) -> dict[str, object]:
    if raw_display is None:
        display = {}
    elif isinstance(raw_display, Mapping):
        display = dict(raw_display)
    else:
        raise ValueError("device.display must be a mapping")

    display_type = infer_display_type(display)
    definition = get_display_definition(display_type)

    _validate_inferred_value(display.get("controller"), definition["controller"], "device.display.controller")
    _validate_inferred_value(display.get("interface"), definition["interface"], "device.display.interface")
    _validate_inferred_value(display.get("spi_mode"), definition["spi_mode"], "device.display.spi_mode")
    _validate_inferred_value(display.get("width_px"), definition["width_px"], "device.display.width_px")
    _validate_inferred_value(display.get("height_px"), definition["height_px"], "device.display.height_px")

    font_config = display.get("font")
    if font_config is None:
        font = {}
    elif isinstance(font_config, str):
        font = {"size": font_config}
    elif isinstance(font_config, Mapping):
        font = dict(font_config)
    else:
        raise ValueError("device.display.font must be a mapping or one of the supported size names")

    pins_config = display.get("pins")
    if pins_config is None:
        pins = {}
    elif isinstance(pins_config, Mapping):
        pins = dict(pins_config)
    else:
        raise ValueError("device.display.pins must be a mapping")

    font_size = _parse_font_size_name(font.get("size"))
    default_font = infer_default_font(
        definition["width_px"],
        definition["height_px"],
        float(definition["diagonal_in"]),
        size_name=font_size,
    )
    resolved_pins = dict(definition["pins"])
    for pin_name, pin_value in pins.items():
        if not isinstance(pin_value, str) or not pin_value.strip():
            raise ValueError(f"device.display.pins.{pin_name} must be a non-empty string")
        resolved_pins[str(pin_name)] = pin_value.strip()

    page_interval_value = display.get("page_interval", display.get("page_interval_s", definition["default_page_interval_s"]))
    resolved = {
        "type": display_type,
        "family": definition["family"],
        "backend": definition["backend"],
        "controller": definition["controller"],
        "interface": definition["interface"],
        "spi_mode": definition["spi_mode"],
        "width_px": definition["width_px"],
        "height_px": definition["height_px"],
        "diagonal_in": definition["diagonal_in"],
        "colors": list(definition["colors"]),
        "mode": _parse_display_mode(display.get("mode")),
        "columns": _parse_columns(display.get("columns")),
        "column_separator": _parse_column_separator(display.get("column_separator")),
        "failure_color": _parse_failure_color(display.get("failure_color")),
        "page_interval_s": _parse_duration_s(page_interval_value, "device.display.page_interval"),
        "boot_logo_duration_s": DEFAULT_BOOT_LOGO_DURATION_S,
        "column_offset": _parse_non_negative_int(
            display.get("column_offset"),
            "device.display.column_offset",
            int(definition.get("default_column_offset", 0)),
        ),
        "font_size": font_size,
        "font": {
            "width_px": _parse_font_size_px(font.get("width_px"), "device.display.font.width_px", default_font["width_px"]),
            "height_px": _parse_font_size_px(font.get("height_px"), "device.display.font.height_px", default_font["height_px"]),
        },
        "liveness": _parse_display_liveness(display.get("liveness")),
        "pins": resolved_pins,
    }

    if definition["supports_brightness"]:
        resolved["brightness"] = _parse_brightness(display.get("brightness"), int(definition["default_brightness"] or 128))
    elif display.get("brightness") is not None:
        raise ValueError("device.display.brightness is not supported by the selected display type")

    if resolved["mode"] == "standard" and resolved["columns"] != 1:
        raise ValueError("device.display.columns must be 1 when device.display.mode is 'standard'; use 'compact' for multiple columns")

    return resolved
