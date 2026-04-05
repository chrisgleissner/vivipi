from __future__ import annotations

from dataclasses import dataclass, replace

from vivipi.core import InputController, PixelShiftController, due_checks, integrate_observations, record_diagnostic_events, render_frame, render_reason
from vivipi.core.models import AppState, CheckDefinition


@dataclass(frozen=True)
class ButtonEvent:
    button: object
    held_ms: int


class RuntimeApp:
    def __init__(
        self,
        definitions: tuple[CheckDefinition, ...],
        executor,
        display,
        button_reader=None,
        input_controller: InputController | None = None,
        shift_controller: PixelShiftController | None = None,
    ):
        self.definitions = tuple(definitions)
        self.executor = executor
        self.display = display
        self.button_reader = button_reader
        self.input_controller = input_controller or InputController()
        self.shift_controller = shift_controller or PixelShiftController()
        self.last_started_at: dict[str, float] = {}
        self.state = AppState()
        self.last_rendered_state: AppState | None = None

    def inject_diagnostics(self, events: tuple[object, ...], activate: bool = True):
        self.state = record_diagnostic_events(self.state, events, activate=activate)

    def _apply_button_events(self, button_events: tuple[ButtonEvent, ...]):
        state = self.state
        for event in button_events:
            state = self.input_controller.apply(state, event.button, held_ms=event.held_ms)
        self.state = state

    def _run_due_checks(self, now_s: float):
        for scheduled in due_checks(self.definitions, self.last_started_at, now_s):
            self.last_started_at[scheduled.definition.identifier] = now_s
            result = self.executor(scheduled.definition, now_s)
            self.state = integrate_observations(
                self.state,
                result.observations,
                replace_source_identifier=result.source_identifier if result.replace_source else None,
            )
            if result.diagnostics:
                self.state = record_diagnostic_events(self.state, result.diagnostics, activate=True)

    def _apply_shift(self, now_s: float):
        offset = self.shift_controller.offset_for_elapsed(now_s)
        if offset != self.state.shift_offset:
            self.state = replace(self.state, shift_offset=offset)

    def tick(self, now_s: float, button_events: tuple[ButtonEvent, ...] | None = None) -> str:
        events = button_events
        if events is None:
            if self.button_reader is None:
                events = ()
            else:
                events = tuple(self.button_reader.poll())

        self._apply_button_events(events)
        self._run_due_checks(now_s)
        self._apply_shift(now_s)

        reason = render_reason(self.last_rendered_state, self.state)
        if reason != "none":
            self.display.draw_frame(render_frame(self.state, now_s=now_s))
            self.last_rendered_state = self.state
        return reason