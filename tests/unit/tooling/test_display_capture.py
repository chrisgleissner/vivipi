import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import vivipi.tooling.display_capture as display_capture
from vivipi.tooling.display_capture import decode_vertical_lsb, rotate_clockwise, rotate_counterclockwise, rotate_180, write_grayscale_png, write_capture_images


def test_decode_vertical_lsb_maps_column_major_bits_to_pixels():
    pixels = decode_vertical_lsb(bytes([0b00000101, 0b00000010]), width=2, height=8)

    assert pixels[0] == [1, 0]
    assert pixels[1] == [0, 1]
    assert pixels[2] == [1, 0]
    assert pixels[3] == [0, 0]


def test_rotation_helpers_cover_all_quadrants():
    pixels = [
        [1, 0, 0],
        [0, 1, 0],
    ]

    assert rotate_clockwise(pixels) == [
        [0, 1],
        [1, 0],
        [0, 0],
    ]
    assert rotate_counterclockwise(pixels) == [
        [0, 0],
        [0, 1],
        [1, 0],
    ]
    assert rotate_180(pixels) == [
        [0, 1, 0],
        [0, 0, 1],
    ]


def test_rotation_helpers_return_empty_for_empty_images():
    assert rotate_clockwise([]) == []
    assert rotate_counterclockwise([]) == []


def test_scale_pixels_preserves_identity_and_expands_rows():
    pixels = [[1, 0], [0, 1]]

    assert display_capture.scale_pixels(pixels, 1) == pixels
    assert display_capture.scale_pixels(pixels, 2) == [
        [1, 1, 0, 0],
        [1, 1, 0, 0],
        [0, 0, 1, 1],
        [0, 0, 1, 1],
    ]


def test_write_grayscale_png_creates_valid_png_signature(tmp_path: Path):
    path = write_grayscale_png(tmp_path / "capture.png", [[1, 0], [0, 1]], scale=1)

    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_write_capture_images_writes_all_variants_and_metadata(tmp_path: Path):
    capture = {
        "width": 2,
        "height": 8,
        "buffer": bytes([0b00000001, 0b00000010]),
        "display_class": "SH1107Display",
        "display_module": "displays.sh1107",
        "version": "0.1.0",
    }

    written = write_capture_images(capture, tmp_path, scale=1)

    assert {path.name for path in written} == {"logical.png", "rot90ccw.png", "rot90cw.png", "rot180.png"}
    assert (tmp_path / "capture.json").exists()


def test_capture_display_buffer_reads_sentinel_payload_from_mpremote(monkeypatch):
    recorded = {}

    def fake_run(command, check, capture_output, text, timeout):
        recorded["command"] = command
        recorded["check"] = check
        recorded["capture_output"] = capture_output
        recorded["text"] = text
        recorded["timeout"] = timeout
        return SimpleNamespace(
            stdout='boot\nVIVIPI_CAPTURE{"width":2,"height":8,"buffer_hex":"0102","display_class":"SH1107Display","display_module":"firmware.displays.sh1107","version":"0.1.0"}\n',
        )

    monkeypatch.setattr(display_capture.subprocess, "run", fake_run)

    capture = display_capture.capture_display_buffer("/dev/ttyACM1", config_path="device.json", mode="current")

    assert recorded["command"][:4] == ["mpremote", "connect", "/dev/ttyACM1", "exec"]
    assert "device.json" in recorded["command"][4]
    assert "'current'" in recorded["command"][4]
    assert capture["buffer"] == bytes([0x01, 0x02])
    assert capture["version"] == "0.1.0"


def test_capture_display_buffer_rejects_missing_sentinel(monkeypatch):
    monkeypatch.setattr(
        display_capture.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="boot\nno capture payload\n"),
    )

    with pytest.raises(ValueError, match="capture payload not found"):
        display_capture.capture_display_buffer("/dev/ttyACM0")


def test_main_captures_and_writes_images_with_clamped_scale(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(
        display_capture,
        "capture_display_buffer",
        lambda port, config_path, mode: {
            "width": 1,
            "height": 8,
            "buffer": bytes([0x01]),
            "display_class": "SH1107Display",
            "display_module": "firmware.displays.sh1107",
            "version": "0.1.0",
        },
    )
    monkeypatch.setattr(
        display_capture,
        "write_capture_images",
        lambda capture, output_dir, scale: [Path(output_dir) / f"logical-scale-{scale}.png"],
    )

    exit_code = display_capture.main(
        [
            "--port",
            "/dev/ttyACM9",
            "--config-path",
            "device.json",
            "--mode",
            "current",
            "--output-dir",
            str(tmp_path),
            "--scale",
            "0",
        ]
    )
    payload = json.loads(capsys.readouterr().out.strip())

    assert exit_code == 0
    assert payload == {
        "files": [str(tmp_path / "logical-scale-1.png")],
        "metadata": str(tmp_path / "capture.json"),
    }