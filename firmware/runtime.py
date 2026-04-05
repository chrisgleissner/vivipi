"""MicroPython runtime glue for ViviPi."""

import json

try:
    import network
    import utime as time
except ImportError:  # pragma: no cover - imported on-device
    network = None
    time = None

from vivipi.core.input import InputController
from vivipi.core.models import DiagnosticEvent
from vivipi.runtime import RuntimeApp, build_executor, build_runtime_definitions

from display import SH1107Display
from input import ButtonReader


def load_config(path="config.json"):
    with open(path, "r") as handle:
        return json.load(handle)


def _now_s():
    if hasattr(time, "time"):
        return float(time.time())
    return float(time.ticks_ms()) / 1000.0


def _sleep_ms(value):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value)
        return
    time.sleep(value / 1000.0)


def connect_wifi(config, timeout_s=10):
    if network is None or time is None:  # pragma: no cover - imported on-device
        return (DiagnosticEvent(code="WIFI", message="module missing"),)

    wifi = config.get("wifi", {})
    ssid = str(wifi.get("ssid", "")).strip()
    password = str(wifi.get("password", "")).strip()
    if not ssid:
        return (DiagnosticEvent(code="WIFI", message="ssid missing"),)

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return ()

    wlan.connect(ssid, password)
    deadline_ms = time.ticks_add(time.ticks_ms(), int(timeout_s * 1000))
    while not wlan.isconnected() and time.ticks_diff(deadline_ms, time.ticks_ms()) > 0:
        _sleep_ms(200)

    if wlan.isconnected():
        return ()
    return (DiagnosticEvent(code="WIFI", message="connect fail"),)


def build_runtime_app(config):
    input_controller = InputController()
    display = SH1107Display(config["device"]["display"])
    button_reader = ButtonReader(config["device"]["buttons"], input_controller=input_controller)
    app = RuntimeApp(
        definitions=build_runtime_definitions(config),
        executor=build_executor(),
        display=display,
        button_reader=button_reader,
        input_controller=input_controller,
    )
    diagnostics = connect_wifi(config)
    if diagnostics:
        app.inject_diagnostics(diagnostics, activate=True)
    return app


def run_forever(config_path="config.json", poll_interval_ms=50):
    app = build_runtime_app(load_config(config_path))
    while True:  # pragma: no cover - imported on-device
        app.tick(_now_s())
        _sleep_ms(poll_interval_ms)