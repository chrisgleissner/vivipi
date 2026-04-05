import pytest

from vivipi.core.models import TransitionThresholds


def test_transition_thresholds_validate_failure_and_success_bounds():
    with pytest.raises(ValueError, match="at least 1"):
        TransitionThresholds(failures_to_degraded=0)

    with pytest.raises(ValueError, match="must not be less"):
        TransitionThresholds(failures_to_degraded=2, failures_to_failed=1)

    with pytest.raises(ValueError, match="at least 1"):
        TransitionThresholds(successes_to_recover=0)
