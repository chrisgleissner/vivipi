import pytest

import vivipi.runtime.control as runtime_control
import vivipi.runtime.debug as runtime_debug
import vivipi.runtime.state as runtime_state
from vivipi.core.execution import CheckExecutionResult
from vivipi.core.models import CheckDefinition, CheckObservation, CheckType, Status
from vivipi.runtime import RuntimeApp


class FakeDisplay:
    def __init__(self):
        self.frames = []

    def draw_frame(self, frame):
        self.frames.append(frame)


@pytest.fixture(autouse=True)
def clear_bound_app():
    runtime_state.clear_bound_app()
    yield
    runtime_state.clear_bound_app()


def make_definition(identifier: str, check_type: CheckType = CheckType.PING) -> CheckDefinition:
    return CheckDefinition(
        identifier=identifier,
        name=identifier.title(),
        check_type=check_type,
        target="192.168.1.1",
        interval_s=15,
        timeout_s=10,
    )


def test_state_module_exposes_checks_metrics_network_logs_and_failures():
    definition = make_definition("router")

    def executor(check_definition, now_s):
        return CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(
                CheckObservation(
                    identifier=check_definition.identifier,
                    name=check_definition.name,
                    status=Status.FAIL,
                    details="timeout",
                    latency_ms=21.0,
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(definitions=(definition,), executor=executor, display=FakeDisplay(), page_interval_s=0)
    app.configure_observability(
        config={"wifi": {"ssid": "Office"}},
        now_provider=lambda: 12.5,
        network_state_reader=lambda config: {"ssid": "Office", "connected": True, "active": True, "ip_address": "192.0.2.20"},
    )
    app._refresh_network_state(connect_duration_ms=15.0)
    runtime_state.bind_app(app)

    app.tick(0.0)
    checks = runtime_state.get_checks()
    failures = runtime_state.get_failures()
    metrics = runtime_state.get_metrics()
    network_state = runtime_state.get_network_state()
    logs = runtime_state.get_logs()

    assert checks[0]["status"] == "DEG"
    assert checks[0]["last_error"] == "timeout"
    assert failures[0]["id"] == "router"
    assert metrics["checks"]["router"]["duration_ms"]["count"] == 1
    assert network_state["connected"] is True
    assert any(line.startswith("[INFO][NET] connected") for line in logs)
    assert any(line.startswith("[INFO][CHECK] run") and "status=FAIL" in line for line in logs)
    assert any(line.startswith("[ERROR][CHECK] failure") and "detail=timeout" in line for line in logs)


def test_control_surface_runs_checks_resets_state_and_restores_log_level():
    definition = make_definition("router")
    calls = []

    def executor(check_definition, now_s):
        calls.append((check_definition.identifier, now_s))
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
    app.configure_observability(
        config={"wifi": {"ssid": "Office"}},
        now_provider=lambda: 9.0,
        wifi_connector=lambda config: (),
        wifi_reconnector=lambda config: (),
        network_state_reader=lambda config: {"ssid": "Office", "connected": True, "active": True, "ip_address": "192.0.2.20"},
    )
    runtime_state.bind_app(app)

    assert runtime_control.set_log_level("WARN") == "WARN"
    assert runtime_control.set_debug_mode(True) is True

    runtime_control.run_all_checks(now_s=5.0)

    assert calls == [("router", 5.0)]
    assert app.logger.level.name == "DEBUG"
    assert runtime_control.reconnect_network()["reconnect_count"] == 1

    runtime_control.set_debug_mode(False)
    reset_snapshot = runtime_control.reset_state()

    assert app.logger.level.name == "WARN"
    assert reset_snapshot["registered_checks"][0]["status"] == "?"
    assert reset_snapshot["metrics"]["checks"]["router"]["duration_ms"]["count"] == 0


def test_runtime_app_captures_executor_exceptions_and_debug_helpers_expose_memory_and_gc():
    definition = make_definition("router")

    def executor(check_definition, now_s):
        raise RuntimeError("boom")

    app = RuntimeApp(definitions=(definition,), executor=executor, display=FakeDisplay(), page_interval_s=0)
    app.configure_observability(config={"wifi": {"ssid": "Office"}}, now_provider=lambda: 7.5)
    runtime_state.bind_app(app)

    reason = app.tick(0.0)
    errors = runtime_state.get_errors()
    memory_snapshot = runtime_debug.mem()
    gc_snapshot = runtime_debug.collect()

    assert reason == "bootstrap"
    assert errors[0]["type"] == "RuntimeError"
    assert app.get_registered_checks()[0]["status"] == "FAIL"
    assert memory_snapshot["label"] == "manual"
    assert gc_snapshot["duration_ms"] >= 0
    assert app.metrics.snapshot()["gc_collections"] == 1


def test_healthy_checks_emit_sampled_summary_logs_but_no_failure_detail_logs():
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
    app.configure_observability(config={"wifi": {"ssid": "Office"}}, now_provider=lambda: 1.0)

    app.tick(0.0)
    app.last_started_at.clear()
    app.tick(1.0)

    summary_logs = [line for line in app.get_logs() if line.startswith("[INFO][CHECK] run")]
    detail_logs = [line for line in app.get_logs() if line.startswith("[ERROR][CHECK] failure")]

    assert len(summary_logs) == 1
    assert detail_logs == []


def test_healthy_checks_emit_sampled_summary_logs_on_cadence_boundary():
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
    app.configure_observability(config={"wifi": {"ssid": "Office"}}, now_provider=lambda: 1.0)
    app.background_workers_enabled = False

    for index in range(10):
        app.last_started_at.clear()
        app.tick(float(index))

    summary_logs = [line for line in app.get_logs() if line.startswith("[INFO][CHECK] run")]

    assert len(summary_logs) == 2


def test_failed_health_checks_emit_extra_detail_logs_every_time():
    definition = make_definition("router")

    def executor(check_definition, now_s):
        return CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(
                CheckObservation(
                    identifier=check_definition.identifier,
                    name=check_definition.name,
                    status=Status.FAIL,
                    details="tcp timeout after SYN",
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(definitions=(definition,), executor=executor, display=FakeDisplay(), page_interval_s=0)
    app.configure_observability(config={"wifi": {"ssid": "Office"}}, now_provider=lambda: 1.0)
    app.background_workers_enabled = False

    app.tick(0.0)
    app.last_started_at.clear()
    app.tick(1.0)

    summary_logs = [line for line in app.get_logs() if line.startswith("[INFO][CHECK] run")]
    detail_logs = [line for line in app.get_logs() if line.startswith("[ERROR][CHECK] failure")]

    assert len(summary_logs) == 2
    assert len(detail_logs) == 2
    assert all("detail=tcp timeout afte…" in line for line in detail_logs)


def test_runtime_app_covers_service_snapshot_network_failures_and_control_error_paths():
    definition = make_definition("svc", check_type=CheckType.SERVICE)

    def executor(check_definition, now_s):
        return CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(),
            replace_source=True,
        )

    app = RuntimeApp(definitions=(definition,), executor=executor, display=FakeDisplay(), page_interval_s=0)
    app.configure_observability(
        config={"wifi": {"ssid": "Office"}},
        now_provider=lambda: 3.0,
        network_state_reader=lambda config: {"ssid": "Office", "connected": False, "active": True, "ip_address": None},
    )

    app.tick(0.0)
    app._refresh_network_state(last_error="connect fail", connect_duration_ms=9.0, reconnect=True)

    registered = app.get_registered_checks()[0]
    network_state = app.get_network_state_snapshot()

    assert registered["status"] == "OK"
    assert registered["details"] == "loaded 0 checks"
    assert network_state["last_error"] == "connect fail"
    assert network_state["reconnect_count"] == 1
    assert any(line.startswith("[WARN][NET] connect-failed") for line in app.get_logs())

    with pytest.raises(RuntimeError, match="not configured"):
        RuntimeApp(definitions=(definition,), executor=executor, display=FakeDisplay()).reconnect_network()


def test_runtime_app_covers_current_time_log_level_error_limit_and_network_exception_paths():
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

    assert app.current_time_s() is None
    assert app.set_log_level(30).name == "WARN"

    app.configure_observability(
        config={"wifi": {"ssid": "Office"}},
        now_provider=lambda: 4.0,
        wifi_reconnector=lambda config: (_ for _ in ()).throw(RuntimeError("wifi boom")),
    )

    with pytest.raises(RuntimeError, match="wifi boom"):
        app.reconnect_network()

    assert app.get_errors(limit=1)[0]["scope"] == "network"
    assert app.get_logs(limit=1)[0].startswith("[ERROR][ERR] exception")


def test_runtime_app_reconnects_before_running_due_checks_when_network_is_down():
    definition = make_definition("router")
    connected = {"value": False}
    reconnect_calls = []

    def executor(check_definition, now_s):
        return CheckExecutionResult(
            source_identifier=check_definition.identifier,
            observations=(
                CheckObservation(
                    identifier=check_definition.identifier,
                    name=check_definition.name,
                    status=Status.OK if connected["value"] else Status.FAIL,
                    details="reachable" if connected["value"] else "timeout",
                    observed_at_s=now_s,
                ),
            ),
        )

    app = RuntimeApp(definitions=(definition,), executor=executor, display=FakeDisplay(), page_interval_s=0)
    app.configure_observability(
        config={"wifi": {"ssid": "Office"}},
        now_provider=lambda: 0.0,
        wifi_reconnector=lambda config: reconnect_calls.append(config) or connected.__setitem__("value", True) or (),
        network_state_reader=lambda config: {
            "ssid": "Office",
            "connected": connected["value"],
            "active": True,
            "ip_address": "192.0.2.20" if connected["value"] else None,
        },
    )
    app.background_workers_enabled = False

    app.tick(0.0)

    assert len(reconnect_calls) == 1
    assert app.get_network_state_snapshot()["connected"] is True
    assert app.state.checks[0].status == Status.OK
