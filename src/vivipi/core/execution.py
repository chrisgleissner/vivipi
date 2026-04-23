from __future__ import annotations

from dataclasses import dataclass, field

from vivipi.core.models import CheckDefinition, CheckObservation, CheckType, DiagnosticEvent, Status
from vivipi.services.schema import parse_service_payload


@dataclass(frozen=True)
class PingProbeResult:
    ok: bool
    latency_ms: float | None = None
    details: str = ""
    status: Status | None = None
    metadata: dict[str, object] = field(default_factory=dict)


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
    probe_latency_ms: float | None = None
    probe_metadata: dict[str, object] = field(default_factory=dict)


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
        probe_latency_ms=None,
    )


def _status_for_http(status_code: int | None) -> Status:
    if status_code is not None and 200 <= status_code < 400:
        return Status.OK
    return Status.FAIL


def _status_for_probe_result(result) -> Status:
    status = getattr(result, "status", None)
    if status is None:
        return Status.OK if result.ok else Status.FAIL
    if isinstance(status, Status):
        return status
    candidate = getattr(status, "value", status)
    return Status(candidate)


def _probe_metadata(result) -> dict[str, object]:
    metadata = getattr(result, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    return {str(key): value for key, value in metadata.items() if value is not None}


def _probe_execution_result(
    definition: CheckDefinition,
    observed_at_s: float,
    result,
    ok_detail: str,
    failure_detail: str,
) -> CheckExecutionResult:
    status = _status_for_probe_result(result)
    details = result.details.strip() or (ok_detail if status == Status.OK else failure_detail)
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
        probe_latency_ms=result.latency_ms,
        probe_metadata=_probe_metadata(result),
    )


def _execute_probe_check(
    definition: CheckDefinition,
    observed_at_s: float,
    runner,
    code: str,
    error_detail: str,
    ok_detail: str,
    failure_detail: str,
) -> CheckExecutionResult:
    try:
        result = runner()
    except Exception:
        return _execution_error(definition, observed_at_s, code, error_detail)

    return _probe_execution_result(definition, observed_at_s, result, ok_detail, failure_detail)


def execute_check(
    definition: CheckDefinition,
    observed_at_s: float,
    ping_runner,
    http_runner,
    ident_runner=None,
    dma_runner=None,
    ftp_runner=None,
    telnet_runner=None,
) -> CheckExecutionResult:
    if definition.check_type == CheckType.PING:
        return _execute_probe_check(
            definition,
            observed_at_s,
            runner=lambda: ping_runner(definition.target, definition.timeout_s),
            code="PING",
            error_detail="probe failed",
            ok_detail="reachable",
            failure_detail="timeout",
        )

    if definition.check_type == CheckType.IDENT:
        return _execute_probe_check(
            definition,
            observed_at_s,
            runner=lambda: ident_runner(definition.target, definition.timeout_s),
            code="IDNT",
            error_detail="probe failed",
            ok_detail="device identified",
            failure_detail="ident failed",
        )

    if definition.check_type == CheckType.DMA:
        return _execute_probe_check(
            definition,
            observed_at_s,
            runner=lambda: dma_runner(
                definition.target,
                definition.timeout_s,
                password=definition.password,
            ),
            code="DMA",
            error_detail="probe failed",
            ok_detail="dma ready",
            failure_detail="dma failed",
        )

    if definition.check_type == CheckType.FTP:
        return _execute_probe_check(
            definition,
            observed_at_s,
            runner=lambda: ftp_runner(
                definition.target,
                definition.timeout_s,
                username=definition.username,
                password=definition.password,
            ),
            code="FTP",
            error_detail="session failed",
            ok_detail="directory listed",
            failure_detail="ftp failed",
        )

    if definition.check_type == CheckType.TELNET:
        return _execute_probe_check(
            definition,
            observed_at_s,
            runner=lambda: telnet_runner(
                definition.target,
                definition.timeout_s,
                username=definition.username,
                password=definition.password,
            ),
            code="TELN",
            error_detail="session failed",
            ok_detail="session ready",
            failure_detail="telnet failed",
        )

    if definition.check_type == CheckType.HTTP:
        try:
            if definition.username is not None or definition.password is not None:
                result = http_runner(
                    definition.method,
                    definition.target,
                    definition.timeout_s,
                    definition.username,
                    definition.password,
                )
            else:
                result = http_runner(definition.method, definition.target, definition.timeout_s)
        except Exception:
            return _execution_error(definition, observed_at_s, "HTTP", "request failed")

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
            probe_latency_ms=result.latency_ms,
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
            probe_latency_ms=None,
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
            probe_latency_ms=result.latency_ms,
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
            probe_latency_ms=result.latency_ms,
        )

    return CheckExecutionResult(
        source_identifier=definition.identifier,
        observations=observations,
        replace_source=True,
        probe_latency_ms=result.latency_ms,
    )
