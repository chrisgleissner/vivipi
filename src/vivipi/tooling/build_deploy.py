from __future__ import annotations

import argparse
import grp
import ipaddress
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent
from urllib.parse import urlparse

import yaml

from vivipi.core.config import parse_checks_config
from vivipi.core.display import (
    _parse_brightness as _core_parse_brightness,
    _parse_column_separator as _core_parse_column_separator,
    _parse_columns as _core_parse_columns,
    _parse_display_mode as _core_parse_display_mode,
    _parse_duration_s as _core_parse_duration_s,
    _parse_font_size_px as _core_parse_font_size_px,
    normalize_display_config,
)
from vivipi.core.models import CheckDefinition, CheckType


PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
OPTIONAL_PLACEHOLDERS = frozenset({"VIVIPI_SERVICE_BASE_URL"})
OPTIONAL_AUTH_PLACEHOLDER_KEYS = frozenset({"username", "password"})
DEFAULT_DEPLOY_PORT = "auto"
PRERELEASE_VERSION_PATTERN = re.compile(r"^(\d+\.\d+\.\d+)-?(a|b|rc)(\d+)$")


def _parse_brightness(value: object) -> int:
    return _core_parse_brightness(value, 128)


_parse_column_separator = _core_parse_column_separator
_parse_columns = _core_parse_columns
_parse_display_mode = _core_parse_display_mode
_parse_duration_s = _core_parse_duration_s
_parse_font_size_px = _core_parse_font_size_px


def resolve_config_path(config_path: str | Path, prefer_local_config: bool = False) -> Path:
    original_path = Path(config_path)
    if not prefer_local_config:
        return original_path
    resolved_path = original_path.resolve()
    if resolved_path.suffix.casefold() != ".yaml" or resolved_path.name.endswith(".local.yaml"):
        return resolved_path

    local_override_path = resolved_path.with_name(f"{resolved_path.stem}.local{resolved_path.suffix}")
    if local_override_path.exists():
        return local_override_path
    return original_path

def _resolve_placeholders(
    value: object,
    env: dict[str, str],
    optional_placeholders: frozenset[str] = frozenset(),
    optional_keys: frozenset[str] = frozenset(),
    key: str | None = None,
) -> object:
    if isinstance(value, dict):
        return {
            item_key: _resolve_placeholders(item, env, optional_placeholders, optional_keys, item_key)
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_placeholders(item, env, optional_placeholders, optional_keys, key) for item in value]
    if isinstance(value, str):
        full_match = PLACEHOLDER_PATTERN.fullmatch(value)

        def replace_match(match: re.Match[str]) -> str:
            variable_name = match.group(1)
            if variable_name not in env:
                if full_match is not None and (variable_name in optional_placeholders or key in optional_keys):
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


def _normalize_device_display_settings(settings: dict[str, object]):
    device = settings.get("device")
    if not isinstance(device, dict):
        return

    device["display"] = normalize_display_config(device.get("display"))


def _check_to_dict(check: CheckDefinition) -> dict[str, object]:
    return {
        "id": check.identifier,
        "name": check.name,
        "type": check.check_type.value,
        "target": check.target,
        "interval_s": check.interval_s,
        "timeout_s": check.timeout_s,
        "method": check.method,
        "username": check.username,
        "password": check.password,
        "service_prefix": check.service_prefix,
    }


def render_device_runtime_config(settings: dict[str, object], checks: tuple[CheckDefinition, ...]) -> dict[str, object]:
    project = dict(settings.get("project", {})) if isinstance(settings.get("project"), dict) else {}
    return {
        "project": project,
        "device": settings["device"],
        "wifi": settings["wifi"],
        "service": settings.get("service", {}),
        "checks": [_check_to_dict(check) for check in checks],
    }


def load_runtime_checks(path: str | Path, env: dict[str, str] | None = None) -> tuple[CheckDefinition, ...]:
    checks_path = Path(path)
    raw = yaml.safe_load(checks_path.read_text(encoding="utf-8")) or {}
    resolved = _resolve_placeholders(
        raw,
        env or dict(os.environ),
        optional_placeholders=OPTIONAL_PLACEHOLDERS,
        optional_keys=OPTIONAL_AUTH_PLACEHOLDER_KEYS,
    )

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
    deploy_port = _resolve_deploy_port(device, None)
    lines = [
        f"board: {device.get('board', 'unknown')}",
        f"micropython_version: {micropython.get('version', 'unspecified')}",
        f"download_page: {micropython.get('download_page', 'https://micropython.org/download/')}",
        f"port: {deploy_port}",
    ]
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def _versioned_release_path(output_dir: str | Path, stem: str, version: str, suffix: str) -> Path:
    return Path(output_dir) / f"{stem}-{version}{suffix}"


def _clear_generated_release_assets(output_dir: str | Path):
    release_dir = Path(output_dir)
    if not release_dir.exists():
        return

    generated_patterns = (
        "pico2w-micropython-*.txt",
        "vivipi-device-filesystem-*.zip",
        "vivipi-service-bundle-*.zip",
        "vivipi-source-*.zip",
        "vivipi-source-*.tar.gz",
    )
    legacy_paths = (
        release_dir / "pico2w-micropython.txt",
        release_dir / "vivipi-device-filesystem.zip",
        release_dir / "vivipi-firmware-bundle.zip",
    )

    for pattern in generated_patterns:
        for path in release_dir.glob(pattern):
            if path.is_file():
                path.unlink()

    for path in legacy_paths:
        if path.exists():
            path.unlink()


def _resolve_release_version(repository_root: Path, version_resolver=None) -> str:
    if version_resolver is not None:
        return version_resolver()

    from vivipi.core.version import resolve_version

    return resolve_version(repository_root)


def _write_service_bundle_readme(output_path: Path, version: str, wheel_name: str) -> Path:
    content = dedent(
        f"""\
        ViviPi service bundle {version}

        Contents:
        - {wheel_name}: installable Python package that provides the default ADB-backed service.
        - custom-service-example.py: minimal HTTP service that exposes custom checks on /checks.
        - service-response-example.json: example payload matching ViviPi's SERVICE schema.

        Default ADB service:
        1. python -m pip install {wheel_name}
        2. vivipi-adb-service --host 0.0.0.0 --port 8080

        Custom service example:
        1. python custom-service-example.py --host 0.0.0.0 --port 8080
        2. Point VIVIPI_SERVICE_BASE_URL at http://<host>:8080/checks in your device config.
        """
    )
    output_path.write_text(content, encoding="utf-8")
    return output_path


def _write_custom_service_example(output_path: Path) -> Path:
    output_path.write_text(
        dedent(
            """\
            from __future__ import annotations

            import argparse
            import json
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            from urllib.parse import urlparse


            PAYLOAD = {
                "checks": [
                    {
                        "name": "Router",
                        "status": "OK",
                        "details": "Reachable",
                        "latency_ms": 3.5,
                    },
                    {
                        "name": "NAS API",
                        "status": "DEG",
                        "details": "Slow response",
                        "latency_ms": 182.0,
                    },
                ]
            }


            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    route = urlparse(self.path).path
                    if route == "/health":
                        payload = {"status": "OK"}
                        self._respond(200, payload)
                        return
                    if route == "/checks":
                        self._respond(200, PAYLOAD)
                        return
                    self._respond(404, {"error": "not_found"})

                def log_message(self, format_string, *args):
                    return None

                def _respond(self, status_code: int, payload: dict[str, object]):
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(status_code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)


            def main(argv: list[str] | None = None) -> int:
                parser = argparse.ArgumentParser(description="Run a minimal ViviPi-compatible custom service")
                parser.add_argument("--host", default="0.0.0.0")
                parser.add_argument("--port", type=int, default=8080)
                args = parser.parse_args(argv)
                server = ThreadingHTTPServer((args.host, args.port), Handler)
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
                return 0


            if __name__ == "__main__":
                raise SystemExit(main())
            """
        ),
        encoding="utf-8",
    )
    return output_path


def _write_service_response_example(output_path: Path) -> Path:
    output_path.write_text(
        json.dumps(
            {
                "checks": [
                    {
                        "name": "Router",
                        "status": "OK",
                        "details": "Reachable",
                        "latency_ms": 3.5,
                    }
                ]
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output_path


def _resolve_release_wheel(dist_dir: str | Path) -> Path:
    matches = sorted(Path(dist_dir).glob("vivipi-*.whl"))
    if len(matches) != 1:
        raise ValueError("release packaging requires exactly one built wheel in dist")
    return matches[0]


def _release_version_from_wheel(wheel_path: Path) -> str:
    filename = wheel_path.name
    if not filename.startswith("vivipi-") or not filename.endswith(".whl"):
        raise ValueError("release packaging requires a vivipi wheel filename")

    parts = filename[:-4].split("-")
    if len(parts) < 5:
        raise ValueError("release packaging requires a standard wheel filename")
    return parts[1]


def _normalize_release_version(value: str) -> str:
    normalized = value.strip()
    match = PRERELEASE_VERSION_PATTERN.fullmatch(normalized)
    if match is None:
        return normalized
    return f"{match.group(1)}{match.group(2)}{match.group(3)}"


def _select_release_version(repository_version: str, wheel_version: str) -> str:
    if _normalize_release_version(repository_version) == _normalize_release_version(wheel_version):
        return repository_version
    return wheel_version


def _copy_release_tree(source: Path, destination: Path):
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def build_service_bundle(output_dir: str | Path, dist_dir: str | Path, version: str) -> Path:
    release_dir = Path(output_dir)
    release_dir.mkdir(parents=True, exist_ok=True)

    wheel_path = _resolve_release_wheel(dist_dir)
    staging_dir = release_dir / "vivipi-service-bundle"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    shutil.copy2(wheel_path, staging_dir / wheel_path.name)
    _write_service_bundle_readme(staging_dir / "README-service.txt", version, wheel_path.name)
    _write_custom_service_example(staging_dir / "custom-service-example.py")
    _write_service_response_example(staging_dir / "service-response-example.json")

    archive_path = _versioned_release_path(release_dir, "vivipi-service-bundle", version, "")
    built_archive = shutil.make_archive(str(archive_path), "zip", root_dir=staging_dir)
    shutil.rmtree(staging_dir)
    return Path(built_archive)


def build_source_archives(
    output_dir: str | Path,
    version: str,
    run_command=subprocess.run,
) -> tuple[Path, Path]:
    repository_root = Path(__file__).resolve().parents[3]
    release_dir = Path(output_dir)
    release_dir.mkdir(parents=True, exist_ok=True)

    archive_prefix = f"vivipi-{version}/"
    zip_path = _versioned_release_path(release_dir, "vivipi-source", version, ".zip")
    tar_path = _versioned_release_path(release_dir, "vivipi-source", version, ".tar.gz")

    for archive_path, archive_format in ((zip_path, "zip"), (tar_path, "tar.gz")):
        run_command(
            [
                "git",
                "archive",
                f"--format={archive_format}",
                f"--prefix={archive_prefix}",
                f"--output={archive_path}",
                "HEAD",
            ],
            check=True,
            cwd=repository_root,
        )

    return zip_path, tar_path


def stage_release_assets(
    config_path: str | Path,
    output_dir: str | Path,
    dist_dir: str | Path,
    env: dict[str, str] | None = None,
    version_resolver=None,
    build_time_resolver=None,
    run_command=subprocess.run,
) -> dict[str, Path]:
    repository_root = Path(__file__).resolve().parents[3]
    _clear_generated_release_assets(output_dir)

    wheel_path = _resolve_release_wheel(dist_dir)
    repository_version = _resolve_release_version(repository_root, version_resolver=version_resolver)
    version = _select_release_version(repository_version, _release_version_from_wheel(wheel_path))

    firmware_bundle = build_firmware_bundle(
        config_path,
        output_dir,
        env=env,
        version_resolver=lambda: version,
        build_time_resolver=build_time_resolver,
    )
    service_bundle = build_service_bundle(output_dir, dist_dir, version)
    source_zip, source_tar = build_source_archives(output_dir, version, run_command=run_command)

    return {
        "firmware_bundle": firmware_bundle,
        "service_bundle": service_bundle,
        "source_zip": source_zip,
        "source_tar": source_tar,
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
    version: str = "",
    build_time: str = "",
) -> Path:
    source_config_path = Path(config_path).resolve()
    settings = load_build_deploy_settings(source_config_path, env=env)
    checks = load_runtime_checks(_resolve_checks_path(source_config_path, settings), env=env)
    validate_runtime_settings(settings, checks)
    runtime_config = render_device_runtime_config(settings, checks)

    if version:
        runtime_config.setdefault("project", {})
        runtime_config["project"]["version"] = version
    if build_time:
        runtime_config.setdefault("project", {})
        runtime_config["project"]["build_time"] = build_time

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(runtime_config, indent=2, sort_keys=True), encoding="utf-8")
    return destination


def build_firmware_bundle(
    config_path: str | Path,
    output_dir: str | Path,
    env: dict[str, str] | None = None,
    version_resolver=None,
    build_time_resolver=None,
) -> Path:
    repository_root = Path(__file__).resolve().parents[3]
    release_dir = Path(output_dir)
    release_dir.mkdir(parents=True, exist_ok=True)

    source_config_path = Path(config_path).resolve()
    settings = load_build_deploy_settings(source_config_path, env=env)

    version = _resolve_release_version(repository_root, version_resolver=version_resolver)

    if build_time_resolver is not None:
        build_time = build_time_resolver()
    else:
        from datetime import datetime, timezone
        build_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    staging_dir = release_dir / "vivipi-device-fs"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    firmware_dir = repository_root / "firmware"
    package_dir = repository_root / "src" / "vivipi"

    for item in firmware_dir.iterdir():
        if item.name == "__pycache__" or item.suffix in {".pyc", ".pyo"}:
            continue
        if item.is_dir():
            _copy_release_tree(item, staging_dir / item.name)
        else:
            shutil.copy2(item, staging_dir / item.name)
    _copy_release_tree(package_dir, staging_dir / "vivipi")
    write_runtime_config(source_config_path, staging_dir / "config.json", env=env, version=version, build_time=build_time)
    write_install_manifest(settings, _versioned_release_path(release_dir, "pico2w-micropython", version, ".txt"))

    archive_path = _versioned_release_path(release_dir, "vivipi-device-filesystem", version, "")
    built_archive = shutil.make_archive(str(archive_path), "zip", root_dir=staging_dir)
    return Path(built_archive)


def _resolve_deploy_port(device: object, port: str | None) -> str:
    for candidate in (port, device.get("micropython_port") if isinstance(device, dict) else None):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return DEFAULT_DEPLOY_PORT


def _wrap_with_dialout(command: list[str]) -> list[str]:
    if os.name != "posix":
        return command
    try:
        dialout = grp.getgrnam("dialout")
    except KeyError:
        return command

    current_groups = set(os.getgroups())
    if dialout.gr_gid in current_groups:
        return command

    try:
        username = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return command

    if username not in set(dialout.gr_mem):
        return command

    return ["sg", "dialout", "-c", shlex.join(command)]


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
    resolved_port = _resolve_deploy_port(settings.get("device"), port)

    try:
        for item in sorted(device_root.iterdir(), key=lambda value: value.name):
            command = ["mpremote", "connect", resolved_port, "fs", "cp"]
            if item.is_dir():
                command.extend(["-r", str(item), ":"])
            else:
                command.extend([str(item), f":{item.name}"])
            run_command(_wrap_with_dialout(command), check=True)
        run_command(_wrap_with_dialout(["mpremote", "connect", resolved_port, "soft-reset"]), check=True)
    except FileNotFoundError as error:
        raise RuntimeError("mpremote is required for deploy") from error

    return bundle_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build ViviPi configuration and firmware artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render-config", help="Render runtime config JSON")
    render_parser.add_argument("--config", required=True)
    render_parser.add_argument("--output", required=True)
    render_parser.add_argument(
        "--prefer-local-config",
        action="store_true",
        help="Prefer a sibling <config>.local.yaml file when it exists",
    )

    bundle_parser = subparsers.add_parser("build-firmware", help="Build a zipped firmware bundle")
    bundle_parser.add_argument("--config", required=True)
    bundle_parser.add_argument("--output-dir", required=True)
    bundle_parser.add_argument(
        "--prefer-local-config",
        action="store_true",
        help="Prefer a sibling <config>.local.yaml file when it exists",
    )

    release_parser = subparsers.add_parser("stage-release-assets", help="Package the versioned GitHub release assets")
    release_parser.add_argument("--config", required=True)
    release_parser.add_argument("--output-dir", required=True)
    release_parser.add_argument("--dist-dir", required=True)
    release_parser.add_argument(
        "--prefer-local-config",
        action="store_true",
        help="Prefer a sibling <config>.local.yaml file when it exists",
    )

    deploy_parser = subparsers.add_parser("deploy-firmware", help="Copy the firmware bundle onto a Pico via mpremote")
    deploy_parser.add_argument("--config", required=True)
    deploy_parser.add_argument("--output-dir", required=True)
    deploy_parser.add_argument("--port")
    deploy_parser.add_argument(
        "--prefer-local-config",
        action="store_true",
        help="Prefer a sibling <config>.local.yaml file when it exists",
    )

    args = parser.parse_args(argv)
    if hasattr(args, "config"):
        args.config = str(resolve_config_path(args.config, prefer_local_config=args.prefer_local_config))
    if args.command == "render-config":
        write_runtime_config(args.config, args.output)
        return 0
    if args.command == "build-firmware":
        build_firmware_bundle(args.config, args.output_dir)
        return 0
    if args.command == "stage-release-assets":
        stage_release_assets(args.config, args.output_dir, args.dist_dir)
        return 0
    if args.command == "deploy-firmware":
        deploy_firmware(args.config, args.output_dir, port=args.port)
        return 0
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())