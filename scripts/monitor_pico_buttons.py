"""Monitor ViviPi button GPIOs on-device and print debounced press/release events."""

import json

try:
    import utime as time
    from machine import Pin
except ImportError as error:  # pragma: no cover - script is intended for MicroPython
    raise RuntimeError("monitor_pico_buttons.py must run on the Pico via mpremote") from error


DEFAULT_BUTTON_PINS = {"a": "GP15", "b": "GP17"}
DEBOUNCE_MS = 30
POLL_MS = 5


def _pin_number(value):
    return int(str(value).replace("GP", ""))


def _sleep_ms(value_ms):
    if value_ms <= 0:
        return
    time.sleep_ms(value_ms)


def _load_config(path="config.json"):
    try:
        with open(path, "r") as handle:
            return json.load(handle)
    except Exception:
        return {}


def _button_config(config):
    device = config.get("device") if isinstance(config, dict) else None
    buttons = device.get("buttons") if isinstance(device, dict) else None
    if not isinstance(buttons, dict):
        return dict(DEFAULT_BUTTON_PINS)

    resolved = {}
    for key, default_pin in DEFAULT_BUTTON_PINS.items():
        value = buttons.get(key, default_pin)
        if isinstance(value, dict):
            pin_value = value.get("pin", default_pin)
        else:
            pin_value = value
        resolved[key] = str(pin_value).strip() or default_pin
    return resolved


def _read_with_pull(pin_number, pull):
    probe = Pin(pin_number, Pin.IN, pull)
    _sleep_ms(2)
    return probe.value()


def _detect_pull(pin_number):
    up_value = _read_with_pull(pin_number, Pin.PULL_UP)
    down_value = _read_with_pull(pin_number, Pin.PULL_DOWN)
    if up_value == down_value == 1:
        return Pin.PULL_UP, "up", up_value
    if up_value == down_value == 0:
        return Pin.PULL_DOWN, "down", down_value
    return Pin.PULL_UP, "up", up_value


def _open_button(name, pin_name):
    pin_number = _pin_number(pin_name)
    pull_constant, pull_name, sampled_value = _detect_pull(pin_number)
    pin = Pin(pin_number, Pin.IN, pull_constant)
    stable_value = pin.value()
    return {
        "name": name.upper(),
        "pin_name": pin_name,
        "pin": pin,
        "pull": pull_name,
        "idle_value": stable_value,
        "raw_value": stable_value,
        "stable_value": stable_value,
        "raw_changed_ms": time.ticks_ms(),
        "sampled_value": sampled_value,
    }


def _print_button_state(prefix, button):
    print(
        prefix,
        f"button={button['name']}",
        f"pin={button['pin_name']}",
        f"pull={button['pull']}",
        f"idle={button['idle_value']}",
    )


def main():
    config = _load_config()
    buttons = _button_config(config)
    state = {
        "a": _open_button("a", buttons["a"]),
        "b": _open_button("b", buttons["b"]),
    }

    print("MONITOR START")
    _print_button_state("CONFIG", state["a"])
    _print_button_state("CONFIG", state["b"])
    print("PRESS Ctrl-C to stop")

    while True:
        now_ms = time.ticks_ms()
        for key in ("a", "b"):
            button = state[key]
            raw_value = button["pin"].value()
            if raw_value != button["raw_value"]:
                button["raw_value"] = raw_value
                button["raw_changed_ms"] = now_ms

            if raw_value == button["stable_value"]:
                continue

            if time.ticks_diff(now_ms, button["raw_changed_ms"]) < DEBOUNCE_MS:
                continue

            previous_value = button["stable_value"]
            button["stable_value"] = raw_value
            pressed = raw_value != button["idle_value"]
            event = "PRESS" if pressed else "RELEASE"
            print(
                event,
                f"button={button['name']}",
                f"pin={button['pin_name']}",
                f"value={raw_value}",
                f"previous={previous_value}",
                f"idle={button['idle_value']}",
                f"t_ms={now_ms}",
            )

        _sleep_ms(POLL_MS)


main()