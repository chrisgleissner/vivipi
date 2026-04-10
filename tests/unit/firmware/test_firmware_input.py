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


def test_button_reader_emits_event_on_debounced_press_and_no_event_on_release(monkeypatch):
    monkeypatch.setattr(firmware_input, "time", FakeTime(100))

    reader = firmware_input.ButtonReader.__new__(firmware_input.ButtonReader)
    reader.input_controller = InputController()
    reader.logger = None
    reader.states = {
        Button.A: {
            "pin": FakePin(0),
            "pin_name": "GP15",
            "pull": "up",
            "idle_value": 1,
            "raw_value": 0,
            "stable_value": 1,
            "raw_changed_ms": 60,
        },
        Button.B: {
            "pin": FakePin(1),
            "pin_name": "GP17",
            "pull": "up",
            "idle_value": 1,
            "raw_value": 1,
            "stable_value": 1,
            "raw_changed_ms": 100,
        },
    }
    reader._log = lambda method, message, fields=(): None

    events = reader.poll()

    assert len(events) == 1
    assert events[0].button == Button.A

    reader.states[Button.A]["pin"].current_value = 1
    reader.states[Button.A]["raw_value"] = 1
    reader.states[Button.A]["raw_changed_ms"] = 60
    cleared = reader.poll()

    assert cleared == ()
    assert reader.states[Button.A]["stable_value"] == 1


def test_button_reader_logs_plain_string_button_ids_without_attribute_errors(monkeypatch):
    monkeypatch.setattr(firmware_input, "time", FakeTime(100))

    logged = []
    reader = firmware_input.ButtonReader.__new__(firmware_input.ButtonReader)
    reader.input_controller = InputController()
    reader.logger = None
    reader.states = {
        "A": {
            "pin": FakePin(0),
            "pin_name": "GP15",
            "pull": "up",
            "idle_value": 1,
            "raw_value": 0,
            "stable_value": 1,
            "raw_changed_ms": 60,
        }
    }
    reader._log = lambda method, message, fields=(): logged.append((method, message, fields))

    events = reader.poll()

    assert len(events) == 1
    assert events[0].button == "A"
    assert any("button=A" in field for _, _, fields in logged for field in fields)
