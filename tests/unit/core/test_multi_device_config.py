from __future__ import annotations

import pytest

from vivipi.core.multi_device_config import expand_multi_device_settings


def test_expand_multi_device_settings_merges_defaults_and_device_overrides():
    settings = {
        "project": {"name": "vivipi"},
        "device": {
            "board": "pico2w",
            "display": {
                "type": "waveshare-pico-oled-1.3",
                "mode": "standard",
            },
        },
        "service": {"default_prefix": "adb"},
        "checks_config": "checks.local.yaml",
        "devices": {
            "oled": {
                "selector": {
                    "serial_by_id": "/dev/serial/by-id/usb-oled",
                },
            },
            "epaper": {
                "selector": {
                    "serial_by_id": "/dev/serial/by-id/usb-epaper",
                    "bootsel_disk": "/dev/disk/by-id/usb-RPI_RP2350_AAAE129318B6B9B6-0:0",
                },
                "device": {
                    "display": {
                        "type": "waveshare-pico-epaper-2.13-b-v4",
                    },
                },
                "checks_config": "checks.epaper.yaml",
            },
        },
    }

    expanded = expand_multi_device_settings(settings)

    assert set(expanded) == {"oled", "epaper"}
    assert expanded["oled"]["project"]["device_id"] == "oled"
    assert expanded["epaper"]["project"]["device_id"] == "epaper"
    assert expanded["oled"]["device"]["display"]["type"] == "waveshare-pico-oled-1.3"
    assert expanded["epaper"]["device"]["display"]["type"] == "waveshare-pico-epaper-2.13-b-v4"
    assert expanded["oled"]["service"]["default_prefix"] == "adb"
    assert expanded["epaper"]["checks_config"] == "checks.epaper.yaml"
    assert expanded["oled"]["selector"] == {"serial_by_id": "/dev/serial/by-id/usb-oled"}


def test_expand_multi_device_settings_rejects_auto_selector_values():
    settings = {
        "device": {"board": "pico2w"},
        "checks_config": "checks.local.yaml",
        "devices": {
            "oled": {
                "selector": {
                    "port": "auto",
                },
            },
        },
    }

    with pytest.raises(ValueError, match="auto"):
        expand_multi_device_settings(settings)


def test_expand_multi_device_settings_rejects_duplicate_selectors():
    settings = {
        "device": {"board": "pico2w"},
        "checks_config": "checks.local.yaml",
        "devices": {
            "oled-a": {
                "selector": {
                    "serial_by_id": "/dev/serial/by-id/usb-shared",
                },
            },
            "oled-b": {
                "selector": {
                    "serial_by_id": "/dev/serial/by-id/usb-shared",
                },
            },
        },
    }

    with pytest.raises(ValueError, match="duplicate selector"):
        expand_multi_device_settings(settings)


def test_expand_multi_device_settings_supports_nested_display_liveness_overrides():
    settings = {
        "device": {
            "display": {
                "type": "waveshare-pico-oled-1.3",
                "liveness": {
                    "contrast_breathing": {"enabled": False},
                    "bottom_heartbeat": {"enabled": True, "period_s": 1, "pixel_count": 1, "position": "left"},
                },
            },
        },
        "checks_config": "checks.local.yaml",
        "devices": {
            "epaper": {
                "selector": {
                    "serial_by_id": "/dev/serial/by-id/usb-epaper",
                },
                "device": {
                    "display": {
                        "type": "waveshare-pico-epaper-2.13-b-v4",
                        "liveness": {
                            "bottom_heartbeat": {"enabled": False},
                        },
                    },
                },
            },
        },
    }

    expanded = expand_multi_device_settings(settings)

    assert expanded["epaper"]["device"]["display"]["liveness"] == {
        "contrast_breathing": {"enabled": False},
        "bottom_heartbeat": {"enabled": False, "period_s": 1, "pixel_count": 1, "position": "left"},
    }


def test_expand_multi_device_settings_normalizes_selector_values():
    settings = {
        "device": {"board": "pico2w"},
        "checks_config": "checks.local.yaml",
        "devices": {
            "oled": {
                "selector": {
                    "serial_by_id": "  /dev/serial/by-id/usb-oled  ",
                    "bootsel_disk": "  /dev/disk/by-id/usb-oled  ",
                },
            },
        },
    }

    expanded = expand_multi_device_settings(settings)

    assert expanded["oled"]["selector"] == {
        "serial_by_id": "/dev/serial/by-id/usb-oled",
        "bootsel_disk": "/dev/disk/by-id/usb-oled",
    }