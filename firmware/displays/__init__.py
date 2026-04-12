"""Display backend registry for ViviPi firmware."""

from __future__ import annotations

from vivipi.core.display import normalize_display_config

try:
    from displays.sh1107 import SH1107Display
    from displays.ssd1305 import SSD1305Display
    from displays.st77xx import ST77xxDisplay
    from displays.waveshare_epaper import WaveshareEPaper213BV4Display
    from displays.waveshare_epaper_mono import WaveshareEPaperMonoDisplay
    from displays.waveshare_epaper_tricolor import WaveshareEPaperTriColorDisplay
except ImportError as error:  # pragma: no cover - used by CPython tests
    if not str(getattr(error, "name", "")).startswith("displays"):
        raise
    from firmware.displays.sh1107 import SH1107Display
    from firmware.displays.ssd1305 import SSD1305Display
    from firmware.displays.st77xx import ST77xxDisplay
    from firmware.displays.waveshare_epaper import WaveshareEPaper213BV4Display
    from firmware.displays.waveshare_epaper_mono import WaveshareEPaperMonoDisplay
    from firmware.displays.waveshare_epaper_tricolor import WaveshareEPaperTriColorDisplay


BACKENDS = {
    "sh1107": SH1107Display,
    "ssd1305": SSD1305Display,
    "st77xx": ST77xxDisplay,
    "waveshare-epaper-2.13-b-v4": WaveshareEPaper213BV4Display,
    "waveshare-epaper-mono": WaveshareEPaperMonoDisplay,
    "waveshare-epaper-tricolor": WaveshareEPaperTriColorDisplay,
}


def create_display(display_config, spi=None):
    resolved = normalize_display_config(display_config)
    backend_name = str(resolved["backend"])
    backend = BACKENDS.get(backend_name)
    if backend is None:
        raise ValueError(f"unsupported display backend: {backend_name}")
    return backend(resolved, spi=spi)


__all__ = [
    "BACKENDS",
    "SH1107Display",
    "SSD1305Display",
    "ST77xxDisplay",
    "WaveshareEPaper213BV4Display",
    "WaveshareEPaperMonoDisplay",
    "WaveshareEPaperTriColorDisplay",
    "create_display",
]