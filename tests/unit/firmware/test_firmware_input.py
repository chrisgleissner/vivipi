from firmware import input as firmware_input
from vivipi.core.input import Button, InputController


class FakeTime:
    def __init__(self, now_ms):
        self.now_ms = now_ms

    def ticks_ms(self):
        return self.now_ms

    def ticks_diff(self, left, right):
        return left - right


class FakePin:
    def __init__(self, current_value):
        self.current_value = current_value

    def value(self):
        return self.current_value


def test_button_reader_emits_events_and_resets_when_released(monkeypatch):
    monkeypatch.setattr(firmware_input, "time", FakeTime(100))

    reader = firmware_input.ButtonReader.__new__(firmware_input.ButtonReader)
    reader.input_controller = InputController()
    reader.pins = {Button.A: FakePin(0), Button.B: FakePin(1)}
    reader.held_since_ms = {Button.A: 60, Button.B: None}
    reader.last_emit_ms = {Button.A: None, Button.B: None}

    events = reader.poll()

    assert len(events) == 1
    assert events[0].button == Button.A

    reader.pins[Button.A].current_value = 1
    cleared = reader.poll()

    assert cleared == ()
    assert reader.held_since_ms[Button.A] is None
    assert reader.last_emit_ms[Button.A] is None