from __future__ import annotations

import argparse
import copy
import grp
import ipaddress
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from textwrap import dedent
from urllib.parse import urlparse
from urllib.request import urlretrieve

import yaml

from vivipi.core.config import parse_checks_config
from vivipi.core.device_inventory import DeviceInventoryState, resolve_device_inventory_state
from vivipi.core.display import (
    _parse_brightness as _core_parse_brightness,
    _parse_column_separator as _core_parse_column_separator,
    _parse_columns as _core_parse_columns,
    _parse_display_mode as _core_parse_display_mode,
    _parse_duration_s as _core_parse_duration_s,
    _parse_font_size_px as _core_parse_font_size_px,
    normalize_display_config,
)
from vivipi.core.multi_device_config import expand_multi_device_settings, is_multi_device_settings
from vivipi.core.models import CheckDefinition, CheckType


PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
OPTIONAL_PLACEHOLDERS = frozenset({"VIVIPI_SERVICE_BASE_URL"})
OPTIONAL_AUTH_PLACEHOLDER_KEYS = frozenset({"username", "password"})
DEFAULT_DEPLOY_PORT = "auto"
PRERELEASE_VERSION_PATTERN = re.compile(r"^(\d+\.\d+\.\d+)-?(a|b|rc)(\d+)$")
MPREMOTE_COMMAND_TIMEOUT_S = 20
MPREMOTE_RECOVERY_TIMEOUT_S = 10
MPREMOTE_RECOVERY_ATTEMPTS = 1
DEFAULT_SINGLE_DEVICE_ID = "default"
DEVICE_ARTIFACTS_DIRNAME = "devices"
BOOTSEL_MOUNT_PATTERN = re.compile(r" at (?P<mount>/[^\n.]+)")


def _default_run_command(command: list[str], *, check: bool, timeout: int | None = None):
    return subprocess.run(
        command,
        check=check,
        timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _process_output_text(result: object) -> str:
    for attribute in ("stdout", "output", "stderr"):
        value = getattr(result, attribute, None)
        if value is None:
            continue
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        if isinstance(value, str):
            return value
    return ""


def _emit_process_output(result: object):
    payload = _process_output_text(result)
    if not payload:
        return
    sys.stdout.write(payload)
    sys.stdout.flush()


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


def _load_raw_build_deploy_settings(path: str | Path, env: dict[str, str] | None = None) -> dict[str, object]:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    resolved = _resolve_placeholders(raw, env or dict(os.environ), optional_placeholders=OPTIONAL_PLACEHOLDERS)

    return resolved


def _normalize_build_deploy_settings(settings: dict[str, object]) -> dict[str, object]:
    normalized = copy.deepcopy(settings)

    service = normalized.get("service")
    if isinstance(service, dict):
        base_url = service.get("base_url")
        if isinstance(base_url, str) and not base_url.strip():
            service.pop("base_url", None)
        _normalize_service_settings(normalized)

    _normalize_device_display_settings(normalized)
    _normalize_check_state_settings(normalized)
    _normalize_probe_schedule_settings(normalized)

    return normalized


def load_build_deploy_settings(path: str | Path, env: dict[str, str] | None = None) -> dict[str, object]:
    return _normalize_build_deploy_settings(_load_raw_build_deploy_settings(path, env=env))


def load_multi_device_build_deploy_settings(path: str | Path, env: dict[str, str] | None = None) -> dict[str, dict[str, object]]:
    resolved = _load_raw_build_deploy_settings(path, env=env)
    expanded = expand_multi_device_settings(resolved)
    return {
        device_id: _normalize_build_deploy_settings(device_settings)
        for device_id, device_settings in expanded.items()
    }


def load_selected_build_deploy_settings(
    path: str | Path,
    *,
    env: dict[str, str] | None = None,
    device_id: str | None = None,
    all_devices: bool = False,
) -> dict[str, dict[str, object]]:
    resolved = _load_raw_build_deploy_settings(path, env=env)
    if is_multi_device_settings(resolved):
        devices = {
            current_id: _normalize_build_deploy_settings(device_settings)
            for current_id, device_settings in expand_multi_device_settings(resolved).items()
        }
        if all_devices:
            return devices
        if device_id is None:
            raise ValueError("multi-device config requires --device or --all-devices")
        if device_id not in devices:
            raise ValueError(f"unknown device id: {device_id}")
        return {device_id: devices[device_id]}

    if device_id is not None and device_id != DEFAULT_SINGLE_DEVICE_ID:
        raise ValueError("single-device config does not define named devices")

    selected = load_build_deploy_settings(path, env=env)
    return {DEFAULT_SINGLE_DEVICE_ID: selected}


def _normalize_service_settings(settings: dict[str, object]):
    service = settings.get("service")
    if not isinstance(service, dict):
        return
    syslog = service.get("syslog")
    if syslog is not None and not isinstance(syslog, dict):
        raise ValueError("service.syslog must be a mapping")
    normalized_syslog = dict(syslog) if isinstance(syslog, dict) else {}
    base_url = service.get("base_url")
    derived_host = None
    if isinstance(base_url, str) and base_url.strip():
        parsed = urlparse(base_url)
        derived_host = parsed.hostname
    host = normalized_syslog.get("host") or derived_host
    if isinstance(host, str) and host.strip():
        normalized_syslog["host"] = host.strip()
    if normalized_syslog or (isinstance(host, str) and host.strip()):
        normalized_syslog["enabled"] = _parse_bool(
            normalized_syslog.get("enabled"),
            "service.syslog.enabled",
            bool(isinstance(host, str) and host.strip()),
        )
        normalized_syslog["port"] = _parse_int(normalized_syslog.get("port"), "service.syslog.port", 514)
        retry_interval = float(normalized_syslog.get("retry_interval_s", 5))
        if retry_interval < 0:
            raise ValueError("service.syslog.retry_interval_s must not be negative")
        normalized_syslog["retry_interval_s"] = retry_interval
        service["syslog"] = normalized_syslog


def _normalize_device_display_settings(settings: dict[str, object]):
    device = settings.get("device")
    if not isinstance(device, dict):
        return

    device["display"] = normalize_display_config(device.get("display"))


def _normalize_check_state_settings(settings: dict[str, object]):
    raw = settings.get("check_state")
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError("check_state must be a mapping")

    settings["check_state"] = {
        "failures_to_degraded": _parse_int(
            raw.get("failures_to_degraded"),
            "check_state.failures_to_degraded",
            1,
        ),
        "failures_to_failed": _parse_int(
            raw.get("failures_to_failed"),
            "check_state.failures_to_failed",
            2,
        ),
        "successes_to_recover": _parse_int(
            raw.get("successes_to_recover"),
            "check_state.successes_to_recover",
            1,
        ),
        "visible_degraded": _parse_bool(
            raw.get("visible_degraded"),
            "check_state.visible_degraded",
            True,
        ),
    }


def _parse_int(value: object, context: str, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} must be an integer") from error


def _parse_bool(value: object, context: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    raise ValueError(f"{context} must be a boolean")


def _normalize_probe_schedule_settings(settings: dict[str, object]):
    raw = settings.get("probe_schedule")
    if raw is None:
        settings["probe_schedule"] = {
            "allow_concurrent_hosts": False,
            "allow_concurrent_same_host": False,
            "same_host_backoff_ms": 250,
        }
        return
    if not isinstance(raw, dict):
        raise ValueError("probe_schedule must be a mapping")

    same_host_backoff_ms = int(raw.get("same_host_backoff_ms", 250))
    if same_host_backoff_ms < 0:
        raise ValueError("probe_schedule.same_host_backoff_ms must not be negative")
    interval_grace_ms = _parse_int(
        raw.get("interval_grace_ms"),
        "probe_schedule.interval_grace_ms",
        1000,
    )
    if interval_grace_ms < 0 or interval_grace_ms > 1000:
        raise ValueError("probe_schedule.interval_grace_ms must be between 0 and 1000")

    settings["probe_schedule"] = {
        "allow_concurrent_hosts": _parse_bool(
            raw.get("allow_concurrent_hosts"),
            "probe_schedule.allow_concurrent_hosts",
            False,
        ),
        "allow_concurrent_same_host": _parse_bool(
            raw.get("allow_concurrent_same_host"),
            "probe_schedule.allow_concurrent_same_host",
            False,
        ),
        "same_host_backoff_ms": same_host_backoff_ms,
        "interval_grace_ms": interval_grace_ms,
    }


def _invoke_run_command(run_command, command: list[str], *, check: bool, timeout: int | None = None):
    try:
        if timeout is None:
            return run_command(command, check=check)
        return run_command(command, check=check, timeout=timeout)
    except TypeError as error:
        if "timeout" not in str(error):
            raise
        return run_command(command, check=check)


def _run_mpremote_command(
    command: list[str],
    *,
    run_command,
    recovery_port: str,
    attempts: int = MPREMOTE_RECOVERY_ATTEMPTS,
):
    wrapped = _wrap_with_dialout(command)
    recovery_command = _wrap_with_dialout(["mpremote", "connect", recovery_port, "soft-reset"])

    for attempt in range(attempts + 1):
        try:
            result = _invoke_run_command(
                run_command,
                wrapped,
                check=True,
                timeout=MPREMOTE_COMMAND_TIMEOUT_S,
            )
            _emit_process_output(result)
            return result
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            if attempt >= attempts:
                _emit_process_output(error)
                raise
            time.sleep(float(attempt + 1))
            try:
                _invoke_run_command(
                    run_command,
                    recovery_command,
                    check=False,
                    timeout=MPREMOTE_RECOVERY_TIMEOUT_S,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass


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
    runtime_config = {
        "project": project,
        "device": settings["device"],
        "wifi": settings["wifi"],
        "service": settings.get("service", {}),
        "checks": [_check_to_dict(check) for check in checks],
    }
    check_state = settings.get("check_state")
    if isinstance(check_state, dict):
        runtime_config["check_state"] = dict(check_state)
    probe_schedule = settings.get("probe_schedule")
    if isinstance(probe_schedule, dict):
        runtime_config["probe_schedule"] = dict(probe_schedule)
    return runtime_config


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


def write_runtime_config_from_settings(
    source_config_path: str | Path,
    settings: dict[str, object],
    output_path: str | Path,
    *,
    env: dict[str, str] | None = None,
    version: str = "",
    build_time: str = "",
) -> Path:
    resolved_source_path = Path(source_config_path).resolve()
    checks = load_runtime_checks(_resolve_checks_path(resolved_source_path, settings), env=env)
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


def write_runtime_config(
    config_path: str | Path,
    output_path: str | Path,
    env: dict[str, str] | None = None,
    version: str = "",
    build_time: str = "",
) -> Path:
    source_config_path = Path(config_path).resolve()
    settings = load_build_deploy_settings(source_config_path, env=env)
    return write_runtime_config_from_settings(
        source_config_path,
        settings,
        output_path,
        env=env,
        version=version,
        build_time=build_time,
    )


def _copy_device_firmware_tree(staging_dir: Path):
    repository_root = Path(__file__).resolve().parents[3]
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


def _build_device_firmware_bundle(
    source_config_path: Path,
    settings: dict[str, object],
    device_output_dir: Path,
    *,
    env: dict[str, str] | None,
    version: str,
    build_time: str,
    include_device_config_copy: bool,
) -> dict[str, Path]:
    device_output_dir.mkdir(parents=True, exist_ok=True)

    staging_dir = device_output_dir / "vivipi-device-fs"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    _copy_device_firmware_tree(staging_dir)
    runtime_config_path = write_runtime_config_from_settings(
        source_config_path,
        settings,
        staging_dir / "config.json",
        env=env,
        version=version,
        build_time=build_time,
    )
    if include_device_config_copy:
        shutil.copy2(runtime_config_path, device_output_dir / "config.json")

    install_manifest_path = _versioned_release_path(device_output_dir, "pico2w-micropython", version, ".txt")
    write_install_manifest(settings, install_manifest_path)

    archive_base = _versioned_release_path(device_output_dir, "vivipi-device-filesystem", version, "")
    built_archive = shutil.make_archive(str(archive_base), "zip", root_dir=staging_dir)

    return {
        "staging_dir": staging_dir,
        "runtime_config": runtime_config_path,
        "install_manifest": install_manifest_path,
        "bundle": Path(built_archive),
    }


def _device_artifact_root(output_dir: str | Path, device_id: str) -> Path:
    return Path(output_dir) / DEVICE_ARTIFACTS_DIRNAME / device_id


def _write_multi_device_manifest(output_dir: str | Path, manifest_entries: dict[str, dict[str, object]]) -> Path:
    manifest_path = Path(output_dir) / DEVICE_ARTIFACTS_DIRNAME / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"devices": manifest_entries}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def build_firmware_bundles(
    config_path: str | Path,
    output_dir: str | Path,
    *,
    env: dict[str, str] | None = None,
    version_resolver=None,
    build_time_resolver=None,
    device_id: str | None = None,
    all_devices: bool = False,
) -> dict[str, dict[str, Path]]:
    repository_root = Path(__file__).resolve().parents[3]
    release_dir = Path(output_dir)
    release_dir.mkdir(parents=True, exist_ok=True)

    source_config_path = Path(config_path).resolve()
    selected_settings = load_selected_build_deploy_settings(
        source_config_path,
        env=env,
        device_id=device_id,
        all_devices=all_devices,
    )
    version = _resolve_release_version(repository_root, version_resolver=version_resolver)

    if build_time_resolver is not None:
        build_time = build_time_resolver()
    else:
        from datetime import datetime, timezone

        build_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    multiple_devices = len(selected_settings) > 1 or is_multi_device_settings(_load_raw_build_deploy_settings(source_config_path, env=env))
    outputs: dict[str, dict[str, Path]] = {}
    manifest_entries: dict[str, dict[str, object]] = {}

    for current_device_id, settings in selected_settings.items():
        device_output_dir = _device_artifact_root(release_dir, current_device_id) if multiple_devices else release_dir
        outputs[current_device_id] = _build_device_firmware_bundle(
            source_config_path,
            settings,
            device_output_dir,
            env=env,
            version=version,
            build_time=build_time,
            include_device_config_copy=multiple_devices,
        )
        manifest_entries[current_device_id] = {
            "device_id": current_device_id,
            "display_type": settings.get("device", {}).get("display", {}).get("type"),
            "checks_config": settings.get("checks_config"),
            "selectors": settings.get("selector", {}),
            "runtime_config": str(outputs[current_device_id]["runtime_config"]),
            "staging_dir": str(outputs[current_device_id]["staging_dir"]),
            "bundle": str(outputs[current_device_id]["bundle"]),
            "install_manifest": str(outputs[current_device_id]["install_manifest"]),
            "build_time": build_time,
        }

    if multiple_devices:
        _write_multi_device_manifest(release_dir, manifest_entries)

    return outputs


def build_firmware_bundle(
    config_path: str | Path,
    output_dir: str | Path,
    env: dict[str, str] | None = None,
    version_resolver=None,
    build_time_resolver=None,
) -> Path:
    outputs = build_firmware_bundles(
        config_path,
        output_dir,
        env=env,
        version_resolver=version_resolver,
        build_time_resolver=build_time_resolver,
    )
    return outputs[DEFAULT_SINGLE_DEVICE_ID]["bundle"]


def _list_serial_by_id_candidates() -> list[str]:
    root = Path("/dev/serial/by-id")
    if not root.exists():
        return []
    return sorted(str(path) for path in root.iterdir())


def _list_port_candidates() -> list[str]:
    candidates = {str(path) for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*") for path in Path("/").glob(pattern[1:])}
    return sorted(candidates)


def _list_bootsel_disk_candidates() -> list[str]:
    root = Path("/dev/disk/by-id")
    if not root.exists():
        return []
    return sorted(str(path) for path in root.iterdir() if "-part" not in path.name)


def _selector_from_settings(settings: dict[str, object]) -> dict[str, str]:
    selector = settings.get("selector")
    if isinstance(selector, dict) and selector:
        return {str(key): str(value) for key, value in selector.items()}

    device = settings.get("device")
    if isinstance(device, dict):
        port = device.get("micropython_port")
        if isinstance(port, str) and port.strip() and port.strip() != DEFAULT_DEPLOY_PORT:
            return {"port": port.strip()}

    raise ValueError("device inventory requires a selector or explicit device.micropython_port")


def resolve_configured_device_inventory(
    config_path: str | Path,
    *,
    env: dict[str, str] | None = None,
    device_id: str | None = None,
    all_devices: bool = False,
    serial_candidates: list[str] | None = None,
    port_candidates: list[str] | None = None,
    bootsel_candidates: list[str] | None = None,
) -> dict[str, DeviceInventoryState]:
    selected_settings = load_selected_build_deploy_settings(
        config_path,
        env=env,
        device_id=device_id,
        all_devices=all_devices,
    )
    resolved_serial_candidates = _list_serial_by_id_candidates() if serial_candidates is None else serial_candidates
    resolved_port_candidates = _list_port_candidates() if port_candidates is None else port_candidates
    resolved_bootsel_candidates = _list_bootsel_disk_candidates() if bootsel_candidates is None else bootsel_candidates

    return {
        current_device_id: resolve_device_inventory_state(
            _selector_from_settings(settings),
            serial_candidates=resolved_serial_candidates,
            port_candidates=resolved_port_candidates,
            bootsel_candidates=resolved_bootsel_candidates,
        )
        for current_device_id, settings in selected_settings.items()
    }


def _deploy_staged_device_root(device_root: Path, resolved_port: str, *, run_command) -> None:
    for item in sorted(device_root.iterdir(), key=lambda value: value.name):
        command = ["mpremote", "connect", resolved_port, "fs", "cp"]
        if item.is_dir():
            command.extend(["-r", str(item), ":"])
        else:
            command.extend([str(item), f":{item.name}"])
        _run_mpremote_command(command, run_command=run_command, recovery_port=resolved_port)
    _run_mpremote_command(
        ["mpremote", "connect", resolved_port, "reset"],
        run_command=run_command,
        recovery_port=resolved_port,
    )


def _resolve_bootstrap_uf2_path(source_config_path: Path, settings: dict[str, object], device_output_dir: Path) -> Path:
    bootstrap = settings.get("bootstrap")
    if not isinstance(bootstrap, dict):
        raise ValueError("BOOTSEL provisioning requires a bootstrap mapping")

    uf2_path_value = bootstrap.get("uf2_path")
    if isinstance(uf2_path_value, str) and uf2_path_value.strip():
        candidate_path = Path(uf2_path_value.strip())
        if not candidate_path.is_absolute():
            candidate_path = (source_config_path.parent / candidate_path).resolve()
        if not candidate_path.exists():
            raise ValueError(f"bootstrap UF2 not found: {candidate_path}")
        return candidate_path

    uf2_url_value = bootstrap.get("uf2_url")
    if isinstance(uf2_url_value, str) and uf2_url_value.strip():
        parsed = urlparse(uf2_url_value.strip())
        filename = Path(parsed.path).name or "bootstrap.uf2"
        destination = device_output_dir / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        urlretrieve(uf2_url_value.strip(), destination)
        return destination

    raise ValueError("BOOTSEL provisioning requires bootstrap.uf2_path or bootstrap.uf2_url")


def _resolve_bootsel_partition_path(bootsel_disk: str) -> Path:
    disk_path = Path(bootsel_disk)
    if disk_path.name.endswith("-part1"):
        if disk_path.exists():
            return disk_path
        raise ValueError(f"could not resolve BOOTSEL partition for {bootsel_disk}")
    partition_path = disk_path.with_name(f"{disk_path.name}-part1")
    if partition_path.exists():
        return partition_path
    raise ValueError(f"could not resolve BOOTSEL partition for {bootsel_disk}")


def _current_mountpoint(partition_path: Path, *, run_command) -> Path | None:
    result = _invoke_run_command(
        run_command,
        ["lsblk", "-no", "MOUNTPOINT", str(partition_path.resolve())],
        check=True,
    )
    mountpoint = _process_output_text(result).strip()
    if not mountpoint:
        return None
    return Path(mountpoint)


def _ensure_bootsel_mountpoint(partition_path: Path, *, run_command) -> Path:
    current_mountpoint = _current_mountpoint(partition_path, run_command=run_command)
    if current_mountpoint is not None:
        return current_mountpoint

    result = _invoke_run_command(
        run_command,
        ["udisksctl", "mount", "-b", str(partition_path.resolve())],
        check=True,
    )
    output = _process_output_text(result)
    match = BOOTSEL_MOUNT_PATTERN.search(output)
    if match is None:
        raise RuntimeError(f"could not determine BOOTSEL mountpoint from: {output.strip()}")
    return Path(match.group("mount"))


def _wait_for_serial_state(
    selector: dict[str, str],
    *,
    timeout_s: float,
    serial_candidates: list[str] | None = None,
    port_candidates: list[str] | None = None,
    bootsel_candidates: list[str] | None = None,
) -> DeviceInventoryState:
    deadline = time.monotonic() + timeout_s
    last_state: DeviceInventoryState | None = None
    while time.monotonic() <= deadline:
        last_state = resolve_device_inventory_state(
            selector,
            serial_candidates=_list_serial_by_id_candidates() if serial_candidates is None else serial_candidates,
            port_candidates=_list_port_candidates() if port_candidates is None else port_candidates,
            bootsel_candidates=_list_bootsel_disk_candidates() if bootsel_candidates is None else bootsel_candidates,
        )
        if last_state.state == "serial-ready":
            return last_state
        time.sleep(1.0)
    raise TimeoutError(f"timed out waiting for serial device; last state was {getattr(last_state, 'state', 'unknown')}")


def _require_serial_identity_selector(selector: dict[str, str]) -> dict[str, str]:
    if "serial_by_id" in selector or "port" in selector:
        return selector
    raise ValueError("provisioning requires selector.serial_by_id or selector.port")


def provision_device(
    config_path: str | Path,
    settings: dict[str, object],
    bootsel_disk: str,
    device_output_dir: Path,
    *,
    run_command=None,
) -> str:
    effective_run_command = _default_run_command if run_command is None else run_command
    source_config_path = Path(config_path).resolve()
    bootstrap = settings.get("bootstrap") if isinstance(settings.get("bootstrap"), dict) else {}
    timeout_s = float(bootstrap.get("serial_timeout_s", 30))
    selector = _require_serial_identity_selector(_selector_from_settings(settings))
    uf2_path = _resolve_bootstrap_uf2_path(source_config_path, settings, device_output_dir)
    partition_path = _resolve_bootsel_partition_path(bootsel_disk)
    mountpoint = _ensure_bootsel_mountpoint(partition_path, run_command=effective_run_command)
    shutil.copy2(uf2_path, mountpoint / uf2_path.name)
    os.sync()

    serial_state = _wait_for_serial_state(selector, timeout_s=timeout_s)
    if serial_state.serial_path is None:
        raise RuntimeError("provisioning completed without a resolved serial path")

    _run_mpremote_command(
        ["mpremote", "connect", serial_state.serial_path, "fs", "ls", ":"],
        run_command=effective_run_command,
        recovery_port=serial_state.serial_path,
        attempts=0,
    )
    return serial_state.serial_path


def deploy_firmware_targets(
    config_path: str | Path,
    output_dir: str | Path,
    *,
    env: dict[str, str] | None = None,
    device_id: str | None = None,
    all_devices: bool = False,
    provision_missing: bool = False,
    run_command=None,
    serial_candidates: list[str] | None = None,
    port_candidates: list[str] | None = None,
    bootsel_candidates: list[str] | None = None,
) -> dict[str, dict[str, object]]:
    effective_run_command = _default_run_command if run_command is None else run_command
    source_config_path = Path(config_path).resolve()
    selected_settings = load_selected_build_deploy_settings(
        source_config_path,
        env=env,
        device_id=device_id,
        all_devices=all_devices,
    )
    inventory = resolve_configured_device_inventory(
        source_config_path,
        env=env,
        device_id=device_id,
        all_devices=all_devices,
        serial_candidates=serial_candidates,
        port_candidates=port_candidates,
        bootsel_candidates=bootsel_candidates,
    )
    outputs = build_firmware_bundles(
        source_config_path,
        output_dir,
        env=env,
        device_id=device_id,
        all_devices=all_devices,
    )

    results: dict[str, dict[str, object]] = {}
    for current_device_id, settings in selected_settings.items():
        state = inventory[current_device_id]
        try:
            resolved_port = None
            if state.state == "serial-ready":
                resolved_port = state.serial_path
            elif state.state == "bootsel" and provision_missing:
                resolved_port = provision_device(
                    source_config_path,
                    settings,
                    state.bootsel_disk,
                    _device_artifact_root(output_dir, current_device_id),
                    run_command=effective_run_command,
                )
            elif state.state == "bootsel":
                raise RuntimeError("device is in BOOTSEL mode; rerun with --provision-missing or provision it explicitly")
            elif state.state == "missing":
                raise RuntimeError("device selector did not match any connected hardware")
            else:
                raise RuntimeError("device selector matched multiple connected devices")

            if resolved_port is None:
                raise RuntimeError("could not resolve a serial port for deploy")

            _deploy_staged_device_root(outputs[current_device_id]["staging_dir"], resolved_port, run_command=effective_run_command)
            results[current_device_id] = {
                "status": "ok",
                "state": state.state,
                "port": resolved_port,
                "bundle": outputs[current_device_id]["bundle"],
            }
        except Exception as error:
            results[current_device_id] = {
                "status": "error",
                "state": state.state,
                "error": str(error),
                "bundle": outputs[current_device_id]["bundle"],
            }

    return results


def provision_firmware_targets(
    config_path: str | Path,
    output_dir: str | Path,
    *,
    env: dict[str, str] | None = None,
    device_id: str | None = None,
    all_devices: bool = False,
    run_command=None,
    serial_candidates: list[str] | None = None,
    port_candidates: list[str] | None = None,
    bootsel_candidates: list[str] | None = None,
) -> dict[str, dict[str, object]]:
    effective_run_command = _default_run_command if run_command is None else run_command
    source_config_path = Path(config_path).resolve()
    selected_settings = load_selected_build_deploy_settings(
        source_config_path,
        env=env,
        device_id=device_id,
        all_devices=all_devices,
    )
    inventory = resolve_configured_device_inventory(
        source_config_path,
        env=env,
        device_id=device_id,
        all_devices=all_devices,
        serial_candidates=serial_candidates,
        port_candidates=port_candidates,
        bootsel_candidates=bootsel_candidates,
    )

    results: dict[str, dict[str, object]] = {}
    for current_device_id, settings in selected_settings.items():
        state = inventory[current_device_id]
        try:
            if state.state == "serial-ready":
                results[current_device_id] = {
                    "status": "ok",
                    "state": state.state,
                    "port": state.serial_path,
                }
                continue
            if state.state == "bootsel":
                device_output_dir = _device_artifact_root(output_dir, current_device_id)
                resolved_port = provision_device(
                    source_config_path,
                    settings,
                    state.bootsel_disk,
                    device_output_dir,
                    run_command=effective_run_command,
                )
                results[current_device_id] = {
                    "status": "ok",
                    "state": state.state,
                    "port": resolved_port,
                }
                continue
            if state.state == "missing":
                raise RuntimeError("device selector did not match any connected hardware")
            raise RuntimeError("device selector matched multiple connected devices")
        except Exception as error:
            results[current_device_id] = {
                "status": "error",
                "state": state.state,
                "error": str(error),
            }

    return results


def write_selected_runtime_config(
    config_path: str | Path,
    output_path: str | Path,
    *,
    env: dict[str, str] | None = None,
    device_id: str | None = None,
) -> Path:
    source_config_path = Path(config_path).resolve()
    selected_settings = load_selected_build_deploy_settings(source_config_path, env=env, device_id=device_id)
    current_device_id, settings = next(iter(selected_settings.items()))
    return write_runtime_config_from_settings(
        source_config_path,
        settings,
        output_path,
        env=env,
        version=settings.get("project", {}).get("version", "") if isinstance(settings.get("project"), dict) else "",
        build_time=settings.get("project", {}).get("build_time", "") if isinstance(settings.get("project"), dict) else "",
    )


def list_devices(
    config_path: str | Path,
    *,
    env: dict[str, str] | None = None,
    device_id: str | None = None,
    all_devices: bool = False,
) -> dict[str, object]:
    inventory = resolve_configured_device_inventory(
        config_path,
        env=env,
        device_id=device_id,
        all_devices=all_devices,
    )
    for current_device_id, state in inventory.items():
        fields = [current_device_id, state.state]
        if state.serial_path is not None:
            fields.append(f"serial={state.serial_path}")
        if state.bootsel_disk is not None:
            fields.append(f"bootsel={state.bootsel_disk}")
        print(" ".join(fields))
    return inventory


def _report_operation_results(results: dict[str, dict[str, object]]) -> int:
    exit_code = 0
    for current_device_id, result in results.items():
        status = result.get("status", "unknown")
        state = result.get("state", "unknown")
        if status != "ok":
            exit_code = 1
        message_parts = [current_device_id, status, f"state={state}"]
        port = result.get("port")
        if isinstance(port, str) and port:
            message_parts.append(f"port={port}")
        error = result.get("error")
        if isinstance(error, str) and error:
            message_parts.append(f"error={error}")
        print(" ".join(message_parts))
    return exit_code


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

    return ["sg", "dialout", "-c", f"exec {shlex.join(command)}"]


def deploy_firmware(
    config_path: str | Path,
    output_dir: str | Path,
    env: dict[str, str] | None = None,
    port: str | None = None,
    run_command=None,
) -> Path:
    source_config_path = Path(config_path).resolve()
    settings = load_build_deploy_settings(source_config_path, env=env)
    bundle_path = build_firmware_bundle(source_config_path, output_dir, env=env)
    device_root = Path(output_dir) / "vivipi-device-fs"
    resolved_port = _resolve_deploy_port(settings.get("device"), port)
    effective_run_command = _default_run_command if run_command is None else run_command

    try:
        _deploy_staged_device_root(device_root, resolved_port, run_command=effective_run_command)
    except FileNotFoundError as error:
        raise RuntimeError("mpremote is required for deploy") from error

    return bundle_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build ViviPi configuration and firmware artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render-config", help="Render runtime config JSON")
    render_parser.add_argument("--config", required=True)
    render_parser.add_argument("--output", required=True)
    render_parser.add_argument("--device")
    render_parser.add_argument(
        "--prefer-local-config",
        action="store_true",
        help="Prefer a sibling <config>.local.yaml file when it exists",
    )

    bundle_parser = subparsers.add_parser("build-firmware", help="Build a zipped firmware bundle")
    bundle_parser.add_argument("--config", required=True)
    bundle_parser.add_argument("--output-dir", required=True)
    bundle_parser.add_argument("--device")
    bundle_parser.add_argument("--all-devices", action="store_true")
    bundle_parser.add_argument(
        "--prefer-local-config",
        action="store_true",
        help="Prefer a sibling <config>.local.yaml file when it exists",
    )

    list_parser = subparsers.add_parser("list-devices", help="List configured multi-device targets and their current state")
    list_parser.add_argument("--config", required=True)
    list_parser.add_argument("--device")
    list_parser.add_argument("--all-devices", action="store_true")
    list_parser.add_argument(
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
    deploy_parser.add_argument("--device")
    deploy_parser.add_argument("--all-devices", action="store_true")
    deploy_parser.add_argument("--provision-missing", action="store_true")
    deploy_parser.add_argument(
        "--prefer-local-config",
        action="store_true",
        help="Prefer a sibling <config>.local.yaml file when it exists",
    )

    provision_parser = subparsers.add_parser("provision-firmware", help="Provision BOOTSEL devices with a base UF2")
    provision_parser.add_argument("--config", required=True)
    provision_parser.add_argument("--output-dir", required=True)
    provision_parser.add_argument("--device")
    provision_parser.add_argument("--all-devices", action="store_true")
    provision_parser.add_argument(
        "--prefer-local-config",
        action="store_true",
        help="Prefer a sibling <config>.local.yaml file when it exists",
    )

    args = parser.parse_args(argv)
    if hasattr(args, "config"):
        args.config = str(resolve_config_path(args.config, prefer_local_config=args.prefer_local_config))
    if args.command == "render-config":
        if getattr(args, "device", None):
            write_selected_runtime_config(args.config, args.output, device_id=args.device)
            return 0
        write_runtime_config(args.config, args.output)
        return 0
    if args.command == "list-devices":
        list_devices(args.config, device_id=getattr(args, "device", None), all_devices=args.all_devices)
        return 0
    if args.command == "build-firmware":
        if args.all_devices or getattr(args, "device", None):
            build_firmware_bundles(
                args.config,
                args.output_dir,
                device_id=getattr(args, "device", None),
                all_devices=args.all_devices,
            )
            return 0
        build_firmware_bundle(args.config, args.output_dir)
        return 0
    if args.command == "stage-release-assets":
        stage_release_assets(args.config, args.output_dir, args.dist_dir)
        return 0
    if args.command == "provision-firmware":
        return _report_operation_results(
            provision_firmware_targets(
                args.config,
                args.output_dir,
                device_id=getattr(args, "device", None),
                all_devices=args.all_devices,
            )
        )
    if args.command == "deploy-firmware":
        if args.port and (args.all_devices or getattr(args, "device", None)):
            raise ValueError("--port is only supported for single-device deploy-firmware")
        if args.all_devices or getattr(args, "device", None):
            return _report_operation_results(
                deploy_firmware_targets(
                    args.config,
                    args.output_dir,
                    device_id=getattr(args, "device", None),
                    all_devices=args.all_devices,
                    provision_missing=args.provision_missing,
                )
            )
        deploy_firmware(args.config, args.output_dir, port=args.port)
        return 0
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
