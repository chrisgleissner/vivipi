from vivipi.core.models import AppState, CheckObservation, CheckRuntime, Status, TransitionThresholds
from vivipi.core.state import apply_observation, move_selection, selected_check, visible_checks, with_checks, with_diagnostics


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


def test_visible_checks_keeps_selected_check_on_current_page():
    checks = tuple(make_check(name) for name in ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India"])
    state = AppState(checks=checks, selected_id="india")

    visible = visible_checks(state)

    assert len(visible) == 1
    assert visible[0].identifier == "india"


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


def test_selected_check_and_diagnostics_helpers_preserve_state_shape():
    state = AppState(checks=(make_check("Router"),), selected_id="router")
    diagnostics = with_diagnostics(state, ("wifi disconnected",))

    assert selected_check(state) is not None
    assert diagnostics.mode.value == "diagnostics"
    assert diagnostics.diagnostics == ("wifi disconnected",)
