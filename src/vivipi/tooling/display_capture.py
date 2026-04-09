from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zlib
from pathlib import Path


CAPTURE_SENTINEL = "VIVIPI_CAPTURE"


def decode_vertical_lsb(buffer: bytes | bytearray, width: int, height: int) -> list[list[int]]:
    pages = height // 8
    pixels = [[0 for _ in range(width)] for _ in range(height)]
    for page in range(pages):
        for x in range(width):
            byte_value = buffer[x + (page * width)]
            for bit in range(8):
                y = (page * 8) + bit
                if y < height:
                    pixels[y][x] = 1 if (byte_value >> bit) & 1 else 0
    return pixels


def rotate_clockwise(pixels: list[list[int]]) -> list[list[int]]:
    if not pixels:
        return []
    height = len(pixels)
    width = len(pixels[0])
    return [[pixels[height - 1 - y][x] for y in range(height)] for x in range(width)]


def rotate_counterclockwise(pixels: list[list[int]]) -> list[list[int]]:
    if not pixels:
        return []
    height = len(pixels)
    width = len(pixels[0])
    return [[pixels[y][width - 1 - x] for y in range(height)] for x in range(width)]


def rotate_180(pixels: list[list[int]]) -> list[list[int]]:
    return [list(reversed(row)) for row in reversed(pixels)]


def scale_pixels(pixels: list[list[int]], scale: int) -> list[list[int]]:
    if scale <= 1:
        return [list(row) for row in pixels]
    scaled: list[list[int]] = []
    for row in pixels:
        expanded_row: list[int] = []
        for value in row:
            expanded_row.extend([value] * scale)
        for _ in range(scale):
            scaled.append(list(expanded_row))
    return scaled


def write_grayscale_png(path: str | Path, pixels: list[list[int]], scale: int = 4) -> Path:
    scaled = scale_pixels(pixels, scale)
    height = len(scaled)
    width = len(scaled[0]) if scaled else 0
    raw = bytearray()
    for row in scaled:
        raw.append(0)
        raw.extend(0 if value else 255 for value in row)

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        payload = len(data).to_bytes(4, "big") + chunk_type + data
        crc = zlib.crc32(chunk_type + data).to_bytes(4, "big")
        return payload + crc

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(
        chunk(
            b"IHDR",
            width.to_bytes(4, "big")
            + height.to_bytes(4, "big")
            + bytes((8, 0, 0, 0, 0)),
        )
    )
    png.extend(chunk(b"IDAT", zlib.compress(bytes(raw), level=9)))
    png.extend(chunk(b"IEND", b""))
    destination.write_bytes(bytes(png))
    return destination


def capture_display_buffer(port: str, config_path: str = "config.json", mode: str = "boot-logo") -> dict[str, object]:
    device_code = f"""
import json
import ubinascii
import runtime

app = runtime.build_runtime_app_from_path({config_path!r})
display = app.display
if {mode!r} == 'boot-logo':
    display.show_boot_logo(getattr(app, 'version', ''))

payload = {{
    'width': int(display.width),
    'height': int(display.height),
    'buffer_hex': ubinascii.hexlify(bytes(display.buffer)).decode(),
    'display_class': display.__class__.__name__,
    'display_module': display.__class__.__module__,
    'version': getattr(app, 'version', ''),
}}
print({CAPTURE_SENTINEL!r} + json.dumps(payload, separators=(',', ':')))
""".strip()
    completed = subprocess.run(
        ["mpremote", "connect", port, "exec", device_code],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    for line in completed.stdout.splitlines():
        if line.startswith(CAPTURE_SENTINEL):
            payload = json.loads(line[len(CAPTURE_SENTINEL) :])
            payload["buffer"] = bytes.fromhex(str(payload.pop("buffer_hex")))
            return payload
    raise ValueError(f"capture payload not found in mpremote output: {completed.stdout!r}")


def write_capture_images(capture: dict[str, object], output_dir: str | Path, scale: int = 4) -> list[Path]:
    width = int(capture["width"])
    height = int(capture["height"])
    pixels = decode_vertical_lsb(capture["buffer"], width, height)
    variants = {
        "logical": pixels,
        "rot90ccw": rotate_counterclockwise(pixels),
        "rot90cw": rotate_clockwise(pixels),
        "rot180": rotate_180(pixels),
    }
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, variant in variants.items():
        written.append(write_grayscale_png(destination / f"{name}.png", variant, scale=scale))
    metadata = {
        key: value
        for key, value in capture.items()
        if key != "buffer"
    }
    metadata["buffer_size"] = len(capture["buffer"])
    (destination / "capture.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture Pico OLED framebuffer and render it as PNGs")
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--config-path", default="config.json")
    parser.add_argument("--mode", choices=("boot-logo", "current"), default="boot-logo")
    parser.add_argument("--output-dir", default="artifacts/display-capture")
    parser.add_argument("--scale", type=int, default=4)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    capture = capture_display_buffer(args.port, config_path=args.config_path, mode=args.mode)
    written = write_capture_images(capture, args.output_dir, scale=max(1, int(args.scale)))
    print(json.dumps({"files": [str(path) for path in written], "metadata": str(Path(args.output_dir) / 'capture.json')}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))