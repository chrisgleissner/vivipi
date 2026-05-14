from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch


@dataclass(frozen=True)
class DeviceInventoryState:
    state: str
    serial_path: str | None = None
    bootsel_disk: str | None = None
    serial_matches: tuple[str, ...] = ()
    bootsel_matches: tuple[str, ...] = ()


def resolve_device_inventory_state(
    selector: dict[str, str],
    *,
    serial_candidates: list[str],
    port_candidates: list[str],
    bootsel_candidates: list[str],
) -> DeviceInventoryState:
    serial_matches = _unique_matches(selector.get("serial_by_id"), serial_candidates)
    port_matches = _unique_matches(selector.get("port"), port_candidates)
    combined_serial_matches = tuple(sorted(set(serial_matches + port_matches)))
    bootsel_matches = _unique_matches(selector.get("bootsel_disk"), bootsel_candidates)

    if len(combined_serial_matches) > 1 or len(bootsel_matches) > 1:
        return DeviceInventoryState(
            state="ambiguous",
            serial_matches=combined_serial_matches,
            bootsel_matches=bootsel_matches,
        )

    if combined_serial_matches:
        return DeviceInventoryState(
            state="serial-ready",
            serial_path=combined_serial_matches[0],
            serial_matches=combined_serial_matches,
            bootsel_matches=bootsel_matches,
        )

    if bootsel_matches:
        return DeviceInventoryState(
            state="bootsel",
            bootsel_disk=bootsel_matches[0],
            bootsel_matches=bootsel_matches,
        )

    return DeviceInventoryState(state="missing")


def _unique_matches(pattern: str | None, candidates: list[str]) -> tuple[str, ...]:
    if pattern is None:
        return ()
    matches = [candidate for candidate in candidates if _matches(pattern, candidate)]
    return tuple(dict.fromkeys(matches))


def _matches(pattern: str, candidate: str) -> bool:
    if any(token in pattern for token in "*?["):
        return fnmatch(candidate, pattern)
    return candidate == pattern