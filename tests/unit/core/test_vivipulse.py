from __future__ import annotations

from dataclasses import replace
import threading

import pytest

from vivipi.core.execution import CheckExecutionResult
from vivipi.core.models import CheckDefinition, CheckObservation, CheckType, Status
import vivipi.core.vivipulse as vivipulse_core
from vivipi.core.vivipulse import (
    FirmwareResearchHints,
    HostProbeRunner,
    FailureBoundary,
    RunOutcome,
    TraceEvent,
    VivipulseProfile,
    build_plan_view,
    run_search,
    select_definitions,
)


def make_definition(identifier: str, *, check_type: CheckType = CheckType.PING, target: str = "http://device.local/health"):
    return CheckDefinition(
        identifier=identifier,
        name=identifier.upper(),
        check_type=check_type,
        target=target,
        interval_s=15,
        timeout_s=10,
    )


class FakeClock:
    def __init__(self):
        self.value = 100.0
        self.sleeps = []

    def wall(self):
        return self.value

    def monotonic(self):
        return self.value

    def sleep(self, seconds: float):
        self.sleeps.append(seconds)
        self.value += seconds


def success_result(definition: CheckDefinition, observed_at_s: float, detail: str = "reachable") -> CheckExecutionResult:
    return CheckExecutionResult(
        source_identifier=definition.identifier,
        observations=(
            CheckObservation(
                identifier=definition.identifier,
                name=definition.name,
                status=Status.OK,
                details=detail,
                latency_ms=12.0,
                observed_at_s=observed_at_s,
            ),
        ),
    )


def failure_result(definition: CheckDefinition, observed_at_s: float, detail: str = "timeout") -> CheckExecutionResult:
    return CheckExecutionResult(
        source_identifier=definition.identifier,
        observations=(
            CheckObservation(
                identifier=definition.identifier,
                name=definition.name,
                status=Status.FAIL,
                details=detail,
                latency_ms=250.0,
                observed_at_s=observed_at_s,
            ),
        ),
    )


def test_select_definitions_filters_by_target_and_check_id():
    first = make_definition("alpha", target="host-a")
    second = make_definition("beta", target="host-b")
    third = make_definition("gamma", target="host-a")

    selected = select_definitions((first, second, third), target="host-a", check_ids=("gamma",))

    assert selected == (third,)


def test_vivipulse_helpers_cover_classification_and_runtime_config():
    definition = make_definition("alpha")
    result = CheckExecutionResult(
        source_identifier="alpha",
        observations=(
            CheckObservation(
                identifier="beta",
                name="BETA",
                status=Status.FAIL,
                details="ignored",
                source_identifier="alpha",
            ),
        ),
        diagnostics=(),
    )

    assert vivipulse_core._exception_text(None) is None
    assert vivipulse_core._exception_text(RuntimeError("")) == "RuntimeError"
    assert vivipulse_core._diagnostic_messages(
        CheckExecutionResult(source_identifier="alpha", observations=(), diagnostics=())
    ) == ()
    assert vivipulse_core._source_observation(definition, result).identifier == "beta"
    assert vivipulse_core._failure_class_from_detail("OK", "reachable", None) == "success"
    assert vivipulse_core._failure_class_from_detail("FAIL", "dns: bad host", None) == "dns"
    assert vivipulse_core._failure_class_from_detail("FAIL", "executor error", None) == "unexpected_exception"
    assert vivipulse_core._failure_class_from_detail("FAIL", "schema error", None) == "protocol"
    assert vivipulse_core._direct_summary("", ("PING:probe failed",), None) == "PING:probe failed"
    assert vivipulse_core._direct_summary("", (), "boom") == "boom"
    runtime_config = vivipulse_core.definitions_to_runtime_config((definition,), profile=VivipulseProfile().probe_policy())
    assert runtime_config["checks"][0]["id"] == "alpha"
    assert runtime_config["probe_schedule"]["allow_concurrent_hosts"] is False
    assert runtime_config["probe_schedule"]["same_host_backoff_ms"] == 250


def test_vivipulse_profile_validation_and_apply_profile_cover_disabled_and_scaled_checks():
    with pytest.raises(ValueError, match="same_host_backoff_ms"):
        VivipulseProfile(same_host_backoff_ms=-1)
    with pytest.raises(ValueError, match="pass_spacing_s"):
        VivipulseProfile(pass_spacing_s=-0.1)
    with pytest.raises(ValueError, match="same_host_spacing_ms"):
        VivipulseProfile(same_host_spacing_ms=-1)
    with pytest.raises(ValueError, match="check_order"):
        VivipulseProfile(check_order="bad-order")
    with pytest.raises(ValueError, match="non-empty strings"):
        VivipulseProfile(interval_scale_by_check_id={"": 2.0})
    with pytest.raises(ValueError, match="at least 1.0"):
        VivipulseProfile(interval_scale_by_check_id={"alpha": 0.5})

    definition = replace(make_definition("alpha"), interval_s=10, timeout_s=9)
    adjusted = vivipulse_core.apply_profile(
        (definition, make_definition("beta")),
        VivipulseProfile(interval_scale_by_check_id={"alpha": 2.0}, disabled_check_ids=("beta",)),
    )

    assert len(adjusted) == 1
    assert adjusted[0].interval_s == 20
    assert adjusted[0].timeout_s == 9


def test_build_plan_view_groups_same_host_checks_and_preserves_deterministic_order():
    definitions = (
        make_definition("b-http", check_type=CheckType.HTTP, target="http://shared.local/b"),
        make_definition("a-ping", target="shared.local"),
        make_definition("c-ftp", check_type=CheckType.FTP, target="ftp://other.local"),
    )

    plan = build_plan_view(definitions, VivipulseProfile())

    assert plan.selected_definition_ids == ("b-http", "a-ping", "c-ftp")
    assert plan.pass_order == ("a-ping", "b-http", "c-ftp")
    assert plan.same_host_groups == (
        ("shared.local", ("b-http", "a-ping")),
        ("other.local", ("c-ftp",)),
    )


def test_ordered_definitions_for_pass_supports_heavy_first():
    definitions = (
        make_definition("alpha", check_type=CheckType.PING, target="shared.local"),
        make_definition("beta", check_type=CheckType.FTP, target="ftp://shared.local"),
        make_definition("gamma", check_type=CheckType.HTTP, target="http://shared.local/health"),
    )

    ordered = vivipulse_core.ordered_definitions_for_pass(
        definitions,
        VivipulseProfile(check_order="network-heavy-first"),
    )

    assert [definition.identifier for definition in ordered] == ["beta", "gamma", "alpha"]


def test_host_probe_runner_enforces_same_host_backoff_and_records_trace():
    clock = FakeClock()
    definitions = (
        make_definition("alpha", target="shared.local"),
        make_definition("beta", target="http://shared.local/health"),
    )
    calls = []

    def executor(definition: CheckDefinition, observed_at_s: float):
        calls.append((definition.identifier, observed_at_s))
        return success_result(definition, observed_at_s)

    runner = HostProbeRunner(
        definitions,
        executor,
        "reproduce",
        VivipulseProfile(same_host_backoff_ms=250),
        wall_time_provider=clock.wall,
        monotonic_time_provider=clock.monotonic,
        sleep=clock.sleep,
    )

    outcome = runner.run_passes(1)

    assert [event.check_id for event in outcome.trace_events] == ["alpha", "beta"]
    assert clock.sleeps == [0.25]
    assert outcome.trace_events[0].sleep_before_ms == 0
    assert outcome.trace_events[1].sleep_before_ms == 250
    assert outcome.trace_events[1].called_function_path.endswith("-> vivipi.core.execution.execute_check")
    assert calls[1][1] == 100.25


def test_host_probe_runner_run_passes_applies_pass_spacing_and_trace_sink():
    clock = FakeClock()
    definition = make_definition("alpha", target="shared.local")
    captured = []

    runner = HostProbeRunner(
        (definition,),
        lambda check, observed_at_s: success_result(check, observed_at_s),
        "reproduce",
        VivipulseProfile(pass_spacing_s=0.5),
        trace_sink=captured.append,
        wall_time_provider=clock.wall,
        monotonic_time_provider=clock.monotonic,
        sleep=clock.sleep,
    )

    outcome = runner.run_passes(2)

    assert len(captured) == 2
    assert clock.sleeps == [0.5]
    assert outcome.total_sleep_s == 0.5


def test_host_probe_runner_runs_distinct_hosts_serially_by_default():
    lock = threading.Lock()
    active = 0
    max_active = 0
    barrier = threading.Barrier(2)
    definitions = (
        make_definition("alpha", target="shared-a.local"),
        make_definition("beta", target="shared-b.local"),
    )

    def executor(definition: CheckDefinition, observed_at_s: float):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            with pytest.raises(threading.BrokenBarrierError):
                barrier.wait(timeout=0.05)
        finally:
            with lock:
                active -= 1
        return success_result(definition, observed_at_s)

    runner = HostProbeRunner(
        definitions,
        executor,
        "reproduce",
        VivipulseProfile(same_host_backoff_ms=250),
    )

    outcome = runner.run_passes(1)

    assert outcome.transport_failure_count == 0
    assert [event.check_id for event in outcome.trace_events] == ["alpha", "beta"]
    assert max_active == 1
    assert {event.probe_host_key for event in outcome.trace_events} == {"shared-a.local", "shared-b.local"}


def test_host_probe_runner_can_run_distinct_hosts_in_parallel_when_enabled():
    barrier = threading.Barrier(2)
    definitions = (
        make_definition("alpha", target="shared-a.local"),
        make_definition("beta", target="shared-b.local"),
    )

    def executor(definition: CheckDefinition, observed_at_s: float):
        barrier.wait(timeout=0.5)
        return success_result(definition, observed_at_s)

    runner = HostProbeRunner(
        definitions,
        executor,
        "reproduce",
        VivipulseProfile(allow_concurrent_hosts=True, same_host_backoff_ms=250),
    )

    outcome = runner.run_passes(1)

    assert outcome.transport_failure_count == 0
    assert [event.check_id for event in outcome.trace_events] == ["alpha", "beta"]


def test_host_probe_runner_stops_same_host_traffic_after_first_transport_failure_boundary():
    clock = FakeClock()
    definitions = (
        make_definition("alpha", target="shared.local"),
        make_definition("beta", check_type=CheckType.HTTP, target="http://shared.local/health"),
        make_definition("gamma", check_type=CheckType.FTP, target="ftp://shared.local"),
    )

    def executor(definition: CheckDefinition, observed_at_s: float):
        if definition.identifier == "alpha":
            return success_result(definition, observed_at_s)
        if definition.identifier == "beta":
            return failure_result(definition, observed_at_s, detail="timeout")
        raise AssertionError("gamma should not execute after the host is blocked")

    runner = HostProbeRunner(
        definitions,
        executor,
        "reproduce",
        VivipulseProfile(same_host_backoff_ms=0),
        wall_time_provider=clock.wall,
        monotonic_time_provider=clock.monotonic,
        sleep=clock.sleep,
    )

    outcome = runner.run_passes(1)

    assert [event.check_id for event in outcome.trace_events] == ["alpha", "beta"]
    assert outcome.transport_failure_count == 1
    assert outcome.blocked_host_keys == ("shared.local",)
    assert len(outcome.failure_boundaries) == 1
    boundary = outcome.failure_boundaries[0]
    assert boundary.last_success is not None
    assert boundary.last_success.check_id == "alpha"
    assert boundary.first_failure.check_id == "beta"


def test_host_probe_runner_records_executor_exception_and_stop_on_failure():
    clock = FakeClock()
    definition = make_definition("alpha", target="shared.local")

    runner = HostProbeRunner(
        (definition,),
        lambda check, observed_at_s: (_ for _ in ()).throw(RuntimeError("boom")),
        "reproduce",
        VivipulseProfile(),
        wall_time_provider=clock.wall,
        monotonic_time_provider=clock.monotonic,
        sleep=clock.sleep,
        stop_on_failure=True,
    )

    outcome = runner.run_passes(1)

    assert outcome.trace_events[0].failure_class == "unexpected_exception"
    assert outcome.aborted is False


def test_host_probe_runner_can_resume_after_interactive_recovery():
    clock = FakeClock()
    definitions = (
        make_definition("alpha", target="shared.local"),
        make_definition("beta", check_type=CheckType.HTTP, target="http://shared.local/health"),
        make_definition("gamma", check_type=CheckType.FTP, target="ftp://shared.local"),
    )
    failures = {"beta": 1}

    def executor(definition: CheckDefinition, observed_at_s: float):
        if failures.get(definition.identifier):
            failures[definition.identifier] -= 1
            return failure_result(definition, observed_at_s, detail="refused: service down")
        return success_result(definition, observed_at_s)

    runner = HostProbeRunner(
        definitions,
        executor,
        "reproduce",
        VivipulseProfile(same_host_backoff_ms=0),
        wall_time_provider=clock.wall,
        monotonic_time_provider=clock.monotonic,
        sleep=clock.sleep,
        interactive_recovery=True,
        resume_after_recovery=True,
        recovery_callback=lambda boundary: boundary.first_failure.check_id == "beta",
    )

    outcome = runner.run_passes(1)

    assert [event.check_id for event in outcome.trace_events] == ["alpha", "beta", "gamma"]
    assert outcome.recovery_count == 1
    assert outcome.blocked_host_keys == ()


def test_host_probe_runner_duration_mode_sleeps_when_nothing_is_due_and_handles_service_success():
    clock = FakeClock()
    definition = make_definition("service", check_type=CheckType.SERVICE, target="http://shared.local/checks")

    def executor(check: CheckDefinition, observed_at_s: float):
        return CheckExecutionResult(
            source_identifier=check.identifier,
            observations=(
                CheckObservation(identifier="svc:one", name="One", status=Status.OK, details="ok"),
                CheckObservation(identifier="svc:two", name="Two", status=Status.FAIL, details="fail"),
            ),
        )

    runner = HostProbeRunner(
        (definition,),
        executor,
        "soak",
        VivipulseProfile(),
        wall_time_provider=clock.wall,
        monotonic_time_provider=clock.monotonic,
        sleep=clock.sleep,
    )

    outcome = runner.run_duration(0.06)

    assert outcome.trace_events[0].failure_class == "success"
    assert clock.sleeps[0] == 0.05
    assert clock.sleeps[-1] == pytest.approx(0.01)


def test_host_probe_runner_internal_helpers_cover_recovery_and_boundaries():
    definition = make_definition("alpha", target="shared.local")
    runner = HostProbeRunner((definition,), lambda check, observed_at_s: success_result(check, observed_at_s), "reproduce", VivipulseProfile())
    event = TraceEvent(
        wall_time="2026-04-11T00:00:00.000000Z",
        monotonic_s=1.0,
        sequence=1,
        target_sequence=1,
        mode="reproduce",
        pass_index=1,
        check_id="alpha",
        check_name="ALPHA",
        check_type="PING",
        target="shared.local",
        probe_host_key="shared.local",
        timeout_s=10,
        same_host_backoff_ms=250,
        called_function_path="path",
        latency_ms=1.0,
        observation_status="FAIL",
        failure_class="timeout",
        response_summary="timeout",
        raw_detail="timeout",
    )

    assert runner._current_pass_index() == 1
    assert runner._record_boundary(event) is None
    runner.last_success_by_host["shared.local"] = event
    boundary = runner._record_boundary(event)
    assert boundary is not None
    runner.interactive_recovery = True
    runner.resume_after_recovery = False
    runner.recovery_callback = lambda value: True
    runner.stop_on_failure = True
    runner._handle_recovery(boundary)
    assert runner.aborted is True


def test_run_search_prefers_the_first_stable_candidate():
    definitions = (make_definition("alpha"),)
    base_profile = VivipulseProfile(same_host_backoff_ms=250)
    research = FirmwareResearchHints(repo_path="/tmp/1541ultimate", recommended_same_host_backoff_ms=1000)

    def make_outcome(profile: VivipulseProfile) -> RunOutcome:
        transport_failures = 0 if profile.same_host_backoff_ms >= 1000 else 1
        trace_events = (
            TraceEvent(
                wall_time="2026-04-11T00:00:00.000000Z",
                monotonic_s=1.0,
                sequence=1,
                target_sequence=1,
                mode="search",
                pass_index=1,
                check_id="alpha",
                check_name="ALPHA",
                check_type="PING",
                target="device.local",
                probe_host_key="device.local",
                timeout_s=10,
                same_host_backoff_ms=profile.same_host_backoff_ms,
                called_function_path="vivipi.runtime.checks.build_executor.<locals>.executor -> vivipi.core.execution.execute_check",
                latency_ms=10.0,
                observation_status="OK" if not transport_failures else "FAIL",
                failure_class="success" if not transport_failures else "timeout",
                response_summary="reachable" if not transport_failures else "timeout",
                raw_detail="reachable" if not transport_failures else "timeout",
            ),
        )
        return RunOutcome(
            mode="search",
            profile=profile,
            started_at="2026-04-11T00:00:00.000000Z",
            completed_at="2026-04-11T00:00:01.000000Z",
            trace_events=trace_events,
            failure_boundaries=(),
            selected_definition_ids=("alpha",),
            blocked_host_keys=() if not transport_failures else ("device.local",),
            aborted=False,
        )

    class FakeRunner:
        def __init__(self, profile: VivipulseProfile):
            self.profile = profile

        def run_passes(self, passes: int) -> RunOutcome:
            assert passes == 1
            return make_outcome(self.profile)

    result = run_search(
        lambda profile: FakeRunner(profile),
        base_profile=base_profile,
        research=research,
        definitions=definitions,
        passes=1,
        max_experiments=3,
    )

    assert result.selected.profile.same_host_backoff_ms == 1000
    assert result.selected.label == "candidate-1"


def test_generate_candidate_profiles_and_run_search_cover_boundary_and_stable_baseline():
    boundary = FailureBoundary(
        target="shared.local",
        probe_host_key="shared.local",
        last_success=None,
        first_failure=TraceEvent(
            wall_time="2026-04-11T00:00:00.000000Z",
            monotonic_s=1.0,
            sequence=1,
            target_sequence=1,
            mode="search",
            pass_index=1,
            check_id="alpha",
            check_name="ALPHA",
            check_type="PING",
            target="shared.local",
            probe_host_key="shared.local",
            timeout_s=10,
            same_host_backoff_ms=250,
            called_function_path="path",
            latency_ms=10.0,
            observation_status="FAIL",
            failure_class="timeout",
            response_summary="timeout",
            raw_detail="timeout",
        ),
    )
    candidates = vivipulse_core.generate_candidate_profiles(
        VivipulseProfile(),
        FirmwareResearchHints(repo_path="/tmp/1541ultimate"),
        (make_definition("alpha"),),
        boundary,
        max_candidates=20,
    )
    assert any(candidate.interval_scale_by_check_id for candidate in candidates)
    assert any(candidate.disabled_check_ids for candidate in candidates)

    stable = run_search(
        lambda profile: type("Runner", (), {"run_passes": lambda self, passes: RunOutcome(
            mode="search",
            profile=profile,
            started_at="2026-04-11T00:00:00.000000Z",
            completed_at="2026-04-11T00:00:00.100000Z",
            trace_events=(
                TraceEvent(
                    wall_time="2026-04-11T00:00:00.000000Z",
                    monotonic_s=1.0,
                    sequence=1,
                    target_sequence=1,
                    mode="search",
                    pass_index=1,
                    check_id="alpha",
                    check_name="ALPHA",
                    check_type="PING",
                    target="device.local",
                    probe_host_key="device.local",
                    timeout_s=10,
                    same_host_backoff_ms=profile.same_host_backoff_ms,
                    called_function_path="path",
                    latency_ms=1.0,
                    observation_status="OK",
                    failure_class="success",
                    response_summary="reachable",
                    raw_detail="reachable",
                ),
            ),
            failure_boundaries=(),
            selected_definition_ids=("alpha",),
            blocked_host_keys=(),
            aborted=False,
        )})(),
        base_profile=VivipulseProfile(),
        research=FirmwareResearchHints(repo_path="/tmp/1541ultimate"),
        definitions=(make_definition("alpha"),),
        passes=1,
        max_experiments=1,
    )

    assert stable.selected.label == "baseline"
