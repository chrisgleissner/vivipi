import pytest

from vivipi.core.execution import CheckExecutionResult
from vivipi.core.input import Button
from vivipi.core.models import CheckDefinition, CheckObservation, CheckType, DiagnosticEvent, DisplayMode, Status
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

    app.tick(0.0)
    app.tick(15.0)

    assert app.state.page_index == 1
    assert display.frames[-1].rows[0].startswith("Echo")


def test_runtime_app_applies_button_events_without_leaving_overview_for_check_diagnostics():
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


def test_runtime_app_validates_page_interval_and_uses_button_reader_when_present():
    with pytest.raises(ValueError, match="must not be negative"):
        RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=FakeDisplay(), page_interval_s=-1)

    class FakeButtonReader:
        def poll(self):
            return (ButtonEvent(button=Button.B, held_ms=30),)

    display = FakeDisplay()
    app = RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=display, button_reader=FakeButtonReader())

    reason = app.tick(0.0)

    assert reason == "bootstrap"
    assert app.state.mode.value == "detail"


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