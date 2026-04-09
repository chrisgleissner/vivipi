from pathlib import Path

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