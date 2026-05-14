from types import SimpleNamespace

import pytest

import firmware.runtime as firmware_runtime
import firmware.main as firmware_main
from vivipi.core.render import TextSpan
from vivipi.core.models import CheckDefinition, CheckRuntime, CheckType, DiagnosticEvent, DisplayMode, ProbeSchedulingPolicy, Status, TransitionThresholds


class FakeTime:
    def __init__(self):
        self.now_ms = 0
        self.sleep_calls = []

    def time(self):
        return self.now_ms / 1000.0

    def ticks_ms(self):
        return self.now_ms

    def ticks_add(self, value, delta):
        return value + delta

    def ticks_diff(self, left, right):
        return left - right

    def sleep_ms(self, value):
        self.sleep_calls.append(value)
        self.now_ms += value


class FakeWlan:
    def __init__(self, connected=False):
        self.connected = connected
        self.active_calls = []
        self.connect_calls = []
        self.disconnect_calls = 0

    def active(self, enabled=None):
        if enabled is None:
            return bool(self.active_calls[-1]) if self.active_calls else False
        self.active_calls.append(enabled)

    def isconnected(self):
        return self.connected

    def connect(self, ssid, password):
        self.connect_calls.append((ssid, password))
        self.connected = True

    def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    def ifconfig(self):
        return ("192.0.2.50", "255.255.255.0", "192.0.2.1", "192.0.2.1")


def test_connect_wifi_requires_ssid(monkeypatch):
    fake_time = FakeTime()
    fake_network = SimpleNamespace(STA_IF="sta", WLAN=lambda interface: FakeWlan())

    monkeypatch.setattr(firmware_runtime, "time", fake_time)
    monkeypatch.setattr(firmware_runtime, "network", fake_network)

    diagnostics = firmware_runtime.connect_wifi({"wifi": {"ssid": "   ", "password": "secret"}})

    assert diagnostics == (DiagnosticEvent(code="WIFI", message="ssid missing"),)


def test_watchdog_timeout_ms_clamps_to_pico_supported_range():
    class Definition:
        def __init__(self, timeout_s):
            self.timeout_s = timeout_s

    timeout_ms = firmware_runtime._watchdog_timeout_ms(
        (Definition(8),) * 7,
        boot_logo_duration_s=4.0,
        button_self_test_s=0.0,
    )

    assert timeout_ms == firmware_runtime.WATCHDOG_MAX_TIMEOUT_MS


def test_build_runtime_watchdog_uses_supported_timeout_when_machine_wdt_accepts_it(monkeypatch):
    created = {}

    class FakeWDT:
        def __init__(self, timeout=None):
            created["timeout"] = timeout

        def feed(self):
            return None

    monkeypatch.setattr(firmware_runtime, "machine", SimpleNamespace(WDT=FakeWDT, reset=lambda: None))

    watchdog = firmware_runtime._build_runtime_watchdog((), 4.0, 0.0)

    assert type(watchdog).__name__ == "_RuntimeWatchdog"
    assert created["timeout"] == firmware_runtime.WATCHDOG_MAX_TIMEOUT_MS
    assert watchdog.timeout_ms == firmware_runtime.WATCHDOG_MAX_TIMEOUT_MS


def test_main_non_eink_imports_runtime_without_bootstrap_watchdog(monkeypatch):
    events = []
    monkeypatch.setattr(firmware_main, "_display_family_from_config", lambda path="config.json": "lcd")

    run_forever_calls = []
    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "runtime":
            events.append("runtime-import")
            return SimpleNamespace(run_forever=lambda: run_forever_calls.append(True))
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    firmware_main.main()

    assert events == ["runtime-import"]
    assert run_forever_calls == [True]


def test_main_oled_uses_runtime_entrypoint(monkeypatch):
    events = []
    monkeypatch.setattr(firmware_main, "_display_family_from_config", lambda path="config.json": "oled")

    run_forever_calls = []
    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "runtime":
            events.append("runtime-import")
            return SimpleNamespace(run_forever=lambda: run_forever_calls.append(True))
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    firmware_main.main()

    assert events == ["runtime-import"]
    assert run_forever_calls == [True]


def test_build_runtime_app_attaches_bootstrap_watchdog_before_boot_logo(monkeypatch):
    class FakeWDT:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def feed(self):
            return None

    display = SimpleNamespace(show_boot_logo=lambda version: None, _watchdog_feed=None)
    observed = {}

    monkeypatch.setattr(firmware_runtime, "machine", SimpleNamespace(WDT=FakeWDT, reset=lambda: None))
    monkeypatch.setattr(
        firmware_runtime,
        "_safe_show_boot_logo",
        lambda passed_display, version: observed.update(
            {
                "display": passed_display,
                "version": version,
                "watchdog_feed": getattr(passed_display, "_watchdog_feed", None),
            }
        ) or ((), ()),
    )

    app = firmware_runtime.build_runtime_app(
        {
            "project": {"version": "1.2.3"},
            "device": {
                "display": {"width_px": 128, "height_px": 64, "font": {"width_px": 8, "height_px": 8}},
                "buttons": {"a": "GP15", "b": "GP17"},
            },
        },
        input_controller_factory=lambda: object(),
        display_factory=lambda config: display,
        button_reader_factory=lambda config, input_controller: object(),
        runtime_app_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        definitions_builder=lambda config: (),
        executor_factory=lambda trace_sink=None: object(),
        now_provider=lambda: 0.0,
        sleep_ms=lambda ms: None,
    )

    assert callable(observed["watchdog_feed"])
    assert observed["display"] is display
    assert observed["version"] == "1.2.3"
    assert callable(display._watchdog_feed)
    assert app.runtime_watchdog.timeout_ms == firmware_runtime.WATCHDOG_MAX_TIMEOUT_MS


def test_connect_wifi_joins_network_when_available(monkeypatch):
    fake_time = FakeTime()
    wlan = FakeWlan(connected=False)
    fake_network = SimpleNamespace(STA_IF="sta", WLAN=lambda interface: wlan)

    monkeypatch.setattr(firmware_runtime, "time", fake_time)
    monkeypatch.setattr(firmware_runtime, "network", fake_network)

    diagnostics = firmware_runtime.connect_wifi({"wifi": {"ssid": "Office", "password": "secret"}})

    assert diagnostics == ()
    assert wlan.active_calls == [True]
    assert wlan.connect_calls == [("Office", "secret")]


def test_connect_wifi_retries_with_backoff_before_reporting_failure(monkeypatch):
    class FailingWlan(FakeWlan):
        def connect(self, ssid, password):
            self.connect_calls.append((ssid, password))

    fake_time = FakeTime()
    wlan = FailingWlan(connected=False)
    fake_network = SimpleNamespace(STA_IF="sta", WLAN=lambda interface: wlan)

    monkeypatch.setattr(firmware_runtime, "time", fake_time)
    monkeypatch.setattr(firmware_runtime, "network", fake_network)

    diagnostics = firmware_runtime.connect_wifi({"wifi": {"ssid": "Office", "password": "secret"}}, timeout_s=3)

    assert diagnostics == (DiagnosticEvent(code="WIFI", message="connect fail"),)
    assert wlan.connect_calls == [("Office", "secret"), ("Office", "secret"), ("Office", "secret")]
    assert wlan.disconnect_calls == 3
    assert 200 in fake_time.sleep_calls
    assert 400 in fake_time.sleep_calls


def test_read_wifi_state_and_reconnect_wifi_capture_current_link_details(monkeypatch):
    fake_time = FakeTime()
    wlan = FakeWlan(connected=True)
    wlan.active(True)
    fake_network = SimpleNamespace(STA_IF="sta", WLAN=lambda interface: wlan)

    monkeypatch.setattr(firmware_runtime, "time", fake_time)
    monkeypatch.setattr(firmware_runtime, "network", fake_network)

    snapshot = firmware_runtime.read_wifi_state({"wifi": {"ssid": "Office"}})
    diagnostics = firmware_runtime.reconnect_wifi({"wifi": {"ssid": "Office", "password": "secret"}})

    assert snapshot == {
        "ssid": "Office",
        "connected": True,
        "active": True,
        "ip_address": "192.0.2.50",
    }
    assert diagnostics == ()
    assert wlan.disconnect_calls == 1
    assert wlan.connect_calls == [("Office", "secret")]


def test_build_runtime_app_uses_injected_factories_and_defers_wifi_startup():
    called = {}
    wifi_calls = []
    sleep_calls = []
    trace_sinks = []

    class FakeApp:
        def __init__(
            self,
            definitions,
            executor,
            display,
            button_reader,
            input_controller,
            page_interval_s,
            page_size,
            row_width,
            display_mode,
            overview_columns,
            column_separator,
            transition_thresholds,
            probe_scheduling,
            visible_degraded,
            highlight_selection,
            display_liveness,
            display_refresh,
            sleep_ms,
            probe_time_provider,
            version="",
            build_time="",
            display_family="oled",
        ):
            called["definitions"] = definitions
            called["executor"] = executor
            called["display"] = display
            called["button_reader"] = button_reader
            called["input_controller"] = input_controller
            called["page_interval_s"] = page_interval_s
            called["page_size"] = page_size
            called["row_width"] = row_width
            called["display_mode"] = display_mode
            called["overview_columns"] = overview_columns
            called["column_separator"] = column_separator
            called["transition_thresholds"] = transition_thresholds
            called["probe_scheduling"] = probe_scheduling
            called["visible_degraded"] = visible_degraded
            called["highlight_selection"] = highlight_selection
            called["display_liveness"] = display_liveness
            called["display_refresh"] = display_refresh
            called["sleep_ms"] = sleep_ms
            called["probe_time_provider"] = probe_time_provider
            called["version"] = version
            called["build_time"] = build_time
            called["display_family"] = display_family
            called["diagnostics"] = None
            called["network_refreshes"] = []

        def inject_diagnostics(self, diagnostics, activate=True):
            called["diagnostics"] = (diagnostics, activate)

        def configure_observability(self, **kwargs):
            called["observability"] = kwargs

        def _refresh_network_state(self, **kwargs):
            called["network_refreshes"].append(kwargs)

    input_controller = object()
    display = SimpleNamespace(show_boot_logo=lambda version: None)
    button_reader = object()
    executor = object()
    definitions = (object(),)
    now_counter = iter([0.0, 6.0, 6.0])

    app = firmware_runtime.build_runtime_app(
        {
            "project": {"version": "1.2.3", "build_time": "2025-04-05T12:00Z"},
            "check_state": {
                "failures_to_degraded": 1,
                "failures_to_failed": 2,
                "successes_to_recover": 1,
                "visible_degraded": False,
            },
            "device": {
                "display": {
                    "width_px": 128,
                    "height_px": 64,
                    "page_interval_s": 15,
                    "mode": "compact",
                    "columns": 3,
                    "column_separator": "|",
                    "liveness": {
                        "contrast_breathing": {"enabled": False, "period_s": 30, "amplitude": 16},
                        "per_row_micro": {"enabled": False, "period_s": 15, "stagger": True},
                        "bottom_heartbeat": {"enabled": True, "period_s": 1, "pixel_count": 1, "position": "left"},
                    },
                    "font": {"width_px": 8, "height_px": 8},
                },
                "buttons": {"a": "GP15", "b": "GP17"},
            },
        },
        input_controller_factory=lambda: input_controller,
        display_factory=lambda config: display,
        button_reader_factory=lambda config, input_controller: button_reader,
        runtime_app_factory=FakeApp,
        definitions_builder=lambda config: definitions,
        executor_factory=lambda trace_sink=None: trace_sinks.append(trace_sink) or executor,
        wifi_connector=lambda config: wifi_calls.append(config) or (DiagnosticEvent(code="WIFI", message="connected"),),
        now_provider=lambda: next(now_counter),
        sleep_ms=lambda ms: sleep_calls.append(ms),
    )

    assert isinstance(app, FakeApp)
    assert called["definitions"] == definitions
    assert called["executor"] is None
    assert app.executor is executor
    assert called["display"] is display
    assert called["button_reader"] is button_reader
    assert called["input_controller"] is input_controller
    assert called["page_interval_s"] == 15
    assert called["page_size"] == 8
    assert called["row_width"] == 16
    assert called["display_mode"] == DisplayMode.COMPACT
    assert called["overview_columns"] == 3
    assert called["column_separator"] == "|"
    assert called["transition_thresholds"] == TransitionThresholds(
        failures_to_degraded=1,
        failures_to_failed=2,
        successes_to_recover=1,
    )
    assert called["visible_degraded"] is False
    assert called["highlight_selection"] is False
    assert called["probe_scheduling"] == ProbeSchedulingPolicy()
    assert called["display_liveness"] == {
        "contrast_breathing": {"enabled": False, "period_s": 30, "amplitude": 16},
        "per_row_micro": {"enabled": False, "period_s": 15, "stagger": True},
        "bottom_heartbeat": {
            "enabled": True,
            "period_s": 1,
            "pixel_count": 1,
            "position": "left",
            "pixel_width_px": 1,
            "pixel_height_px": 1,
            "gap_px": 0,
        },
    }
    assert called["display_refresh"] == {"min_interval_s": 0, "probe_cycles_per_refresh": 1}
    assert called["version"] == "1.2.3"
    assert called["build_time"] == "2025-04-05T12:00Z"
    assert called["display_family"] == "oled"
    assert called["diagnostics"] is None
    assert called["network_refreshes"] == [{}]
    assert wifi_calls == []
    assert sleep_calls == []
    assert trace_sinks == [None]
    assert app.boot_logo_until_s == 4.0


def test_build_executor_with_optional_trace_uses_trace_sink_when_runtime_app_exposes_it():
    class FakeApp:
        def emit_probe_trace(self, definition, event, fields):
            return (definition, event, fields)

    app = FakeApp()

    built = firmware_runtime._build_executor_with_optional_trace(
        lambda trace_sink=None: trace_sink,
        getattr(app, "emit_probe_trace"),
    )

    assert built.__self__ is app
    assert built.__func__ is FakeApp.emit_probe_trace


def test_build_runtime_app_passes_eink_family_to_runtime_app():
    called = {}

    class FakeApp:
        def __init__(self, **kwargs):
            called.update(kwargs)

        def inject_diagnostics(self, diagnostics, activate=True):
            called["diagnostics"] = (diagnostics, activate)

        def configure_observability(self, **kwargs):
            called["observability"] = kwargs

        def _refresh_network_state(self, **kwargs):
            called.setdefault("network_refreshes", []).append(kwargs)

    app = firmware_runtime.build_runtime_app(
        {
            "project": {},
            "device": {
                "display": {"type": "waveshare-pico-epaper-2.13-b-v4"},
                "buttons": {"a": "GP15", "b": "GP17"},
            },
        },
        input_controller_factory=lambda: object(),
        display_factory=lambda config: SimpleNamespace(show_boot_logo=lambda version: None),
        button_reader_factory=lambda config, input_controller: object(),
        runtime_app_factory=FakeApp,
        definitions_builder=lambda config: (),
        executor_factory=lambda trace_sink=None: object(),
        wifi_connector=lambda config: (),
        now_provider=lambda: 0.0,
        sleep_ms=lambda ms: None,
    )

    assert isinstance(app, FakeApp)
    assert called["display_family"] == "eink"


def test_visible_degraded_from_config_defaults_true_and_parses_boolean_strings():
    assert firmware_runtime._visible_degraded_from_config({}) is True
    assert firmware_runtime._visible_degraded_from_config({"check_state": {"visible_degraded": False}}) is False
    assert firmware_runtime._visible_degraded_from_config({"check_state": {"visible_degraded": "no"}}) is False

    with pytest.raises(ValueError, match="check_state.visible_degraded must be a boolean"):
        firmware_runtime._visible_degraded_from_config({"check_state": {"visible_degraded": object()}})

def test_build_runtime_app_forces_serial_probe_execution_when_background_workers_are_enabled():
    trace_sinks = []

    class FakeApp:
        background_workers_enabled = True

        def __init__(self, **kwargs):
            self.executor = None
            self.logger = SimpleNamespace(sink=None)

        def emit_probe_trace(self, definition, event, fields):
            return (definition, event, fields)

        def configure_observability(self, **kwargs):
            return None

        def _refresh_network_state(self, **kwargs):
            return None

    app = firmware_runtime.build_runtime_app(
        {"device": {"display": {"width_px": 128, "height_px": 64, "font": {"width_px": 8, "height_px": 8}}}},
        input_controller_factory=lambda: object(),
        display_factory=lambda config: SimpleNamespace(show_boot_logo=lambda version: None),
        button_reader_factory=lambda config, input_controller: None,
        runtime_app_factory=lambda **kwargs: FakeApp(**kwargs),
        definitions_builder=lambda config: (),
        executor_factory=lambda trace_sink=None: trace_sinks.append(trace_sink) or object(),
        wifi_connector=lambda config: (),
        now_provider=lambda: 0.0,
        sleep_ms=lambda ms: None,
    )

    assert app.executor is not None
    assert app.background_workers_enabled is False
    assert len(trace_sinks) == 1
    assert trace_sinks[0].__self__ is app
    assert trace_sinks[0].__func__ is FakeApp.emit_probe_trace


def test_build_runtime_app_does_not_prime_initial_checks_during_boot():
    called = {}
    definitions = (object(),)

    class FakeApp:
        def __init__(self, **kwargs):
            called.update(kwargs)
            called["prime_now_s"] = None

        def inject_diagnostics(self, diagnostics, activate=True):
            called["diagnostics"] = (diagnostics, activate)

        def run_all_checks(self, now_s=None):
            called["prime_now_s"] = now_s

    now_values = iter([0.0, 1.0, 1.0])

    firmware_runtime.build_runtime_app(
        {
            "project": {},
            "device": {
                "display": {"width_px": 128, "height_px": 64, "font": {"width_px": 8, "height_px": 8}},
                "buttons": {"a": "GP15", "b": "GP17"},
            },
        },
        input_controller_factory=lambda: object(),
        display_factory=lambda config: SimpleNamespace(show_boot_logo=lambda version: None),
        button_reader_factory=lambda config, input_controller: object(),
        runtime_app_factory=FakeApp,
        definitions_builder=lambda config: definitions,
        executor_factory=lambda: object(),
        wifi_connector=lambda config: (),
        now_provider=lambda: next(now_values),
        sleep_ms=lambda ms: None,
    )

    assert called["prime_now_s"] is None


def test_build_runtime_app_uses_fixed_boot_logo_duration():
    class FakeApp:
        def __init__(self, **kwargs):
            pass

    app = firmware_runtime.build_runtime_app(
        {
            "project": {"version": "1.2.3"},
            "device": {
                "display": {
                    "width_px": 128,
                    "height_px": 64,
                    "boot_logo_duration_s": 7,
                    "font": {"width_px": 8, "height_px": 8},
                },
                "buttons": {"a": "GP15", "b": "GP17"},
            },
        },
        input_controller_factory=lambda: object(),
        display_factory=lambda config: SimpleNamespace(show_boot_logo=lambda version: None),
        button_reader_factory=lambda config, input_controller: object(),
        runtime_app_factory=FakeApp,
        definitions_builder=lambda config: (),
        executor_factory=lambda: object(),
        wifi_connector=lambda config: (),
        now_provider=lambda: 10.0,
        sleep_ms=lambda ms: None,
    )

    assert app.boot_logo_until_s == 14.0


def test_build_runtime_app_falls_back_to_default_display_when_primary_display_init_fails():
    called = {}

    class FakeApp:
        def __init__(self, **kwargs):
            called.update(kwargs)
            called["diagnostics"] = None

        def inject_diagnostics(self, diagnostics, activate=True):
            called["diagnostics"] = (diagnostics, activate)

    def fake_display_factory(config):
        if config["type"] != "waveshare-pico-oled-1.3":
            raise RuntimeError("display init boom")
        return SimpleNamespace(show_boot_logo=lambda version: None)

    firmware_runtime.build_runtime_app(
        {
            "project": {},
            "device": {
                "display": {"type": "waveshare-pico-epaper-2.13-v4"},
                "buttons": {"a": "GP15", "b": "GP17"},
            },
        },
        input_controller_factory=lambda: object(),
        display_factory=fake_display_factory,
        button_reader_factory=lambda config, input_controller: object(),
        runtime_app_factory=FakeApp,
        definitions_builder=lambda config: (),
        executor_factory=lambda: object(),
        wifi_connector=lambda config: (),
        now_provider=lambda: 0.0,
        sleep_ms=lambda ms: None,
    )

    assert called["row_width"] == 16
    assert called["page_size"] == 8
    diagnostics, activate = called["diagnostics"]
    assert activate is True
    assert any(event.code == "DISP" and event.message == "init failed" for event in diagnostics)
    assert any(event.code == "DISP" and event.message == "fallback" for event in diagnostics)


def test_build_runtime_app_recovers_from_invalid_definitions_and_records_boot_error():
    display = SimpleNamespace(show_boot_logo=lambda version: None, draw_frame=lambda frame: None)

    app = firmware_runtime.build_runtime_app(
        {
            "project": {},
            "device": {
                "display": {
                    "width_px": 128,
                    "height_px": 64,
                    "font": {"width_px": 8, "height_px": 8},
                },
                "buttons": {"a": "GP15", "b": "GP17"},
            },
        },
        input_controller_factory=lambda: object(),
        display_factory=lambda config: display,
        button_reader_factory=lambda config, input_controller: object(),
        definitions_builder=lambda config: (_ for _ in ()).throw(ValueError("bad checks")),
        executor_factory=lambda: object(),
        wifi_connector=lambda config: (),
        now_provider=lambda: 0.0,
        sleep_ms=lambda ms: None,
    )

    assert app.definitions == ()
    assert app.state.mode.value == "diagnostics"
    assert any(line.startswith("CONF checks bad") for line in app.state.diagnostics)
    assert any(error["scope"] == "config" for error in app.get_errors())


def test_build_runtime_app_from_path_uses_fallback_config_when_config_file_is_missing(monkeypatch):
    called = {}

    class FakeApp:
        def __init__(self, **kwargs):
            called.update(kwargs)
            called["diagnostics"] = None

        def inject_diagnostics(self, diagnostics, activate=True):
            called["diagnostics"] = (diagnostics, activate)

    monkeypatch.setattr(firmware_runtime, "load_config", lambda path: (_ for _ in ()).throw(OSError("missing")))

    firmware_runtime.build_runtime_app_from_path(
        "missing.json",
        input_controller_factory=lambda: object(),
        display_factory=lambda config: SimpleNamespace(show_boot_logo=lambda version: None),
        button_reader_factory=lambda config, input_controller: object(),
        runtime_app_factory=FakeApp,
        definitions_builder=lambda config: (),
        executor_factory=lambda: object(),
        wifi_connector=lambda config: (),
        now_provider=lambda: 0.0,
        sleep_ms=lambda ms: None,
    )

    diagnostics, activate = called["diagnostics"]
    assert activate is True
    assert any(event.code == "CONF" and event.message == "missing" for event in diagnostics)


def test_headless_display_retains_only_the_latest_frame():
    display = firmware_runtime.HeadlessDisplay()

    display.draw_frame("frame-1")
    display.draw_frame("frame-2")

    assert display.frames == ["frame-2"]


def test_build_runtime_app_infers_geometry_and_page_interval_from_display_type():
    called = {}

    class FakeApp:
        def __init__(self, **kwargs):
            called.update(kwargs)

        def inject_diagnostics(self, diagnostics, activate=True):
            called["diagnostics"] = diagnostics

    captured_display = {}

    def fake_display_factory(config):
        captured_display["config"] = config
        return SimpleNamespace(show_boot_logo=lambda version: None)

    firmware_runtime.build_runtime_app(
        {
            "project": {},
            "device": {
                "display": {"type": "waveshare-pico-epaper-2.13-b-v4"},
                "buttons": {"a": "GP15", "b": "GP17"},
            },
        },
        input_controller_factory=lambda: object(),
        display_factory=fake_display_factory,
        button_reader_factory=lambda config, input_controller: object(),
        runtime_app_factory=FakeApp,
        definitions_builder=lambda config: (),
        executor_factory=lambda: object(),
        wifi_connector=lambda config: (),
        now_provider=lambda: 0.0,
        sleep_ms=lambda ms: None,
    )

    assert captured_display["config"]["width_px"] == 250
    assert captured_display["config"]["height_px"] == 122
    assert captured_display["config"]["font_size"] == "medium"
    assert captured_display["config"]["font"] == {"width_px": 13, "height_px": 13}
    assert captured_display["config"]["rotation"] == 0
    assert called["row_width"] == 19
    assert called["page_size"] == 9
    assert called["page_interval_s"] == 180


def test_build_runtime_app_does_not_wait_for_boot_logo_or_wifi_during_boot():
    sleep_calls = []
    wifi_calls = []

    class FakeApp:
        def __init__(self, **kwargs):
            pass

        def inject_diagnostics(self, diagnostics, activate=True):
            pass

    now_times = iter([0.0, 1.0, 1.0])
    display = SimpleNamespace(show_boot_logo=lambda version: None)

    firmware_runtime.build_runtime_app(
        {
            "project": {"version": "0.1.0"},
            "device": {
                "display": {
                    "width_px": 128,
                    "height_px": 64,
                    "font": {"width_px": 8, "height_px": 8},
                },
                "buttons": {"a": "GP15", "b": "GP17"},
            },
        },
        input_controller_factory=lambda: object(),
        display_factory=lambda config: display,
        button_reader_factory=lambda config, input_controller: object(),
        runtime_app_factory=FakeApp,
        definitions_builder=lambda config: (),
        executor_factory=lambda: object(),
        wifi_connector=lambda config: wifi_calls.append(config) or (),
        now_provider=lambda: next(now_times),
        sleep_ms=lambda ms: sleep_calls.append(ms),
    )

    assert sleep_calls == []
    assert wifi_calls == []


def test_build_runtime_app_runs_button_self_test_when_enabled():
    calls = []

    class FakeApp:
        def __init__(self, **kwargs):
            self.executor = None

        def inject_diagnostics(self, diagnostics, activate=True):
            pass

    display = SimpleNamespace(show_boot_logo=lambda version: None)
    button_reader = object()

    original_self_test = firmware_runtime._run_button_self_test
    firmware_runtime._run_button_self_test = (
        lambda display, button_reader, button_config, row_width, page_size, now_provider, sleep_ms: calls.append(
            {
                "display": display,
                "button_reader": button_reader,
                "button_config": button_config,
                "row_width": row_width,
                "page_size": page_size,
            }
        )
    )
    try:
        firmware_runtime.build_runtime_app(
            {
                "project": {"version": "0.1.0"},
                "device": {
                    "display": {
                        "width_px": 128,
                        "height_px": 64,
                        "font": {"width_px": 8, "height_px": 8},
                    },
                    "buttons": {"a": "GP15", "b": "GP17", "startup_self_test_s": 12},
                },
            },
            input_controller_factory=lambda: object(),
            display_factory=lambda config: display,
            button_reader_factory=lambda config, input_controller: button_reader,
            runtime_app_factory=FakeApp,
            definitions_builder=lambda config: (),
            executor_factory=lambda trace_sink=None: object(),
            wifi_connector=lambda config: (),
            now_provider=lambda: 0.0,
            sleep_ms=lambda ms: None,
        )
    finally:
        firmware_runtime._run_button_self_test = original_self_test

    assert calls == [
        {
            "display": display,
            "button_reader": button_reader,
            "button_config": {"a": "GP15", "b": "GP17", "startup_self_test_s": 12},
            "row_width": 16,
            "page_size": 8,
        }
    ]


def test_button_self_test_frame_shows_configured_and_candidate_pins():
    frame = firmware_runtime._button_self_test_frame(
        {
            "A": {"pin": "GP15", "raw": 0, "stable": 1},
            "B": {"pin": "GP17", "raw": 1, "stable": 1},
        },
        {"GP15": 0, "GP17": 1, "GP21": 1, "GP22": 1},
        row_width=16,
        page_size=8,
        remaining_s=11.4,
    )

    assert tuple(row.rstrip() for row in frame.rows[:7]) == (
        "BTN SELFTEST",
        "A15 r0 s1",
        "B17 r1 s1",
        "15:0 17:1",
        "21:1 22:1",
        "PRESS BTN NOW",
        "T-11.4s",
    )


def test_run_button_self_test_skips_when_disabled(monkeypatch):
    logs = []

    monkeypatch.setattr(firmware_runtime, "_serial_log", lambda stage, message, **fields: logs.append((stage, message, fields)))

    ran = firmware_runtime._run_button_self_test(
        display=object(),
        button_reader=object(),
        button_config={"a": "GP15", "b": "GP17"},
        row_width=16,
        page_size=8,
        now_provider=lambda: 0.0,
        sleep_ms=lambda ms: None,
    )

    assert ran is False
    assert logs == [("BTNTEST", "skip", {"reason": "disabled"})]


def test_run_forever_runs_second_chance_button_self_test_before_startup_tick(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.boot_logo_until_s = 18.5
            self.tick_calls = []
            self.prime_calls = []
            self.config = {"device": {"buttons": {"a": "GP15", "b": "GP17", "startup_self_test_s": 12}}}
            self.display = object()
            self.button_reader = object()
            self.state = SimpleNamespace(row_width=16, page_size=8)
            self.button_self_test_ran = False

        def prime_due_checks(self, now_s):
            self.prime_calls.append(now_s)

        def tick(self, now_s, button_events=None):
            self.tick_calls.append((now_s, button_events))

    fake_app = FakeApp()
    called = {}
    self_test_calls = []

    monkeypatch.setattr(firmware_runtime, "build_runtime_app_from_path", lambda path: fake_app)
    monkeypatch.setattr(firmware_runtime, "_now_s", lambda: 12.5)
    monkeypatch.setattr(
        firmware_runtime,
        "_maybe_run_button_self_test_from_app",
        lambda app, now_provider, sleep_ms, watchdog=None: self_test_calls.append(app),
    )
    monkeypatch.setattr(firmware_runtime, "_run_startup_network", lambda app, now_s: now_s)
    monkeypatch.setattr(
        firmware_runtime,
        "_wait_for_boot_logo",
        lambda app, now_provider=None, sleep_ms=None, watchdog=None: 18.5,
    )
    monkeypatch.setattr(
        firmware_runtime,
        "run_loop",
        lambda app, poll_interval_ms=50, now_provider=None, watchdog=None: called.update({"app": app, "poll_interval_ms": poll_interval_ms}),
    )

    firmware_runtime.run_forever(poll_interval_ms=75)

    assert self_test_calls == [fake_app]
    assert called == {"app": fake_app, "poll_interval_ms": 75}
    assert fake_app.prime_calls == [18.5]
    assert fake_app.tick_calls == [(18.5, ())]


def test_run_loop_ticks_and_sleeps_with_injected_clock():
    now_values = iter([1.0, 2.0, 3.0])
    app = SimpleNamespace(ticks=[])
    app.tick = lambda now_s: app.ticks.append(now_s)
    sleeps = []

    firmware_runtime.run_loop(
        app,
        poll_interval_ms=50,
        iterations=3,
        now_provider=lambda: next(now_values),
        sleep_ms=lambda value: sleeps.append(value),
    )

    assert app.ticks == [1.0, 2.0, 3.0]
    assert sleeps == [50, 50, 50]


def test_run_loop_defaults_to_20ms_sleep_interval():
    now_values = iter([1.0, 2.0, 3.0])
    app = SimpleNamespace(ticks=[])
    app.tick = lambda now_s: app.ticks.append(now_s)
    sleeps = []

    firmware_runtime.run_loop(
        app,
        iterations=3,
        now_provider=lambda: next(now_values),
        sleep_ms=lambda value: sleeps.append(value),
    )

    assert app.ticks == [1.0, 2.0, 3.0]
    assert sleeps == [20, 20, 20]


def test_wait_for_boot_logo_sleeps_in_chunks_and_feeds_watchdog():
    clock = {"now": 0.0}
    sleeps = []

    class Watchdog:
        def __init__(self):
            self.feed_count = 0

        def feed(self):
            self.feed_count += 1
            return True

    def sleep_ms(value):
        sleeps.append(value)
        clock["now"] += value / 1000.0

    ready_now_s = firmware_runtime._wait_for_boot_logo(
        SimpleNamespace(boot_logo_until_s=1.0),
        now_provider=lambda: clock["now"],
        sleep_ms=sleep_ms,
        watchdog=Watchdog(),
    )

    assert ready_now_s == pytest.approx(1.0)
    assert sleeps == [250, 250, 250, 250]


def test_run_loop_requests_watchdog_reset_after_repeated_tick_failures():
    class App:
        def tick(self, now_s):
            raise RuntimeError(f"boom-{now_s}")

    class Watchdog:
        def __init__(self):
            self.feed_calls = 0
            self.reset_requests = []

        def feed(self):
            self.feed_calls += 1
            return True

        def request_reset(self, reason, **fields):
            self.reset_requests.append((reason, fields))
            return True

    clock = iter([1.0, 2.0, 3.0])
    watchdog = Watchdog()

    firmware_runtime.run_loop(
        App(),
        iterations=3,
        now_provider=lambda: next(clock),
        sleep_ms=lambda value: None,
        watchdog=watchdog,
        max_consecutive_loop_failures=2,
    )

    assert watchdog.reset_requests == [
        ("loop-failures", {"count": 2, "error": "RuntimeError"}),
    ]


def test_run_loop_requests_watchdog_reset_after_repeated_display_failures():
    class App:
        def __init__(self):
            self.display_failure_count = 5

        def tick(self, now_s):
            return None

    class Watchdog:
        def __init__(self):
            self.reset_requests = []

        def feed(self):
            return True

        def request_reset(self, reason, **fields):
            self.reset_requests.append((reason, fields))
            return True

    watchdog = Watchdog()

    firmware_runtime.run_loop(
        App(),
        iterations=1,
        now_provider=lambda: 1.0,
        sleep_ms=lambda value: None,
        watchdog=watchdog,
        max_consecutive_display_failures=5,
    )

    assert watchdog.reset_requests == [
        ("display-failures", {"count": 5}),
    ]


def test_run_forever_waits_for_boot_logo_before_startup_work_and_enters_run_loop(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.boot_logo_until_s = 18.5
            self.tick_calls = []
            self.prime_calls = []

        def prime_due_checks(self, now_s):
            self.prime_calls.append(now_s)

        def tick(self, now_s, button_events=None):
            self.tick_calls.append((now_s, button_events))

    fake_app = FakeApp()
    called = {}
    wait_calls = []

    monkeypatch.setattr(firmware_runtime, "build_runtime_app_from_path", lambda path: fake_app)
    monkeypatch.setattr(firmware_runtime, "_now_s", lambda: 12.5)
    monkeypatch.setattr(firmware_runtime, "_run_startup_network", lambda app, now_s: now_s)
    monkeypatch.setattr(
        firmware_runtime,
        "_wait_for_boot_logo",
        lambda app, now_provider=None, sleep_ms=None, watchdog=None: wait_calls.append(app.boot_logo_until_s) or 18.5,
    )
    monkeypatch.setattr(
        firmware_runtime,
        "run_loop",
        lambda app, poll_interval_ms=50, now_provider=None, watchdog=None: called.update({"app": app, "poll_interval_ms": poll_interval_ms}),
    )

    firmware_runtime.run_forever(poll_interval_ms=75)

    assert called == {"app": fake_app, "poll_interval_ms": 75}
    assert wait_calls == [18.5]
    assert fake_app.prime_calls == [18.5]
    assert fake_app.tick_calls == [(18.5, ())]


def test_run_startup_network_connects_without_activating_diagnostics(monkeypatch):
    calls = []

    class FakeApp:
        def __init__(self):
            self.connected = False

        def get_network_state_snapshot(self):
            return {"connected": self.connected, "ip_address": "192.0.2.50"}

        def connect_network(self, activate_diagnostics=False):
            calls.append(activate_diagnostics)
            self.connected = True
            return self.get_network_state_snapshot()

    monkeypatch.setattr(firmware_runtime, "_now_s", lambda: 15.5)

    ready_now_s = firmware_runtime._run_startup_network(FakeApp(), 12.5)

    assert calls == [False]
    assert ready_now_s == 15.5


def test_run_forever_connects_network_before_waiting_for_boot_logo(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.boot_logo_until_s = 16.5
            self.tick_calls = []
            self.prime_calls = []
            self.connect_calls = []
            self.connected = False

        def get_network_state_snapshot(self):
            return {"connected": self.connected, "ip_address": "192.0.2.50"}

        def connect_network(self, activate_diagnostics=False):
            self.connect_calls.append(activate_diagnostics)
            self.connected = True
            return self.get_network_state_snapshot()

        def prime_due_checks(self, now_s):
            self.prime_calls.append((now_s, self.connected))

        def tick(self, now_s, button_events=None):
            self.tick_calls.append((now_s, button_events, self.connected))

    fake_app = FakeApp()
    called = {}
    events = []

    monkeypatch.setattr(firmware_runtime, "build_runtime_app_from_path", lambda path: fake_app)
    monkeypatch.setattr(firmware_runtime, "_now_s", lambda: 12.5)
    original_run_startup_network = firmware_runtime._run_startup_network
    monkeypatch.setattr(
        firmware_runtime,
        "_run_startup_network",
        lambda app, now_s: events.append(("network", now_s)) or original_run_startup_network(app, now_s),
    )
    monkeypatch.setattr(
        firmware_runtime,
        "_wait_for_boot_logo",
        lambda app, now_provider=None, sleep_ms=None, watchdog=None: events.append(("wait", app.boot_logo_until_s)) or 16.5,
    )
    monkeypatch.setattr(
        firmware_runtime,
        "run_loop",
        lambda app, poll_interval_ms=50, now_provider=None, watchdog=None: called.update({"app": app, "poll_interval_ms": poll_interval_ms}),
    )

    firmware_runtime.run_forever(poll_interval_ms=75)

    assert called == {"app": fake_app, "poll_interval_ms": 75}
    assert fake_app.connect_calls == [False]
    assert events == [("network", 12.5), ("wait", 16.5)]
    assert fake_app.prime_calls == [(16.5, True)]
    assert fake_app.tick_calls == [(16.5, (), True)]


def test_build_eink_probe_summary_frame_formats_ok_and_fail_rows():
    display = SimpleNamespace(height=122, font_height=15)
    app = SimpleNamespace(
        state=SimpleNamespace(
            row_width=16,
            checks=(
                CheckRuntime(identifier="u64-rest", name="U64 REST", status=Status.OK),
                CheckRuntime(identifier="u64-ftp", name="U64 FTP", status=Status.OK),
                CheckRuntime(identifier="pixel4-adb", name="PIXEL4 ADB", status=Status.OK),
                CheckRuntime(identifier="u64-telnet", name="U64 TELNET", status=Status.OK),
                CheckRuntime(identifier="c64u-rest", name="C64U REST", status=Status.FAIL),
                CheckRuntime(identifier="c64u-ftp", name="C64U FTP", status=Status.FAIL),
                CheckRuntime(identifier="c64u-telnet", name="C64U TELNET", status=Status.FAIL),
            ),
        ),
        display=display,
        display_liveness={
            "bottom_heartbeat": {
                "enabled": True,
                "period_s": 1,
                "pixel_count": 1,
                "position": "left",
                "pixel_width_px": 2,
                "pixel_height_px": 2,
                "gap_px": 3,
            }
        },
        _frame_bottom_pixels=lambda now_s: (6,),
    )

    frame = firmware_runtime._build_eink_probe_summary_frame(app, now_s=18.5)

    assert tuple(row.rsplit(None, 1)[0] for row in frame.rows) == (
        "C64U FTP",
        "C64U REST",
        "C64U TELNET",
        "PIXEL4 ADB",
        "U64 FTP",
        "U64 REST",
        "U64 TELNET",
    )
    assert tuple(row.rsplit(None, 1)[1] for row in frame.rows) == (
        "FAIL",
        "FAIL",
        "FAIL",
        "OK",
        "OK",
        "OK",
        "OK",
    )
    assert frame.failure_spans == (
        TextSpan(row_index=0, start_column=12, end_column=16),
        TextSpan(row_index=1, start_column=12, end_column=16),
        TextSpan(row_index=2, start_column=12, end_column=16),
    )
    assert frame.shift_offset == (0, 6)
    assert frame.bottom_pixels == (6,)
    assert frame.bottom_pixel_width_px == 2
    assert frame.bottom_pixel_height_px == 2


def test_build_runtime_app_skips_boot_logo_for_eink(monkeypatch):
    display = SimpleNamespace(show_boot_logo=lambda version: None, _watchdog_feed=None, font_width=16, font_height=16)
    called = {"boot_logo": 0}

    monkeypatch.setattr(
        firmware_runtime,
        "_safe_show_boot_logo",
        lambda passed_display, version: called.__setitem__("boot_logo", called["boot_logo"] + 1) or ((), ()),
    )

    app = firmware_runtime.build_runtime_app(
        {
            "project": {"version": "1.2.3"},
            "device": {
                "display": {
                    "family": "eink",
                    "type": "waveshare-pico-epaper-2.13-b-v4",
                    "width_px": 250,
                    "height_px": 122,
                    "font": {"width_px": 15, "height_px": 15},
                },
                "buttons": {"a": "GP15", "b": "GP17"},
            },
        },
        input_controller_factory=lambda: object(),
        display_factory=lambda config: display,
        button_reader_factory=lambda config, input_controller: object(),
        runtime_app_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        definitions_builder=lambda config: (),
        executor_factory=lambda trace_sink=None: object(),
        now_provider=lambda: 0.0,
        sleep_ms=lambda ms: None,
    )

    assert called["boot_logo"] == 0
    assert app.boot_logo_until_s == 0.0


def test_run_eink_probe_summary_runs_checks_and_draws_frame():
    definitions = (
        CheckDefinition(identifier="adb", name="ADB", check_type=CheckType.HTTP, target="adb"),
        CheckDefinition(identifier="ping", name="PING", check_type=CheckType.PING, target="ping"),
    )
    display = firmware_runtime.HeadlessDisplay()
    calls = []

    class Watchdog:
        def __init__(self):
            self.feed_calls = 0

        def feed(self):
            self.feed_calls += 1
            return True

    app = SimpleNamespace(
        state=SimpleNamespace(row_width=16),
        display=display,
        definitions=definitions,
        registered_results={
            "adb": {"name": "ADB", "status": "?"},
            "ping": {"name": "PING", "status": "?"},
        },
        run_all_checks=lambda now_s: calls.append(now_s) or app.registered_results.update(
            {
                "adb": {"name": "ADB", "status": "OK"},
                "ping": {"name": "PING", "status": "FAIL"},
            }
        ),
    )
    watchdog = Watchdog()

    firmware_runtime._run_eink_probe_summary(app, 18.5, watchdog=watchdog)

    assert calls == [18.5]
    assert watchdog.feed_calls == 3
    assert len(display.frames) == 1
    assert display.frames[0].rows == (
        "ADB           OK",
        "PING        FAIL",
    )


def test_render_eink_summary_frame_skips_redraw_when_summary_output_is_already_visible():
    drawn_frames = []
    propagation_logs = []
    display = SimpleNamespace(height=16, font_height=8, draw_frame=lambda frame: drawn_frames.append(frame))
    app = SimpleNamespace(
        state=SimpleNamespace(
            row_width=16,
            checks=(CheckRuntime(identifier="router", name="Router", status=Status.FAIL),),
        ),
        display=display,
        display_liveness={"bottom_heartbeat": {"enabled": False}},
        pending_status_updates={"router": {"status": "FAIL", "observed_at_s": 5.0}},
        display_retry_at_s=None,
        last_rendered_frame=None,
        _log_display_propagation=lambda now_s, reason: propagation_logs.append((now_s, reason)),
    )
    app.last_rendered_frame = firmware_runtime._build_eink_probe_summary_frame(app, now_s=5.0)

    reason = firmware_runtime._render_eink_summary_frame(app, 6.0, "state")

    assert reason == "none"
    assert drawn_frames == []
    assert propagation_logs == [(6.0, "state")]


def test_run_eink_refresh_loop_renders_state_changes_without_waiting_for_periodic_refresh(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.page_interval_s = 180
            self.last_rendered_frame = None
            self.pending_status_updates = {}

    fake_app = FakeApp()
    tick_calls = []
    render_calls = []
    sleep_calls = []
    steady_values = iter((18.7, 18.9, 19.1))
    sentinel = RuntimeError("stop after third poll")

    monkeypatch.setattr(
        firmware_runtime,
        "_tick_eink_summary_state",
        lambda app, now_s, watchdog=None: (
            tick_calls.append(now_s),
            setattr(app, "pending_status_updates", {"c64u": {"status": "FAIL", "observed_at_s": now_s}})
            if len(tick_calls) == 2
            else None,
        ),
    )
    monkeypatch.setattr(
        firmware_runtime,
        "_render_eink_summary_frame",
        lambda app, now_s, reason: render_calls.append((now_s, reason)) or setattr(app, "last_rendered_frame", object()) or app.pending_status_updates.clear() or reason,
    )
    monkeypatch.setattr(firmware_runtime, "_steady_now_s", lambda: next(steady_values))
    monkeypatch.setattr(
        firmware_runtime,
        "_sleep_ms",
        lambda value: sleep_calls.append(value) if len(sleep_calls) < 2 else (_ for _ in ()).throw(sentinel),
    )

    with pytest.raises(RuntimeError, match="stop after third poll"):
        firmware_runtime._run_eink_refresh_loop(fake_app, 18.5, poll_interval_ms=200, now_provider=firmware_runtime._steady_now_s, sleep_ms=firmware_runtime._sleep_ms)

    assert tick_calls == [18.5, 18.7, 18.9]
    assert render_calls == [(18.5, "startup"), (18.7, "state")]
    assert sleep_calls == [200, 200]


def test_run_eink_refresh_loop_waits_for_a_completed_probe_cycle_before_rendering_state_change(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.page_interval_s = 180
            self.last_rendered_frame = None
            self.pending_status_updates = {}
            self.pending_status_cycle_started = None
            self.display_refresh = {"min_interval_s": 0, "probe_cycles_per_refresh": 1}
            self.completed_cycles = 0

        def completed_probe_cycles(self):
            return self.completed_cycles

    fake_app = FakeApp()
    tick_calls = []
    render_calls = []
    sleep_calls = []
    steady_values = iter((18.7, 18.9, 19.1))
    sentinel = RuntimeError("stop after third poll")

    def fake_tick(app, now_s, watchdog=None):
        tick_calls.append(now_s)
        if len(tick_calls) == 2:
            app.pending_status_updates = {"c64u": {"status": "FAIL", "observed_at_s": now_s}}
            app.pending_status_cycle_started = 0
        elif len(tick_calls) == 3:
            app.completed_cycles = 1

    monkeypatch.setattr(firmware_runtime, "_tick_eink_summary_state", fake_tick)
    monkeypatch.setattr(
        firmware_runtime,
        "_render_eink_summary_frame",
        lambda app, now_s, reason: render_calls.append((now_s, reason))
        or setattr(app, "last_rendered_frame", object())
        or app.pending_status_updates.clear()
        or setattr(app, "pending_status_cycle_started", None)
        or reason,
    )
    monkeypatch.setattr(firmware_runtime, "_steady_now_s", lambda: next(steady_values))
    monkeypatch.setattr(
        firmware_runtime,
        "_sleep_ms",
        lambda value: sleep_calls.append(value) if len(sleep_calls) < 2 else (_ for _ in ()).throw(sentinel),
    )

    with pytest.raises(RuntimeError, match="stop after third poll"):
        firmware_runtime._run_eink_refresh_loop(fake_app, 18.5, poll_interval_ms=200, now_provider=firmware_runtime._steady_now_s, sleep_ms=firmware_runtime._sleep_ms)

    assert tick_calls == [18.5, 18.7, 18.9]
    assert render_calls == [(18.5, "startup"), (18.9, "state")]
    assert sleep_calls == [200, 200]


def test_run_eink_refresh_loop_respects_minimum_refresh_interval_for_state_changes(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.page_interval_s = 180
            self.last_rendered_frame = object()
            self.last_render_at_s = 5.0
            self.pending_status_updates = {"c64u": {"status": "FAIL", "observed_at_s": 5.0}}
            self.pending_status_cycle_started = 0
            self.display_refresh = {"min_interval_s": 10, "probe_cycles_per_refresh": 1}

        def completed_probe_cycles(self):
            return 1

    fake_app = FakeApp()
    tick_calls = []
    render_calls = []
    sleep_calls = []
    steady_values = iter((10.0, 15.0, 20.0))
    sentinel = RuntimeError("stop after third poll")

    monkeypatch.setattr(
        firmware_runtime,
        "_tick_eink_summary_state",
        lambda app, now_s, watchdog=None: tick_calls.append(now_s),
    )
    monkeypatch.setattr(
        firmware_runtime,
        "_render_eink_summary_frame",
        lambda app, now_s, reason: render_calls.append((now_s, reason))
        or app.pending_status_updates.clear()
        or setattr(app, "pending_status_cycle_started", None)
        or reason,
    )
    monkeypatch.setattr(firmware_runtime, "_steady_now_s", lambda: next(steady_values))
    monkeypatch.setattr(
        firmware_runtime,
        "_sleep_ms",
        lambda value: sleep_calls.append(value) if len(sleep_calls) < 2 else (_ for _ in ()).throw(sentinel),
    )

    with pytest.raises(RuntimeError, match="stop after third poll"):
        firmware_runtime._run_eink_refresh_loop(fake_app, 6.0, poll_interval_ms=200, now_provider=firmware_runtime._steady_now_s, sleep_ms=firmware_runtime._sleep_ms)

    assert tick_calls == [6.0, 10.0, 15.0]
    assert render_calls == [(15.0, "state")]
    assert sleep_calls == [200, 200]


def test_tick_eink_summary_state_limits_runtime_step_to_one_due_check():
    calls = []

    class FakeApp:
        def step(self, now_s, button_events=(), render=True, max_due_checks=None):
            calls.append((now_s, button_events, render, max_due_checks))

    firmware_runtime._tick_eink_summary_state(FakeApp(), 18.5)

    assert calls == [(18.5, (), False, 1)]


def test_run_forever_routes_eink_to_summary_refresh_loop(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.boot_logo_until_s = 18.5
            self.display_family = "eink"

    fake_app = FakeApp()
    called = {}

    monkeypatch.setattr(firmware_runtime, "build_runtime_app_from_path", lambda path: fake_app)
    monkeypatch.setattr(firmware_runtime, "_now_s", lambda: 12.5)
    monkeypatch.setattr(firmware_runtime, "_run_startup_network", lambda app, now_s: now_s)
    monkeypatch.setattr(
        firmware_runtime,
        "_wait_for_boot_logo",
        lambda app, now_provider=None, sleep_ms=None, watchdog=None: 18.5,
    )
    monkeypatch.setattr(
        firmware_runtime,
        "_run_eink_refresh_loop",
        lambda app, now_s, poll_interval_ms=20, now_provider=None, sleep_ms=None, watchdog=None: called.update(
            {
                "app": app,
                "now_s": now_s,
                "poll_interval_ms": poll_interval_ms,
                "watchdog": watchdog,
            }
        ),
    )

    firmware_runtime.run_forever(poll_interval_ms=75)

    assert called == {
        "app": fake_app,
        "now_s": 18.5,
        "poll_interval_ms": 75,
        "watchdog": None,
    }


def test_render_eink_safe_hold_screen_draws_sparse_text_frame():
    display = firmware_runtime.HeadlessDisplay()
    app = SimpleNamespace(display=display, state=SimpleNamespace(row_width=19))

    firmware_runtime._render_eink_safe_hold_screen(app, 18.5)

    assert len(display.frames) == 1
    assert display.frames[0].rows == (
        "VIVIPI EPAPER      ",
        "SAFE HOLD MODE     ",
        "STATIC SCREEN      ",
    )


def test_hold_safe_display_feeds_watchdog_while_idle():
    class Watchdog:
        def __init__(self):
            self.feed_calls = 0

        def feed(self):
            self.feed_calls += 1
            return True

    sleeps = []
    watchdog = Watchdog()

    firmware_runtime.hold_safe_display(
        poll_interval_ms=75,
        sleep_ms=lambda value: sleeps.append(value),
        watchdog=watchdog,
        iterations=3,
    )

    assert sleeps == [75, 75, 75]
    assert watchdog.feed_calls == 3


def test_run_startup_tick_uses_prime_due_checks_when_available():
    calls = []

    class FakeApp:
        def prime_due_checks(self, now_s):
            calls.append(("prime", now_s))

    firmware_runtime._run_startup_tick(FakeApp(), 12.5)

    assert calls == [("prime", 12.5)]
