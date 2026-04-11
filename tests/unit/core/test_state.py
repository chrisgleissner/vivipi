from vivipi.core.models import AppState, CheckObservation, CheckRuntime, DiagnosticEvent, DisplayMode, Status, TransitionThresholds
from vivipi.core.state import (
    apply_observation,
    integrate_observations,
    move_selection,
    set_page_index,
    record_diagnostic_events,
    selected_check,
    visible_checks,
    with_checks,
    with_diagnostics,
    would_wrap_selection,
)


def make_check(name: str, status: Status = Status.OK) -> CheckRuntime:
    return CheckRuntime(identifier=name.casefold(), name=name, status=status)


def test_failure_hysteresis_moves_from_ok_to_deg_to_fail():
    runtime = make_check("Router", status=Status.OK)
    thresholds = TransitionThresholds(failures_to_degraded=1, failures_to_failed=2)

    runtime = apply_observation(
        runtime,
        CheckObservation(identifier="router", name="Router", status=Status.FAIL),
        thresholds,
    )
    assert runtime.status == Status.DEG

    runtime = apply_observation(
        runtime,
        CheckObservation(identifier="router", name="Router", status=Status.FAIL),
        thresholds,
    )
    assert runtime.status == Status.FAIL


def test_immediate_failure_threshold_can_replace_ok_with_fail():
    runtime = make_check("Router", status=Status.OK)
    thresholds = TransitionThresholds(failures_to_degraded=1, failures_to_failed=1)

    updated = apply_observation(
        runtime,
        CheckObservation(identifier="router", name="Router", status=Status.FAIL),
        thresholds,
    )

    assert updated.status == Status.FAIL


def test_success_recovers_from_fail_and_unknown():
    failed = CheckRuntime(
        identifier="router",
        name="Router",
        status=Status.FAIL,
        consecutive_failures=2,
    )
    recovered = apply_observation(
        failed,
        CheckObservation(identifier="router", name="Router", status=Status.OK),
    )
    assert recovered.status == Status.OK

    unknown = CheckRuntime(identifier="nas", name="NAS")
    discovered = apply_observation(
        unknown,
        CheckObservation(identifier="nas", name="NAS", status=Status.OK),
    )
    assert discovered.status == Status.OK


def test_apply_observation_coerces_string_status_values():
    runtime = make_check("Router", status=Status.UNKNOWN)

    updated = apply_observation(
        runtime,
        CheckObservation(identifier="router", name="Router", status="OK"),
    )

    assert updated.status == Status.OK


def test_integrate_observations_recovers_fail_to_ok_without_retaining_stale_status():
    state = AppState(
        checks=(
            CheckRuntime(
                identifier="router",
                name="Router",
                status=Status.FAIL,
                consecutive_failures=1,
            ),
        )
    )

    updated = integrate_observations(
        state,
        (
            CheckObservation(identifier="router", name="Router", status=Status.OK, details="reachable"),
        ),
    )

    assert updated.checks[0].status == Status.OK
    assert updated.checks[0].details == "reachable"


def test_selection_tracks_identity_when_wrapping_sorted_checks():
    state = AppState(
        checks=(make_check("Zulu"), make_check("Alpha"), make_check("Mike")),
        selected_id="mike",
    )

    moved = move_selection(state, 1)
    wrapped = move_selection(moved, 1)

    assert moved.selected_id == "zulu"
    assert wrapped.selected_id == "alpha"


def test_with_checks_preserves_identity_and_falls_back_to_first_visible_check():
    state = AppState(checks=(make_check("Zulu"), make_check("Alpha")), selected_id="zulu")

    preserved = with_checks(state, (make_check("Zulu"), make_check("Bravo")))
    replaced = with_checks(state, (make_check("Bravo"), make_check("Charlie")))

    assert preserved.selected_id == "zulu"
    assert replaced.selected_id == "bravo"


def test_visible_checks_uses_explicit_page_index():
    checks = tuple(make_check(name) for name in ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India"])
    state = AppState(checks=checks, selected_id="alpha", page_index=1)

    visible = visible_checks(state)

    assert [check.identifier for check in visible] == ["india"]


def test_set_page_index_can_keep_selection_visible():
    checks = tuple(make_check(name) for name in ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India"])
    state = AppState(checks=checks, selected_id="alpha")

    updated = set_page_index(state, 1, select_visible=True)

    assert updated.page_index == 1
    assert updated.selected_id == "india"


def test_visible_checks_use_page_capacity_when_multiple_columns_are_enabled():
    checks = tuple(make_check(name) for name in ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"])
    state = AppState(
        checks=checks,
        selected_id="alpha",
        display_mode=DisplayMode.COMPACT,
        overview_columns=2,
        page_size=2,
        page_index=1,
    )

    visible = visible_checks(state)

    assert [check.identifier for check in visible] == ["echo", "foxtrot"]


def test_with_checks_keeps_selection_visible_in_compact_mode():
    state = AppState(
        checks=(make_check("Alpha"), make_check("Bravo")),
        selected_id="alpha",
        display_mode=DisplayMode.COMPACT,
    )

    updated = with_checks(
        state,
        (
            make_check("Alpha", status=Status.OK),
            make_check("Bravo", status=Status.FAIL),
        ),
    )

    assert updated.selected_id == "bravo"


def test_unknown_observation_resets_runtime_state():
    runtime = CheckRuntime(
        identifier="router",
        name="Router",
        status=Status.FAIL,
        consecutive_failures=2,
        consecutive_successes=1,
    )

    updated = apply_observation(
        runtime,
        CheckObservation(identifier="router", name="Router", status=Status.UNKNOWN, details="pending"),
    )

    assert updated.status == Status.UNKNOWN
    assert updated.consecutive_failures == 0
    assert updated.consecutive_successes == 0


def test_explicit_degraded_observation_keeps_degraded_state():
    runtime = make_check("Router", status=Status.OK)

    updated = apply_observation(
        runtime,
        CheckObservation(identifier="router", name="Router", status=Status.DEG),
    )

    assert updated.status == Status.DEG
    assert updated.consecutive_failures == 1


def test_move_selection_on_empty_state_keeps_no_selection():
    moved = move_selection(AppState(), 1)

    assert moved.selected_id is None


def test_move_selection_recovers_from_missing_selected_identifier():
    state = AppState(checks=(make_check("Alpha"), make_check("Bravo")), selected_id="missing")

    moved = move_selection(state, 1)

    assert moved.selected_id == "bravo"


def test_selected_check_and_diagnostics_helpers_preserve_state_shape():
    state = AppState(checks=(make_check("Router"),), selected_id="router")
    diagnostics = with_diagnostics(state, ("wifi disconnected",))

    assert selected_check(state) is not None
    assert diagnostics.mode.value == "diagnostics"
    assert diagnostics.diagnostics == ("wifi disconnected",)


def test_selected_check_returns_none_when_no_checks_exist():
    state = AppState(selected_id="missing")

    assert selected_check(state) is None


def test_set_page_index_can_leave_visible_selection_unchanged():
    checks = tuple(make_check(name) for name in ["Alpha", "Bravo", "Charlie", "Delta"])
    state = AppState(checks=checks, selected_id="alpha", page_size=2)

    updated = set_page_index(state, 0, select_visible=True)

    assert updated.page_index == 0
    assert updated.selected_id == "alpha"


def test_integrate_observations_replaces_previous_service_children_by_source_identifier():
    state = AppState(
        checks=(
            CheckRuntime(identifier="router", name="Router"),
            CheckRuntime(identifier="adb:pixel-8", name="Pixel 8", source_identifier="android-devices"),
            CheckRuntime(identifier="adb:pixel-9", name="Pixel 9", source_identifier="android-devices"),
        ),
        selected_id="adb:pixel-9",
    )

    updated = integrate_observations(
        state,
        observations=(
            CheckObservation(
                identifier="adb:pixel-10",
                name="Pixel 10",
                status=Status.OK,
                source_identifier="android-devices",
            ),
        ),
        replace_source_identifier="android-devices",
    )

    assert [check.identifier for check in updated.checks] == ["router", "adb:pixel-10"]
    assert updated.selected_id == "router"


def test_record_diagnostic_events_deduplicates_and_can_activate_mode():
    state = AppState(diagnostics=("WIFI down",), selected_id=None)

    updated = record_diagnostic_events(
        state,
        events=(
            DiagnosticEvent(code="wifi", message="down"),
            DiagnosticEvent(code="serv", message="schema error"),
        ),
        activate=True,
    )

    assert updated.mode.value == "diagnostics"
    assert updated.diagnostics == ("WIFI down", "SERV schema err…")


def test_would_wrap_selection_returns_true_at_last_check():
    checks = (make_check("Alpha"), make_check("Bravo"), make_check("Charlie"))
    state = AppState(checks=checks, selected_id="charlie")

    assert would_wrap_selection(state, step=1) is True


def test_would_wrap_selection_returns_false_when_not_at_end():
    checks = (make_check("Alpha"), make_check("Bravo"), make_check("Charlie"))
    state = AppState(checks=checks, selected_id="alpha")

    assert would_wrap_selection(state, step=1) is False


def test_would_wrap_selection_returns_true_for_single_check():
    state = AppState(checks=(make_check("Alpha"),), selected_id="alpha")

    assert would_wrap_selection(state, step=1) is True


def test_would_wrap_selection_returns_true_with_large_step():
    checks = (make_check("Alpha"), make_check("Bravo"), make_check("Charlie"))
    state = AppState(checks=checks, selected_id="bravo")

    assert would_wrap_selection(state, step=5) is True


def test_would_wrap_selection_returns_false_for_empty_checks():
    assert would_wrap_selection(AppState(), step=1) is True
