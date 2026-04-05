from vivipi.core.execution import CheckExecutionResult
from vivipi.core.input import Button
from vivipi.core.models import CheckDefinition, CheckObservation, CheckType, DiagnosticEvent, Status
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


def test_runtime_app_renders_when_shift_changes_without_other_state_changes():
    display = FakeDisplay()
    app = RuntimeApp(definitions=(), executor=lambda definition, now_s: None, display=display)

    app.tick(0.0)
    reason = app.tick(30.0)

    assert reason == "shift"
    assert len(display.frames) == 2
    assert display.frames[-1].shift_offset == (1, 0)


def test_runtime_app_applies_button_events_and_activates_diagnostics():
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
    assert app.state.mode.value == "diagnostics"
    assert app.state.diagnostics == ("WIFI down",)

    next_reason = app.tick(1.0, button_events=(ButtonEvent(button=Button.B, held_ms=30),))

    assert next_reason == "state"
    assert app.state.mode.value == "overview"