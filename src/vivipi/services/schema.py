from __future__ import annotations

from vivipi.core.config import build_service_check_id
from vivipi.core.models import CheckObservation, Status


def parse_service_payload(
    payload: object,
    service_prefix: str | None = None,
    observed_at_s: float | None = None,
) -> tuple[CheckObservation, ...]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise ValueError("payload must contain a checks list")

    observations: list[CheckObservation] = []
    seen_ids: set[str] = set()
    for item in checks:
        if not isinstance(item, dict):
            raise ValueError("each service check must be an object")

        name = item.get("name")
        status = item.get("status")
        details = item.get("details")
        latency_ms = item.get("latency_ms")

        if not isinstance(name, str) or not name.strip():
            raise ValueError("service check name must be a non-empty string")
        if not isinstance(status, str):
            raise ValueError("service check status must be a string")
        if not isinstance(details, str):
            raise ValueError("service check details must be a string")
        if not isinstance(latency_ms, int | float):
            raise ValueError("service check latency_ms must be numeric")

        status_enum = Status(status)
        identifier = build_service_check_id(service_prefix, name)
        if identifier in seen_ids:
            raise ValueError(f"duplicate service check id: {identifier}")
        seen_ids.add(identifier)

        observations.append(
            CheckObservation(
                identifier=identifier,
                name=name,
                status=status_enum,
                details=details,
                latency_ms=float(latency_ms),
                observed_at_s=observed_at_s,
            )
        )

    return tuple(observations)
