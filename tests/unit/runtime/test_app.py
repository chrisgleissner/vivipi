from dataclasses import replace

import pytest
from types import SimpleNamespace

from vivipi.core.execution import CheckExecutionResult
from vivipi.core.input import Button
from vivipi.core.models import CheckDefinition, CheckObservation, CheckType, DiagnosticEvent, DisplayMode, ProbeSchedulingPolicy, Status, TransitionThresholds
from vivipi.core.render import Frame
import vivipi.runtime.app as runtime_app_module
from vivipi.runtime import ButtonEvent, RuntimeApp


class FakeDisplay:
    def __init__(self):
        self.frames = []

    def draw_frame(self, frame):
        self.frames.append(frame)


def make_definition(identifier: str, check_type: CheckType = CheckType.PING) -> CheckDefinition:
    return CheckDefinition(
        identifier=identifier,
        name=identifier.title(),
        check_type=check_type,
        target="192.168.1.1",
        interval_s=15,
        timeout_s=10,
    )


def test_runtime_app_renders_on_bootstrap_and_skips_identical_ticks():
    display = FakeDisplay()
    app = RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=display)

    first = app.tick(0.0)
    second = app.tick(1.0)

    assert first == "bootstrap"
    assert second == "none"
    assert len(display.frames) == 1


def test_runtime_app_render_once_returns_boot_logo_before_first_frame():
    app = RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=FakeDisplay())
    app.boot_logo_until_s = 5.0

    assert app.render_once(1.0) == "boot-logo"


def test_runtime_app_executes_due_checks_and_updates_state():
    display = FakeDisplay()
    definition = make_definition("router")

    def executor(check_definition, now_s):
        return CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(
                CheckObservation(
                    identifier=check_definition.identifier,
                    name=check_definition.name,
                    status=Status.OK,
                    details="reachable",
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(definitions=(definition,), executor=executor, display=display)

    reason = app.tick(0.0)

    assert reason == "bootstrap"
    assert app.state.checks[0].status == Status.OK
    assert display.frames[-1].rows[0].startswith("Router")


def test_runtime_app_executor_exception_replaces_previous_ok_state_on_display():
    display = FakeDisplay()
    definition = make_definition("router", check_type=CheckType.HTTP)
    calls = {"count": 0}

    def executor(check_definition, now_s):
        calls["count"] += 1
        if calls["count"] == 1:
            return CheckExecutionResult(
                source_identifier=check_definition.identifier,
                observations=(
                    CheckObservation(
                        identifier=check_definition.identifier,
                        name=check_definition.name,
                        status=Status.OK,
                        details="HTTP 200",
                        observed_at_s=now_s,
                    ),
                ),
            )
        raise OSError("network down")

    app = RuntimeApp(definitions=(definition,), executor=executor, display=display, page_interval_s=0)
    app.background_workers_enabled = False

    app.tick(0.0)
    app.last_started_at.clear()
    app.tick(1.0)

    assert app.state.checks[0].status == Status.DEG
    assert app.state.checks[0].details == "executor exception"
    assert app.get_registered_checks()[0]["status"] == "FAIL"


def test_runtime_app_applies_immediate_failure_thresholds_when_configured():
    display = FakeDisplay()
    definition = make_definition("router", check_type=CheckType.HTTP)

    def executor(check_definition, now_s):
        return CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(
                CheckObservation(
                    identifier=check_definition.identifier,
                    name=check_definition.name,
                    status=Status.FAIL,
                    details="timeout",
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(
        definitions=(definition,),
        executor=executor,
        display=display,
        page_interval_s=0,
        transition_thresholds=TransitionThresholds(failures_to_degraded=1, failures_to_failed=1),
    )

    app.tick(0.0)

    assert app.state.checks[0].status == Status.FAIL


def test_runtime_app_starts_with_unknown_rows_before_the_first_check_runs():
    display = FakeDisplay()
    definition = make_definition("router")
    app = RuntimeApp(definitions=(definition,), executor=lambda definition, now_s: None, display=display)

    reason = app.render_once(0.0)

    assert reason == "bootstrap"
    assert display.frames[-1].rows[0].startswith("Router")
    assert display.frames[-1].rows[0].endswith("?")


def test_runtime_app_renders_when_shift_changes_without_other_state_changes():
    display = FakeDisplay()
    app = RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=display)

    app.tick(0.0)
    reason = app.tick(30.0)

    assert reason == "shift"
    assert len(display.frames) == 2
    assert display.frames[-1].shift_offset == (1, 0)


def test_runtime_app_rotates_pages_when_interval_elapsed():
    display = FakeDisplay()
    definitions = tuple(make_definition(identifier) for identifier in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india"))

    def executor(definition, now_s):
        return CheckExecutionResult(
            source_identifier=definition.identifier,
            observations=(
                CheckObservation(
                    identifier=definition.identifier,
                    name=definition.name,
                    status=Status.OK,
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(
        definitions=definitions,
        executor=executor,
        display=display,
        page_interval_s=15,
    )

    app.tick(0.0)
    reason = app.tick(15.0)

    assert reason == "state"
    assert app.state.page_index == 1
    assert app.state.selected_id == "india"
    assert display.frames[-1].rows[0].startswith("India")


def test_runtime_app_rotates_over_filtered_compact_pages_only():
    display = FakeDisplay()
    definitions = tuple(make_definition(identifier) for identifier in ("alpha", "bravo", "charlie", "delta", "echo"))
    statuses = {
        "alpha": Status.OK,
        "bravo": Status.FAIL,
        "charlie": Status.OK,
        "delta": Status.FAIL,
        "echo": Status.FAIL,
    }

    def executor(definition, now_s):
        return CheckExecutionResult(
            source_identifier=definition.identifier,
            observations=(
                CheckObservation(
                    identifier=definition.identifier,
                    name=definition.name,
                    status=statuses[definition.identifier],
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(
        definitions=definitions,
        executor=executor,
        display=display,
        page_interval_s=15,
        display_mode=DisplayMode.COMPACT,
        overview_columns=1,
        page_size=2,
    )
    app.background_workers_enabled = False

    app.tick(0.0)
    app.tick(15.0)

    assert app.state.page_index == 1
    assert display.frames[-1].rows[0].startswith("Echo")


def test_runtime_app_enters_detail_on_button_b_and_returns_to_overview():
    display = FakeDisplay()
    definition = make_definition("router")

    def executor(check_definition, now_s):
        return CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(
                CheckObservation(
                    identifier=check_definition.identifier,
                    name=check_definition.name,
                    status=Status.FAIL,
                    details="executor error",
                    observed_at_s=now_s,
                ),
            ),
            diagnostics=(DiagnosticEvent(code="wifi", message="down"),),
        )

    app = RuntimeApp(definitions=(definition,), executor=executor, display=display)

    reason = app.tick(0.0)

    assert reason == "bootstrap"
    assert app.state.mode.value == "overview"
    assert app.state.diagnostics == ("WIFI down",)

    next_reason = app.tick(1.0, button_events=(ButtonEvent(button=Button.B, held_ms=30),))

    assert next_reason == "state"
    assert app.state.mode.value == "detail"
    assert display.frames[-1].rows[0].startswith("Router")
    assert display.frames[-1].rows[1].startswith("STATUS:")

    final_reason = app.tick(2.0, button_events=(ButtonEvent(button=Button.B, held_ms=30),))

    assert final_reason == "state"
    assert app.state.mode.value == "overview"


def test_runtime_app_moves_selection_with_button_a():
    display = FakeDisplay()
    definitions = (make_definition("alpha"), make_definition("bravo"))

    def executor(check_definition, now_s):
        return CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(
                CheckObservation(
                    identifier=check_definition.identifier,
                    name=check_definition.name,
                    status=Status.OK,
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(definitions=definitions, executor=executor, display=display, page_interval_s=0)

    app.tick(0.0)
    reason = app.tick(1.0, button_events=(ButtonEvent(button=Button.A, held_ms=30),))

    assert reason == "state"
    assert app.state.selected_id == "bravo"
    assert display.frames[-1].inverted_row == 1


def test_apply_button_events_sets_and_clears_press_feedback_for_no_op_button_press():
    display = FakeDisplay()
    definition = make_definition("router")

    app = RuntimeApp(
        definitions=(definition,),
        executor=lambda check_definition, now_s: CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(
                CheckObservation(
                    identifier=check_definition.identifier,
                    name=check_definition.name,
                    status=Status.OK,
                    observed_at_s=now_s,
                ),
            ),
        ),
        display=display,
        page_interval_s=0,
        probe_time_provider=lambda: 2.0,
    )

    app.tick(0.0)
    unchanged_state = app.state

    app._apply_button_events((ButtonEvent(button=Button.A, held_ms=30),))

    assert app.state == unchanged_state
    assert app.press_feedback_until_s == pytest.approx(2.15)
    assert app._press_feedback_text(2.0) == "BTN A"
    assert app._press_feedback_text(2.2) is None


def test_runtime_app_validates_page_interval_and_uses_button_reader_when_present():
    with pytest.raises(ValueError, match="must not be negative"):
        RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=FakeDisplay(), page_interval_s=-1)

    class FakeButtonReader:
        def poll(self):
            return (ButtonEvent(button=Button.B, held_ms=30),)

    display = FakeDisplay()
    definition = make_definition("router")
    app = RuntimeApp(
        definitions=(definition,),
        executor=lambda check_definition, now_s: CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(
                CheckObservation(
                    identifier=check_definition.identifier,
                    name=check_definition.name,
                    status=Status.OK,
                    observed_at_s=now_s,
                ),
            ),
        ),
        display=display,
        button_reader=FakeButtonReader(),
    )

    reason = app.tick(0.0)

    assert reason == "bootstrap"
    assert app.state.mode.value == "detail"
    assert display.frames[-1].rows[0].startswith("Router")


def test_runtime_app_helper_functions_cover_platform_fallbacks(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(
        runtime_app_module,
        "time",
        SimpleNamespace(
            sleep_ms=lambda value: sleep_calls.append(("ms", value)),
            sleep=lambda value: sleep_calls.append(("s", value)),
            ticks_ms=lambda: 1234,
            perf_counter=lambda: 9.25,
        ),
    )

    runtime_app_module._sleep_ms(0)
    runtime_app_module._sleep_ms(5)

    assert sleep_calls == [("ms", 5)]
    assert runtime_app_module._monotonic_now_s() == 1.234

    monkeypatch.setattr(
        runtime_app_module,
        "time",
        SimpleNamespace(
            sleep=lambda value: sleep_calls.append(("fallback", value)),
            perf_counter=lambda: 9.25,
        ),
    )

    runtime_app_module._sleep_ms(25)

    assert sleep_calls[-1] == ("fallback", 0.025)
    assert runtime_app_module._monotonic_now_s() == 9.25


def test_runtime_app_thread_helpers_cover_success_and_failure_paths(monkeypatch):
    class FakeLock:
        def __init__(self):
            self.acquired = False

        def acquire(self):
            self.acquired = True

    started = []

    class FakeThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append((self.args, self.daemon))

    monkeypatch.setattr(runtime_app_module, "threading", SimpleNamespace(Lock=FakeLock, Thread=FakeThread))
    monkeypatch.setattr(runtime_app_module, "_thread", None)

    lock = runtime_app_module._allocate_lock()

    assert isinstance(lock, FakeLock)
    assert runtime_app_module._lock_context(lock) is True
    assert lock.acquired is True
    assert runtime_app_module._lock_context(None) is False
    assert runtime_app_module._start_background_thread(lambda value: None, ("job",)) is True
    assert started == [(("job",), True)]

    class FailingThread(FakeThread):
        def start(self):
            raise RuntimeError("thread boom")

    monkeypatch.setattr(runtime_app_module, "threading", SimpleNamespace(Lock=FakeLock, Thread=FailingThread))

    assert runtime_app_module._start_background_thread(lambda value: None, ("job",)) is False

    thread_calls = []
    monkeypatch.setattr(runtime_app_module, "threading", None)
    monkeypatch.setattr(
        runtime_app_module,
        "_thread",
        SimpleNamespace(
            allocate_lock=FakeLock,
            start_new_thread=lambda target, args: thread_calls.append(args),
        ),
    )

    assert isinstance(runtime_app_module._allocate_lock(), FakeLock)
    assert runtime_app_module._start_background_thread(lambda value: None, ("fallback",)) is True
    assert thread_calls == [("fallback",)]

    monkeypatch.setattr(
        runtime_app_module,
        "_thread",
        SimpleNamespace(
            allocate_lock=FakeLock,
            start_new_thread=lambda target, args: (_ for _ in ()).throw(RuntimeError("_thread boom")),
        ),
    )

    assert runtime_app_module._start_background_thread(lambda value: None, ("fallback",)) is False

    monkeypatch.setattr(runtime_app_module, "_thread", None)

    assert runtime_app_module._start_background_thread(lambda value: None, ("fallback",)) is False
    assert runtime_app_module._allocate_lock() is None


def test_runtime_app_accepts_plain_string_button_events():
    display = FakeDisplay()
    app = RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=display)

    reason = app.tick(0.0, button_events=(ButtonEvent(button="B", held_ms=30),))

    assert reason == "bootstrap"
    assert app.state.mode.value == "detail"


def test_runtime_app_record_result_falls_back_to_first_observation_and_warns_for_degraded_status():
    definition = make_definition("router")
    app = RuntimeApp(definitions=(definition,), executor=lambda definition, now_s: None, display=FakeDisplay(), page_interval_s=0)

    app._record_result(
        definition,
        CheckExecutionResult(
            source_identifier="service-snapshot",
            observations=(
                CheckObservation(
                    identifier="service:router",
                    name="Router",
                    status=Status.DEG,
                    details="slow",
                    latency_ms=12.0,
                    observed_at_s=5.0,
                ),
            ),
        ),
        duration_ms=20.0,
    )

    registered = app.get_registered_checks()[0]

    assert registered["status"] == "DEG"
    assert registered["details"] == "slow"
    assert registered["latency_ms"] == 12.0
    assert any(line.startswith("[WARN][CHECK] failure") and "detail=slow" in line for line in app.get_logs())


def test_runtime_app_network_operation_helpers_cover_sync_async_and_guard_paths(monkeypatch):
    app = RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=FakeDisplay(), page_interval_s=0)
    app.config = {"wifi": {"ssid": "Office"}}
    app.now_provider = lambda: 10.0
    captured_snapshots = []
    app._capture_memory_snapshot = lambda label, now_s=None: captured_snapshots.append((label, now_s))

    app._start_network_operation(reconnect=False, now_s=0.0)
    assert app.network_operation_result is None

    app.network_operation_inflight = True
    app.wifi_connector = lambda config: ()
    app._start_network_operation(reconnect=False, now_s=1.0)
    app.network_operation_inflight = False

    app.wifi_connector = lambda config: (DiagnosticEvent(code="WIFI", message="connected"),)
    app._start_network_operation(reconnect=False, now_s=2.0)
    assert app.network_operation_result["ok"] is True
    app._drain_network_operation()

    assert app.network_state["last_error"] == "connected"
    assert captured_snapshots[-1] == ("reconnect", 10.0)

    app.wifi_connector = lambda config: (_ for _ in ()).throw(RuntimeError("wifi down"))
    app._start_network_operation(reconnect=False, now_s=3.0)
    assert app.network_operation_result["ok"] is False
    app._drain_network_operation()

    assert app.get_errors(limit=1)[0]["scope"] == "network"
    assert app.network_state["last_error"] == "wifi down"

    class FakeLock:
        def __init__(self):
            self.depth = 0

        def acquire(self):
            self.depth += 1

        def release(self):
            self.depth -= 1

    app.background_workers_enabled = True
    app.background_lock = FakeLock()
    app.wifi_reconnector = lambda config: ()

    def run_immediately(target, args):
        target(*args)
        return True

    monkeypatch.setattr(runtime_app_module, "_start_background_thread", run_immediately)

    app._start_network_operation(reconnect=True, now_s=4.0)

    assert app.network_operation_result["ok"] is True
    app._drain_network_operation()
    assert app.network_operation_result is None


def test_runtime_app_maybe_reconnect_network_respects_connectivity_inflight_and_interval_guards():
    app = RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=FakeDisplay(), page_interval_s=0)
    app.config = {"wifi": {"ssid": "Office"}}
    app.wifi_reconnector = lambda config: ()
    events = []
    app._drain_network_operation = lambda: events.append("drain")
    app._start_network_operation = lambda reconnect, now_s: events.append(("start", reconnect, now_s))

    app.network_state_reader = lambda config: {"connected": True}
    app._maybe_reconnect_network(1.0)

    app.network_state_reader = lambda config: {"connected": False}
    app.network_state["connected"] = False
    app.network_operation_inflight = True
    app._maybe_reconnect_network(2.0)

    app.network_operation_inflight = False
    app.last_network_reconnect_attempt_s = 0.0
    app.network_reconnect_interval_s = 15.0
    app._maybe_reconnect_network(5.0)

    app._maybe_reconnect_network(20.0)

    assert events == ["drain", "drain", "drain", "drain", ("start", True, 20.0), "drain"]


def test_runtime_app_background_worker_queue_controls_and_reset_paths(monkeypatch):
    definition = make_definition("router")

    def executor(check_definition, now_s):
        return CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(
                CheckObservation(
                    identifier=check_definition.identifier,
                    name=check_definition.name,
                    status=Status.OK,
                    details="reachable",
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(definitions=(definition,), executor=executor, display=FakeDisplay(), page_interval_s=0)
    app.background_workers_enabled = True
    app.background_lock = runtime_app_module._allocate_lock()

    monkeypatch.setattr(runtime_app_module, "_start_background_thread", lambda target, args: False)

    app._queue_check(definition, 1.0, manual=True)

    assert app.active_workers == set()
    assert app.inflight_check_ids == set()
    assert app.pending_checks_by_worker == {}
    assert app.state.checks[0].status == Status.OK

    worker_key = app._worker_key(definition)
    app.pending_checks_by_worker[worker_key] = [runtime_app_module.PendingCheckRun(definition=definition, requested_at_s=2.0)]
    app.active_workers.add(worker_key)
    app.inflight_check_ids.add(definition.identifier)

    monkeypatch.setattr(
        app,
        "_execute_check_once",
        lambda definition, now_s, manual=False: runtime_app_module.CompletedCheckRun(
            definition=definition,
            observed_at_s=now_s,
            duration_ms=1.0,
            manual=manual,
            result=executor(definition, now_s),
        ),
    )

    app._background_worker(worker_key)

    assert len(app.completed_checks) == 1
    assert worker_key not in app.active_workers
    assert definition.identifier not in app.inflight_check_ids

    app._drain_completed_checks()
    assert app.state.checks[0].status == Status.OK

    app.last_started_at[definition.identifier] = 3.0
    app.last_completed_at_by_host[definition.identifier] = 3.0
    app.pending_checks_by_worker[worker_key] = [runtime_app_module.PendingCheckRun(definition=definition, requested_at_s=4.0)]
    app.active_workers.add(worker_key)
    app.inflight_check_ids.add(definition.identifier)
    app.pending_status_updates[definition.identifier] = {"status": "OK", "observed_at_s": 4.0}
    app.completed_checks.append(
        runtime_app_module.CompletedCheckRun(definition=definition, observed_at_s=4.0, duration_ms=1.0, result=executor(definition, 4.0))
    )

    snapshot = app.reset_runtime_state()

    assert snapshot["registered_checks"][0]["status"] == "?"
    assert app.pending_checks_by_worker == {}
    assert app.completed_checks == []
    assert app.active_workers == set()


def test_runtime_app_serializes_different_hosts_by_default():
    first = CheckDefinition(identifier="router", name="Router", check_type=CheckType.PING, target="router.local")
    second = CheckDefinition(identifier="phone", name="Phone", check_type=CheckType.PING, target="phone.local")
    app = RuntimeApp(definitions=(first, second), executor=lambda definition, now_s: None, display=FakeDisplay(), page_interval_s=0)

    assert app._worker_key(first) == app._worker_key(second)


def test_runtime_app_can_opt_into_cross_host_parallel_workers():
    first = CheckDefinition(identifier="router", name="Router", check_type=CheckType.PING, target="router.local")
    second = CheckDefinition(identifier="phone", name="Phone", check_type=CheckType.PING, target="phone.local")
    app = RuntimeApp(
        definitions=(first, second),
        executor=lambda definition, now_s: None,
        display=FakeDisplay(),
        page_interval_s=0,
        probe_scheduling=ProbeSchedulingPolicy(allow_concurrent_hosts=True, allow_concurrent_same_host=False, same_host_backoff_ms=250),
    )

    assert app._worker_key(first) != app._worker_key(second)


def test_runtime_app_queues_probe_traces_from_background_workers_and_drains_them():
    definition = make_definition("router", check_type=CheckType.HTTP)
    app = RuntimeApp(definitions=(definition,), executor=lambda definition, now_s: None, display=FakeDisplay(), page_interval_s=0)

    class FakeLock:
        def __init__(self):
            self.depth = 0

        def acquire(self):
            self.depth += 1

        def release(self):
            self.depth -= 1

    app.background_workers_enabled = True
    app.background_lock = FakeLock()

    app.emit_probe_trace(definition, "socket-open", {"stage": "connect", "target": "192.0.2.1:80"})

    assert len(app.pending_probe_traces) == 1
    assert not any("[INFO][PROBE]" in line for line in app.get_logs())

    app._drain_probe_traces()

    assert app.pending_probe_traces == []
    assert any(line.startswith("[INFO][PROBE] socket-open") and "id=router" in line for line in app.get_logs())

def test_runtime_app_manual_control_overlay_feedback_and_propagation_paths():
    definition = make_definition("router")
    app = RuntimeApp(definitions=(definition,), executor=lambda definition, now_s: None, display=FakeDisplay(), page_interval_s=0, page_size=2)
    app.config = {"wifi": {"ssid": "Office"}}
    app.now_provider = lambda: 4.0
    snapshots = []
    queued = []
    runs = []
    app._capture_memory_snapshot = lambda label, now_s=None: snapshots.append((label, now_s))
    app._queue_check = lambda definition, now_s, manual=False: queued.append((definition.identifier, now_s, manual))
    app._run_check = lambda definition, now_s, manual=False: runs.append((definition.identifier, now_s, manual))
    app.wifi_connector = lambda config: (DiagnosticEvent(code="WIFI", message="joined"),)

    assert app.run_all_checks(5.0)[0]["status"] == "?"
    assert runs == [("router", 5.0, True)]
    assert snapshots[0] == ("manual-run", 5.0)
    assert app.request_refresh(6.0) == 6.0
    assert queued == [("router", 6.0, True)]

    network_state = app.connect_network(activate_diagnostics=True)

    assert network_state["last_error"] == "joined"
    assert snapshots[-1] == ("connect", 4.0)
    assert app.state.diagnostics == ("WIFI joined",)

    app._set_feedback("HELLO", 5.0, duration_s=1.0)
    app.debug_mode = True
    app.last_cycle_ms = 12.4
    app.network_state["connected"] = True
    app.state = replace(app.state, checks=(replace(app.state.checks[0], last_update_s=3.0),), page_index=99)
    app._track_status_transition("router", None, "FAIL", 4.0)
    app.pending_status_updates["stale"] = {"status": "?", "observed_at_s": None}

    decorated = app._decorate_frame(Frame(rows=(" " * app.state.row_width, " " * app.state.row_width)), now_s=5.0)
    app._log_display_propagation(5.0, "state")
    app._apply_page_rotation(0.0)

    assert decorated.rows[0].startswith("UPD:2s LP:12")
    assert decorated.rows[1].strip() == "HELLO"
    assert app._feedback_text(7.1) is None
    assert app.pending_status_updates == {}
    assert app.state.page_index == 0
    assert any(line.startswith("[INFO][DISP] propagation") for line in app.get_logs())


def test_runtime_app_button_a_probe_slot_and_network_control_edge_paths(monkeypatch):
    definition = make_definition("router", check_type=CheckType.HTTP)
    second_definition = replace(definition, identifier="switch", name="Switch", target="192.168.1.2")
    sleep_calls = []
    display = FakeDisplay()

    def executor(check_definition, now_s):
        return CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(
                CheckObservation(
                    identifier=check_definition.identifier,
                    name=check_definition.name,
                    status=Status.OK,
                    details="reachable",
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(
        definitions=(definition, second_definition),
        executor=executor,
        display=display,
        page_interval_s=0,
        sleep_ms=lambda value: sleep_calls.append(value),
        probe_time_provider=lambda: 10.0,
    )
    app.now_provider = lambda: 10.0

    reason = app.tick(0.0, button_events=(ButtonEvent(button=Button.A, held_ms=30),))

    assert reason == "bootstrap"
    assert app.state.selected_id == "switch"
    assert display.frames[-1].inverted_row == 1
    assert any(line.startswith("[INFO][BTN] action") and "selected=switch" in line for line in app.get_logs())

    app.inject_diagnostics((), activate=False)

    background_lock = runtime_app_module._allocate_lock()
    app.background_workers_enabled = True
    app.background_lock = background_lock
    app.last_completed_at_by_host["192.168.1.1"] = 9.9
    app._wait_for_probe_slot(definition)

    assert sleep_calls == [150]

    app.last_completed_at_by_host.clear()
    app._mark_probe_complete(make_definition("ping"))
    app._mark_probe_complete(definition)

    assert app.last_completed_at_by_host["192.168.1.1"] == 10.0

    app.background_workers_enabled = False
    app.wifi_reconnector = lambda config: (DiagnosticEvent(code="WIFI", message="restored"),)
    app.wifi_connector = None
    app.config = {"wifi": {"ssid": "Office"}}
    app._capture_memory_snapshot = lambda label, now_s=None: None

    network_state = app.reconnect_network()

    assert network_state["last_error"] == "restored"

    with pytest.raises(RuntimeError, match="wifi connect is not configured"):
        app.connect_network()

    app.wifi_connector = lambda config: (_ for _ in ()).throw(RuntimeError("connect boom"))

    with pytest.raises(RuntimeError, match="connect boom"):
        app.connect_network()


def test_runtime_app_injects_diagnostics_without_forcing_mode_and_skips_rotation_when_disabled():
    display = FakeDisplay()
    app = RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=display, page_interval_s=0)

    app.inject_diagnostics((DiagnosticEvent(code="wifi", message="down"),), activate=False)
    reason = app.tick(0.0)

    assert app.state.mode.value == "overview"
    assert app.state.diagnostics == ("WIFI down",)
    assert reason == "bootstrap"


def test_runtime_app_backs_off_after_display_failure_and_recovers_on_retry():
    class FlakyDisplay:
        def __init__(self):
            self.calls = 0
            self.frames = []

        def draw_frame(self, frame):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("spi write failed")
            self.frames.append(frame)

    display = FlakyDisplay()
    app = RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=display, page_interval_s=0)

    first_reason = app.tick(0.0)
    second_reason = app.tick(0.5)
    third_reason = app.tick(1.0)

    assert first_reason == "bootstrap"
    assert second_reason == "bootstrap"
    assert third_reason == "bootstrap"
    assert display.calls == 2
    assert len(display.frames) == 1
    assert app.display_failure_count == 0
    assert app.display_retry_at_s is None
    assert app.state.mode.value == "diagnostics"
    assert any(error["scope"] == "display" for error in app.get_errors())


def test_runtime_app_service_result_and_display_helpers_cover_remaining_branches():
    definition = make_definition("svc", check_type=CheckType.SERVICE)
    app = RuntimeApp(definitions=(definition,), executor=lambda definition, now_s: None, display=FakeDisplay(), page_interval_s=0)

    app.configure_observability(config="bad-config", now_provider=lambda: 2.0, memory_snapshot_interval_s=0.25)
    app._refresh_network_state(connect_duration_ms=12.3)
    app._reset_display_failure_state()
    app._record_result(CheckDefinition(identifier="svc", name="Svc", check_type=CheckType.SERVICE, target="http://service"), CheckExecutionResult(source_identifier="svc", observations=(), replace_source=True), 1.5)

    registered = app.get_registered_checks()[0]

    assert registered["status"] == "OK"
    assert registered["details"] == "loaded 0 checks"
    assert app._display_retry_delay_s() == 0.0
    assert app.get_network_state_snapshot()["last_error"] == ""


def test_runtime_app_waits_between_due_checks_for_the_same_host_by_default():
    display = FakeDisplay()
    definitions = (
        CheckDefinition(identifier="http", name="Http", check_type=CheckType.HTTP, target="http://router.local/health"),
        CheckDefinition(identifier="ftp", name="Ftp", check_type=CheckType.FTP, target="router.local"),
        CheckDefinition(identifier="other", name="Other", check_type=CheckType.HTTP, target="http://nas.local/health"),
    )
    calls = []
    sleep_calls = []
    probe_clock = {"now": 0.0}

    def executor(definition, now_s):
        calls.append(definition.identifier)
        return CheckExecutionResult(
            source_identifier=definition.identifier,
            observations=(
                CheckObservation(
                    identifier=definition.identifier,
                    name=definition.name,
                    status=Status.OK,
                    observed_at_s=now_s,
                ),
            ),
        )

    def sleep_ms(value):
        sleep_calls.append(value)
        probe_clock["now"] += value / 1000.0

    app = RuntimeApp(
        definitions=definitions,
        executor=executor,
        display=display,
        page_interval_s=0,
        sleep_ms=sleep_ms,
        probe_time_provider=lambda: probe_clock["now"],
    )

    app.tick(0.0)
    for _ in range(20):
        if len(calls) == 3:
            break
        app.tick(0.05)

    assert set(calls) == {"ftp", "http", "other"}
    assert calls.index("http") < calls.index("ftp")
    assert sleep_calls == [250]


def test_runtime_app_can_disable_same_host_probe_backoff():
    display = FakeDisplay()
    definitions = (
        CheckDefinition(identifier="http", name="Http", check_type=CheckType.HTTP, target="http://router.local/health"),
        CheckDefinition(identifier="ftp", name="Ftp", check_type=CheckType.FTP, target="router.local"),
    )
    sleep_calls = []

    def executor(definition, now_s):
        return CheckExecutionResult(
            source_identifier=definition.identifier,
            observations=(
                CheckObservation(
                    identifier=definition.identifier,
                    name=definition.name,
                    status=Status.OK,
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(
        definitions=definitions,
        executor=executor,
        display=display,
        page_interval_s=0,
        probe_scheduling=ProbeSchedulingPolicy(allow_concurrent_same_host=True, same_host_backoff_ms=250),
        sleep_ms=lambda value: sleep_calls.append(value),
        probe_time_provider=lambda: 0.0,
    )

    app.tick(0.0)
    for _ in range(20):
        if app.state.checks[0].status == Status.OK and app.state.checks[1].status == Status.OK:
            break
        app.tick(0.05)

    assert sleep_calls == []


def test_runtime_app_spaces_same_host_requests_from_previous_probe_completion():
    display = FakeDisplay()
    definitions = (
        CheckDefinition(identifier="ftp", name="Ftp", check_type=CheckType.FTP, target="router.local"),
        CheckDefinition(identifier="http", name="Http", check_type=CheckType.HTTP, target="http://router.local/health"),
    )
    sleep_calls = []
    probe_clock = {"now": 100.0}
    started = []

    def sleep_ms(value):
        sleep_calls.append(value)
        probe_clock["now"] += value / 1000.0

    def executor(definition, now_s):
        started.append((definition.identifier, probe_clock["now"]))
        probe_clock["now"] += 0.1
        return CheckExecutionResult(
            source_identifier=definition.identifier,
            observations=(
                CheckObservation(
                    identifier=definition.identifier,
                    name=definition.name,
                    status=Status.OK,
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(
        definitions=definitions,
        executor=executor,
        display=display,
        page_interval_s=0,
        sleep_ms=sleep_ms,
        probe_time_provider=lambda: probe_clock["now"],
    )

    app.tick(0.0)
    for _ in range(20):
        if len(started) == 2:
            break
        app.tick(0.05)

    assert [item[0] for item in started] == ["http", "ftp"]
    assert started[1][1] - started[0][1] == pytest.approx(0.35)
    assert sleep_calls == [250]
