from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy


SELECTOR_KEYS = ("serial_by_id", "port", "bootsel_disk")


def is_multi_device_settings(settings: Mapping[str, object]) -> bool:
    devices = settings.get("devices")
    return isinstance(devices, Mapping) and bool(devices)


def expand_multi_device_settings(settings: Mapping[str, object]) -> dict[str, dict[str, object]]:
    devices = settings.get("devices")
    if not isinstance(devices, Mapping) or not devices:
        return {}

    defaults = {key: deepcopy(value) for key, value in settings.items() if key != "devices"}
    expanded: dict[str, dict[str, object]] = {}
    seen_selectors: dict[tuple[str, str], str] = {}

    for device_id, override in devices.items():
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("devices keys must be non-empty strings")
        if not isinstance(override, Mapping):
            raise ValueError(f"devices.{device_id} must be a mapping")

        merged = _deep_merge(defaults, override)
        selector = _normalize_selector(device_id, merged.get("selector"))
        merged["selector"] = selector
        for selector_key, selector_value in selector.items():
            selector_identity = (selector_key, selector_value)
            existing_owner = seen_selectors.get(selector_identity)
            if existing_owner is not None:
                raise ValueError(
                    f"duplicate selector {selector_key}={selector_value!r} for devices {existing_owner!r} and {device_id!r}"
                )
            seen_selectors[selector_identity] = device_id

        project = merged.get("project")
        if project is None:
            merged["project"] = {"device_id": device_id}
        elif isinstance(project, Mapping):
            merged["project"] = dict(project)
            merged["project"]["device_id"] = device_id
        else:
            raise ValueError("project must be a mapping")

        expanded[device_id] = merged

    return expanded


def _deep_merge(base: Mapping[str, object], override: Mapping[str, object]) -> dict[str, object]:
    merged: dict[str, object] = {key: deepcopy(value) for key, value in base.items()}

    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
            continue
        merged[key] = deepcopy(value)

    return merged


def _normalize_selector(device_id: str, selector: object) -> dict[str, str]:
    if not isinstance(selector, Mapping) or not selector:
        raise ValueError(f"devices.{device_id}.selector must be a non-empty mapping")

    normalized: dict[str, str] = {}
    for key in SELECTOR_KEYS:
        value = selector.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"devices.{device_id}.selector.{key} must be a non-empty string")
        candidate = value.strip()
        if candidate.casefold() == "auto":
            raise ValueError(f"devices.{device_id}.selector.{key} must not use auto in multi-device mode")
        normalized[key] = candidate

    if not normalized:
        raise ValueError(f"devices.{device_id}.selector must include one of {', '.join(SELECTOR_KEYS)}")
    return normalized