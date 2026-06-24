from __future__ import annotations

import sys
from pathlib import Path

import pytest

from vivipi.core.secrets import (
    NETWORK_PASSWORD_KEY,
    NETWORK_USERNAME_KEY,
    _resolve_repository_root,
    extend_environment,
    load_network_secrets,
)


def _write_env_local(root: Path, body: str) -> Path:
    path = root / ".env.local"
    path.write_text(body, encoding="utf-8")
    return path


def _write_secrets_yaml(root: Path, body: str) -> Path:
    path = root / "config" / "secrets.local"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_load_network_secrets_prefers_environment_over_dotenv_over_yaml(tmp_path: Path):
    _write_secrets_yaml(tmp_path, "VIVIPI_NETWORK_USERNAME: yaml_user\nVIVIPI_NETWORK_PASSWORD: yaml_pwd\n")
    _write_env_local(tmp_path, "VIVIPI_NETWORK_USERNAME=dotenv_user\nVIVIPI_NETWORK_PASSWORD=dotenv_pwd\n")

    secrets = load_network_secrets(
        tmp_path,
        env={
            NETWORK_USERNAME_KEY: "shell_user",
            NETWORK_PASSWORD_KEY: "shell_pwd",
        },
    )

    assert secrets == {
        NETWORK_USERNAME_KEY: "shell_user",
        NETWORK_PASSWORD_KEY: "shell_pwd",
    }


def test_load_network_secrets_falls_back_to_dotenv_when_shell_unset(tmp_path: Path):
    _write_secrets_yaml(tmp_path, "VIVIPI_NETWORK_USERNAME: yaml_user\nVIVIPI_NETWORK_PASSWORD: yaml_pwd\n")
    _write_env_local(tmp_path, "VIVIPI_NETWORK_USERNAME=dotenv_user\nVIVIPI_NETWORK_PASSWORD=dotenv_pwd\n")

    secrets = load_network_secrets(tmp_path, env={})

    assert secrets == {
        NETWORK_USERNAME_KEY: "dotenv_user",
        NETWORK_PASSWORD_KEY: "dotenv_pwd",
    }


def test_load_network_secrets_falls_back_to_yaml_when_dotenv_missing(tmp_path: Path):
    _write_secrets_yaml(tmp_path, "VIVIPI_NETWORK_USERNAME: yaml_user\nVIVIPI_NETWORK_PASSWORD: yaml_pwd\n")

    secrets = load_network_secrets(tmp_path, env={})

    assert secrets == {
        NETWORK_USERNAME_KEY: "yaml_user",
        NETWORK_PASSWORD_KEY: "yaml_pwd",
    }


def test_load_network_secrets_ignores_comments_and_blank_lines_in_dotenv(tmp_path: Path):
    _write_env_local(
        tmp_path,
        (
            "# comment\n"
            "\n"
            "VIVIPI_NETWORK_USERNAME=admin\n"
            "VIVIPI_NETWORK_PASSWORD='quoted secret'\n"
        ),
    )

    secrets = load_network_secrets(tmp_path, env={})

    assert secrets == {
        NETWORK_USERNAME_KEY: "admin",
        NETWORK_PASSWORD_KEY: "quoted secret",
    }


def test_load_network_secrets_returns_empty_when_sources_missing(tmp_path: Path):
    secrets = load_network_secrets(tmp_path, env={})
    assert secrets == {}


def test_extend_environment_merges_resolved_secrets_into_existing_env(tmp_path: Path):
    _write_secrets_yaml(tmp_path, "VIVIPI_NETWORK_PASSWORD: yaml_pwd\n")

    merged = extend_environment(tmp_path, env={"UNRELATED": "keep"})

    assert merged["UNRELATED"] == "keep"
    assert merged[NETWORK_PASSWORD_KEY] == "yaml_pwd"
    assert NETWORK_USERNAME_KEY not in merged


def test_extend_environment_lets_caller_override_resolved_secret(tmp_path: Path):
    _write_secrets_yaml(tmp_path, "VIVIPI_NETWORK_PASSWORD: yaml_pwd\n")

    merged = extend_environment(tmp_path, env={NETWORK_PASSWORD_KEY: "caller_override"})

    assert merged[NETWORK_PASSWORD_KEY] == "caller_override"


@pytest.mark.parametrize("bad_yaml", ["not: a: mapping: problem\n", "[\"not\", \"a\", \"mapping\"]\n"])
def test_load_network_secrets_silently_ignores_malformed_yaml(tmp_path: Path, bad_yaml: str):
    _write_secrets_yaml(tmp_path, bad_yaml)
    _write_env_local(tmp_path, "VIVIPI_NETWORK_PASSWORD=dotenv_pwd\n")

    secrets = load_network_secrets(tmp_path, env={})

    assert secrets == {NETWORK_PASSWORD_KEY: "dotenv_pwd"}


def test_load_network_secrets_discovers_pyproject_at_explicit_root(tmp_path: Path):
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    (repository_root / "pyproject.toml").write_text("[project]\nname='vivipi'\n", encoding="utf-8")
    repo_config = repository_root / "config" / "secrets.local"
    repo_config.parent.mkdir(parents=True, exist_ok=True)
    repo_config.write_text("VIVIPI_NETWORK_PASSWORD: repo_pwd\n", encoding="utf-8")

    secrets = load_network_secrets(repository_root, env={})

    assert secrets == {NETWORK_PASSWORD_KEY: "repo_pwd"}


def test_resolve_repository_root_falls_back_to_start_without_pyproject(tmp_path: Path):
    # pytest's tmp_path has no pyproject.toml in its ancestors, so the walk
    # exhausts and returns the start directory unchanged.
    assert _resolve_repository_root(tmp_path).resolve() == tmp_path.resolve()


def test_resolve_repository_root_walks_up_to_find_pyproject(tmp_path: Path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='vivipi'\n", encoding="utf-8")

    assert _resolve_repository_root(nested).resolve() == tmp_path.resolve()


def test_load_network_secrets_uses_vivipi_repo_root_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_secrets_yaml(tmp_path, "VIVIPI_NETWORK_PASSWORD: override_pwd\n")
    monkeypatch.setenv("VIVIPI_REPO_ROOT", str(tmp_path))

    secrets = load_network_secrets(env={})

    assert secrets == {NETWORK_PASSWORD_KEY: "override_pwd"}


def test_load_network_secrets_resolves_root_from_cwd_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_secrets_yaml(tmp_path, "VIVIPI_NETWORK_PASSWORD: cwd_pwd\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='vivipi'\n", encoding="utf-8")
    monkeypatch.delenv("VIVIPI_REPO_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    secrets = load_network_secrets(env={})

    assert secrets == {NETWORK_PASSWORD_KEY: "cwd_pwd"}


def test_load_dotenv_skips_lines_that_do_not_match_key_pattern(tmp_path: Path):
    # Lowercase / numeric keys do not match the [A-Z][A-Z0-9_]* pattern and
    # must be ignored rather than raising.
    _write_env_local(
        tmp_path,
        "VIVIPI_NETWORK_USERNAME=ok\nlowercase_invalid=skip\n123=skip\n",
    )

    secrets = load_network_secrets(tmp_path, env={})

    assert secrets == {NETWORK_USERNAME_KEY: "ok"}


def test_load_yaml_secrets_skips_non_string_entries(tmp_path: Path):
    # Non-string YAML values (e.g. integers) are not valid credential strings.
    _write_secrets_yaml(
        tmp_path,
        "VIVIPI_NETWORK_USERNAME: str_user\nVIVIPI_NETWORK_PASSWORD: 12345\n",
    )

    secrets = load_network_secrets(tmp_path, env={})

    assert secrets == {NETWORK_USERNAME_KEY: "str_user"}


def test_load_yaml_secrets_returns_empty_when_yaml_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_secrets_yaml(tmp_path, "VIVIPI_NETWORK_PASSWORD: yaml_pwd\n")
    # A None entry in sys.modules makes ``import yaml`` raise ImportError.
    monkeypatch.setitem(sys.modules, "yaml", None)

    secrets = load_network_secrets(tmp_path, env={})

    assert secrets == {}