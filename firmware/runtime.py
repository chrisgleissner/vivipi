"""MicroPython runtime glue for ViviPi."""

import json

try:
    import network
    import utime as time
except ImportError:  # pragma: no cover - imported on-device
    network = None
    time = None

from vivipi.core.input import InputController
from vivipi.core.display import normalize_display_config
from vivipi.core.logging import bound_text
from vivipi.core.models import DiagnosticEvent, DisplayMode
from vivipi.runtime import state as runtime_state
from vivipi.runtime.metrics import elapsed_ms, start_timer
from vivipi.runtime import RuntimeApp, build_executor, build_runtime_definitions

try:
    from display import create_display
    from input import ButtonReader
except ImportError:  # pragma: no cover - used by CPython tests
    from firmware.display import create_display
    from firmware.input import ButtonReader


DEFAULT_BUTTON_PINS = {"a": "GP14", "b": "GP15"}


class HeadlessDisplay:
    def __init__(self):
        self.boot_logo_versions = []
        self.frames = []

    def draw_frame(self, frame):
        if self.frames:
            self.frames[0] = frame
            return
        self.frames.append(frame)

    def show_boot_logo(self, version):
        self.boot_logo_versions.append(str(version))


def load_config(path="config.json"):
    with open(path, "r") as handle:
        return json.load(handle)


def _diagnostic_message(value: object, limit: int = 11) -> str:
    return bound_text(value, limit) or "error"


def _boot_diagnostic(code: str, message: str) -> DiagnosticEvent:
    return DiagnosticEvent(code=code, message=_diagnostic_message(message))


def _normalize_runtime_config(config):
    normalized = dict(config) if isinstance(config, dict) else {}

    project = normalized.get("project")
    device = normalized.get("device")
    wifi = normalized.get("wifi")
    service = normalized.get("service")
    checks = normalized.get("checks")

    normalized["project"] = dict(project) if isinstance(project, dict) else {}
    resolved_device = dict(device) if isinstance(device, dict) else {}
    buttons = resolved_device.get("buttons")
    resolved_buttons = dict(buttons) if isinstance(buttons, dict) else {}
    resolved_buttons.setdefault("a", DEFAULT_BUTTON_PINS["a"])
    resolved_buttons.setdefault("b", DEFAULT_BUTTON_PINS["b"])
    resolved_device["buttons"] = resolved_buttons
    resolved_device["display"] = resolved_device.get("display", {})
    normalized["device"] = resolved_device
    normalized["wifi"] = dict(wifi) if isinstance(wifi, dict) else {}
    normalized["service"] = dict(service) if isinstance(service, dict) else {}
    normalized["checks"] = list(checks) if isinstance(checks, list) else []
    return normalized


def load_config_with_fallback(path="config.json"):
    try:
        return _normalize_runtime_config(load_config(path)), (), ()
    except OSError as error:
        return _normalize_runtime_config({}), (_boot_diagnostic("CONF", "missing"),), (("config", error),)
    except ValueError as error:
        return _normalize_runtime_config({}), (_boot_diagnostic("CONF", "json bad"),), (("config", error),)
    except Exception as error:
        return _normalize_runtime_config({}), (_boot_diagnostic("CONF", "load failed"),), (("config", error),)


def _build_display_with_fallback(display_factory, display_config):
    try:
        return display_factory(display_config), display_config, (), ()
    except Exception as error:
        boot_diagnostics = [_boot_diagnostic("DISP", "init failed")]
        boot_errors = [("display", error)]

    fallback_display_config = normalize_display_config({})
    if dict(display_config).get("type") != fallback_display_config.get("type"):
        try:
            return (
                display_factory(fallback_display_config),
                fallback_display_config,
                tuple(boot_diagnostics + [_boot_diagnostic("DISP", "fallback")]),
                tuple(boot_errors),
            )
        except Exception as error:
            boot_errors.append(("display", error))

    return HeadlessDisplay(), fallback_display_config, tuple(boot_diagnostics + [_boot_diagnostic("DISP", "headless")]), tuple(boot_errors)


def _safe_show_boot_logo(display, version):
    try:
        display.show_boot_logo(version)
        return (), ()
    except Exception as error:
        return (_boot_diagnostic("DISP", "boot failed"),), (("display", error),)


def _safe_build_definitions(definitions_builder, config):
    try:
        return definitions_builder(config), (), ()
    except Exception as error:
        return (), (_boot_diagnostic("CONF", "checks bad"),), (("config", error),)


def _safe_build_button_reader(button_reader_factory, buttons_config, input_controller):
    try:
        return button_reader_factory(buttons_config, input_controller=input_controller), (), ()
    except Exception as error:
        return None, (_boot_diagnostic("BTN", "init failed"),), (("buttons", error),)


def _safe_connect_wifi(wifi_connector, config):
    try:
        return wifi_connector(config), ()
    except Exception as error:
        return (_boot_diagnostic("WIFI", "init failed"),), (("wifi", error),)


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

    deadline_ms = time.ticks_add(time.ticks_ms(), int(timeout_s * 1000))
    attempt = 0
    while time.ticks_diff(deadline_ms, time.ticks_ms()) > 0 and attempt < 3:
        attempt += 1
        wlan.connect(ssid, password)
        remaining_ms = max(0, time.ticks_diff(deadline_ms, time.ticks_ms()))
        per_attempt_ms = max(200, remaining_ms // max(1, 4 - attempt))
        attempt_deadline_ms = time.ticks_add(time.ticks_ms(), per_attempt_ms)
        # Hot path: wait deterministically without per-iteration logging.
        while not wlan.isconnected() and time.ticks_diff(attempt_deadline_ms, time.ticks_ms()) > 0:
            _sleep_ms(200)
        if wlan.isconnected():
            return ()
        if hasattr(wlan, "disconnect"):
            wlan.disconnect()
        if attempt < 3 and time.ticks_diff(deadline_ms, time.ticks_ms()) > 0:
            _sleep_ms(200 * (2 ** (attempt - 1)))

    if wlan.isconnected():
        return ()
    return (DiagnosticEvent(code="WIFI", message="connect fail"),)


def read_wifi_state(config):
    wifi = config.get("wifi", {}) if isinstance(config.get("wifi"), dict) else {}
    snapshot = {
        "ssid": str(wifi.get("ssid", "")).strip(),
        "connected": False,
        "active": False,
        "ip_address": None,
    }
    if network is None:
        return snapshot

    wlan = network.WLAN(network.STA_IF)
    try:
        snapshot["active"] = bool(wlan.active())
    except TypeError:
        snapshot["active"] = True
    snapshot["connected"] = bool(wlan.isconnected())
    if snapshot["connected"] and hasattr(wlan, "ifconfig"):
        snapshot["ip_address"] = wlan.ifconfig()[0]
    return snapshot


def reconnect_wifi(config, timeout_s=10):
    if network is None or time is None:  # pragma: no cover - imported on-device
        return (DiagnosticEvent(code="WIFI", message="module missing"),)

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if hasattr(wlan, "disconnect"):
        wlan.disconnect()
        _sleep_ms(100)
    return connect_wifi(config, timeout_s=timeout_s)


def build_runtime_app(
    config,
    input_controller_factory=InputController,
    display_factory=create_display,
    button_reader_factory=ButtonReader,
    runtime_app_factory=RuntimeApp,
    definitions_builder=build_runtime_definitions,
    executor_factory=build_executor,
    wifi_connector=connect_wifi,
    now_provider=_now_s,
    sleep_ms=_sleep_ms,
    boot_logo_min_s=5,
    boot_diagnostics=(),
    boot_errors=(),
):
    config = _normalize_runtime_config(config)
    input_controller = input_controller_factory()
    device = config.get("device", {}) if isinstance(config.get("device"), dict) else {}
    display_input = device.get("display")
    button_input = device.get("buttons", DEFAULT_BUTTON_PINS)
    collected_diagnostics = list(boot_diagnostics)
    collected_errors = list(boot_errors)

    try:
        display_config = normalize_display_config(display_input)
    except Exception as error:
        display_config = normalize_display_config({})
        collected_diagnostics.append(_boot_diagnostic("CONF", "display bad"))
        collected_errors.append(("config", error))

    display, display_config, display_diagnostics, display_errors = _build_display_with_fallback(display_factory, display_config)
    collected_diagnostics.extend(display_diagnostics)
    collected_errors.extend(display_errors)
    font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
    font_width = int(font.get("width_px", 8)) if isinstance(font, dict) else 8
    font_height = int(font.get("height_px", 8)) if isinstance(font, dict) else 8

    project = config.get("project", {}) if isinstance(config.get("project"), dict) else {}
    version = str(project.get("version", ""))
    build_time_value = str(project.get("build_time", ""))

    boot_start_s = now_provider()
    logo_diagnostics, logo_errors = _safe_show_boot_logo(display, version)
    collected_diagnostics.extend(logo_diagnostics)
    collected_errors.extend(logo_errors)

    button_reader, button_diagnostics, button_errors = _safe_build_button_reader(button_reader_factory, button_input, input_controller)
    collected_diagnostics.extend(button_diagnostics)
    collected_errors.extend(button_errors)
    connect_started, timer_kind = start_timer()
    diagnostics, wifi_errors = _safe_connect_wifi(wifi_connector, config)
    connect_duration_ms = elapsed_ms(connect_started, timer_kind)
    collected_errors.extend(wifi_errors)

    elapsed_s = now_provider() - boot_start_s
    remaining_ms = max(0, int((boot_logo_min_s - elapsed_s) * 1000))
    if remaining_ms > 0:
        sleep_ms(remaining_ms)

    definitions, definition_diagnostics, definition_errors = _safe_build_definitions(definitions_builder, config)
    collected_diagnostics.extend(definition_diagnostics)
    collected_errors.extend(definition_errors)

    app = runtime_app_factory(
        definitions=definitions,
        executor=executor_factory(),
        display=display,
        button_reader=button_reader,
        input_controller=input_controller,
        page_interval_s=int(display_config.get("page_interval_s", 15)),
        page_size=max(1, int(display_config.get("height_px", 64)) // font_height),
        row_width=max(1, int(display_config.get("width_px", 128)) // font_width),
        display_mode=DisplayMode(str(display_config.get("mode", str(DisplayMode.STANDARD)))),
        overview_columns=int(display_config.get("columns", 1)),
        column_separator=str(display_config.get("column_separator", " ")),
        version=version,
        build_time=build_time_value,
    )
    if hasattr(app, "configure_observability"):
        app.configure_observability(
            config=config,
            now_provider=now_provider,
            wifi_connector=wifi_connector,
            wifi_reconnector=reconnect_wifi,
            network_state_reader=read_wifi_state,
        )
        app._refresh_network_state(
            last_error="; ".join(event.message for event in diagnostics) if diagnostics else "",
            connect_duration_ms=connect_duration_ms,
        )
    if hasattr(app, "_record_exception") and collected_errors:
        observed_at_s = now_provider()
        for scope, error in collected_errors:
            app._record_exception(scope, error, observed_at_s=observed_at_s)
    all_diagnostics = tuple(collected_diagnostics) + tuple(diagnostics)
    if all_diagnostics:
        app.inject_diagnostics(all_diagnostics, activate=True)
    runtime_state.bind_app(app)
    return app


def build_runtime_app_from_path(config_path="config.json", **kwargs):
    config, boot_diagnostics, boot_errors = load_config_with_fallback(config_path)
    return build_runtime_app(config, boot_diagnostics=boot_diagnostics, boot_errors=boot_errors, **kwargs)


def run_loop(app, poll_interval_ms=50, iterations=None, now_provider=_now_s, sleep_ms=_sleep_ms):
    iteration = 0
    while iterations is None or iteration < iterations:  # pragma: no branch - tiny loop helper
        now_s = now_provider()
        try:
            app.tick(now_s)
        except KeyboardInterrupt:
            raise
        except Exception as error:
            if hasattr(app, "_record_exception"):
                app._record_exception("loop", error, observed_at_s=now_s)
        iteration += 1
        sleep_ms(poll_interval_ms)


def run_forever(config_path="config.json", poll_interval_ms=50):
    app = build_runtime_app_from_path(config_path)
    run_loop(app, poll_interval_ms=poll_interval_ms)