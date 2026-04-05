from __future__ import annotations

from dataclasses import replace

from vivipi.core.models import AppMode, AppState, CheckObservation, CheckRuntime, Status, TransitionThresholds


def sort_checks(checks: tuple[CheckRuntime, ...]) -> tuple[CheckRuntime, ...]:
    return tuple(sorted(checks, key=lambda check: check.name.casefold()))


def normalize_selection(checks: tuple[CheckRuntime, ...], selected_id: str | None) -> str | None:
    if not checks:
        return None
    check_ids = {check.identifier for check in checks}
    if selected_id in check_ids:
        return selected_id
    return sort_checks(checks)[0].identifier


def with_checks(state: AppState, checks: tuple[CheckRuntime, ...]) -> AppState:
    return replace(state, checks=checks, selected_id=normalize_selection(checks, state.selected_id))


def _failure_status(failures: int, thresholds: TransitionThresholds) -> Status:
    if failures >= thresholds.failures_to_failed:
        return Status.FAIL
    if failures >= thresholds.failures_to_degraded:
        return Status.DEG
    return Status.UNKNOWN


def apply_observation(
    runtime: CheckRuntime,
    observation: CheckObservation,
    thresholds: TransitionThresholds | None = None,
) -> CheckRuntime:
    policy = thresholds or TransitionThresholds()
    if observation.status == Status.UNKNOWN:
        return replace(
            runtime,
            status=Status.UNKNOWN,
            details=observation.details,
            latency_ms=observation.latency_ms,
            last_update_s=observation.observed_at_s,
            consecutive_failures=0,
            consecutive_successes=0,
        )

    if observation.status == Status.OK:
        successes = runtime.consecutive_successes + 1
        next_status = runtime.status
        if runtime.status in {Status.DEG, Status.FAIL, Status.UNKNOWN}:
            if successes >= policy.successes_to_recover:
                next_status = Status.OK
        else:
            next_status = Status.OK
        return replace(
            runtime,
            status=next_status,
            details=observation.details,
            latency_ms=observation.latency_ms,
            last_update_s=observation.observed_at_s,
            consecutive_failures=0,
            consecutive_successes=successes,
        )

    if observation.status == Status.DEG:
        return replace(
            runtime,
            status=Status.DEG,
            details=observation.details,
            latency_ms=observation.latency_ms,
            last_update_s=observation.observed_at_s,
            consecutive_failures=max(policy.failures_to_degraded, runtime.consecutive_failures + 1),
            consecutive_successes=0,
        )

    failures = runtime.consecutive_failures + 1
    return replace(
        runtime,
        status=_failure_status(failures, policy),
        details=observation.details,
        latency_ms=observation.latency_ms,
        last_update_s=observation.observed_at_s,
        consecutive_failures=failures,
        consecutive_successes=0,
    )


def _sorted_selected_index(state: AppState) -> int | None:
    checks = sort_checks(state.checks)
    if not checks:
        return None
    selected_id = normalize_selection(checks, state.selected_id)
    for index, check in enumerate(checks):
        if check.identifier == selected_id:
            return index
    return 0


def move_selection(state: AppState, step: int = 1) -> AppState:
    checks = sort_checks(state.checks)
    if not checks:
        return replace(state, selected_id=None)
    current_index = _sorted_selected_index(state)
    assert current_index is not None
    next_index = (current_index + step) % len(checks)
    return replace(state, selected_id=checks[next_index].identifier)


def page_index(state: AppState) -> int:
    selected_index = _sorted_selected_index(state)
    if selected_index is None:
        return 0
    return selected_index // state.page_size


def visible_checks(state: AppState) -> tuple[CheckRuntime, ...]:
    checks = sort_checks(state.checks)
    current_page = page_index(state)
    start_index = current_page * state.page_size
    end_index = start_index + state.page_size
    return checks[start_index:end_index]


def selected_check(state: AppState) -> CheckRuntime | None:
    selected_id = normalize_selection(state.checks, state.selected_id)
    for check in state.checks:
        if check.identifier == selected_id:
            return check
    return None


def enter_detail(state: AppState) -> AppState:
    return replace(state, mode=AppMode.DETAIL, selected_id=normalize_selection(state.checks, state.selected_id))


def exit_detail(state: AppState) -> AppState:
    return replace(state, mode=AppMode.OVERVIEW)


def with_diagnostics(state: AppState, lines: tuple[str, ...]) -> AppState:
    return replace(state, mode=AppMode.DIAGNOSTICS, diagnostics=lines)
