from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import yaml

from vivipi.core.config import parse_checks_config
from vivipi.core.models import CheckDefinition, CheckType


PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
OPTIONAL_PLACEHOLDERS = frozenset({"VIVIPI_SERVICE_BASE_URL"})
DURATION_PATTERN = re.compile(r"^(\d+)(?:\s*s)?$", re.IGNORECASE)
DEFAULT_FONT_WIDTH_PX = 8
DEFAULT_FONT_HEIGHT_PX = 8
MIN_FONT_SIZE_PX = 6
MAX_FONT_SIZE_PX = 32
DEFAULT_PAGE_INTERVAL_S = 15
DEFAULT_BRIGHTNESS = 128
DEFAULT_DISPLAY_MODE = "standard"
DEFAULT_COLUMNS = 1
DEFAULT_COLUMN_SEPARATOR = " "
DISPLAY_MODES = frozenset({"standard", "compact"})
BRIGHTNESS_PRESETS = {
    "low": 64,
    "medium": DEFAULT_BRIGHTNESS,
    "high": 192,
    "max": 255,
}


def _resolve_placeholders(value: object, env: dict[str, str], optional_placeholders: frozenset[str] = frozenset()) -> object:
    if isinstance(value, dict):
        return {key: _resolve_placeholders(item, env, optional_placeholders) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(item, env, optional_placeholders) for item in value]
    if isinstance(value, str):
        full_match = PLACEHOLDER_PATTERN.fullmatch(value)

        def replace_match(match: re.Match[str]) -> str:
            variable_name = match.group(1)
            if variable_name not in env:
                if variable_name in optional_placeholders and full_match is not None:
                    return ""
                raise KeyError(f"missing environment variable: {variable_name}")
            return env[variable_name]

        return PLACEHOLDER_PATTERN.sub(replace_match, value)
    return value


def load_build_deploy_settings(path: str | Path, env: dict[str, str] | None = None) -> dict[str, object]:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    resolved = _resolve_placeholders(raw, env or dict(os.environ), optional_placeholders=OPTIONAL_PLACEHOLDERS)

    service = resolved.get("service")
    if isinstance(service, dict):
        base_url = service.get("base_url")
        if isinstance(base_url, str) and not base_url.strip():
            service.pop("base_url", None)

    _normalize_device_display_settings(resolved)

    return resolved


def _parse_duration_s(value: object, context: str) -> int:
    if isinstance(value, int):
        seconds = value
    elif isinstance(value, float) and value.is_integer():
        seconds = int(value)
    elif isinstance(value, str):
        match = DURATION_PATTERN.fullmatch(value.strip())
        if match is None:
            raise ValueError(f"{context} must be an integer number of seconds or use the '<seconds>s' format")
        seconds = int(match.group(1))
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


def _parse_brightness(value: object) -> int:
    if value is None:
        return DEFAULT_BRIGHTNESS
    if isinstance(value, str):
        normalized = value.strip().casefold()
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
        return DEFAULT_DISPLAY_MODE
    if not isinstance(value, str):
        raise ValueError("device.display.mode must be 'standard' or 'compact'")

    normalized = value.strip().casefold()
    if normalized not in DISPLAY_MODES:
        raise ValueError("device.display.mode must be 'standard' or 'compact'")
    return normalized


def _parse_columns(value: object) -> int:
    if value is None:
        return DEFAULT_COLUMNS
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
        return DEFAULT_COLUMN_SEPARATOR
    if not isinstance(value, str):
        raise ValueError("device.display.column_separator must be exactly one character")
    if len(value) != 1:
        raise ValueError("device.display.column_separator must be exactly one character")
    return value


def _normalize_device_display_settings(settings: dict[str, object]):
    device = settings.get("device")
    if not isinstance(device, dict):
        return

    display = device.get("display")
    if not isinstance(display, dict):
        return

    font_config = display.get("font")
    if font_config is None:
        font = {}
    elif isinstance(font_config, dict):
        font = dict(font_config)
    else:
        raise ValueError("device.display.font must be a mapping")

    display["font"] = {
        "width_px": _parse_font_size_px(font.get("width_px"), "device.display.font.width_px", DEFAULT_FONT_WIDTH_PX),
        "height_px": _parse_font_size_px(font.get("height_px"), "device.display.font.height_px", DEFAULT_FONT_HEIGHT_PX),
    }

    interval_value = display.get("page_interval", display.get("page_interval_s", DEFAULT_PAGE_INTERVAL_S))
    display["page_interval_s"] = _parse_duration_s(interval_value, "device.display.page_interval")
    display.pop("page_interval", None)
    display["brightness"] = _parse_brightness(display.get("brightness"))
    display["mode"] = _parse_display_mode(display.get("mode"))
    display["columns"] = _parse_columns(display.get("columns"))
    display["column_separator"] = _parse_column_separator(display.get("column_separator"))


def _check_to_dict(check: CheckDefinition) -> dict[str, object]:
    return {
        "id": check.identifier,
        "name": check.name,
        "type": check.check_type.value,
        "target": check.target,
        "interval_s": check.interval_s,
        "timeout_s": check.timeout_s,
        "method": check.method,
        "service_prefix": check.service_prefix,
    }


def render_device_runtime_config(settings: dict[str, object], checks: tuple[CheckDefinition, ...]) -> dict[str, object]:
    return {
        "project": settings.get("project", {}),
        "device": settings["device"],
        "wifi": settings["wifi"],
        "service": settings.get("service", {}),
        "checks": [_check_to_dict(check) for check in checks],
    }


def load_runtime_checks(path: str | Path, env: dict[str, str] | None = None) -> tuple[CheckDefinition, ...]:
    checks_path = Path(path)
    raw = yaml.safe_load(checks_path.read_text(encoding="utf-8")) or {}
    resolved = _resolve_placeholders(raw, env or dict(os.environ), optional_placeholders=OPTIONAL_PLACEHOLDERS)

    checks = resolved.get("checks")
    if not isinstance(checks, list):
        raise ValueError("checks must be a list")

    filtered_checks = []
    for item in checks:
        if not isinstance(item, dict):
            filtered_checks.append(item)
            continue

        item_type = item.get("type")
        target = item.get("target")
        if isinstance(item_type, str) and item_type.strip().casefold() == "service" and isinstance(target, str) and not target.strip():
            continue
        filtered_checks.append(item)

    return parse_checks_config({"checks": filtered_checks})


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().casefold()
    if normalized in {"localhost", "::1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_device_reachable_url(url: str, context: str):
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError(f"{context} must be an absolute http or https URL")
    if _is_loopback_host(host):
        raise ValueError(f"{context} must use a host reachable from the Pico")


def validate_runtime_settings(settings: dict[str, object], checks: tuple[CheckDefinition, ...]):
    service = settings.get("service", {})
    if isinstance(service, dict):
        base_url = service.get("base_url")
        if isinstance(base_url, str) and base_url.strip():
            _validate_device_reachable_url(base_url, "service.base_url")

    for check in checks:
        if check.check_type == CheckType.SERVICE:
            _validate_device_reachable_url(check.target, f"check {check.identifier}")


def write_install_manifest(settings: dict[str, object], output_path: str | Path) -> Path:
    device = settings["device"]
    micropython = device.get("micropython", {}) if isinstance(device, dict) else {}
    lines = [
        f"board: {device.get('board', 'unknown')}",
        f"micropython_version: {micropython.get('version', 'unspecified')}",
        f"download_page: {micropython.get('download_page', 'https://micropython.org/download/')}",
        f"port: {device.get('micropython_port', '')}",
    ]
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def _resolve_checks_path(config_path: Path, settings: dict[str, object]) -> Path:
    checks_config = settings.get("checks_config")
    if not isinstance(checks_config, str) or not checks_config.strip():
        raise ValueError("checks_config must be present in the build/deploy settings")
    return (config_path.parent / checks_config).resolve()


def write_runtime_config(
    config_path: str | Path,
    output_path: str | Path,
    env: dict[str, str] | None = None,
) -> Path:
    source_config_path = Path(config_path).resolve()
    settings = load_build_deploy_settings(source_config_path, env=env)
    checks = load_runtime_checks(_resolve_checks_path(source_config_path, settings), env=env)
    validate_runtime_settings(settings, checks)
    runtime_config = render_device_runtime_config(settings, checks)

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(runtime_config, indent=2, sort_keys=True), encoding="utf-8")
    return destination


def build_firmware_bundle(
    config_path: str | Path,
    output_dir: str | Path,
    env: dict[str, str] | None = None,
) -> Path:
    repository_root = Path(__file__).resolve().parents[3]
    release_dir = Path(output_dir)
    release_dir.mkdir(parents=True, exist_ok=True)

    source_config_path = Path(config_path).resolve()
    settings = load_build_deploy_settings(source_config_path, env=env)

    staging_dir = release_dir / "vivipi-device-fs"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    firmware_dir = repository_root / "firmware"
    package_dir = repository_root / "src" / "vivipi"

    for item in firmware_dir.iterdir():
        if item.is_dir():
            shutil.copytree(item, staging_dir / item.name)
        else:
            shutil.copy2(item, staging_dir / item.name)
    shutil.copytree(package_dir, staging_dir / "vivipi")
    write_runtime_config(source_config_path, staging_dir / "config.json", env=env)
    write_install_manifest(settings, release_dir / "pico2w-micropython.txt")

    archive_path = release_dir / "vivipi-firmware-bundle"
    built_archive = shutil.make_archive(str(archive_path), "zip", root_dir=staging_dir)
    shutil.make_archive(str(release_dir / "vivipi-device-filesystem"), "zip", root_dir=staging_dir)
    return Path(built_archive)


def deploy_firmware(
    config_path: str | Path,
    output_dir: str | Path,
    env: dict[str, str] | None = None,
    port: str | None = None,
    run_command=subprocess.run,
) -> Path:
    source_config_path = Path(config_path).resolve()
    settings = load_build_deploy_settings(source_config_path, env=env)
    bundle_path = build_firmware_bundle(source_config_path, output_dir, env=env)
    device_root = Path(output_dir) / "vivipi-device-fs"
    resolved_port = port or str(settings["device"].get("micropython_port", "")).strip()
    if not resolved_port:
        raise ValueError("device.micropython_port must be configured for deploy")

    try:
        for item in sorted(device_root.iterdir(), key=lambda value: value.name):
            command = ["mpremote", "connect", resolved_port, "fs", "cp"]
            if item.is_dir():
                command.extend(["-r", str(item), ":"])
            else:
                command.extend([str(item), f":{item.name}"])
            run_command(command, check=True)
    except FileNotFoundError as error:
        raise RuntimeError("mpremote is required for deploy") from error

    return bundle_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build ViviPi configuration and firmware artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render-config", help="Render runtime config JSON")
    render_parser.add_argument("--config", required=True)
    render_parser.add_argument("--output", required=True)

    bundle_parser = subparsers.add_parser("build-firmware", help="Build a zipped firmware bundle")
    bundle_parser.add_argument("--config", required=True)
    bundle_parser.add_argument("--output-dir", required=True)

    deploy_parser = subparsers.add_parser("deploy-firmware", help="Copy the firmware bundle onto a Pico via mpremote")
    deploy_parser.add_argument("--config", required=True)
    deploy_parser.add_argument("--output-dir", required=True)
    deploy_parser.add_argument("--port")

    args = parser.parse_args(argv)
    if args.command == "render-config":
        write_runtime_config(args.config, args.output)
        return 0
    if args.command == "build-firmware":
        build_firmware_bundle(args.config, args.output_dir)
        return 0
    if args.command == "deploy-firmware":
        deploy_firmware(args.config, args.output_dir, port=args.port)
        return 0
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())