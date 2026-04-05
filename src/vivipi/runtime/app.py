from __future__ import annotations

from dataclasses import dataclass, replace

from vivipi.core import InputController, PixelShiftController, due_checks, integrate_observations, page_count, record_diagnostic_events, render_frame, render_reason, set_page_index
from vivipi.core.models import AppMode, AppState, CheckDefinition, DisplayMode
from vivipi.core.state import overview_checks


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
        page_interval_s: int = 15,
        page_size: int = 8,
        row_width: int = 16,
        display_mode: DisplayMode = DisplayMode.STANDARD,
        overview_columns: int = 1,
        column_separator: str = " ",
        version: str = "",
        build_time: str = "",
    ):
        if page_interval_s < 0:
            raise ValueError("page_interval_s must not be negative")
        self.definitions = tuple(definitions)
        self.executor = executor
        self.display = display
        self.button_reader = button_reader
        self.input_controller = input_controller or InputController()
        self.shift_controller = shift_controller or PixelShiftController()
        self.page_interval_s = page_interval_s
        self.last_started_at: dict[str, float] = {}
        self.state = AppState(
            page_size=page_size,
            row_width=row_width,
            display_mode=display_mode,
            overview_columns=overview_columns,
            column_separator=column_separator,
            version=version,
            build_time=build_time,
        )
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

    def _apply_page_rotation(self, now_s: float):
        normalized = set_page_index(self.state, self.state.page_index)
        if normalized != self.state:
            self.state = normalized

        if self.state.mode != AppMode.OVERVIEW:
            return

        total_pages = page_count(overview_checks(self.state), self.state.page_size * self.state.overview_columns)
        if total_pages <= 1 or self.page_interval_s == 0:
            return

        next_page = int(now_s // self.page_interval_s) % total_pages
        if next_page != self.state.page_index:
            self.state = set_page_index(self.state, next_page, select_visible=True)

    def tick(self, now_s: float, button_events: tuple[ButtonEvent, ...] | None = None) -> str:
        events = button_events
        if events is None:
            if self.button_reader is None:
                events = ()
            else:
                events = tuple(self.button_reader.poll())

        self._apply_button_events(events)
        self._run_due_checks(now_s)
        self._apply_page_rotation(now_s)
        self._apply_shift(now_s)

        reason = render_reason(self.last_rendered_state, self.state)
        if reason != "none":
            self.display.draw_frame(render_frame(self.state, now_s=now_s))
            self.last_rendered_state = self.state
        return reason