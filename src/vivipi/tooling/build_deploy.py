from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

import yaml

from vivipi.core.config import load_checks_config
from vivipi.core.models import CheckDefinition


PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _resolve_placeholders(value: object, env: dict[str, str]) -> object:
    if isinstance(value, dict):
        return {key: _resolve_placeholders(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(item, env) for item in value]
    if isinstance(value, str):
        def replace_match(match: re.Match[str]) -> str:
            variable_name = match.group(1)
            if variable_name not in env:
                raise KeyError(f"missing environment variable: {variable_name}")
            return env[variable_name]

        return PLACEHOLDER_PATTERN.sub(replace_match, value)
    return value


def load_build_deploy_settings(path: str | Path, env: dict[str, str] | None = None) -> dict[str, object]:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return _resolve_placeholders(raw, env or dict(os.environ))


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
        "service": settings["service"],
        "checks": [_check_to_dict(check) for check in checks],
    }


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
    checks = load_checks_config(_resolve_checks_path(source_config_path, settings))
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

    staging_dir = release_dir / "vivipi-firmware"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    firmware_dir = repository_root / "firmware"
    package_dir = repository_root / "src" / "vivipi"

    shutil.copy2(firmware_dir / "boot.py", staging_dir / "boot.py")
    shutil.copy2(firmware_dir / "main.py", staging_dir / "main.py")
    shutil.copytree(package_dir, staging_dir / "vivipi")
    write_runtime_config(config_path, staging_dir / "config.json", env=env)

    archive_path = release_dir / "vivipi-firmware-bundle"
    built_archive = shutil.make_archive(str(archive_path), "zip", root_dir=staging_dir)
    shutil.rmtree(staging_dir)
    return Path(built_archive)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build ViviPi configuration and firmware artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render-config", help="Render runtime config JSON")
    render_parser.add_argument("--config", required=True)
    render_parser.add_argument("--output", required=True)

    bundle_parser = subparsers.add_parser("build-firmware", help="Build a zipped firmware bundle")
    bundle_parser.add_argument("--config", required=True)
    bundle_parser.add_argument("--output-dir", required=True)

    args = parser.parse_args(argv)
    if args.command == "render-config":
        write_runtime_config(args.config, args.output)
        return 0
    if args.command == "build-firmware":
        build_firmware_bundle(args.config, args.output_dir)
        return 0
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())