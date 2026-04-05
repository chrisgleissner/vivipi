from vivipi.core.models import AppState
from vivipi.core.scheduler import render_reason


def test_render_reason_reports_bootstrap_when_no_previous_state_exists():
    assert render_reason(None, AppState()) == "bootstrap"


def test_render_reason_reports_shift_before_other_state_changes():
    previous = AppState(shift_offset=(0, 0))
    current = AppState(shift_offset=(1, 0))

    assert render_reason(previous, current) == "shift"


def test_render_reason_reports_state_changes():
    previous = AppState()
    current = AppState(diagnostics=("wifi disconnected",))

    assert render_reason(previous, current) == "state"


def test_render_reason_reports_none_for_identical_states():
    state = AppState()

    assert render_reason(state, state) == "none"
