from __future__ import annotations

from dataclasses import replace
from enum import Enum

from vivipi.core.models import AppMode, AppState
from vivipi.core.state import enter_detail, exit_detail, move_selection


class Button(str, Enum):
    A = "A"
    B = "B"


class InputController:
    def __init__(self, debounce_ms: int = 30, repeat_ms: int = 500):
        if debounce_ms < 20 or debounce_ms > 50:
            raise ValueError("debounce_ms must be between 20 and 50")
        if repeat_ms < 1:
            raise ValueError("repeat_ms must be positive")
        self.debounce_ms = debounce_ms
        self.repeat_ms = repeat_ms

    def _accepted(self, held_ms: int) -> bool:
        return held_ms >= self.debounce_ms

    def _step_count(self, held_ms: int) -> int:
        if not self._accepted(held_ms):
            return 0
        return 1 + max(0, (held_ms - self.debounce_ms) // self.repeat_ms)

    def apply(self, state: AppState, button: Button, held_ms: int = 0) -> AppState:
        if not self._accepted(held_ms):
            return state

        if button == Button.A:
            return move_selection(state, self._step_count(held_ms))

        if button == Button.B:
            if state.mode == AppMode.DETAIL:
                return exit_detail(state)
            if state.mode == AppMode.DIAGNOSTICS:
                return replace(state, mode=AppMode.OVERVIEW)
            return enter_detail(state)

        return state
