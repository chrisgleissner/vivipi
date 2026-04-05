import json
import zipfile
from pathlib import Path

import pytest

from vivipi.core.models import CheckDefinition, CheckType
from vivipi.tooling import build_deploy
from vivipi.tooling.build_deploy import (
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


def test_load_runtime_checks_skips_unconfigured_service_checks(tmp_path: Path):
    checks_path = tmp_path / "checks.yaml"
    checks_path.write_text(
        """
checks:
  - name: Router
    type: ping
    target: 192.168.1.1
  - name: NAS API
    type: rest
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


def test_write_runtime_config_excludes_service_checks_without_service_url(tmp_path: Path):
    checks_path = tmp_path / "checks.yaml"
    checks_path.write_text(
        """
checks:
  - name: Router
    type: ping
    target: 192.168.1.1
  - name: NAS API
    type: rest
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


def test_build_deploy_main_dispatches_deploy_firmware(monkeypatch):
    called = {}

    def fake_deploy_firmware(config_path, output_dir, port=None):
        called["config"] = config_path
        called["output_dir"] = output_dir
        called["port"] = port

    monkeypatch.setattr(build_deploy, "deploy_firmware", fake_deploy_firmware)

    exit_code = build_deploy.main(
        ["deploy-firmware", "--config", "config.yaml", "--output-dir", "release-dir", "--port", "/dev/ttyUSB0"]
    )

    assert exit_code == 0
    assert called == {"config": "config.yaml", "output_dir": "release-dir", "port": "/dev/ttyUSB0"}
