import importlib.util
import json
import runpy
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from vivipi.core.models import CheckDefinition, CheckType
from vivipi.tooling import build_deploy
from vivipi.tooling.build_deploy import (
    _clear_generated_release_assets,
    _copy_release_tree,
    _invoke_run_command,
    _is_loopback_host,
    _parse_brightness,
    _parse_column_separator,
    _parse_columns,
    _parse_display_mode,
    _parse_duration_s,
    _parse_font_size_px,
    _release_version_from_wheel,
    _resolve_checks_path,
    _resolve_release_wheel,
    _run_mpremote_command,
    _wrap_with_dialout,
    build_firmware_bundle,
    build_service_bundle,
    build_source_archives,
    deploy_firmware,
    load_build_deploy_settings,
    load_runtime_checks,
    render_device_runtime_config,
    resolve_config_path,
    stage_release_assets,
    validate_runtime_settings,
    write_install_manifest,
    write_runtime_config,
)


FIXTURE_ENV = {
    "VIVIPI_WIFI_SSID": "TestWifi",
    "VIVIPI_WIFI_PASSWORD": "TestPassword",
    "VIVIPI_SERVICE_BASE_URL": "http://192.0.2.10:8080/checks",
}


def write_fixture_files(tmp_path: Path) -> Path:
    checks_path = tmp_path / "checks.yaml"
    checks_path.write_text(
        """
checks:
  - name: Router
    type: ping
    target: 192.168.1.1
    interval_s: 15
    timeout_s: 10
""".strip(),
        encoding="utf-8",
    )

    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "project:",
                "  name: vivipi",
                "device:",
                "  board: pico2w",
                "  micropython_port: /dev/ttyACM0",
                "  micropython:",
                "    version: 1.25.0",
                "    download_page: https://micropython.org/download/RPI_PICO2_W/",
                "  buttons:",
                "    a: GP15",
                "    b: GP17",
                "  display:",
                "    type: waveshare-pico-oled-1.3",
                "    mode: standard",
                "    columns: 1",
                "    column_separator: ' '",
                "    boot_logo_duration: 2s",
                "wifi:",
                "  ssid: ${VIVIPI_WIFI_SSID}",
                "  password: ${VIVIPI_WIFI_PASSWORD}",
                "service:",
                "  base_url: ${VIVIPI_SERVICE_BASE_URL}",
                "check_state:",
                "  failures_to_degraded: 1",
                "  failures_to_failed: 2",
                "  successes_to_recover: 1",
                "  visible_degraded: false",
                "probe_schedule:",
                "  allow_concurrent_same_host: false",
                "  same_host_backoff_ms: 250",
                "checks_config: checks.yaml",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def test_load_build_deploy_settings_substitutes_environment_placeholders(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)

    settings = load_build_deploy_settings(
        config_path,
        env=FIXTURE_ENV,
    )

    assert settings["wifi"]["ssid"] == "TestWifi"
    assert settings["wifi"]["password"] == "TestPassword"
    assert settings["service"]["base_url"] == FIXTURE_ENV["VIVIPI_SERVICE_BASE_URL"]
    assert settings["check_state"]["failures_to_failed"] == 2
    assert settings["check_state"]["visible_degraded"] is False
    assert settings["device"]["display"]["type"] == "waveshare-pico-oled-1.3"
    assert settings["device"]["display"]["width_px"] == 128
    assert settings["device"]["display"]["height_px"] == 64
    assert settings["device"]["display"]["font"] == {"width_px": 8, "height_px": 8}
    assert settings["device"]["display"]["page_interval_s"] == 15
    assert settings["device"]["display"]["boot_logo_duration_s"] == 2
    assert settings["device"]["display"]["brightness"] == 128
    assert settings["device"]["display"]["mode"] == "standard"
    assert settings["device"]["display"]["columns"] == 1
    assert settings["device"]["display"]["column_separator"] == " "
    assert settings["device"]["display"]["failure_color"] == "red"
    assert settings["device"]["display"]["pins"]["dc"] == "GP8"
    assert settings["probe_schedule"] == {
        "allow_concurrent_hosts": False,
        "allow_concurrent_same_host": False,
        "same_host_backoff_ms": 250,
    }


def test_resolve_config_path_prefers_existing_local_override_when_requested(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text("device:\n  board: pico2w\n", encoding="utf-8")
    local_path = tmp_path / "build-deploy.local.yaml"
    local_path.write_text("device:\n  board: pico2w\n", encoding="utf-8")

    assert resolve_config_path(config_path) == config_path.resolve()
    assert resolve_config_path(config_path, prefer_local_config=True) == local_path.resolve()


def test_resolve_config_path_handles_non_yaml_and_missing_local_override(tmp_path: Path):
    text_path = tmp_path / "config.txt"
    text_path.write_text("device:\n  board: pico2w\n", encoding="utf-8")
    yaml_path = tmp_path / "build-deploy.yaml"
    yaml_path.write_text("device:\n  board: pico2w\n", encoding="utf-8")

    assert resolve_config_path(text_path, prefer_local_config=True) == text_path.resolve()
    assert resolve_config_path(yaml_path, prefer_local_config=True) == yaml_path


def test_write_runtime_config_embeds_wifi_and_checks(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)
    output_path = tmp_path / "build" / "config.json"

    write_runtime_config(
        config_path,
        output_path,
        env=FIXTURE_ENV,
    )
    rendered = json.loads(output_path.read_text(encoding="utf-8"))

    assert rendered["wifi"]["ssid"] == "TestWifi"
    assert rendered["checks"][0]["id"] == "router"
    assert rendered["check_state"]["failures_to_failed"] == 2
    assert rendered["check_state"]["visible_degraded"] is False
    assert rendered["probe_schedule"]["same_host_backoff_ms"] == 250


def test_build_firmware_bundle_creates_a_releaseable_zip_archive(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)
    archive_path = build_firmware_bundle(
        config_path,
        tmp_path / "release",
        env=FIXTURE_ENV,
        version_resolver=lambda: "0.2.1-rc0",
    )

    assert archive_path.exists()
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "boot.py" in names
    assert "__future__.py" in names
    assert "dataclasses.py" in names
    assert "enum.py" in names
    assert "config.json" in names
    assert "display.py" in names
    assert "control.py" in names
    assert "debug.py" in names
    assert "input.py" in names
    assert "main.py" in names
    assert "state.py" in names
    assert "urllib/__init__.py" in names
    assert "urllib/parse.py" in names
    assert "vivipi/__init__.py" in names
    assert not any("__pycache__/" in name for name in names)
    assert not any(name.endswith(".pyc") for name in names)
    assert archive_path == tmp_path / "release" / "vivipi-device-filesystem-0.2.1-rc0.zip"
    assert (tmp_path / "release" / "pico2w-micropython-0.2.1-rc0.txt").exists()
    assert not (tmp_path / "release" / "vivipi-firmware-bundle.zip").exists()
    assert not (tmp_path / "release" / "vivipi-device-filesystem.zip").exists()


def test_build_firmware_bundle_uses_build_time_resolver_and_replaces_existing_staging_dir(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)
    staging_dir = tmp_path / "release" / "vivipi-device-fs"
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "stale.txt").write_text("stale", encoding="utf-8")

    archive_path = build_firmware_bundle(
        config_path,
        tmp_path / "release",
        env=FIXTURE_ENV,
        version_resolver=lambda: "0.2.1-rc0",
        build_time_resolver=lambda: "2026-04-11T00:00Z",
    )
    rendered = json.loads((tmp_path / "release" / "vivipi-device-fs" / "config.json").read_text(encoding="utf-8"))

    assert archive_path.exists()
    assert not (staging_dir / "stale.txt").exists()
    assert rendered["project"]["build_time"] == "2026-04-11T00:00Z"


def test_build_service_bundle_packages_wheel_and_service_examples(tmp_path: Path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True)
    wheel_path = dist_dir / "vivipi-0.2.1rc0-py3-none-any.whl"
    wheel_path.write_text("wheel", encoding="utf-8")

    archive_path = build_service_bundle(tmp_path / "release", dist_dir, "0.2.1-rc0")

    assert archive_path == tmp_path / "release" / "vivipi-service-bundle-0.2.1-rc0.zip"
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())

    assert "vivipi-0.2.1rc0-py3-none-any.whl" in names
    assert "README-service.txt" in names
    assert "custom-service-example.py" in names
    assert "service-response-example.json" in names


def test_build_service_bundle_replaces_existing_staging_dir(tmp_path: Path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "vivipi-0.2.1rc0-py3-none-any.whl").write_text("wheel", encoding="utf-8")
    stale_dir = tmp_path / "release" / "vivipi-service-bundle"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "stale.txt").write_text("stale", encoding="utf-8")

    archive_path = build_service_bundle(tmp_path / "release", dist_dir, "0.2.1-rc0")

    assert archive_path.exists()
    assert not stale_dir.exists()


def test_resolve_release_wheel_requires_exactly_one_match(tmp_path: Path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True)

    with pytest.raises(ValueError, match="exactly one built wheel"):
        _resolve_release_wheel(dist_dir)

    (dist_dir / "vivipi-0.2.1rc0-py3-none-any.whl").write_text("wheel-a", encoding="utf-8")
    (dist_dir / "vivipi-0.2.1rc1-py3-none-any.whl").write_text("wheel-b", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one built wheel"):
        _resolve_release_wheel(dist_dir)


def test_release_version_from_wheel_validates_filename_shape(tmp_path: Path):
    with pytest.raises(ValueError, match="vivipi wheel filename"):
        _release_version_from_wheel(tmp_path / "other-0.2.1-py3-none-any.whl")

    with pytest.raises(ValueError, match="standard wheel filename"):
        _release_version_from_wheel(tmp_path / "vivipi-0.2.1.whl")


def test_copy_release_tree_skips_python_cache_files(tmp_path: Path):
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    pycache_dir = source_dir / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "main.cpython-312.pyc").write_text("cache", encoding="utf-8")

    destination_dir = tmp_path / "destination"
    _copy_release_tree(source_dir, destination_dir)

    assert (destination_dir / "main.py").exists()
    assert not (destination_dir / "__pycache__").exists()


def test_clear_generated_release_assets_removes_only_release_outputs(tmp_path: Path):
    release_dir = tmp_path / "release"
    release_dir.mkdir(parents=True)
    generated_files = [
        release_dir / "pico2w-micropython-0.2.1-rc0.txt",
        release_dir / "vivipi-device-filesystem-0.2.1-rc0.zip",
        release_dir / "vivipi-service-bundle-0.2.1-rc0.zip",
        release_dir / "vivipi-source-0.2.1-rc0.zip",
        release_dir / "vivipi-source-0.2.1-rc0.tar.gz",
        release_dir / "pico2w-micropython.txt",
        release_dir / "vivipi-device-filesystem.zip",
        release_dir / "vivipi-firmware-bundle.zip",
    ]
    for path in generated_files:
        path.write_text("generated", encoding="utf-8")
    keep_path = release_dir / "keep-me.txt"
    keep_path.write_text("keep", encoding="utf-8")

    _clear_generated_release_assets(release_dir)

    assert keep_path.exists()
    assert all(not path.exists() for path in generated_files)


def test_build_source_archives_emits_versioned_zip_and_tar(tmp_path: Path):
    commands = []

    def fake_run_command(command, check, cwd):
        commands.append((command, cwd))
        output_arg = next(arg for arg in command if arg.startswith("--output="))
        output_path = Path(output_arg.split("=", 1)[1])
        output_path.write_text("archive", encoding="utf-8")

    zip_path, tar_path = build_source_archives(tmp_path / "release", "0.2.1-rc0", run_command=fake_run_command)

    assert zip_path == tmp_path / "release" / "vivipi-source-0.2.1-rc0.zip"
    assert tar_path == tmp_path / "release" / "vivipi-source-0.2.1-rc0.tar.gz"
    assert zip_path.exists()
    assert tar_path.exists()
    assert len(commands) == 2
    assert any("--format=zip" in item for item in commands[0][0])
    assert any("--format=tar.gz" in item for item in commands[1][0])


def test_stage_release_assets_builds_versioned_release_set(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "vivipi-0.2.1rc0-py3-none-any.whl").write_text("wheel", encoding="utf-8")

    def fake_run_command(command, check, cwd):
        output_arg = next(arg for arg in command if arg.startswith("--output="))
        Path(output_arg.split("=", 1)[1]).write_text("archive", encoding="utf-8")

    outputs = stage_release_assets(
        config_path,
        tmp_path / "release",
        dist_dir,
        env=FIXTURE_ENV,
        version_resolver=lambda: "0.2.1-rc0",
        run_command=fake_run_command,
    )

    assert outputs["firmware_bundle"] == tmp_path / "release" / "vivipi-device-filesystem-0.2.1-rc0.zip"
    assert outputs["service_bundle"] == tmp_path / "release" / "vivipi-service-bundle-0.2.1-rc0.zip"
    assert outputs["source_zip"] == tmp_path / "release" / "vivipi-source-0.2.1-rc0.zip"
    assert outputs["source_tar"] == tmp_path / "release" / "vivipi-source-0.2.1-rc0.tar.gz"
    assert (tmp_path / "release" / "pico2w-micropython-0.2.1-rc0.txt").exists()


def test_stage_release_assets_falls_back_to_the_built_wheel_version_when_repo_version_diverges(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "vivipi-0.2.3.dev0+g029105f9a.d20260406-py3-none-any.whl").write_text("wheel", encoding="utf-8")

    def fake_run_command(command, check, cwd):
        output_arg = next(arg for arg in command if arg.startswith("--output="))
        Path(output_arg.split("=", 1)[1]).write_text("archive", encoding="utf-8")

    outputs = stage_release_assets(
        config_path,
        tmp_path / "release",
        dist_dir,
        env=FIXTURE_ENV,
        version_resolver=lambda: "0.2.2",
        run_command=fake_run_command,
    )

    assert outputs["firmware_bundle"] == tmp_path / "release" / "vivipi-device-filesystem-0.2.3.dev0+g029105f9a.d20260406.zip"
    assert outputs["service_bundle"] == tmp_path / "release" / "vivipi-service-bundle-0.2.3.dev0+g029105f9a.d20260406.zip"
    assert outputs["source_zip"] == tmp_path / "release" / "vivipi-source-0.2.3.dev0+g029105f9a.d20260406.zip"
    assert outputs["source_tar"] == tmp_path / "release" / "vivipi-source-0.2.3.dev0+g029105f9a.d20260406.tar.gz"
    rendered = json.loads((tmp_path / "release" / "vivipi-device-fs" / "config.json").read_text(encoding="utf-8"))
    assert rendered["project"]["version"] == "0.2.3.dev0+g029105f9a.d20260406"


def test_load_build_deploy_settings_requires_all_environment_placeholders(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)

    with pytest.raises(KeyError, match="VIVIPI_WIFI_PASSWORD"):
        load_build_deploy_settings(config_path, env={"VIVIPI_WIFI_SSID": "TestWifi", "VIVIPI_SERVICE_BASE_URL": "http://192.0.2.10:8080/checks"})


def test_load_build_deploy_settings_allows_missing_service_base_url(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)

    settings = load_build_deploy_settings(
        config_path,
        env={"VIVIPI_WIFI_SSID": "TestWifi", "VIVIPI_WIFI_PASSWORD": "TestPassword"},
    )

    assert settings["wifi"]["ssid"] == "TestWifi"
    assert settings["service"] == {}


def test_invoke_run_command_and_mpremote_recovery_cover_fallback_paths(monkeypatch):
    calls = []

    def fake_run_command(command, check, timeout=None):
        calls.append((tuple(command), check, timeout))
        if timeout is not None:
            raise TypeError("timeout unsupported")
        return "ok"

    assert _invoke_run_command(fake_run_command, ["mpremote", "fs", "ls"], check=True, timeout=5) == "ok"
    assert calls == [(("mpremote", "fs", "ls"), True, 5), (("mpremote", "fs", "ls"), True, None)]

    with pytest.raises(TypeError, match="other failure"):
        _invoke_run_command(lambda command, check, timeout=None: (_ for _ in ()).throw(TypeError("other failure")), ["cmd"], check=True, timeout=1)

    sleep_calls = []
    monkeypatch.setattr(build_deploy.time, "sleep", lambda value: sleep_calls.append(value))
    attempt_log = []

    def flaky_run_command(command, check, timeout=None):
        attempt_log.append((tuple(command), check, timeout))
        if "soft-reset" in command:
            raise subprocess.TimeoutExpired(command, timeout)
        if len([entry for entry in attempt_log if entry[1] is True]) == 1:
            raise subprocess.CalledProcessError(1, command)
        return "recovered"

    assert _run_mpremote_command(["mpremote", "fs", "ls"], run_command=flaky_run_command, recovery_port="auto", attempts=1) == "recovered"
    assert sleep_calls == [1.0]

    def always_fail(command, check, timeout=None):
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(subprocess.CalledProcessError):
        _run_mpremote_command(["mpremote", "fs", "ls"], run_command=always_fail, recovery_port="auto", attempts=0)


def test_normalize_probe_schedule_settings_validates_non_mapping(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  board: pico2w",
                "  display:",
                "    type: waveshare-pico-oled-1.3",
                "wifi:",
                "  ssid: ${VIVIPI_WIFI_SSID}",
                "  password: ${VIVIPI_WIFI_PASSWORD}",
                "probe_schedule: invalid",
                "checks_config: checks.yaml",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "checks.yaml").write_text("checks: []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="probe_schedule must be a mapping"):
        load_build_deploy_settings(config_path, env=FIXTURE_ENV)


def test_load_build_deploy_settings_validates_display_limits(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  display:",
                "    type: waveshare-pico-oled-1.3",
                "    page_interval: nope",
                "    font:",
                "      width_px: 4",
                "      height_px: 8",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="device.display.page_interval|device.display.font.width_px"):
        load_build_deploy_settings(config_path, env={})


def test_load_build_deploy_settings_supports_numeric_brightness_and_disabled_paging(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  display:",
                "    type: waveshare-pico-oled-1.3",
                "    brightness: 32",
                "    page_interval: 0s",
                "    font:",
                "      width_px: 6",
                "      height_px: 10",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_build_deploy_settings(config_path, env={})

    assert settings["device"]["display"]["brightness"] == 32
    assert settings["device"]["display"]["page_interval_s"] == 0
    assert settings["device"]["display"]["font"] == {"width_px": 6, "height_px": 10}


def test_load_build_deploy_settings_validates_display_mode_columns_and_separator(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  display:",
                "    type: waveshare-pico-oled-1.3",
                "    mode: dense",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="device.display.mode"):
        load_build_deploy_settings(config_path, env={})


def test_load_build_deploy_settings_rejects_standard_multi_column_overview(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  display:",
                "    type: waveshare-pico-oled-1.3",
                "    mode: standard",
                "    columns: 2",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="use 'compact' for multiple columns"):
        load_build_deploy_settings(config_path, env={})


def test_load_build_deploy_settings_rejects_invalid_column_count(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  display:",
                "    type: waveshare-pico-oled-1.3",
                "    mode: compact",
                "    columns: 5",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="device.display.columns"):
        load_build_deploy_settings(config_path, env={})


def test_load_build_deploy_settings_rejects_invalid_column_separator(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  display:",
                "    type: waveshare-pico-oled-1.3",
                "    mode: compact",
                "    columns: 2",
                "    column_separator: '::'",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="device.display.column_separator"):
        load_build_deploy_settings(config_path, env={})


def test_load_build_deploy_settings_defaults_overview_fields_when_omitted(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  display:",
                "    type: waveshare-pico-oled-1.3",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_build_deploy_settings(config_path, env={})

    assert settings["device"]["display"]["type"] == "waveshare-pico-oled-1.3"
    assert settings["device"]["display"]["mode"] == "standard"
    assert settings["device"]["display"]["columns"] == 1
    assert settings["device"]["display"]["column_separator"] == " "


def test_load_build_deploy_settings_infers_epaper_metadata_from_type(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  display:",
                "    type: waveshare-pico-epaper-2.13-b-v4",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_build_deploy_settings(config_path, env={})

    assert settings["device"]["display"]["family"] == "eink"
    assert settings["device"]["display"]["width_px"] == 250
    assert settings["device"]["display"]["height_px"] == 122
    assert settings["device"]["display"]["font_size"] == "medium"
    assert settings["device"]["display"]["font"] == {"width_px": 10, "height_px": 10}
    assert settings["device"]["display"]["page_interval_s"] == 180
    assert settings["device"]["display"]["pins"]["busy"] == "GP13"


def test_load_build_deploy_settings_accepts_symbolic_font_size(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  display:",
                "    type: waveshare-pico-lcd-1.3",
                "    font: extralarge",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_build_deploy_settings(config_path, env={})

    assert settings["device"]["display"]["font_size"] == "extralarge"
    assert settings["device"]["display"]["font"]["width_px"] >= 12
    assert settings["device"]["display"]["font"]["height_px"] >= 12


def test_load_build_deploy_settings_rejects_oled_geometry_override(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  display:",
                "    type: waveshare-pico-oled-1.3",
                "    width_px: 64",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="device.display.width_px"):
        load_build_deploy_settings(config_path, env={})


def test_load_build_deploy_settings_rejects_brightness_for_epaper(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  display:",
                "    type: waveshare-pico-epaper-2.13-b-v4",
                "    brightness: medium",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="brightness is not supported"):
        load_build_deploy_settings(config_path, env={})


def test_build_deploy_helper_parsers_cover_string_float_and_error_paths():
    assert _parse_duration_s(15.0, "duration") == 15
    assert _parse_font_size_px("10", "font", 8) == 10
    assert _parse_brightness("high") == 192
    assert _parse_display_mode(" Compact ") == "compact"
    assert _parse_columns("4") == 4
    assert _parse_column_separator("|") == "|"

    with pytest.raises(ValueError, match="must not be negative"):
        _parse_duration_s(-1, "duration")

    with pytest.raises(ValueError, match="integer number of pixels"):
        _parse_font_size_px("abc", "font", 8)

    with pytest.raises(ValueError, match="0-255"):
        _parse_brightness("bright")

    with pytest.raises(ValueError, match="standard' or 'compact"):
        _parse_display_mode(3)

    with pytest.raises(ValueError, match="integer from 1 to 4"):
        _parse_columns("abc")

    with pytest.raises(ValueError, match="exactly one character"):
        _parse_column_separator(1)


def test_build_deploy_helper_parsers_cover_additional_numeric_paths():
    assert _parse_font_size_px(10.0, "font", 8) == 10
    assert _parse_brightness("255") == 255
    assert _parse_brightness(32.0) == 32
    assert _parse_columns(2.0) == 2

    with pytest.raises(ValueError, match="integer number of seconds"):
        _parse_duration_s(object(), "duration")

    with pytest.raises(ValueError, match="0-255"):
        _parse_brightness(1.5)


def test_render_device_runtime_config_uses_empty_project_when_omitted():
    settings = {
        "device": {"board": "pico2w", "buttons": {"a": "GP15", "b": "GP17"}},
        "wifi": {"ssid": "wifi", "password": "secret"},
        "service": {"base_url": "http://192.0.2.10:8080/checks"},
    }
    checks = (
        CheckDefinition(
            identifier="router",
            name="Router",
            check_type=CheckType.PING,
            target="192.168.1.1",
        ),
    )

    rendered = render_device_runtime_config(settings, checks)

    assert rendered["project"] == {}
    assert rendered["checks"][0]["type"] == "PING"


def test_render_device_runtime_config_serializes_optional_auth_fields():
    settings = {
        "device": {"board": "pico2w", "buttons": {"a": "GP15", "b": "GP17"}},
        "wifi": {"ssid": "wifi", "password": "secret"},
        "service": {},
    }
    checks = (
        CheckDefinition(
            identifier="nas-ftp",
            name="NAS FTP",
            check_type=CheckType.FTP,
            target="ftp://nas.example.local",
            username="admin",
            password="secret",
        ),
        CheckDefinition(
            identifier="switch-console",
            name="Switch Console",
            check_type=CheckType.TELNET,
            target="telnet://switch.example.local",
        ),
    )

    rendered = render_device_runtime_config(settings, checks)

    assert rendered["checks"][0]["username"] == "admin"
    assert rendered["checks"][0]["password"] == "secret"
    assert rendered["checks"][1]["username"] is None
    assert rendered["checks"][1]["password"] is None


def test_render_device_runtime_config_serializes_inferred_display_column_offset():
    settings = {
        "device": {
            "board": "pico2w",
            "buttons": {"a": "GP15", "b": "GP17"},
            "display": {"type": "waveshare-pico-oled-1.3", "column_offset": 32},
        },
        "wifi": {"ssid": "wifi", "password": "secret"},
        "service": {},
    }
    checks = (
        CheckDefinition(
            identifier="router",
            name="Router",
            check_type=CheckType.PING,
            target="192.168.1.1",
        ),
    )

    rendered = render_device_runtime_config(settings, checks)

    assert rendered["device"]["display"]["column_offset"] == 32


def test_render_device_runtime_config_serializes_optional_check_state():
    settings = {
        "device": {"board": "pico2w", "buttons": {"a": "GP15", "b": "GP17"}},
        "wifi": {"ssid": "wifi", "password": "secret"},
        "service": {},
        "check_state": {
            "failures_to_degraded": 1,
            "failures_to_failed": 2,
            "successes_to_recover": 1,
            "visible_degraded": False,
        },
    }
    checks = (
        CheckDefinition(
            identifier="router",
            name="Router",
            check_type=CheckType.PING,
            target="192.168.1.1",
        ),
    )

    rendered = render_device_runtime_config(settings, checks)

    assert rendered["check_state"]["failures_to_failed"] == 2
    assert rendered["check_state"]["visible_degraded"] is False


def test_render_device_runtime_config_serializes_probe_schedule():
    settings = {
        "device": {"board": "pico2w", "buttons": {"a": "GP15", "b": "GP17"}},
        "wifi": {"ssid": "wifi", "password": "secret"},
        "service": {},
        "probe_schedule": {
            "allow_concurrent_same_host": False,
            "same_host_backoff_ms": 250,
        },
    }
    checks = (
        CheckDefinition(
            identifier="router",
            name="Router",
            check_type=CheckType.PING,
            target="192.168.1.1",
        ),
    )

    rendered = render_device_runtime_config(settings, checks)

    assert rendered["probe_schedule"] == {
        "allow_concurrent_same_host": False,
        "same_host_backoff_ms": 250,
    }


def test_load_runtime_checks_skips_unconfigured_service_checks(tmp_path: Path):
    checks_path = tmp_path / "checks.yaml"
    checks_path.write_text(
        """
checks:
  - name: Router
    type: ping
    target: 192.168.1.1
  - name: NAS API
    type: http
    target: https://nas.example.local/health
  - name: Android Devices
    type: service
    target: ${VIVIPI_SERVICE_BASE_URL}
    prefix: adb
""".strip(),
        encoding="utf-8",
    )

    definitions = load_runtime_checks(
        checks_path,
        env={"VIVIPI_WIFI_SSID": "TestWifi", "VIVIPI_WIFI_PASSWORD": "TestPassword"},
    )

    assert [definition.identifier for definition in definitions] == ["router", "nas-api"]
    assert all(definition.check_type != CheckType.SERVICE for definition in definitions)


def test_load_runtime_checks_treats_missing_auth_placeholders_as_optional(tmp_path: Path):
    checks_path = tmp_path / "checks.yaml"
    checks_path.write_text(
        """
checks:
  - name: C64U FTP
    type: ftp
    target: 192.168.1.167
    username: ${VIVIPI_NETWORK_USERNAME}
    password: ${VIVIPI_NETWORK_PASSWORD}
  - name: U64 TELNET
    type: telnet
    target: 192.168.1.13:23
    password: ${VIVIPI_NETWORK_PASSWORD}
""".strip(),
        encoding="utf-8",
    )

    definitions = load_runtime_checks(checks_path, env={})

    assert [definition.name for definition in definitions] == ["C64U FTP", "U64 TELNET"]
    assert definitions[0].username is None
    assert definitions[0].password is None
    assert definitions[1].password is None


def test_load_runtime_checks_rejects_non_list_roots_and_preserves_invalid_items_for_parser(tmp_path: Path):
    checks_path = tmp_path / "checks.yaml"
    checks_path.write_text("checks: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checks must be a list"):
        load_runtime_checks(checks_path)

    checks_path.write_text("checks:\n  - invalid\n", encoding="utf-8")

    with pytest.raises(ValueError, match="each check must be a mapping"):
        load_runtime_checks(checks_path)


def test_write_runtime_config_excludes_service_checks_without_service_url(tmp_path: Path):
    checks_path = tmp_path / "checks.yaml"
    checks_path.write_text(
        """
checks:
  - name: Router
    type: ping
    target: 192.168.1.1
  - name: NAS API
    type: http
    target: https://nas.example.local/health
  - name: Android Devices
    type: service
    target: ${VIVIPI_SERVICE_BASE_URL}
    prefix: adb
""".strip(),
        encoding="utf-8",
    )

    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "\n".join(
            [
                "device:",
                "  board: pico2w",
                "wifi:",
                "  ssid: ${VIVIPI_WIFI_SSID}",
                "  password: ${VIVIPI_WIFI_PASSWORD}",
                "service:",
                "  base_url: ${VIVIPI_SERVICE_BASE_URL}",
                "checks_config: checks.yaml",
            ]
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "config.json"

    write_runtime_config(
        config_path,
        output_path,
        env={"VIVIPI_WIFI_SSID": "TestWifi", "VIVIPI_WIFI_PASSWORD": "TestPassword"},
    )

    rendered = json.loads(output_path.read_text(encoding="utf-8"))

    assert [check["id"] for check in rendered["checks"]] == ["router", "nas-api"]
    assert rendered["service"] == {}


def test_write_runtime_config_requires_checks_config_key(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        """
device:
  board: pico2w
wifi:
  ssid: ${VIVIPI_WIFI_SSID}
  password: ${VIVIPI_WIFI_PASSWORD}
service:
    base_url: ${VIVIPI_SERVICE_BASE_URL}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="checks_config"):
        write_runtime_config(
            config_path,
            tmp_path / "config.json",
                        env=FIXTURE_ENV,
        )


def test_validate_runtime_settings_rejects_loopback_service_urls():
    settings = {
        "device": {"board": "pico2w"},
        "wifi": {"ssid": "wifi", "password": "secret"},
        "service": {"base_url": "http://127.0.0.1:8080/checks"},
    }
    checks = ()

    with pytest.raises(ValueError, match="reachable from the Pico"):
        validate_runtime_settings(settings, checks)


def test_validate_runtime_settings_rejects_service_check_targets_on_loopback():
    settings = {
        "device": {"board": "pico2w"},
        "wifi": {"ssid": "wifi", "password": "secret"},
        "service": {"base_url": "http://192.0.2.10:8080/checks"},
    }
    checks = (
        CheckDefinition(
            identifier="adb",
            name="Android Devices",
            check_type=CheckType.SERVICE,
            target="http://localhost:8080/checks",
        ),
    )

    with pytest.raises(ValueError, match="reachable from the Pico"):
        validate_runtime_settings(settings, checks)


def test_validate_runtime_settings_rejects_non_http_urls_and_detects_loopback_hosts():
    settings = {
        "device": {"board": "pico2w"},
        "wifi": {"ssid": "wifi", "password": "secret"},
        "service": {"base_url": "ftp://192.0.2.10/checks"},
    }

    with pytest.raises(ValueError, match="absolute http or https URL"):
        validate_runtime_settings(settings, ())

    assert _is_loopback_host("127.0.0.1") is True
    assert _is_loopback_host("localhost") is True
    assert _is_loopback_host("example.invalid") is False


def test_resolve_checks_path_requires_checks_config_setting(tmp_path: Path):
    with pytest.raises(ValueError, match="checks_config"):
        _resolve_checks_path(tmp_path / "build-deploy.yaml", {})


def test_build_deploy_main_dispatches_render_config(monkeypatch, tmp_path: Path):
    output_path = tmp_path / "config.json"
    called = {}

    def fake_write_runtime_config(config_path, destination_path):
        called["config"] = config_path
        called["output"] = destination_path

    monkeypatch.setattr(build_deploy, "write_runtime_config", fake_write_runtime_config)

    exit_code = build_deploy.main(["render-config", "--config", "config.yaml", "--output", str(output_path)])

    assert exit_code == 0
    assert called == {"config": "config.yaml", "output": str(output_path)}


def test_build_deploy_main_prefers_local_config_for_render_config(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text("device:\n  board: pico2w\n", encoding="utf-8")
    local_path = tmp_path / "build-deploy.local.yaml"
    local_path.write_text("device:\n  board: pico2w\n", encoding="utf-8")
    output_path = tmp_path / "config.json"
    called = {}

    def fake_write_runtime_config(config_arg, destination_path):
        called["config"] = config_arg
        called["output"] = destination_path

    monkeypatch.setattr(build_deploy, "write_runtime_config", fake_write_runtime_config)

    exit_code = build_deploy.main(
        [
            "render-config",
            "--config",
            str(config_path),
            "--output",
            str(output_path),
            "--prefer-local-config",
        ]
    )

    assert exit_code == 0
    assert called == {"config": str(local_path.resolve()), "output": str(output_path)}


def test_build_deploy_main_dispatches_build_firmware(monkeypatch, tmp_path: Path):
    called = {}

    def fake_build_firmware(config_path, output_dir):
        called["config"] = config_path
        called["output_dir"] = output_dir
        return tmp_path / "bundle.zip"

    monkeypatch.setattr(build_deploy, "build_firmware_bundle", fake_build_firmware)

    exit_code = build_deploy.main(["build-firmware", "--config", "config.yaml", "--output-dir", "release-dir"])

    assert exit_code == 0
    assert called == {"config": "config.yaml", "output_dir": "release-dir"}


def test_build_deploy_main_dispatches_stage_release_assets(monkeypatch):
    called = {}

    def fake_stage_release_assets(config_path, output_dir, dist_dir):
        called["config"] = config_path
        called["output_dir"] = output_dir
        called["dist_dir"] = dist_dir
        return {}

    monkeypatch.setattr(build_deploy, "stage_release_assets", fake_stage_release_assets)

    exit_code = build_deploy.main(["stage-release-assets", "--config", "config.yaml", "--output-dir", "release-dir", "--dist-dir", "dist-dir"])

    assert exit_code == 0
    assert called == {"config": "config.yaml", "output_dir": "release-dir", "dist_dir": "dist-dir"}


def test_build_deploy_main_dispatches_deploy_firmware(monkeypatch):
    called = {}

    def fake_deploy_firmware(config_path, output_dir, port=None):
        called["config"] = config_path
        called["output_dir"] = output_dir
        called["port"] = port
        return Path("bundle.zip")

    monkeypatch.setattr(build_deploy, "deploy_firmware", fake_deploy_firmware)

    exit_code = build_deploy.main(["deploy-firmware", "--config", "config.yaml", "--output-dir", "release-dir", "--port", "/dev/ttyACM0"])

    assert exit_code == 0
    assert called == {"config": "config.yaml", "output_dir": "release-dir", "port": "/dev/ttyACM0"}


def test_write_install_manifest_records_supported_install_metadata(tmp_path: Path):
    output_path = tmp_path / "pico2w-micropython.txt"

    write_install_manifest(
        {
            "device": {
                "board": "pico2w",
                "micropython_port": "/dev/ttyACM0",
                "micropython": {
                    "version": "1.25.0",
                    "download_page": "https://micropython.org/download/RPI_PICO2_W/",
                },
            }
        },
        output_path,
    )

    content = output_path.read_text(encoding="utf-8")

    assert "micropython_version: 1.25.0" in content
    assert "download_page: https://micropython.org/download/RPI_PICO2_W/" in content


def test_write_install_manifest_defaults_port_to_auto(tmp_path: Path):
    output_path = tmp_path / "pico2w-micropython.txt"

    write_install_manifest(
        {
            "device": {
                "board": "pico2w",
                "micropython": {
                    "version": "1.25.0",
                    "download_page": "https://micropython.org/download/RPI_PICO2_W/",
                },
            }
        },
        output_path,
    )

    content = output_path.read_text(encoding="utf-8")

    assert "port: auto" in content


def test_deploy_firmware_defaults_to_auto_port_when_not_configured(tmp_path: Path, monkeypatch):
    config_path = write_fixture_files(tmp_path)
    commands = []

    monkeypatch.setattr(build_deploy, "_wrap_with_dialout", lambda command: command)

    def fake_build_firmware_bundle(*args, **kwargs):
        device_root = tmp_path / "release-no-port" / "vivipi-device-fs"
        device_root.mkdir(parents=True, exist_ok=True)
        (device_root / "main.py").write_text("print('hi')\n", encoding="utf-8")
        return tmp_path / "release-no-port" / "bundle.zip"

    monkeypatch.setattr(build_deploy, "build_firmware_bundle", fake_build_firmware_bundle)
    monkeypatch.setattr(
        build_deploy,
        "load_build_deploy_settings",
        lambda *args, **kwargs: {"device": {"board": "pico2w"}, "wifi": {"ssid": "wifi", "password": "secret"}, "service": {}},
    )

    deploy_firmware(
        config_path,
        tmp_path / "release-no-port",
        port="",
        run_command=lambda command, check: commands.append(command),
    )

    assert commands[0][:4] == ["mpremote", "connect", "auto", "fs"]
    assert commands[-1] == ["mpremote", "connect", "auto", "reset"]


def test_deploy_firmware_copies_files_via_mpremote(tmp_path: Path, monkeypatch):
    config_path = write_fixture_files(tmp_path)
    commands = []

    monkeypatch.setattr(build_deploy, "_wrap_with_dialout", lambda command: command)

    deploy_firmware(
        config_path,
        tmp_path / "release",
        env=FIXTURE_ENV,
        port="/dev/ttyUSB0",
        run_command=lambda command, check: commands.append(command),
    )

    assert any(command[:4] == ["mpremote", "connect", "/dev/ttyUSB0", "fs"] for command in commands)
    assert any(command[-1] == ":boot.py" for command in commands)
    assert any(command[-1] == ":config.json" for command in commands)
    assert commands[-1] == ["mpremote", "connect", "/dev/ttyUSB0", "reset"]


def test_build_firmware_bundle_staged_entrypoint_imports_on_flattened_filesystem(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)

    build_firmware_bundle(config_path, tmp_path / "release", env=FIXTURE_ENV, version_resolver=lambda: "0.0.0-test")

    staging_dir = tmp_path / "release" / "vivipi-device-fs"
    sys.path.insert(0, str(staging_dir))
    try:
        spec = importlib.util.spec_from_file_location("staged_main", staging_dir / "main.py")
        module = importlib.util.module_from_spec(spec)
        assert spec is not None
        assert spec.loader is not None
        spec.loader.exec_module(module)
        assert callable(module.main)
    finally:
        sys.path.remove(str(staging_dir))
        for module_name in ("runtime", "display", "input", "displays", "vivipi"):
            sys.modules.pop(module_name, None)


def test_deploy_firmware_uses_sg_dialout_when_process_lacks_group_membership(tmp_path: Path, monkeypatch):
    config_path = write_fixture_files(tmp_path)
    commands = []

    monkeypatch.setattr(build_deploy.os, "name", "posix")
    monkeypatch.setattr(build_deploy.os, "getgroups", lambda: [1000])
    monkeypatch.setattr(build_deploy.os, "getuid", lambda: 1000)
    monkeypatch.setattr(build_deploy.grp, "getgrnam", lambda name: type("Group", (), {"gr_gid": 20, "gr_mem": ["chris"]})())
    monkeypatch.setattr(build_deploy.pwd, "getpwuid", lambda uid: type("User", (), {"pw_name": "chris"})())

    deploy_firmware(
        config_path,
        tmp_path / "release",
        env=FIXTURE_ENV,
        port="/dev/ttyUSB0",
        run_command=lambda command, check: commands.append(command),
    )

    assert all(command[:3] == ["sg", "dialout", "-c"] for command in commands)
    assert "exec mpremote connect /dev/ttyUSB0 fs cp" in commands[0][3]
    assert commands[-1] == ["sg", "dialout", "-c", "exec mpremote connect /dev/ttyUSB0 reset"]


def test_deploy_firmware_retries_mpremote_after_timeout(tmp_path: Path, monkeypatch):
    config_path = write_fixture_files(tmp_path)
    commands = []
    attempts = {"fs": 0}

    monkeypatch.setattr(build_deploy, "_wrap_with_dialout", lambda command: command)

    def fake_run(command, check, timeout=None):
        commands.append((command, check, timeout))
        if command[:4] == ["mpremote", "connect", "/dev/ttyUSB0", "fs"]:
            attempts["fs"] += 1
            if attempts["fs"] == 1:
                raise build_deploy.subprocess.TimeoutExpired(command, timeout or 0)

    deploy_firmware(
        config_path,
        tmp_path / "release",
        env=FIXTURE_ENV,
        port="/dev/ttyUSB0",
        run_command=fake_run,
    )

    fs_commands = [command for command, _, _ in commands if command[:4] == ["mpremote", "connect", "/dev/ttyUSB0", "fs"]]
    soft_resets = [command for command, _, _ in commands if command == ["mpremote", "connect", "/dev/ttyUSB0", "soft-reset"]]
    final_resets = [command for command, _, _ in commands if command == ["mpremote", "connect", "/dev/ttyUSB0", "reset"]]
    assert len(fs_commands) >= 2
    assert len(soft_resets) == 1
    assert len(final_resets) == 1


def test_deploy_firmware_reports_missing_mpremote(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)

    with pytest.raises(RuntimeError, match="mpremote"):
        deploy_firmware(
            config_path,
            tmp_path / "release",
            env=FIXTURE_ENV,
            port="/dev/ttyUSB0",
            run_command=lambda command, check: (_ for _ in ()).throw(FileNotFoundError("mpremote")),
        )


def test_wrap_with_dialout_returns_original_command_for_non_posix_and_non_membership(monkeypatch):
    command = ["mpremote", "connect", "auto"]

    monkeypatch.setattr(build_deploy.os, "name", "nt")
    assert _wrap_with_dialout(command) == command

    monkeypatch.setattr(build_deploy.os, "name", "posix")
    monkeypatch.setattr(build_deploy.grp, "getgrnam", lambda name: (_ for _ in ()).throw(KeyError("dialout")))
    assert _wrap_with_dialout(command) == command

    monkeypatch.setattr(build_deploy.grp, "getgrnam", lambda name: type("Group", (), {"gr_gid": 20, "gr_mem": ["chris"]})())
    monkeypatch.setattr(build_deploy.os, "getgroups", lambda: [20])
    assert _wrap_with_dialout(command) == command

    monkeypatch.setattr(build_deploy.os, "getgroups", lambda: [1000])
    monkeypatch.setattr(build_deploy.os, "getuid", lambda: 1000)
    monkeypatch.setattr(build_deploy.pwd, "getpwuid", lambda uid: (_ for _ in ()).throw(KeyError("uid")))
    assert _wrap_with_dialout(command) == command

    monkeypatch.setattr(build_deploy.pwd, "getpwuid", lambda uid: type("User", (), {"pw_name": "alex"})())
    assert _wrap_with_dialout(command) == command


def test_load_build_deploy_settings_handles_missing_device_and_validates_probe_schedule(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text("probe_schedule:\n  allow_concurrent_same_host: on\n  same_host_backoff_ms: 0\n", encoding="utf-8")

    settings = load_build_deploy_settings(config_path, env={})

    assert settings["probe_schedule"] == {
        "allow_concurrent_hosts": False,
        "allow_concurrent_same_host": True,
        "same_host_backoff_ms": 0,
    }

    config_path.write_text("device: []\nprobe_schedule: []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="probe_schedule must be a mapping"):
        load_build_deploy_settings(config_path, env={})

    config_path.write_text(
        "probe_schedule:\n  allow_concurrent_same_host: maybe\n  same_host_backoff_ms: -1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="probe_schedule.same_host_backoff_ms|must be a boolean"):
        load_build_deploy_settings(config_path, env={})


def test_load_build_deploy_settings_parses_false_probe_schedule_values(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "probe_schedule:\n  allow_concurrent_same_host: off\n  same_host_backoff_ms: 25\n",
        encoding="utf-8",
    )

    settings = load_build_deploy_settings(config_path, env={})

    assert settings["probe_schedule"] == {
        "allow_concurrent_hosts": False,
        "allow_concurrent_same_host": False,
        "same_host_backoff_ms": 25,
    }


def test_build_deploy_main_rejects_monkeypatched_unknown_command(monkeypatch):
    monkeypatch.setattr(
        build_deploy.argparse.ArgumentParser,
        "parse_args",
        lambda self, argv=None: type("Args", (), {"command": "unknown"})(),
    )

    with pytest.raises(ValueError, match="unsupported command: unknown"):
        build_deploy.main(["unknown"])


def test_parse_bool_covers_default_and_string_branches():
    assert build_deploy._parse_bool(None, "flag", True) is True
    assert build_deploy._parse_bool(" yes ", "flag", False) is True
    assert build_deploy._parse_bool(" off ", "flag", True) is False

    with pytest.raises(ValueError, match="flag must be a boolean"):
        build_deploy._parse_bool("maybe", "flag", False)


def test_load_build_deploy_settings_reports_check_state_integer_context(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "check_state:\n  failures_to_failed: nope\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="check_state.failures_to_failed must be an integer"):
        load_build_deploy_settings(config_path, env={})


def test_build_deploy_module_entrypoint_executes_main(monkeypatch):
    monkeypatch.setattr(
        build_deploy.argparse.ArgumentParser,
        "parse_args",
        lambda self, argv=None: type("Args", (), {"command": "unknown"})(),
    )

    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        with pytest.raises(ValueError, match="unsupported command: unknown"):
            runpy.run_module("vivipi.tooling.build_deploy", run_name="__main__")
