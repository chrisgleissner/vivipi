"""MicroPython runtime glue for ViviPi."""

import json

try:
    import network
    import utime as time
except ImportError:  # pragma: no cover - imported on-device
    network = None
    time = None

from vivipi.core.input import InputController
from vivipi.core.config import parse_probe_schedule_config
from vivipi.core.display import normalize_display_config
from vivipi.core.logging import bound_text
from vivipi.core.models import DiagnosticEvent, DisplayMode, TransitionThresholds
from vivipi.core.render import Frame
from vivipi.runtime import state as runtime_state
from vivipi.runtime import RuntimeApp, build_executor, build_runtime_definitions

try:
    from display import create_display
except ImportError as error:  # pragma: no cover - used by CPython tests
    if getattr(error, "name", None) != "display":
        raise
    from firmware.display import create_display

try:
    from input import ButtonReader, probe_pin_states
except ImportError as error:  # pragma: no cover - used by CPython tests
    if getattr(error, "name", None) != "input":
        raise
    from firmware.input import ButtonReader, probe_pin_states


DEFAULT_BUTTON_PINS = {"a": "GP15", "b": "GP17"}
BUTTON_SELF_TEST_DISCOVERY_PINS = (15, 17, 21, 22)


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


def _serial_log(stage: str, message: str, **fields):
    parts = [f"[BOOT][{stage}] {message}"]
    for key in sorted(fields):
        parts.append(f"{key}={fields[key]}")
    print(" ".join(parts))


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


def _build_executor_with_optional_trace(executor_factory, trace_sink=None):
    if trace_sink is None:
        return executor_factory()
    try:
        return executor_factory(trace_sink=trace_sink)
    except TypeError as error:
        if "trace_sink" not in str(error):
            raise
        return executor_factory()


def _safe_build_button_reader(button_reader_factory, buttons_config, input_controller):
    try:
        return button_reader_factory(buttons_config, input_controller=input_controller), (), ()
    except Exception as error:
        return None, (_boot_diagnostic("BTN", "init failed"),), (("buttons", error),)


def _transition_thresholds_from_config(config):
    raw = config.get("check_state") if isinstance(config, dict) else None
    if not isinstance(raw, dict):
        return TransitionThresholds()
    return TransitionThresholds(
        failures_to_degraded=int(raw.get("failures_to_degraded", 1)),
        failures_to_failed=int(raw.get("failures_to_failed", 2)),
        successes_to_recover=int(raw.get("successes_to_recover", 1)),
    )


def _probe_scheduling_from_config(config):
    raw = config.get("probe_schedule") if isinstance(config, dict) else None
    return parse_probe_schedule_config(raw)


def _safe_connect_wifi(wifi_connector, config):
    try:
        return wifi_connector(config), ()
    except Exception as error:
        return (_boot_diagnostic("WIFI", "init failed"),), (("wifi", error),)


def _now_s():
    if hasattr(time, "time"):
        return float(time.time())
    return float(time.ticks_ms()) / 1000.0


def _steady_now_s():
    if hasattr(time, "ticks_ms"):
        return float(time.ticks_ms()) / 1000.0
    return _now_s()


def _sleep_ms(value):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value)
        return
    time.sleep(value / 1000.0)


def _fit_row(value: object, row_width: int) -> str:
    text = str(value)
    if len(text) >= row_width:
        return text[:row_width]
    return text + (" " * (row_width - len(text)))


def _button_self_test_duration_s(button_config) -> float:
    if not isinstance(button_config, dict):
        return 0.0
    raw_value = button_config.get("startup_self_test_s")
    if raw_value is None:
        raw_value = button_config.get("startup_self_test_duration_s")
    if raw_value is None:
        return 0.0
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        return 0.0


def _format_button_snapshot(label: str, snapshot: dict) -> str:
    pin = str(snapshot.get("pin", "GP?"))
    pin_suffix = pin.replace("GP", "")
    return f"{label}{pin_suffix} r{snapshot.get('raw', '?')} s{snapshot.get('stable', '?')}"


def _button_self_test_frame(snapshot, probe_snapshot, row_width: int, page_size: int, remaining_s: float) -> Frame:
    rows = [
        _fit_row("BTN SELFTEST", row_width),
        _fit_row(_format_button_snapshot("A", snapshot.get("A", {})), row_width),
        _fit_row(_format_button_snapshot("B", snapshot.get("B", {})), row_width),
        _fit_row(f"15:{probe_snapshot.get('GP15', '?')} 17:{probe_snapshot.get('GP17', '?')}", row_width),
        _fit_row(f"21:{probe_snapshot.get('GP21', '?')} 22:{probe_snapshot.get('GP22', '?')}", row_width),
        _fit_row("PRESS BTN NOW", row_width),
        _fit_row(f"T-{max(0.0, remaining_s):0.1f}s", row_width),
    ]
    while len(rows) < page_size:
        rows.append(" " * row_width)
    return Frame(rows=tuple(rows[:page_size]))


def _run_button_self_test(display, button_reader, button_config, row_width: int, page_size: int, now_provider, sleep_ms):
    duration_s = _button_self_test_duration_s(button_config)
    if duration_s <= 0:
        _serial_log("BTNTEST", "skip", reason="disabled")
        return False
    if button_reader is None:
        _serial_log("BTNTEST", "skip", reason="button_reader_missing")
        return False

    _serial_log("BTNTEST", "start", duration_s=f"{duration_s:.1f}")
    deadline_s = now_provider() + duration_s
    last_serial_snapshot = None
    while True:
        now_s = now_provider()
        remaining_s = deadline_s - now_s
        if remaining_s <= 0:
            break
        events = tuple(button_reader.poll())
        snapshot = button_reader.snapshot() if hasattr(button_reader, "snapshot") else {}
        probe_snapshot = probe_pin_states(BUTTON_SELF_TEST_DISCOVERY_PINS)
        serial_snapshot = (
            _format_button_snapshot("A", snapshot.get("A", {})),
            _format_button_snapshot("B", snapshot.get("B", {})),
            tuple((key, probe_snapshot.get(key)) for key in ("GP15", "GP17", "GP21", "GP22")),
        )
        if serial_snapshot != last_serial_snapshot:
            _serial_log(
                "BTNTEST",
                "snapshot",
                a=serial_snapshot[0],
                b=serial_snapshot[1],
                gp15=probe_snapshot.get("GP15", "?"),
                gp17=probe_snapshot.get("GP17", "?"),
                gp21=probe_snapshot.get("GP21", "?"),
                gp22=probe_snapshot.get("GP22", "?"),
            )
            last_serial_snapshot = serial_snapshot
        display.draw_frame(_button_self_test_frame(snapshot, probe_snapshot, row_width, page_size, remaining_s))
        if events:
            _serial_log("BTNTEST", "button-detected", count=len(events))
            return True
        sleep_ms(100)
    _serial_log("BTNTEST", "done", result="timeout")
    return True


def _button_config_from_runtime_config(config):
    if not isinstance(config, dict):
        return {}
    device = config.get("device")
    if not isinstance(device, dict):
        return {}
    buttons = device.get("buttons")
    if not isinstance(buttons, dict):
        return {}
    return buttons


def _maybe_run_button_self_test_from_app(app, now_provider, sleep_ms):
    if getattr(app, "button_self_test_ran", False):
        return False
    config = getattr(app, "config", None)
    button_config = _button_config_from_runtime_config(config)
    state = getattr(app, "state", None)
    ran = _run_button_self_test(
        getattr(app, "display", None),
        getattr(app, "button_reader", None),
        button_config,
        getattr(state, "row_width", 16),
        getattr(state, "page_size", 8),
        now_provider,
        sleep_ms,
    )
    app.button_self_test_ran = bool(ran)
    return bool(ran)


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
    boot_logo_min_s=2,
    boot_diagnostics=(),
    boot_errors=(),
):
    config = _normalize_runtime_config(config)
    _serial_log("BOOT", "config normalized")
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
    _serial_log(
        "BOOT",
        "display config",
        display=display_config.get("type", "?"),
        spi_mode=display_config.get("spi_mode", "?"),
        width=display_config.get("width_px", "?"),
        height=display_config.get("height_px", "?"),
    )

    display, display_config, display_diagnostics, display_errors = _build_display_with_fallback(display_factory, display_config)
    _serial_log("BOOT", "display ready", backend=display_config.get("backend", "?"))
    collected_diagnostics.extend(display_diagnostics)
    collected_errors.extend(display_errors)
    font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
    font_width = int(font.get("width_px", 8)) if isinstance(font, dict) else 8
    font_height = int(font.get("height_px", 8)) if isinstance(font, dict) else 8
    page_size = max(1, int(display_config.get("height_px", 64)) // font_height)
    row_width = max(1, int(display_config.get("width_px", 128)) // font_width)

    project = config.get("project", {}) if isinstance(config.get("project"), dict) else {}
    version = str(project.get("version", ""))
    build_time_value = str(project.get("build_time", ""))
    explicit_boot_logo_duration = None
    if isinstance(display_input, dict):
        explicit_boot_logo_duration = display_input.get("boot_logo_duration")
        if explicit_boot_logo_duration is None:
            explicit_boot_logo_duration = display_input.get("boot_logo_duration_s")

    boot_start_s = now_provider()
    logo_diagnostics, logo_errors = _safe_show_boot_logo(display, version)
    _serial_log("BOOT", "boot logo shown", version=version or "-")
    collected_diagnostics.extend(logo_diagnostics)
    collected_errors.extend(logo_errors)

    button_reader, button_diagnostics, button_errors = _safe_build_button_reader(button_reader_factory, button_input, input_controller)
    _serial_log("BOOT", "buttons ready", a=button_input.get("a", DEFAULT_BUTTON_PINS["a"]), b=button_input.get("b", DEFAULT_BUTTON_PINS["b"]))
    collected_diagnostics.extend(button_diagnostics)
    collected_errors.extend(button_errors)

    definitions, definition_diagnostics, definition_errors = _safe_build_definitions(definitions_builder, config)
    _serial_log("BOOT", "checks loaded", count=len(definitions))
    collected_diagnostics.extend(definition_diagnostics)
    collected_errors.extend(definition_errors)

    button_self_test_ran = _run_button_self_test(display, button_reader, button_input, row_width, page_size, now_provider, sleep_ms)

    app = runtime_app_factory(
        definitions=definitions,
        executor=None,
        display=display,
        button_reader=button_reader,
        input_controller=input_controller,
        page_interval_s=int(display_config.get("page_interval_s", 15)),
        page_size=page_size,
        row_width=row_width,
        display_mode=DisplayMode(str(display_config.get("mode", str(DisplayMode.STANDARD)))),
        overview_columns=int(display_config.get("columns", 1)),
        column_separator=str(display_config.get("column_separator", " ")),
        transition_thresholds=_transition_thresholds_from_config(config),
        probe_scheduling=_probe_scheduling_from_config(config),
        sleep_ms=sleep_ms,
        probe_time_provider=_steady_now_s,
        version=version,
        build_time=build_time_value,
    )
    trace_sink = getattr(app, "emit_probe_trace", None)
    if getattr(app, "background_workers_enabled", False):
        trace_sink = None
    app.executor = _build_executor_with_optional_trace(executor_factory, trace_sink)
    boot_logo_duration_s = float(boot_logo_min_s)
    if explicit_boot_logo_duration is not None:
        boot_logo_duration_s = max(float(boot_logo_min_s), float(display_config.get("boot_logo_duration_s", boot_logo_min_s)))
    app.boot_logo_until_s = boot_start_s + boot_logo_duration_s
    if hasattr(app, "logger"):
        app.logger.sink = print
    if button_reader is not None and hasattr(button_reader, "bind_logger") and hasattr(app, "logger"):
        button_reader.bind_logger(app.logger)
    if hasattr(app, "configure_observability"):
        app.configure_observability(
            config=config,
            now_provider=now_provider,
            wifi_connector=wifi_connector,
            wifi_reconnector=reconnect_wifi,
            network_state_reader=read_wifi_state,
        )
        app._refresh_network_state()
    app.button_self_test_ran = bool(button_self_test_ran)
    _serial_log("BOOT", "app ready", boot_logo_until_s=f"{app.boot_logo_until_s:.3f}")
    if hasattr(app, "_record_exception") and collected_errors:
        observed_at_s = now_provider()
        for scope, error in collected_errors:
            _serial_log(
                "BOOT",
                "captured startup error",
                scope=scope,
                error=type(error).__name__,
                detail=repr(error),
            )
            app._record_exception(scope, error, observed_at_s=observed_at_s)
    all_diagnostics = tuple(collected_diagnostics)
    if all_diagnostics:
        app.inject_diagnostics(all_diagnostics, activate=True)

    runtime_state.bind_app(app)
    return app


def build_runtime_app_from_path(config_path="config.json", **kwargs):
    _serial_log("BOOT", "loading config", path=config_path)
    config, boot_diagnostics, boot_errors = load_config_with_fallback(config_path)
    return build_runtime_app(config, boot_diagnostics=boot_diagnostics, boot_errors=boot_errors, **kwargs)


def _run_startup_network(app, now_s):
    _serial_log("BOOT", "network deferred", now_s=f"{now_s:.3f}")


def _render_initial_frame(app, now_s):
    if hasattr(app, "render_once"):
        try:
            app.render_once(now_s)
        except Exception as error:
            if hasattr(app, "_record_exception"):
                app._record_exception("loop", error, observed_at_s=now_s)
        return
    if not hasattr(app, "tick"):
        return
    try:
        app.tick(now_s, button_events=())
    except Exception as error:
        if hasattr(app, "_record_exception"):
            app._record_exception("loop", error, observed_at_s=now_s)


def _run_startup_tick(app, now_s):
    if hasattr(app, "prime_due_checks"):
        try:
            app.prime_due_checks(now_s)
        except Exception as error:
            if hasattr(app, "_record_exception"):
                app._record_exception("loop", error, observed_at_s=now_s)
        return
    if all(hasattr(app, attribute) for attribute in ("definitions", "_run_check", "render_once")):
        for definition in getattr(app, "definitions", ()):  # pragma: no branch - tiny startup loop
            try:
                app._run_check(definition, now_s)
            except Exception as error:
                if hasattr(app, "_record_exception"):
                    app._record_exception("loop", error, observed_at_s=now_s)
            _render_initial_frame(app, now_s)
        if hasattr(app, "_apply_page_rotation"):
            app._apply_page_rotation(now_s)
        if hasattr(app, "_apply_shift"):
            app._apply_shift(now_s)
        _render_initial_frame(app, now_s)
        return
    if not hasattr(app, "tick"):
        return
    try:
        app.tick(now_s, button_events=())
    except Exception as error:
        if hasattr(app, "_record_exception"):
            app._record_exception("loop", error, observed_at_s=now_s)


def _wait_for_boot_logo(app, now_provider=None, sleep_ms=None):
    now_provider = _now_s if now_provider is None else now_provider
    sleep_ms = _sleep_ms if sleep_ms is None else sleep_ms
    ready_at_s = getattr(app, "boot_logo_until_s", None)
    now_s = now_provider()
    if ready_at_s is None or now_s >= float(ready_at_s):
        return now_s

    remaining_ms = max(0, int(((float(ready_at_s) - now_s) * 1000.0) + 0.5))
    if remaining_ms > 0:
        sleep_ms(remaining_ms)
    return now_provider()


def run_loop(app, poll_interval_ms=50, iterations=None, now_provider=_now_s, sleep_ms=_sleep_ms):
    iteration = 0
    while iterations is None or iteration < iterations:  # pragma: no branch - tiny loop helper
        now_s = now_provider()
        try:
            app.tick(now_s)
        except KeyboardInterrupt:
            raise
        except Exception as error:
            _serial_log("LOOP", "exception", error=type(error).__name__, detail=repr(error))
            if hasattr(app, "_record_exception"):
                app._record_exception("loop", error, observed_at_s=now_s)
        iteration += 1
        sleep_ms(poll_interval_ms)


def run_forever(config_path="config.json", poll_interval_ms=50):
    app = build_runtime_app_from_path(config_path)
    _maybe_run_button_self_test_from_app(app, _now_s, _sleep_ms)
    startup_now_s = _now_s()
    _serial_log("BOOT", "startup tick", now_s=f"{startup_now_s:.3f}")
    _run_startup_network(app, startup_now_s)
    _run_startup_tick(app, startup_now_s)
    if hasattr(app, "tick"):
        try:
            app.tick(startup_now_s, button_events=())
        except Exception as error:
            if hasattr(app, "_record_exception"):
                app._record_exception("loop", error, observed_at_s=startup_now_s)
    else:
        _render_initial_frame(app, startup_now_s)
    _serial_log("BOOT", "enter loop", poll_interval_ms=poll_interval_ms)
    run_loop(app, poll_interval_ms=poll_interval_ms)
