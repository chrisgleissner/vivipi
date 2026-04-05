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


class ButtonReader:
    def __init__(self, button_config, input_controller: InputController):
        if time is None or Pin is None:  # pragma: no cover - imported on-device
            raise RuntimeError("machine and utime modules are required on device")

        self.input_controller = input_controller
        self.pins = {
            Button.A: Pin(_pin_number(button_config["a"]), Pin.IN, Pin.PULL_UP),
            Button.B: Pin(_pin_number(button_config["b"]), Pin.IN, Pin.PULL_UP),
        }
        self.held_since_ms = {button: None for button in self.pins}
        self.last_emit_ms = {button: None for button in self.pins}

    def poll(self):
        now_ms = time.ticks_ms()
        events = []
        for button, pin in self.pins.items():
            pressed = pin.value() == 0
            if not pressed:
                self.held_since_ms[button] = None
                self.last_emit_ms[button] = None
                continue

            if self.held_since_ms[button] is None:
                self.held_since_ms[button] = now_ms

            held_ms = time.ticks_diff(now_ms, self.held_since_ms[button])
            if held_ms < self.input_controller.debounce_ms:
                continue

            last_emit = self.last_emit_ms[button]
            should_emit = last_emit is None or time.ticks_diff(now_ms, last_emit) >= self.input_controller.repeat_ms
            if should_emit:
                events.append(ButtonEvent(button=button, held_ms=self.input_controller.debounce_ms))
                self.last_emit_ms[button] = now_ms

        return tuple(events)