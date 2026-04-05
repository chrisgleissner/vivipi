from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from vivipi.core.models import CheckDefinition, CheckType


SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def slugify(value: str) -> str:
    normalized = SLUG_PATTERN.sub("-", value.casefold()).strip("-")
    return normalized or "check"


def build_direct_check_id(name: str) -> str:
    return slugify(name)


def build_service_check_id(prefix: str | None, check_name: str) -> str:
    check_id = slugify(check_name)
    if prefix:
        return f"{slugify(prefix)}:{check_id}"
    return check_id


def _resolve_placeholders(value: object, env: dict[str, str]) -> object:
    if isinstance(value, dict):
        return {key: _resolve_placeholders(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(item, env) for item in value]
    if isinstance(value, str):
        def replace_match(match: re.Match[str]) -> str:
            variable_name = match.group(1)
            if variable_name not in env:
                raise KeyError(f"missing environment variable: {variable_name}")
            return env[variable_name]

        return PLACEHOLDER_PATTERN.sub(replace_match, value)
    return value


def _require_str(item: dict[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _validate_timing(interval_s: int, timeout_s: int):
    if interval_s < 1:
        raise ValueError("interval_s must be positive")
    if timeout_s < 1:
        raise ValueError("timeout_s must be positive")
    if timeout_s > interval_s * 0.8:
        raise ValueError("timeout_s must be at least 20% smaller than interval_s")


def load_checks_config(path: str | Path, env: dict[str, str] | None = None) -> tuple[CheckDefinition, ...]:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw = _resolve_placeholders(raw, env or dict(os.environ))
    checks = raw.get("checks")
    if not isinstance(checks, list):
        raise ValueError("checks must be a list")

    definitions: list[CheckDefinition] = []
    seen_ids: set[str] = set()

    for item in checks:
        if not isinstance(item, dict):
            raise ValueError("each check must be a mapping")

        name = _require_str(item, "name")
        target = _require_str(item, "target")
        check_type = CheckType(_require_str(item, "type").upper())
        interval_s = int(item.get("interval_s", 15))
        timeout_s = int(item.get("timeout_s", 10))
        method = str(item.get("method", "GET")).upper()
        prefix = item.get("prefix")
        service_prefix = str(prefix).strip() if isinstance(prefix, str) and prefix.strip() else None

        _validate_timing(interval_s, timeout_s)

        identifier = build_direct_check_id(name)
        if identifier in seen_ids:
            raise ValueError(f"duplicate check id: {identifier}")
        seen_ids.add(identifier)

        definitions.append(
            CheckDefinition(
                identifier=identifier,
                name=name,
                check_type=check_type,
                target=target,
                interval_s=interval_s,
                timeout_s=timeout_s,
                method=method,
                service_prefix=service_prefix,
            )
        )

    return tuple(definitions)
