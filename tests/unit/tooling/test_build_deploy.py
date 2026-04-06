import json
import zipfile
from pathlib import Path

import pytest

from vivipi.core.models import CheckDefinition, CheckType
from vivipi.tooling import build_deploy
from vivipi.tooling.build_deploy import (
    _is_loopback_host,
    _parse_brightness,
    _parse_column_separator,
    _parse_columns,
    _parse_display_mode,
    _parse_duration_s,
    _parse_font_size_px,
    _resolve_checks_path,
    build_firmware_bundle,
    deploy_firmware,
    load_build_deploy_settings,
    load_runtime_checks,
    render_device_runtime_config,
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
                "    a: GP14",
                "    b: GP15",
                "  display:",
                "    type: waveshare-pico-oled-1.3",
                "    mode: standard",
                "    columns: 1",
                "    column_separator: ' '",
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
    assert settings["device"]["display"]["type"] == "waveshare-pico-oled-1.3"
    assert settings["device"]["display"]["width_px"] == 128
    assert settings["device"]["display"]["height_px"] == 64
    assert settings["device"]["display"]["font"] == {"width_px": 8, "height_px": 8}
    assert settings["device"]["display"]["page_interval_s"] == 15
    assert settings["device"]["display"]["brightness"] == 128
    assert settings["device"]["display"]["mode"] == "standard"
    assert settings["device"]["display"]["columns"] == 1
    assert settings["device"]["display"]["column_separator"] == " "
    assert settings["device"]["display"]["failure_color"] == "red"
    assert settings["device"]["display"]["pins"]["dc"] == "GP8"


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


def test_build_firmware_bundle_creates_a_releaseable_zip_archive(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)
    archive_path = build_firmware_bundle(
        config_path,
        tmp_path / "release",
        env=FIXTURE_ENV,
    )

    assert archive_path.exists()
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "boot.py" in names
    assert "config.json" in names
    assert "display.py" in names
    assert "input.py" in names
    assert "main.py" in names
    assert "vivipi/__init__.py" in names
    assert (tmp_path / "release" / "vivipi-device-filesystem.zip").exists()
    assert (tmp_path / "release" / "pico2w-micropython.txt").exists()


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
        "device": {"board": "pico2w", "buttons": {"a": "GP14", "b": "GP15"}},
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
        "device": {"board": "pico2w", "buttons": {"a": "GP14", "b": "GP15"}},
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


def test_deploy_firmware_requires_port_and_reports_missing_mpremote(tmp_path: Path, monkeypatch):
    config_path = write_fixture_files(tmp_path)

    def fake_build_firmware_bundle_no_port(*args, **kwargs):
        device_root = tmp_path / "release-no-port" / "vivipi-device-fs"
        device_root.mkdir(parents=True, exist_ok=True)
        return tmp_path / "release-no-port" / "bundle.zip"

    monkeypatch.setattr(build_deploy, "build_firmware_bundle", fake_build_firmware_bundle_no_port)
    monkeypatch.setattr(
        build_deploy,
        "load_build_deploy_settings",
        lambda *args, **kwargs: {"device": {"board": "pico2w"}, "wifi": {"ssid": "wifi", "password": "secret"}, "service": {}},
    )

    with pytest.raises(ValueError, match="micropython_port"):
        deploy_firmware(
            config_path,
            tmp_path / "release-no-port",
            port="",
            run_command=lambda *args, **kwargs: None,
        )

    def fake_load_build_deploy_settings(*args, **kwargs):
        return {
            "device": {"board": "pico2w", "micropython_port": "/dev/ttyACM0"},
            "wifi": {"ssid": "wifi", "password": "secret"},
            "service": {},
        }

    def fake_build_firmware_bundle(*args, **kwargs):
        device_root = tmp_path / "release-missing" / "vivipi-device-fs"
        device_root.mkdir(parents=True, exist_ok=True)
        (device_root / "main.py").write_text("print('hi')\n", encoding="utf-8")
        return tmp_path / "release-missing" / "bundle.zip"

    monkeypatch.setattr(build_deploy, "load_build_deploy_settings", fake_load_build_deploy_settings)
    monkeypatch.setattr(build_deploy, "build_firmware_bundle", fake_build_firmware_bundle)

    def fake_run_command(command, check):
        raise FileNotFoundError("mpremote")

    with pytest.raises(RuntimeError, match="mpremote is required"):
        deploy_firmware(config_path, tmp_path / "release-missing", run_command=fake_run_command)


def test_deploy_firmware_copies_files_via_mpremote(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)
    commands = []

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
