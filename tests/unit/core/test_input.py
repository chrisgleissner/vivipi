import pytest

from vivipi.core.input import Button, InputController
from vivipi.core.models import AppMode, AppState, CheckRuntime


def make_state() -> AppState:
    checks = tuple(CheckRuntime(identifier=name, name=name.title()) for name in ("alpha", "bravo", "charlie", "delta"))
    return AppState(checks=checks, selected_id="alpha")


def test_button_a_debounces_short_presses():
    controller = InputController(debounce_ms=30)
    state = make_state()

    assert controller.apply(state, Button.A, held_ms=10) == state


def test_button_a_auto_repeats_every_500ms():
    controller = InputController(debounce_ms=30, repeat_ms=500)
    state = make_state()

    repeated = controller.apply(state, Button.A, held_ms=1030)

    assert repeated.selected_id == "delta"


def test_button_b_toggles_detail_and_back_to_overview():
    controller = InputController()
    state = make_state()

    detail = controller.apply(state, Button.B, held_ms=30)
    back = controller.apply(detail, Button.B, held_ms=30)

    assert detail.mode == AppMode.DETAIL
    assert back.mode == AppMode.OVERVIEW


def test_button_a_cycles_checks_in_detail_view():
    controller = InputController()
    state = AppState(checks=make_state().checks, selected_id="alpha", mode=AppMode.DETAIL)

    moved = controller.apply(state, Button.A, held_ms=30)

    assert moved.selected_id == "bravo"
    assert moved.mode == AppMode.DETAIL


def test_input_controller_validates_debounce_window():
    with pytest.raises(ValueError, match="between 20 and 50"):
        InputController(debounce_ms=10)


def test_input_controller_validates_repeat_interval_and_exits_diagnostics():
    with pytest.raises(ValueError, match="positive"):
        InputController(repeat_ms=0)

    controller = InputController()
    diagnostics = AppState(mode=AppMode.DIAGNOSTICS)

    updated = controller.apply(diagnostics, Button.B, held_ms=30)

    assert updated.mode == AppMode.OVERVIEW


def test_unknown_button_input_leaves_state_unchanged():
    controller = InputController()
    state = make_state()

    updated = controller.apply(state, "UNKNOWN", held_ms=30)

    assert updated == state
