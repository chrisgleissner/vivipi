import re
import tomllib
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPOSITORY_ROOT / "docs" / "spec.md"
TRACEABILITY_PATH = REPOSITORY_ROOT / "docs" / "spec-traceability.md"
PYPROJECT_PATH = REPOSITORY_ROOT / "pyproject.toml"
REQUIREMENT_PATTERN = re.compile(r"\[(VIVIPI-[A-Z-]+-\d{3})\]")
TRACEABILITY_PATTERN = re.compile(r"^\|\s*(VIVIPI-[A-Z-]+-\d{3})\s*\|", re.MULTILINE)


def test_every_requirement_in_the_spec_has_a_traceability_mapping():
    spec_ids = set(REQUIREMENT_PATTERN.findall(SPEC_PATH.read_text(encoding="utf-8")))
    traceability_ids = set(TRACEABILITY_PATTERN.findall(TRACEABILITY_PATH.read_text(encoding="utf-8")))

    assert traceability_ids == spec_ids


def test_traceability_rows_reference_test_locations():
    traceability_text = TRACEABILITY_PATH.read_text(encoding="utf-8")
    for line in traceability_text.splitlines():
        if line.startswith("| VIVIPI-"):
            assert "tests/" in line


def test_pytest_config_enforces_the_coverage_gate():
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]

    assert "--cov-branch" in addopts
    assert "--cov-fail-under=91" in addopts