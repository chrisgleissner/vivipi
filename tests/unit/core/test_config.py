import builtins
import importlib.util
import sys

from pathlib import Path

import pytest

from vivipi.core.config import (
    build_direct_check_id,
    build_service_check_id,
    load_checks_config,
    parse_checks_config,
    parse_probe_schedule_config,
    slugify,
)
from vivipi.core.models import CheckType


def test_load_checks_config_reads_yaml_definitions(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text(
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

    definitions = load_checks_config(config_path)

    assert len(definitions) == 1
    assert definitions[0].identifier == "router"
    assert definitions[0].check_type == CheckType.PING


def test_load_checks_config_supports_service_checks(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text(
        """
checks:
  - name: Android Devices
    type: service
    target: http://127.0.0.1:8080/checks
    prefix: adb
    interval_s: 15
    timeout_s: 10
""".strip(),
        encoding="utf-8",
    )

    definitions = load_checks_config(config_path)

    assert definitions[0].check_type == CheckType.SERVICE
    assert definitions[0].service_prefix == "adb"


def test_load_checks_config_rejects_timeout_too_close_to_interval(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text(
        """
checks:
  - name: Router
    type: ping
    target: 192.168.1.1
    interval_s: 15
    timeout_s: 13
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="20% smaller"):
        load_checks_config(config_path)


def test_check_ids_are_stable_for_direct_and_service_checks():
    assert build_direct_check_id("NAS API") == "nas-api"
    assert build_service_check_id("adb", "Pixel 8 Pro") == "adb:pixel-8-pro"
    assert build_service_check_id(None, "Pixel 8 Pro") == "pixel-8-pro"


def test_slugify_falls_back_to_a_default_identifier_for_empty_text():
    assert slugify("!!!") == "check"


def test_load_checks_config_rejects_non_list_checks(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text("checks: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checks must be a list"):
        load_checks_config(config_path)


def test_load_checks_config_rejects_duplicate_ids(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text(
        """
checks:
  - name: NAS API
    type: ping
    target: 192.168.1.2
  - name: NAS-API
    type: ping
    target: 192.168.1.3
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate check id"):
        load_checks_config(config_path)


def test_load_checks_config_rejects_non_mapping_items(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text("checks:\n  - not-a-mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a mapping"):
        load_checks_config(config_path)


def test_load_checks_config_rejects_missing_required_strings(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text(
        """
checks:
  - name: ""
    type: ping
    target: 192.168.1.1
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="name must be a non-empty string"):
        load_checks_config(config_path)


def test_load_checks_config_uses_defaults_and_normalizes_blank_prefixes(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text(
        """
checks:
  - name: Status API
    type: http
    target: https://example.invalid/health
    method: post
    prefix: "   "
""".strip(),
        encoding="utf-8",
    )

    definitions = load_checks_config(config_path)

    assert definitions[0].interval_s == 15
    assert definitions[0].timeout_s == 10
    assert definitions[0].method == "POST"
    assert definitions[0].service_prefix is None


def test_load_checks_config_accepts_legacy_rest_alias_and_normalizes_to_http(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text(
        """
checks:
  - name: Legacy API
    type: rest
    target: https://example.invalid/health
""".strip(),
        encoding="utf-8",
    )

    definitions = load_checks_config(config_path)

    assert definitions[0].check_type == CheckType.HTTP


def test_load_checks_config_supports_ftp_and_telnet_credentials(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text(
        """
checks:
  - name: NAS FTP
    type: ftp
    target: ftp://nas.example.local
    username: admin
    password: secret
  - name: Switch Console
    type: telnet
    target: telnet://switch.example.local:2323
    username: ops
    password: "   "
""".strip(),
        encoding="utf-8",
    )

    definitions = load_checks_config(config_path)

    assert definitions[0].check_type == CheckType.FTP
    assert definitions[0].username == "admin"
    assert definitions[0].password == "secret"
    assert definitions[1].check_type == CheckType.TELNET
    assert definitions[1].username == "ops"
    assert definitions[1].password is None


def test_load_checks_config_treats_missing_auth_placeholders_as_optional(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text(
        """
checks:
  - name: NAS FTP
    type: ftp
    target: ftp://nas.example.local
    username: ${VIVIPI_NETWORK_USERNAME}
    password: ${VIVIPI_NETWORK_PASSWORD}
  - name: Switch Console
    type: telnet
    target: telnet://switch.example.local:23
    username: admin
    password: ${VIVIPI_NETWORK_PASSWORD}
""".strip(),
        encoding="utf-8",
    )

    definitions = load_checks_config(config_path, env={})

    assert definitions[0].username is None
    assert definitions[0].password is None
    assert definitions[1].username == "admin"
    assert definitions[1].password is None


def test_load_checks_config_substitutes_environment_placeholders(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text(
        """
checks:
  - name: Android Devices
    type: service
    target: ${VIVIPI_SERVICE_BASE_URL}
    prefix: adb
""".strip(),
        encoding="utf-8",
    )

    definitions = load_checks_config(
        config_path,
        env={"VIVIPI_SERVICE_BASE_URL": "http://192.0.2.10:8080/checks"},
    )

    assert definitions[0].target == "http://192.0.2.10:8080/checks"


def test_parse_checks_config_rejects_non_mapping_root():
    with pytest.raises(ValueError, match="mapping"):
        parse_checks_config([])


def test_parse_checks_config_rejects_non_string_auth_fields():
    with pytest.raises(ValueError, match="username must be a string"):
        parse_checks_config(
            {
                "checks": [
                    {
                        "name": "NAS FTP",
                        "type": "ftp",
                        "target": "ftp://nas.example.local",
                        "username": 123,
                    }
                ]
            }
        )


def test_parse_probe_schedule_config_defaults_and_validates_boolean_strings():
    defaults = parse_probe_schedule_config(None)
    assert defaults.allow_concurrent_same_host is False
    assert defaults.same_host_backoff_ms == 250

    custom = parse_probe_schedule_config(
        {
            "allow_concurrent_same_host": "true",
            "same_host_backoff_ms": 750,
        }
    )
    assert custom.allow_concurrent_same_host is True
    assert custom.same_host_backoff_ms == 750

    with pytest.raises(ValueError, match="password must be a string"):
        parse_checks_config(
            {
                "checks": [
                    {
                        "name": "Switch Console",
                        "type": "telnet",
                        "target": "telnet://switch.example.local",
                        "password": False,
                    }
                ]
            }
        )


def test_load_checks_config_requires_present_placeholders_and_positive_timings(tmp_path: Path):
    config_path = tmp_path / "checks.yaml"
    config_path.write_text(
        """
checks:
  - name: NAS API
    type: http
    target: ${VIVIPI_SERVICE_BASE_URL}
    interval_s: 0
    timeout_s: 0
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(KeyError, match="VIVIPI_SERVICE_BASE_URL"):
        load_checks_config(config_path, env={"OTHER": "value"})

    with pytest.raises(ValueError, match="interval_s must be positive"):
        load_checks_config(config_path, env={"VIVIPI_SERVICE_BASE_URL": "https://example.invalid/health"})

    config_path.write_text(
        """
checks:
  - name: NAS API
    type: http
    target: https://example.invalid/health
    interval_s: 15
    timeout_s: 0
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="timeout_s must be positive"):
        load_checks_config(config_path, env={"OTHER": "value"})


def test_config_module_import_does_not_require_pathlib_or_yaml(monkeypatch):
    config_path = Path(__file__).resolve().parents[3] / "src" / "vivipi" / "core" / "config.py"
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"pathlib", "yaml"}:
            raise ImportError(f"no module named '{name}'", name=name)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    spec = importlib.util.spec_from_file_location("test_config_micropython", config_path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("test_config_micropython", None)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("test_config_micropython", None)

    defaults = module.parse_probe_schedule_config(None)
    assert defaults.allow_concurrent_same_host is False
    assert defaults.same_host_backoff_ms == 250
