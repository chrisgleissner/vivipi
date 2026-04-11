import pytest

from firmware import input as firmware_input
from vivipi.core.input import Button, InputController


class FakeTime:
    def __init__(self, now_ms):
        self.now_ms = now_ms

    def ticks_ms(self):
        return self.now_ms

    def ticks_diff(self, left, right):
        return left - right


def make_pin_stub(initial_values):
    class PinStub:
        IN = "IN"
        PULL_UP = "PULL_UP"
        PULL_DOWN = "PULL_DOWN"
        instances = []

        def __init__(self, pin_number, mode, pull):
            self.pin_number = pin_number
            self.mode = mode
            self.pull = pull
            self.current_value = initial_values.get(pin_number, 1)
            type(self).instances.append(self)

        def value(self):
            return self.current_value

    return PinStub


def build_reader(monkeypatch, button_config=None, initial_values=None, input_controller=None):
    fake_time = FakeTime(0)
    pin_stub = make_pin_stub(initial_values or {15: 1, 17: 1})
    monkeypatch.setattr(firmware_input, "time", fake_time)
    monkeypatch.setattr(firmware_input, "Pin", pin_stub)
    reader = firmware_input.ButtonReader(button_config or {"a": "GP15", "b": "GP17"}, input_controller or InputController())
    return reader, fake_time, pin_stub


def test_button_reader_defaults_to_pull_up_and_idle_high(monkeypatch):
    reader, _, pin_stub = build_reader(monkeypatch)

    assert reader.states[Button.A]["pull"] == "up"
    assert reader.states[Button.A]["idle_value"] == 1
    assert reader.states[Button.B]["pull"] == "up"
    assert reader.states[Button.B]["idle_value"] == 1
    assert [instance.pull for instance in pin_stub.instances] == [pin_stub.PULL_UP, pin_stub.PULL_UP]


def test_button_reader_emits_one_event_on_press_and_none_on_release(monkeypatch):
    reader, fake_time, _ = build_reader(monkeypatch)
    button_a = reader.states[Button.A]["pin"]

    fake_time.now_ms = 10
    button_a.current_value = 0
    assert reader.poll() == ()

    fake_time.now_ms = 40
    events = reader.poll()

    assert len(events) == 1
    assert events[0].button == Button.A
    assert events[0].held_ms == 30
    assert reader.snapshot()["A"]["pressed"] is True

    fake_time.now_ms = 60
    button_a.current_value = 1
    assert reader.poll() == ()

    fake_time.now_ms = 90
    assert reader.poll() == ()
    assert reader.snapshot()["A"]["pressed"] is False


def test_button_reader_repeats_button_a_on_repeat_intervals(monkeypatch):
    reader, fake_time, _ = build_reader(monkeypatch)
    button_a = reader.states[Button.A]["pin"]

    fake_time.now_ms = 10
    button_a.current_value = 0
    reader.poll()

    fake_time.now_ms = 40
    first_events = reader.poll()

    fake_time.now_ms = 540
    second_events = reader.poll()

    fake_time.now_ms = 1040
    third_events = reader.poll()

    assert len(first_events) + len(second_events) + len(third_events) == 3
    assert all(event.button == Button.A for event in first_events + second_events + third_events)


def test_button_reader_clamps_button_b_to_one_event_while_held(monkeypatch):
    reader, fake_time, _ = build_reader(monkeypatch)
    button_b = reader.states[Button.B]["pin"]

    fake_time.now_ms = 10
    button_b.current_value = 0
    reader.poll()

    fake_time.now_ms = 40
    first_events = reader.poll()

    fake_time.now_ms = 1040
    held_events = reader.poll()

    assert len(first_events) == 1
    assert first_events[0].button == Button.B
    assert held_events == ()


def test_button_reader_uses_pull_down_when_requested(monkeypatch):
    reader, fake_time, pin_stub = build_reader(
        monkeypatch,
        button_config={"a": {"pin": "GP15", "pull": "down"}, "b": "GP17"},
        initial_values={15: 0, 17: 1},
    )
    button_a = reader.states[Button.A]["pin"]

    assert reader.states[Button.A]["pull"] == "down"
    assert reader.states[Button.A]["idle_value"] == 0
    assert button_a.pull == pin_stub.PULL_DOWN

    fake_time.now_ms = 10
    button_a.current_value = 1
    reader.poll()

    fake_time.now_ms = 40
    events = reader.poll()

    assert len(events) == 1
    assert events[0].button == Button.A
    assert events[0].held_ms == 30


def test_button_reader_rejects_auto_pull_mode(monkeypatch):
    fake_time = FakeTime(0)
    monkeypatch.setattr(firmware_input, "time", fake_time)
    monkeypatch.setattr(firmware_input, "Pin", make_pin_stub({15: 1, 17: 1}))

    with pytest.raises(ValueError, match="button pull must be up or down"):
        firmware_input.ButtonReader({"a": {"pin": "GP15", "pull": "auto"}, "b": "GP17"}, InputController())
