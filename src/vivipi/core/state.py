from __future__ import annotations

from dataclasses import replace
from math import ceil

from vivipi.core.diagnostics import append_diagnostic_lines
from vivipi.core.models import AppMode, AppState, CheckObservation, CheckRuntime, DiagnosticEvent, DisplayMode, Status, TransitionThresholds


def sort_checks(checks: tuple[CheckRuntime, ...]) -> tuple[CheckRuntime, ...]:
    return tuple(sorted(checks, key=lambda check: check.name.casefold()))


def normalize_selection(
    checks: tuple[CheckRuntime, ...],
    selected_id: str | None,
    visible_checks: tuple[CheckRuntime, ...] | None = None,
) -> str | None:
    candidates = visible_checks if visible_checks is not None else sort_checks(checks)
    if not candidates:
        return None
    check_ids = {check.identifier for check in candidates}
    if selected_id in check_ids:
        return selected_id
    return candidates[0].identifier


def overview_checks(state: AppState) -> tuple[CheckRuntime, ...]:
    checks = sort_checks(state.checks)
    if state.display_mode != DisplayMode.COMPACT:
        return checks

    non_healthy = tuple(check for check in checks if check.status != Status.OK)
    if non_healthy:
        return non_healthy
    return checks


def checks_per_page(state: AppState) -> int:
    return state.page_size * state.overview_columns


def page_count(checks: tuple[CheckRuntime, ...], page_size: int) -> int:
    if not checks:
        return 0
    return ceil(len(checks) / page_size)


def normalize_page_index(checks: tuple[CheckRuntime, ...], page_size: int, page_index: int) -> int:
    total_pages = page_count(checks, page_size)
    if total_pages == 0:
        return 0
    return min(max(page_index, 0), total_pages - 1)


def with_checks(state: AppState, checks: tuple[CheckRuntime, ...]) -> AppState:
    next_state = replace(state, checks=checks)
    displayed = overview_checks(next_state)
    return replace(
        next_state,
        checks=checks,
        selected_id=normalize_selection(checks, state.selected_id, displayed),
        page_index=normalize_page_index(displayed, checks_per_page(next_state), state.page_index),
    )


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
            name=observation.name,
            status=Status.UNKNOWN,
            details=observation.details,
            latency_ms=observation.latency_ms,
            last_update_s=observation.observed_at_s,
            consecutive_failures=0,
            consecutive_successes=0,
            source_identifier=observation.source_identifier,
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
            name=observation.name,
            status=next_status,
            details=observation.details,
            latency_ms=observation.latency_ms,
            last_update_s=observation.observed_at_s,
            consecutive_failures=0,
            consecutive_successes=successes,
            source_identifier=observation.source_identifier,
        )

    if observation.status == Status.DEG:
        return replace(
            runtime,
            name=observation.name,
            status=Status.DEG,
            details=observation.details,
            latency_ms=observation.latency_ms,
            last_update_s=observation.observed_at_s,
            consecutive_failures=max(policy.failures_to_degraded, runtime.consecutive_failures + 1),
            consecutive_successes=0,
            source_identifier=observation.source_identifier,
        )

    failures = runtime.consecutive_failures + 1
    return replace(
        runtime,
        name=observation.name,
        status=_failure_status(failures, policy),
        details=observation.details,
        latency_ms=observation.latency_ms,
        last_update_s=observation.observed_at_s,
        consecutive_failures=failures,
        consecutive_successes=0,
        source_identifier=observation.source_identifier,
    )


def integrate_observations(
    state: AppState,
    observations: tuple[CheckObservation, ...],
    thresholds: TransitionThresholds | None = None,
    replace_source_identifier: str | None = None,
) -> AppState:
    runtimes = {runtime.identifier: runtime for runtime in state.checks}
    if replace_source_identifier is not None:
        runtimes = {
            identifier: runtime
            for identifier, runtime in runtimes.items()
            if runtime.source_identifier != replace_source_identifier
        }

    for observation in observations:
        current = runtimes.get(
            observation.identifier,
            CheckRuntime(
                identifier=observation.identifier,
                name=observation.name,
                source_identifier=observation.source_identifier,
            ),
        )
        runtimes[observation.identifier] = apply_observation(current, observation, thresholds)

    return with_checks(state, tuple(runtimes.values()))


def _sorted_selected_index(state: AppState) -> int | None:
    checks = overview_checks(state)
    if not checks:
        return None
    selected_id = normalize_selection(state.checks, state.selected_id, checks)
    for index, check in enumerate(checks):
        if check.identifier == selected_id:
            return index
    return 0


def move_selection(state: AppState, step: int = 1) -> AppState:
    checks = overview_checks(state)
    if not checks:
        return replace(state, selected_id=None, page_index=0)
    current_index = _sorted_selected_index(state)
    assert current_index is not None
    next_index = (current_index + step) % len(checks)
    return replace(
        state,
        selected_id=checks[next_index].identifier,
        page_index=next_index // checks_per_page(state),
    )


def would_wrap_selection(state: AppState, step: int = 1) -> bool:
    checks = overview_checks(state)
    if len(checks) <= 1:
        return True
    current_index = _sorted_selected_index(state)
    if current_index is None:
        return False
    next_index = (current_index + step) % len(checks)
    if step > 0:
        return next_index <= current_index
    return next_index >= current_index


def page_index(state: AppState) -> int:
    return normalize_page_index(overview_checks(state), checks_per_page(state), state.page_index)


def set_page_index(state: AppState, page_index_value: int, select_visible: bool = False) -> AppState:
    displayed = overview_checks(state)
    normalized_page = normalize_page_index(displayed, checks_per_page(state), page_index_value)
    selected_id = normalize_selection(state.checks, state.selected_id, displayed)
    if select_visible and state.checks:
        visible = visible_checks(replace(state, page_index=normalized_page, selected_id=selected_id))
        visible_ids = {check.identifier for check in visible}
        if selected_id not in visible_ids and visible:
            selected_id = visible[0].identifier
    return replace(state, selected_id=selected_id, page_index=normalized_page)


def visible_checks(state: AppState) -> tuple[CheckRuntime, ...]:
    checks = overview_checks(state)
    current_page = page_index(state)
    start_index = current_page * checks_per_page(state)
    end_index = start_index + checks_per_page(state)
    return checks[start_index:end_index]


def selected_check(state: AppState) -> CheckRuntime | None:
    selected_id = normalize_selection(state.checks, state.selected_id, overview_checks(state))
    for check in state.checks:
        if check.identifier == selected_id:
            return check
    return None


def enter_detail(state: AppState) -> AppState:
    return replace(
        state,
        mode=AppMode.DETAIL,
        selected_id=normalize_selection(state.checks, state.selected_id, overview_checks(state)),
    )


def exit_detail(state: AppState) -> AppState:
    return replace(state, mode=AppMode.OVERVIEW)


def with_diagnostics(state: AppState, lines: tuple[str, ...]) -> AppState:
    return replace(state, mode=AppMode.DIAGNOSTICS, diagnostics=lines)


def record_diagnostic_events(
    state: AppState,
    events: tuple[DiagnosticEvent, ...],
    activate: bool = False,
) -> AppState:
    lines = append_diagnostic_lines(state.diagnostics, events)
    mode = AppMode.DIAGNOSTICS if activate and lines else state.mode
    return replace(state, mode=mode, diagnostics=lines)
