"""Thin button polling adapter for ViviPi."""

try:
    import utime as time
    from machine import Pin
except ImportError:  # pragma: no cover - imported on-device
    time = None
    Pin = None

from vivipi.core.input import Button, InputController
from vivipi.runtime import ButtonEvent


def _pin_number(value):
    return int(str(value).replace("GP", ""))


def _button_name(button) -> str:
    return str(getattr(button, "value", button))


def _button_fields(state, button, **extra_fields):
    fields = [
        f"button={_button_name(button)}",
        f"pin={state['pin_name']}",
        f"pull={state['pull']}",
        f"idle={state['idle_value']}",
        f"raw={state['raw_value']}",
        f"stable={state['stable_value']}",
    ]
    for key, value in extra_fields.items():
        fields.append(f"{key}={value}")
    return tuple(fields)


def probe_pin_states(pin_numbers, pull="up"):
    if Pin is None:  # pragma: no cover - imported on-device
        raise RuntimeError("machine.Pin is required on device")
    pull_constant = Pin.PULL_DOWN if pull == "down" else Pin.PULL_UP
    values = {}
    for pin_number in pin_numbers:
        values[f"GP{pin_number}"] = Pin(pin_number, Pin.IN, pull_constant).value()
    return values


class ButtonReader:
    def __init__(self, button_config, input_controller: InputController):
        if time is None or Pin is None:  # pragma: no cover - imported on-device
            raise RuntimeError("machine and utime modules are required on device")

        self.input_controller = input_controller
        self.logger = None
        self.states = {}

        for button, key in ((Button.A, "a"), (Button.B, "b")):
            entry = self._normalize_button_entry(button_config[key])
            pin_number = _pin_number(entry["pin"])
            bias = entry["pull"]
            pin = Pin(pin_number, Pin.IN, self._pull_constant(bias))
            raw_value = pin.value()
            idle_value = 1 if bias == "up" else 0
            self.states[button] = {
                "pin": pin,
                "pin_name": entry["pin"],
                "pull": bias,
                "idle_value": idle_value,
                "raw_value": raw_value,
                "stable_value": raw_value,
                "raw_changed_ms": time.ticks_ms(),
                "pressed_since_ms": None,
                "emitted_steps": 0,
            }
            self._log(
                "info",
                "init",
                _button_fields(self.states[button], button, sample=raw_value),
            )

    def bind_logger(self, logger):
        self.logger = logger
        for button, state in self.states.items():
            self._log(
                "info",
                "bind",
                _button_fields(state, button),
            )

    def _normalize_button_entry(self, value):
        if isinstance(value, str):
            return {"pin": value, "pull": "up"}
        if isinstance(value, dict):
            pin_value = value.get("pin", value.get("gpio"))
            if not isinstance(pin_value, str) or not pin_value.strip():
                raise ValueError("button config must include a GP pin")
            pull_value = str(value.get("pull", "up")).strip().lower()
            if pull_value not in {"up", "down"}:
                raise ValueError("button pull must be up or down")
            return {"pin": pin_value.strip(), "pull": pull_value}
        raise ValueError("button config must be a GP pin or mapping")

    def _pull_constant(self, bias):
        return Pin.PULL_DOWN if bias == "down" else Pin.PULL_UP

    def _log(self, method, message, fields=()):
        if self.logger is None:
            return
        log_method = getattr(self.logger, method, None)
        if log_method is None:
            return
        log_method("BTN", message, tuple(str(field) for field in fields))

    def poll(self):
        now_ms = time.ticks_ms()
        events = []
        for button, state in self.states.items():
            raw_value = state["pin"].value()
            if raw_value != state["raw_value"]:
                edge = "rising" if raw_value > state["raw_value"] else "falling"
                state["raw_value"] = raw_value
                state["raw_changed_ms"] = now_ms
                self._log(
                    "info",
                    "raw",
                    _button_fields(state, button, edge=edge),
                )

            if raw_value != state["stable_value"]:
                if time.ticks_diff(now_ms, state["raw_changed_ms"]) < self.input_controller.debounce_ms:
                    continue

                previous_value = state["stable_value"]
                state["stable_value"] = raw_value
                pressed = raw_value != state["idle_value"]
                edge = "rising" if raw_value > previous_value else "falling"
                if pressed:
                    state["pressed_since_ms"] = state["raw_changed_ms"]
                    state["emitted_steps"] = 0
                else:
                    state["pressed_since_ms"] = None
                    state["emitted_steps"] = 0
                self._log(
                    "info",
                    "debounced",
                    _button_fields(state, button, edge=edge, pressed=pressed),
                )
                self._log(
                    "info",
                    "press" if pressed else "release",
                    _button_fields(state, button, edge=edge, pressed=pressed),
                )

            if state["stable_value"] == state["idle_value"]:
                continue

            pressed_since_ms = state["pressed_since_ms"]
            if pressed_since_ms is None:
                pressed_since_ms = state["raw_changed_ms"]
                state["pressed_since_ms"] = pressed_since_ms

            held_ms = max(0, time.ticks_diff(now_ms, pressed_since_ms))
            step_count = self.input_controller._step_count(held_ms)
            if button != Button.A:
                step_count = min(step_count, 1)

            emitted_steps = state["emitted_steps"]
            if step_count <= emitted_steps:
                continue

            state["emitted_steps"] = step_count
            for _ in range(step_count - emitted_steps):
                events.append(ButtonEvent(button=button, held_ms=self.input_controller.debounce_ms))
                self._log(
                    "info",
                    "event",
                    _button_fields(state, button, held_ms=held_ms, step=state["emitted_steps"], pressed=True),
                )

        return tuple(events)

    def snapshot(self):
        snapshot = {}
        for button, state in self.states.items():
            raw_value = state["pin"].value()
            snapshot[_button_name(button)] = {
                "pin": state["pin_name"],
                "pull": state["pull"],
                "idle": state["idle_value"],
                "raw": raw_value,
                "stable": state["stable_value"],
                "pressed": raw_value != state["idle_value"],
            }
        return snapshot
