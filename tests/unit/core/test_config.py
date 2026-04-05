from pathlib import Path

import pytest

from vivipi.core.config import build_direct_check_id, build_service_check_id, load_checks_config, parse_checks_config, slugify
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
    type: rest
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
