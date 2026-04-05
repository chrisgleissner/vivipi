import pytest

from vivipi.core.models import AppState, TransitionThresholds


def test_transition_thresholds_validate_failure_and_success_bounds():
    with pytest.raises(ValueError, match="at least 1"):
        TransitionThresholds(failures_to_degraded=0)

    with pytest.raises(ValueError, match="must not be less"):
        TransitionThresholds(failures_to_degraded=2, failures_to_failed=1)

    with pytest.raises(ValueError, match="at least 1"):
        TransitionThresholds(successes_to_recover=0)


def test_app_state_validates_overview_columns_separator_and_width():
    with pytest.raises(ValueError, match="between 1 and 4"):
        AppState(overview_columns=5)

    with pytest.raises(ValueError, match="exactly one character"):
        AppState(column_separator="||")

    with pytest.raises(ValueError, match="too small"):
        AppState(row_width=2, overview_columns=2)
