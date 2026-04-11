from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from vivipi.core.models import AppState, CheckDefinition, ProbeSchedulingPolicy


@dataclass(frozen=True)
class ScheduledCheck:
    definition: CheckDefinition
    due_at_s: float


def _fold_case(value: str) -> str:
    text = str(value)
    casefold = getattr(text, "casefold", None)
    if callable(casefold):
        return casefold()
    return text.lower()


def probe_host_key(definition: CheckDefinition) -> str | None:
    target = str(definition.target).strip()
    if not target:
        return None
    if "://" in target:
        parsed = urlparse(target)
        hostname = getattr(parsed, "hostname", None)
        return _fold_case(hostname) if hostname else None

    host, separator, port_text = target.rpartition(":")
    if separator and host and port_text.isdigit():
        normalized_host = _fold_case(host.strip())
        return normalized_host or None
    return _fold_case(target)


def probe_backoff_remaining_s(
    definition: CheckDefinition,
    last_completed_at_by_host: dict[str, float],
    now_s: float,
    policy: ProbeSchedulingPolicy,
) -> float:
    if policy.allow_concurrent_same_host:
        return 0.0

    host_key = probe_host_key(definition)
    if host_key is None:
        return 0.0

    completed_at_s = last_completed_at_by_host.get(host_key)
    if completed_at_s is None:
        return 0.0

    required_gap_s = float(policy.same_host_backoff_ms) / 1000.0
    return max(0.0, required_gap_s - (now_s - completed_at_s))


def next_due_at(definition: CheckDefinition, last_started_at_s: float | None) -> float:
    if last_started_at_s is None:
        return 0.0
    return last_started_at_s + definition.interval_s


def due_checks(
    definitions: tuple[CheckDefinition, ...],
    last_started_at: dict[str, float],
    now_s: float,
) -> tuple[ScheduledCheck, ...]:
    due: list[ScheduledCheck] = []
    for definition in sorted(definitions, key=lambda item: item.identifier):
        due_at_s = next_due_at(definition, last_started_at.get(definition.identifier))
        if due_at_s <= now_s:
            due.append(ScheduledCheck(definition=definition, due_at_s=due_at_s))
    return tuple(sorted(due, key=lambda item: (item.due_at_s, item.definition.identifier)))


def render_reason(previous: AppState | None, current: AppState) -> str:
    if previous is None:
        return "bootstrap"
    if previous.shift_offset != current.shift_offset:
        return "shift"
    if previous != current:
        return "state"
    return "none"
