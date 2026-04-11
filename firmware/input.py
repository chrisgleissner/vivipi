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
            bias, raw_value = self._detect_bias(pin_number, entry["pull"])
            pin = Pin(pin_number, Pin.IN, self._pull_constant(bias))
            stable_value = pin.value()
            self.states[button] = {
                "pin": pin,
                "pin_name": entry["pin"],
                "pull": bias,
                "idle_value": stable_value,
                "raw_value": stable_value,
                "stable_value": stable_value,
                "raw_changed_ms": time.ticks_ms(),
            }
            self._log(
                "info",
                "init",
                (
                    f"button={_button_name(button)}",
                    f"pin={entry['pin']}",
                    f"pull={bias}",
                    f"idle={stable_value}",
                    f"sample={raw_value}",
                ),
            )

    def bind_logger(self, logger):
        self.logger = logger
        for button, state in self.states.items():
            self._log(
                "info",
                "bind",
                (
                    f"button={_button_name(button)}",
                    f"pin={state['pin_name']}",
                    f"pull={state['pull']}",
                    f"idle={state['idle_value']}",
                ),
            )

    def _normalize_button_entry(self, value):
        if isinstance(value, str):
            return {"pin": value, "pull": "auto"}
        if isinstance(value, dict):
            pin_value = value.get("pin", value.get("gpio"))
            if not isinstance(pin_value, str) or not pin_value.strip():
                raise ValueError("button config must include a GP pin")
            pull_value = str(value.get("pull", "auto")).strip().lower()
            if pull_value not in {"auto", "up", "down"}:
                raise ValueError("button pull must be auto, up, or down")
            return {"pin": pin_value.strip(), "pull": pull_value}
        raise ValueError("button config must be a GP pin or mapping")

    def _pull_constant(self, bias):
        return Pin.PULL_DOWN if bias == "down" else Pin.PULL_UP

    def _sample_with_pull(self, pin_number, bias):
        probe = Pin(pin_number, Pin.IN, self._pull_constant(bias))
        if hasattr(time, "sleep_ms"):
            time.sleep_ms(2)
        return probe.value()

    def _detect_bias(self, pin_number, requested_pull):
        if requested_pull in {"up", "down"}:
            return requested_pull, self._sample_with_pull(pin_number, requested_pull)

        up_value = self._sample_with_pull(pin_number, "up")
        down_value = self._sample_with_pull(pin_number, "down")
        if up_value == down_value == 1:
            return "up", up_value
        if up_value == down_value == 0:
            return "down", down_value
        return "up", up_value

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
                    (
                        f"button={_button_name(button)}",
                        f"value={raw_value}",
                        f"edge={edge}",
                    ),
                )

            if raw_value == state["stable_value"]:
                continue

            if time.ticks_diff(now_ms, state["raw_changed_ms"]) < self.input_controller.debounce_ms:
                continue

            previous_value = state["stable_value"]
            state["stable_value"] = raw_value
            pressed = raw_value != state["idle_value"]
            edge = "rising" if raw_value > previous_value else "falling"
            self._log(
                "info",
                "debounced",
                (
                    f"button={_button_name(button)}",
                    f"raw={raw_value}",
                    f"stable={raw_value}",
                    f"idle={state['idle_value']}",
                    f"pressed={pressed}",
                    f"edge={edge}",
                ),
            )
            if pressed:
                events.append(ButtonEvent(button=button, held_ms=self.input_controller.debounce_ms))
                self._log(
                    "info",
                    "event",
                    (
                        f"button={_button_name(button)}",
                        f"pressed={pressed}",
                        f"raw={raw_value}",
                        f"stable={state['stable_value']}",
                    ),
                )

        return tuple(events)
