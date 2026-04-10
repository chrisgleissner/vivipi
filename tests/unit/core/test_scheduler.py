import pytest

from vivipi.core.models import AppState, CheckDefinition, CheckType, ProbeSchedulingPolicy
from vivipi.core.scheduler import due_checks, next_due_at, probe_backoff_remaining_s, probe_host_key, render_reason


def make_definition(identifier: str, interval_s: int = 15) -> CheckDefinition:
    return CheckDefinition(
        identifier=identifier,
        name=identifier.title(),
        check_type=CheckType.PING,
        target="127.0.0.1",
        interval_s=interval_s,
        timeout_s=max(1, int(interval_s * 0.6)),
    )


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


def test_next_due_at_defaults_to_immediate_first_run():
    definition = make_definition("router")

    assert next_due_at(definition, None) == 0.0


def test_due_checks_orders_by_due_time_then_identifier():
    definitions = (
        make_definition("router", interval_s=15),
        make_definition("api", interval_s=30),
        make_definition("backup", interval_s=15),
    )

    due = due_checks(
        definitions,
        last_started_at={"router": 30.0, "api": 0.0, "backup": 20.0},
        now_s=40.0,
    )

    assert [item.definition.identifier for item in due] == ["api", "backup"]


def test_probe_host_key_extracts_normalized_host_from_http_and_socket_targets():
    http_definition = CheckDefinition(
        identifier="api",
        name="Api",
        check_type=CheckType.HTTP,
        target="http://Example.COM:8080/health",
        interval_s=30,
        timeout_s=18,
    )

    assert probe_host_key(make_definition("router")) == "127.0.0.1"
    assert probe_host_key(http_definition) == "example.com"


def test_probe_backoff_remaining_s_respects_same_host_policy():
    definition = make_definition("router")
    policy = ProbeSchedulingPolicy(allow_concurrent_same_host=False, same_host_backoff_ms=250)

    assert probe_backoff_remaining_s(definition, {"127.0.0.1": 10.0}, now_s=10.1, policy=policy) == pytest.approx(0.15)
    assert probe_backoff_remaining_s(definition, {"127.0.0.1": 10.0}, now_s=10.3, policy=policy) == 0.0
    assert probe_backoff_remaining_s(
        definition,
        {"127.0.0.1": 10.0},
        now_s=10.1,
        policy=ProbeSchedulingPolicy(allow_concurrent_same_host=True, same_host_backoff_ms=250),
    ) == 0.0
