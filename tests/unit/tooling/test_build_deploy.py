import json
import zipfile
from pathlib import Path

import pytest

from vivipi.core.models import CheckDefinition, CheckType
from vivipi.tooling import build_deploy
from vivipi.tooling.build_deploy import (
    build_firmware_bundle,
    load_build_deploy_settings,
    render_device_runtime_config,
    write_runtime_config,
)


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
        """
project:
  name: vivipi
device:
  board: pico2w
wifi:
  ssid: ${VIVIPI_WIFI_SSID}
  password: ${VIVIPI_WIFI_PASSWORD}
service:
  base_url: http://127.0.0.1:8080/checks
checks_config: checks.yaml
""".strip(),
        encoding="utf-8",
    )
    return config_path


def test_load_build_deploy_settings_substitutes_environment_placeholders(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)

    settings = load_build_deploy_settings(
        config_path,
        env={"VIVIPI_WIFI_SSID": "TestWifi", "VIVIPI_WIFI_PASSWORD": "TestPassword"},
    )

    assert settings["wifi"]["ssid"] == "TestWifi"
    assert settings["wifi"]["password"] == "TestPassword"


def test_write_runtime_config_embeds_wifi_and_checks(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)
    output_path = tmp_path / "build" / "config.json"

    write_runtime_config(
        config_path,
        output_path,
        env={"VIVIPI_WIFI_SSID": "TestWifi", "VIVIPI_WIFI_PASSWORD": "TestPassword"},
    )
    rendered = json.loads(output_path.read_text(encoding="utf-8"))

    assert rendered["wifi"]["ssid"] == "TestWifi"
    assert rendered["checks"][0]["id"] == "router"


def test_build_firmware_bundle_creates_a_releaseable_zip_archive(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)
    archive_path = build_firmware_bundle(
        config_path,
        tmp_path / "release",
        env={"VIVIPI_WIFI_SSID": "TestWifi", "VIVIPI_WIFI_PASSWORD": "TestPassword"},
    )

    assert archive_path.exists()
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "boot.py" in names
    assert "config.json" in names
    assert "main.py" in names
    assert "vivipi/__init__.py" in names


def test_load_build_deploy_settings_requires_all_environment_placeholders(tmp_path: Path):
    config_path = write_fixture_files(tmp_path)

    with pytest.raises(KeyError, match="VIVIPI_WIFI_PASSWORD"):
        load_build_deploy_settings(config_path, env={"VIVIPI_WIFI_SSID": "TestWifi"})


def test_render_device_runtime_config_uses_empty_project_when_omitted():
    settings = {
        "device": {"board": "pico2w"},
        "wifi": {"ssid": "wifi", "password": "secret"},
        "service": {"base_url": "http://127.0.0.1:8080/checks"},
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
  base_url: http://127.0.0.1:8080/checks
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="checks_config"):
        write_runtime_config(
            config_path,
            tmp_path / "config.json",
            env={"VIVIPI_WIFI_SSID": "TestWifi", "VIVIPI_WIFI_PASSWORD": "TestPassword"},
        )


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
