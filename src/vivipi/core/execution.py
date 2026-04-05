from __future__ import annotations

from dataclasses import dataclass, field

from vivipi.core.models import CheckDefinition, CheckObservation, CheckType, DiagnosticEvent, Status
from vivipi.services.schema import parse_service_payload


@dataclass(frozen=True)
class PingProbeResult:
    ok: bool
    latency_ms: float | None = None
    details: str = ""


@dataclass(frozen=True)
class HttpResponseResult:
    status_code: int | None = None
    body: object | None = None
    latency_ms: float | None = None
    details: str = ""


@dataclass(frozen=True)
class CheckExecutionResult:
    source_identifier: str
    observations: tuple[CheckObservation, ...]
    diagnostics: tuple[DiagnosticEvent, ...] = field(default_factory=tuple)
    replace_source: bool = False


def _direct_observation(
    definition: CheckDefinition,
    status: Status,
    observed_at_s: float,
    details: str = "",
    latency_ms: float | None = None,
) -> CheckObservation:
    return CheckObservation(
        identifier=definition.identifier,
        name=definition.name,
        status=status,
        details=details,
        latency_ms=latency_ms,
        observed_at_s=observed_at_s,
    )


def _execution_error(
    definition: CheckDefinition,
    observed_at_s: float,
    code: str,
    detail: str,
) -> CheckExecutionResult:
    observation = _direct_observation(
        definition,
        status=Status.FAIL,
        observed_at_s=observed_at_s,
        details="executor error",
        latency_ms=None,
    )
    return CheckExecutionResult(
        source_identifier=definition.identifier,
        observations=(observation,),
        diagnostics=(
            DiagnosticEvent(
                code=code,
                message=detail,
                observed_at_s=observed_at_s,
                source_identifier=definition.identifier,
            ),
        ),
    )


def _status_for_http(status_code: int | None) -> Status:
    if status_code is not None and 200 <= status_code < 400:
        return Status.OK
    return Status.FAIL


def execute_check(
    definition: CheckDefinition,
    observed_at_s: float,
    ping_runner,
    http_runner,
) -> CheckExecutionResult:
    if definition.check_type == CheckType.PING:
        try:
            result = ping_runner(definition.target, definition.timeout_s)
        except Exception:
            return _execution_error(definition, observed_at_s, "PING", "probe failed")

        status = Status.OK if result.ok else Status.FAIL
        details = result.details.strip() or ("reachable" if status == Status.OK else "timeout")
        return CheckExecutionResult(
            source_identifier=definition.identifier,
            observations=(
                _direct_observation(
                    definition,
                    status=status,
                    observed_at_s=observed_at_s,
                    details=details,
                    latency_ms=result.latency_ms,
                ),
            ),
        )

    if definition.check_type == CheckType.REST:
        try:
            result = http_runner(definition.method, definition.target, definition.timeout_s)
        except Exception:
            return _execution_error(definition, observed_at_s, "REST", "request failed")

        status = _status_for_http(result.status_code)
        details = result.details.strip() or (
            f"HTTP {result.status_code}" if result.status_code is not None else "request failed"
        )
        return CheckExecutionResult(
            source_identifier=definition.identifier,
            observations=(
                _direct_observation(
                    definition,
                    status=status,
                    observed_at_s=observed_at_s,
                    details=details,
                    latency_ms=result.latency_ms,
                ),
            ),
        )

    try:
        result = http_runner(definition.method, definition.target, definition.timeout_s)
    except Exception:
        service_failure = _direct_observation(
            definition,
            status=Status.FAIL,
            observed_at_s=observed_at_s,
            details="request failed",
        )
        failure = _execution_error(definition, observed_at_s, "SERV", "request failed")
        return CheckExecutionResult(
            source_identifier=definition.identifier,
            observations=(service_failure,),
            diagnostics=failure.diagnostics,
            replace_source=True,
        )

    if result.status_code is None or not 200 <= result.status_code < 400:
        details = result.details.strip() or (
            f"HTTP {result.status_code}" if result.status_code is not None else "request failed"
        )
        return CheckExecutionResult(
            source_identifier=definition.identifier,
            observations=(
                _direct_observation(
                    definition,
                    status=Status.FAIL,
                    observed_at_s=observed_at_s,
                    details=details,
                    latency_ms=result.latency_ms,
                ),
            ),
            replace_source=True,
        )

    try:
        observations = parse_service_payload(
            result.body,
            service_prefix=definition.service_prefix,
            observed_at_s=observed_at_s,
            source_identifier=definition.identifier,
        )
    except ValueError:
        return CheckExecutionResult(
            source_identifier=definition.identifier,
            observations=(
                _direct_observation(
                    definition,
                    status=Status.FAIL,
                    observed_at_s=observed_at_s,
                    details="schema error",
                    latency_ms=result.latency_ms,
                ),
            ),
            diagnostics=(
                DiagnosticEvent(
                    code="SERV",
                    message="schema error",
                    observed_at_s=observed_at_s,
                    source_identifier=definition.identifier,
                ),
            ),
            replace_source=True,
        )

    return CheckExecutionResult(
        source_identifier=definition.identifier,
        observations=observations,
        replace_source=True,
    )