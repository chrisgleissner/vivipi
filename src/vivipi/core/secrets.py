"""Shared loader for local-only network credentials.

The build tooling and the c64_health_check probe both need access to the
device-side network password that protects the 1541Ultimate HTTP, Telnet,
FTP, and DMA TCP/64 listeners. None of those secrets are ever committed to the
repository; instead they live in one of the following local-only sources:

  1. Already exported environment variables (highest priority)
  2. .env.local in the repository root (KEY=VALUE dotenv syntax)
  3. config/secrets.local in the repository (YAML mapping)

This module merges all three into a plain dict so callers can feed it to the
existing ${VIVIPI_NETWORK_PASSWORD} placeholder resolver in
vivipi.core.config. The merging order is intentional: shell exports win
because they are the most explicit, dotenv is preferred over the YAML file
because it is the more familiar dev ergonomics, and the YAML file is the
fallback when neither is present.

The single shared network-password model is what keeps the four listeners
consistent: firmware-side they all gate on CFG_NETWORK_PASSWORD
(1541ultimate/software/network/socket_dma.cc:99-115, socket_gui.cc, ftpd.cc),
and host-side this loader is the only place that knows the value.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping


NETWORK_USERNAME_KEY = "VIVIPI_NETWORK_USERNAME"
NETWORK_PASSWORD_KEY = "VIVIPI_NETWORK_PASSWORD"
NETWORK_SECRET_KEYS = frozenset({NETWORK_USERNAME_KEY, NETWORK_PASSWORD_KEY})
DOTENV_LINE_PATTERN = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def _resolve_repository_root(start: Path) -> Path:
    candidate = start.resolve()
    for directory in [candidate, *candidate.parents]:
        if (directory / "pyproject.toml").is_file():
            return directory
    return candidate


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = DOTENV_LINE_PATTERN.match(raw_line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        result[key] = value
    return result


def _load_yaml_secrets(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        result[key] = value
    return result


def load_network_secrets(
    repository_root: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    if repository_root is None:
        override = os.environ.get("VIVIPI_REPO_ROOT")
        if override:
            repository_root = Path(override)
        else:
            repository_root = _resolve_repository_root(Path.cwd())
    root = repository_root
    dotenv_values = _load_dotenv(root / ".env.local")
    yaml_values = _load_yaml_secrets(root / "config" / "secrets.local")
    environment = dict(env) if env is not None else dict(os.environ)

    merged: dict[str, str] = {}
    merged.update(yaml_values)
    merged.update(dotenv_values)
    for key, value in environment.items():
        if key in NETWORK_SECRET_KEYS and value:
            merged[key] = value

    return {key: merged[key] for key in NETWORK_SECRET_KEYS if merged.get(key)}


def extend_environment(
    repository_root: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a copy of ``env`` augmented with the locally-resolved network secrets.

    Existing values in ``env`` win so callers can still override the resolved
    secrets explicitly (which is how ``./build --network-password`` is meant
    to behave in the future).
    """

    base = dict(env) if env is not None else dict(os.environ)
    base.update(load_network_secrets(repository_root, env=base))
    return base


__all__ = [
    "NETWORK_PASSWORD_KEY",
    "NETWORK_SECRET_KEYS",
    "NETWORK_USERNAME_KEY",
    "extend_environment",
    "load_network_secrets",
]