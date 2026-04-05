"""MicroPython runtime glue for ViviPi."""

import json

try:
    import network
    import utime as time
except ImportError:  # pragma: no cover - imported on-device
    network = None
    time = None

from vivipi.core.input import InputController
from vivipi.core.models import DiagnosticEvent, DisplayMode
from vivipi.runtime import RuntimeApp, build_executor, build_runtime_definitions

try:
    from display import SH1107Display
    from input import ButtonReader
except ImportError:  # pragma: no cover - used by CPython tests
    from firmware.display import SH1107Display
    from firmware.input import ButtonReader


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


def build_runtime_app(
    config,
    input_controller_factory=InputController,
    display_factory=SH1107Display,
    button_reader_factory=ButtonReader,
    runtime_app_factory=RuntimeApp,
    definitions_builder=build_runtime_definitions,
    executor_factory=build_executor,
    wifi_connector=connect_wifi,
):
    input_controller = input_controller_factory()
    display_config = config["device"]["display"]
    font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
    font_width = int(font.get("width_px", 8)) if isinstance(font, dict) else 8
    font_height = int(font.get("height_px", 8)) if isinstance(font, dict) else 8
    display = display_factory(display_config)
    button_reader = button_reader_factory(config["device"]["buttons"], input_controller=input_controller)
    app = runtime_app_factory(
        definitions=definitions_builder(config),
        executor=executor_factory(),
        display=display,
        button_reader=button_reader,
        input_controller=input_controller,
        page_interval_s=int(display_config.get("page_interval_s", 15)),
        page_size=max(1, int(display_config.get("height_px", 64)) // font_height),
        row_width=max(1, int(display_config.get("width_px", 128)) // font_width),
        display_mode=DisplayMode(str(display_config.get("mode", DisplayMode.STANDARD.value))),
        overview_columns=int(display_config.get("columns", 1)),
        column_separator=str(display_config.get("column_separator", " ")),
    )
    diagnostics = wifi_connector(config)
    if diagnostics:
        app.inject_diagnostics(diagnostics, activate=True)
    return app


def run_loop(app, poll_interval_ms=50, iterations=None, now_provider=_now_s, sleep_ms=_sleep_ms):
    iteration = 0
    while iterations is None or iteration < iterations:  # pragma: no branch - tiny loop helper
        app.tick(now_provider())
        iteration += 1
        sleep_ms(poll_interval_ms)


def run_forever(config_path="config.json", poll_interval_ms=50):
    app = build_runtime_app(load_config(config_path))
    run_loop(app, poll_interval_ms=poll_interval_ms)