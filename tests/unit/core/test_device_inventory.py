from __future__ import annotations

from vivipi.core.device_inventory import resolve_device_inventory_state


def test_resolve_device_inventory_state_reports_serial_ready():
    state = resolve_device_inventory_state(
        {"serial_by_id": "/dev/serial/by-id/usb-oled"},
        serial_candidates=["/dev/serial/by-id/usb-oled"],
        port_candidates=["/dev/ttyACM0"],
        bootsel_candidates=["/dev/disk/by-id/usb-RPI_RP2350_AAAE129318B6B9B6-0:0"],
    )

    assert state.state == "serial-ready"
    assert state.serial_path == "/dev/serial/by-id/usb-oled"
    assert state.bootsel_disk is None


def test_resolve_device_inventory_state_reports_bootsel_when_serial_is_missing():
    state = resolve_device_inventory_state(
        {
            "serial_by_id": "/dev/serial/by-id/usb-epaper",
            "bootsel_disk": "/dev/disk/by-id/usb-RPI_RP2350_AAAE129318B6B9B6-0:0",
        },
        serial_candidates=["/dev/serial/by-id/usb-oled"],
        port_candidates=["/dev/ttyACM0"],
        bootsel_candidates=["/dev/disk/by-id/usb-RPI_RP2350_AAAE129318B6B9B6-0:0"],
    )

    assert state.state == "bootsel"
    assert state.serial_path is None
    assert state.bootsel_disk == "/dev/disk/by-id/usb-RPI_RP2350_AAAE129318B6B9B6-0:0"


def test_resolve_device_inventory_state_reports_missing_when_no_selector_matches():
    state = resolve_device_inventory_state(
        {
            "serial_by_id": "/dev/serial/by-id/usb-epaper",
            "bootsel_disk": "/dev/disk/by-id/usb-RPI_RP2350_AAAE129318B6B9B6-0:0",
        },
        serial_candidates=["/dev/serial/by-id/usb-oled"],
        port_candidates=["/dev/ttyACM0"],
        bootsel_candidates=[],
    )

    assert state.state == "missing"
    assert state.serial_path is None
    assert state.bootsel_disk is None


def test_resolve_device_inventory_state_reports_ambiguous_when_multiple_serials_match():
    state = resolve_device_inventory_state(
        {"serial_by_id": "/dev/serial/by-id/usb-oled*"},
        serial_candidates=[
            "/dev/serial/by-id/usb-oled-a",
            "/dev/serial/by-id/usb-oled-b",
        ],
        port_candidates=["/dev/ttyACM0", "/dev/ttyACM1"],
        bootsel_candidates=[],
    )

    assert state.state == "ambiguous"
    assert state.serial_matches == (
        "/dev/serial/by-id/usb-oled-a",
        "/dev/serial/by-id/usb-oled-b",
    )