from types import SimpleNamespace

import firmware.runtime as firmware_runtime
from vivipi.core.models import DiagnosticEvent, DisplayMode


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

    def active(self, enabled):
        self.active_calls.append(enabled)

    def isconnected(self):
        return self.connected

    def connect(self, ssid, password):
        self.connect_calls.append((ssid, password))
        self.connected = True


def test_connect_wifi_requires_ssid(monkeypatch):
    fake_time = FakeTime()
    fake_network = SimpleNamespace(STA_IF="sta", WLAN=lambda interface: FakeWlan())

    monkeypatch.setattr(firmware_runtime, "time", fake_time)
    monkeypatch.setattr(firmware_runtime, "network", fake_network)

    diagnostics = firmware_runtime.connect_wifi({"wifi": {"ssid": "   ", "password": "secret"}})

    assert diagnostics == (DiagnosticEvent(code="WIFI", message="ssid missing"),)


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


def test_build_runtime_app_uses_injected_factories_and_records_wifi_diagnostics():
    called = {}

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
            version="",
            build_time="",
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
            called["version"] = version
            called["build_time"] = build_time
            called["diagnostics"] = None

        def inject_diagnostics(self, diagnostics, activate=True):
            called["diagnostics"] = (diagnostics, activate)

    input_controller = object()
    display = SimpleNamespace(show_boot_logo=lambda version: None)
    button_reader = object()
    executor = object()
    definitions = (object(),)
    now_counter = iter([0.0, 6.0])

    app = firmware_runtime.build_runtime_app(
        {
            "project": {"version": "1.2.3", "build_time": "2025-04-05T12:00Z"},
            "device": {
                "display": {
                    "width_px": 128,
                    "height_px": 64,
                    "page_interval_s": 15,
                    "mode": "compact",
                    "columns": 3,
                    "column_separator": "|",
                    "font": {"width_px": 8, "height_px": 8},
                },
                "buttons": {"a": "GP14", "b": "GP15"},
            },
        },
        input_controller_factory=lambda: input_controller,
        display_factory=lambda config: display,
        button_reader_factory=lambda config, input_controller: button_reader,
        runtime_app_factory=FakeApp,
        definitions_builder=lambda config: definitions,
        executor_factory=lambda: executor,
        wifi_connector=lambda config: (DiagnosticEvent(code="WIFI", message="connected"),),
        now_provider=lambda: next(now_counter),
        sleep_ms=lambda ms: None,
    )

    assert isinstance(app, FakeApp)
    assert called["definitions"] == definitions
    assert called["executor"] is executor
    assert called["display"] is display
    assert called["button_reader"] is button_reader
    assert called["input_controller"] is input_controller
    assert called["page_interval_s"] == 15
    assert called["page_size"] == 8
    assert called["row_width"] == 16
    assert called["display_mode"] == DisplayMode.COMPACT
    assert called["overview_columns"] == 3
    assert called["column_separator"] == "|"
    assert called["version"] == "1.2.3"
    assert called["build_time"] == "2025-04-05T12:00Z"
    assert called["diagnostics"] == (((DiagnosticEvent(code="WIFI", message="connected"),)), True)


def test_boot_logo_shown_for_minimum_duration_by_waiting_after_fast_wifi():
    called = {}
    sleep_calls = []

    class FakeApp:
        def __init__(self, **kwargs):
            called.update(kwargs)
            called["diagnostics"] = None

        def inject_diagnostics(self, diagnostics, activate=True):
            called["diagnostics"] = diagnostics

    now_times = iter([0.0, 1.0])
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
                "buttons": {"a": "GP14", "b": "GP15"},
            },
        },
        input_controller_factory=lambda: object(),
        display_factory=lambda config: display,
        button_reader_factory=lambda config, input_controller: object(),
        runtime_app_factory=FakeApp,
        definitions_builder=lambda config: (),
        executor_factory=lambda: object(),
        wifi_connector=lambda config: (),
        now_provider=lambda: next(now_times),
        sleep_ms=lambda ms: sleep_calls.append(ms),
        boot_logo_min_s=5,
    )

    assert sleep_calls == [4000]


def test_boot_logo_no_wait_when_wifi_takes_longer_than_minimum():
    sleep_calls = []

    class FakeApp:
        def __init__(self, **kwargs):
            pass

        def inject_diagnostics(self, diagnostics, activate=True):
            pass

    now_times = iter([0.0, 6.0])
    display = SimpleNamespace(show_boot_logo=lambda version: None)

    firmware_runtime.build_runtime_app(
        {
            "project": {},
            "device": {
                "display": {
                    "width_px": 128,
                    "height_px": 64,
                    "font": {"width_px": 8, "height_px": 8},
                },
                "buttons": {"a": "GP14", "b": "GP15"},
            },
        },
        input_controller_factory=lambda: object(),
        display_factory=lambda config: display,
        button_reader_factory=lambda config, input_controller: object(),
        runtime_app_factory=FakeApp,
        definitions_builder=lambda config: (),
        executor_factory=lambda: object(),
        wifi_connector=lambda config: (),
        now_provider=lambda: next(now_times),
        sleep_ms=lambda ms: sleep_calls.append(ms),
        boot_logo_min_s=5,
    )

    assert sleep_calls == []


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


def test_run_forever_builds_app_then_runs_loop(monkeypatch):
    fake_app = object()
    called = {}

    monkeypatch.setattr(firmware_runtime, "load_config", lambda path: {"device": {}, "wifi": {}, "checks": []})
    monkeypatch.setattr(firmware_runtime, "build_runtime_app", lambda config: fake_app)
    monkeypatch.setattr(
        firmware_runtime,
        "run_loop",
        lambda app, poll_interval_ms=50: called.update({"app": app, "poll_interval_ms": poll_interval_ms}),
    )

    firmware_runtime.run_forever(poll_interval_ms=75)

    assert called == {"app": fake_app, "poll_interval_ms": 75}