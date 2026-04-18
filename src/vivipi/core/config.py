from __future__ import annotations

import os
import re

from vivipi.core.models import CheckDefinition, CheckType, ProbeSchedulingPolicy


SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
OPTIONAL_AUTH_PLACEHOLDER_KEYS = frozenset({"username", "password"})


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


def _resolve_placeholders(value: object, env: dict[str, str], key: str | None = None) -> object:
    if isinstance(value, dict):
        return {item_key: _resolve_placeholders(item, env, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(item, env, key) for item in value]
    if isinstance(value, str):
        full_match = PLACEHOLDER_PATTERN.fullmatch(value)

        def replace_match(match: re.Match[str]) -> str:
            variable_name = match.group(1)
            if variable_name not in env:
                if key in OPTIONAL_AUTH_PLACEHOLDER_KEYS and full_match is not None:
                    return ""
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


def _parse_check_type(value: str) -> CheckType:
    normalized = value.strip().upper()
    if normalized == "REST":
        normalized = "HTTP"
    return CheckType(normalized)


def _optional_auth_value(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string when provided")
    normalized = value.strip()
    return normalized or None


def _parse_bool(value: object, context: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    raise ValueError(f"{context} must be a boolean")


def parse_probe_schedule_config(raw: object) -> ProbeSchedulingPolicy:
    if raw is None:
        return ProbeSchedulingPolicy()
    if not isinstance(raw, dict):
        raise ValueError("probe_schedule must be a mapping")
    return ProbeSchedulingPolicy(
        allow_concurrent_hosts=_parse_bool(
            raw.get("allow_concurrent_hosts"),
            "probe_schedule.allow_concurrent_hosts",
            False,
        ),
        allow_concurrent_same_host=_parse_bool(
            raw.get("allow_concurrent_same_host"),
            "probe_schedule.allow_concurrent_same_host",
            False,
        ),
        same_host_backoff_ms=int(raw.get("same_host_backoff_ms", 250)),
        interval_grace_ms=int(raw.get("interval_grace_ms", 1000)),
    )


def parse_checks_config(raw: object) -> tuple[CheckDefinition, ...]:
    if not isinstance(raw, dict):
        raise ValueError("checks config must be a mapping")

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
        check_type = _parse_check_type(_require_str(item, "type"))
        interval_s = int(item.get("interval_s", 15))
        timeout_s = int(item.get("timeout_s", 10))
        method = str(item.get("method", "GET")).upper()
        prefix = item.get("prefix")
        username = _optional_auth_value(item, "username")
        password = _optional_auth_value(item, "password")
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
                username=username,
                password=password,
                service_prefix=service_prefix,
            )
        )

    return tuple(definitions)


def load_checks_config(path: str | os.PathLike[str], env: dict[str, str] | None = None) -> tuple[CheckDefinition, ...]:
    import yaml

    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle.read()) or {}
    raw = _resolve_placeholders(raw, env or dict(os.environ))
    return parse_checks_config(raw)
