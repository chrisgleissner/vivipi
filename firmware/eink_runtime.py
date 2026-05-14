"""Lightweight one-shot eInk startup path for ViviPi."""

from __future__ import annotations

import gc
import json

from vivipi.core.liveness import bottom_heartbeat_pixels

try:
    import machine  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - used by CPython tests
    machine = None

try:
    import network  # type: ignore[import-not-found]
    import utime as time  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - used by CPython tests
    network = None
    import time


DEFAULT_WIFI_CONNECT_TIMEOUT_S = 10
WATCHDOG_TIMEOUT_MS = 8388


def _sleep_ms(value_ms: int):
    if value_ms <= 0:
        return
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value_ms)
        return
    time.sleep(value_ms / 1000.0)


def _now_s() -> float:
    if hasattr(time, "time"):
        return float(time.time())
    return float(time.ticks_ms()) / 1000.0


def _steady_now_s() -> float:
    if hasattr(time, "ticks_ms"):
        return float(time.ticks_ms()) / 1000.0
    return _now_s()


def _build_watchdog():
    if machine is None or not hasattr(machine, "WDT"):
        return None
    try:
        watchdog = machine.WDT(timeout=WATCHDOG_TIMEOUT_MS)
    except TypeError:
        watchdog = machine.WDT(WATCHDOG_TIMEOUT_MS)
    except Exception:
        return None
    try:
        watchdog.feed()
    except Exception:
        return None
    return watchdog


def _feed_watchdog(watchdog):
    if watchdog is None:
        return
    try:
        watchdog.feed()
    except Exception:
        return


def _load_config(path: str):
    with open(path, "r") as handle:
        return json.load(handle)


def _fit_row(value: object, row_width: int) -> str:
    text = str(value)
    if len(text) >= row_width:
        return text[:row_width]
    return text + (" " * (row_width - len(text)))


def _status_text(value) -> str:
    candidate = getattr(value, "value", value)
    return "OK" if str(candidate).strip().upper() == "OK" else "FAIL"


def _fold_text(value: object) -> str:
    text = value if isinstance(value, str) else str(value)
    casefold = getattr(text, "casefold", None)
    if callable(casefold):
        return casefold()
    lower = getattr(text, "lower", None)
    if callable(lower):
        return lower()
    return str(text)


def _summary_bottom_indicator_px(app) -> int:
    display_liveness = getattr(app, "display_liveness", {})
    if not isinstance(display_liveness, dict):
        return 0
    heartbeat = display_liveness.get("bottom_heartbeat", {})
    if not isinstance(heartbeat, dict):
        return 0
    return max(0, int(heartbeat.get("pixel_height_px", 0))) + max(0, int(heartbeat.get("gap_px", 0)))


def _summary_bottom_pixels(app) -> tuple[int, ...]:
    display_liveness = getattr(app, "display_liveness", {})
    if not isinstance(display_liveness, dict):
        return ()
    heartbeat = display_liveness.get("bottom_heartbeat", {})
    if not isinstance(heartbeat, dict) or not heartbeat.get("enabled"):
        return ()
    display_width_px = int(getattr(getattr(app, "display", None), "width", 0) or 0)
    if display_width_px < 1:
        return ()
    return bottom_heartbeat_pixels(
        display_width_px,
        int(heartbeat.get("pixel_count", 1)),
        str(heartbeat.get("position", "left")),
        pixel_width_px=int(heartbeat.get("pixel_width_px", 1)),
        step_index=int(getattr(app, "bottom_heartbeat_step", 0)),
        step_px=int(heartbeat.get("pixel_width_px", 1)),
    )


def _connect_wifi(config, watchdog=None, timeout_s: int = DEFAULT_WIFI_CONNECT_TIMEOUT_S):
    if network is None:
        return
    wifi = config.get("wifi", {}) if isinstance(config.get("wifi"), dict) else {}
    ssid = str(wifi.get("ssid", "")).strip()
    password = str(wifi.get("password", ""))
    if not ssid:
        return

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return
    if hasattr(wlan, "disconnect"):
        wlan.disconnect()
        _sleep_ms(100)

    wlan.connect(ssid, password)
    deadline_s = _steady_now_s() + max(1, int(timeout_s))
    while not wlan.isconnected() and _steady_now_s() < deadline_s:
        _feed_watchdog(watchdog)
        _sleep_ms(200)


def _build_summary_frame(app):
    from vivipi.core.render import Frame, TextSpan

    row_width = max(1, int(getattr(getattr(app, "state", None), "row_width", 16)))
    row_height = max(8, int(getattr(getattr(app, "display", None), "font_height", 8)))
    definitions = tuple(getattr(app, "definitions", ()))
    registered_results = getattr(app, "registered_results", {})
    summary_rows = []
    rows = []
    failure_spans = []

    for definition in definitions:
        current = registered_results.get(definition.identifier, {}) if isinstance(registered_results, dict) else {}
        name_text = str(current.get("name") or getattr(definition, "name", getattr(definition, "identifier", "CHECK")))
        status_text = _status_text(current.get("status"))
        summary_rows.append((_fold_text(name_text), _fold_text(getattr(definition, "identifier", "")), name_text, status_text))

    for row_index, (_, _, name_text, status_text) in enumerate(sorted(summary_rows, key=lambda item: (item[0], item[1]))):
        name_width = max(1, row_width - len(status_text) - 1)
        rows.append(f"{_fit_row(name_text[:name_width], name_width)} {status_text}")
        if status_text == "FAIL":
            failure_spans.append(TextSpan(row_index=row_index, start_column=max(0, row_width - len(status_text)), end_column=row_width))

    if not rows:
        rows = [_fit_row("NO CHECKS", row_width)]

    display_height = int(getattr(getattr(app, "display", None), "height", len(rows) * row_height))
    content_height = len(rows) * row_height
    reserved_bottom_px = _summary_bottom_indicator_px(app)
    content_area_height = max(0, display_height - reserved_bottom_px)
    display_liveness = getattr(app, "display_liveness", {})
    bottom_heartbeat = display_liveness.get("bottom_heartbeat", {}) if isinstance(display_liveness, dict) else {}
    return Frame(
        rows=tuple(rows),
        shift_offset=(0, max(0, (content_area_height - content_height) // 2)),
        failure_spans=tuple(failure_spans),
        bottom_pixels=_summary_bottom_pixels(app),
        bottom_pixel_width_px=int(bottom_heartbeat.get("pixel_width_px", 1)) if isinstance(bottom_heartbeat, dict) else 1,
        bottom_pixel_height_px=int(bottom_heartbeat.get("pixel_height_px", 1)) if isinstance(bottom_heartbeat, dict) else 1,
        bottom_pixel_gap_px=int(bottom_heartbeat.get("gap_px", 0)) if isinstance(bottom_heartbeat, dict) else 0,
    )


def run_forever(config_path: str = "config.json", poll_interval_ms: int = 20):
    del poll_interval_ms
    config = _load_config(config_path)

    from vivipi.core.display import normalize_display_config, reserved_bottom_indicator_px
    import vivipi.runtime.checks as runtime_checks_module
    from vivipi.core.input import InputController
    from vivipi.core.models import DisplayMode, TransitionThresholds
    from vivipi.runtime.app import RuntimeApp
    from vivipi.runtime.checks import build_executor, build_runtime_definitions

    display_config = normalize_display_config(dict(config.get("device", {})).get("display", {}))
    definitions = build_runtime_definitions(config)
    executor = build_executor()
    gc.collect()
    try:
        from display import create_display
    except ImportError as error:  # pragma: no cover - used by CPython tests
        if getattr(error, "name", None) != "display":
            raise
        from firmware.display import create_display

    display = create_display(display_config)
    gc.collect()
    watchdog = _build_watchdog()
    _feed_watchdog(watchdog)
    if hasattr(display, "_watchdog_feed"):
        display._watchdog_feed = (lambda: _feed_watchdog(watchdog))

    font = display_config.get("font", {}) if isinstance(display_config, dict) else {}
    font_width = int(getattr(display, "font_width", int(font.get("width_px", 8)) if isinstance(font, dict) else 8))
    font_height = int(getattr(display, "font_height", int(font.get("height_px", 8)) if isinstance(font, dict) else 8))
    page_size = max(
        1,
        (int(display_config.get("height_px", 64)) - reserved_bottom_indicator_px(display_config)) // max(1, font_height),
    )
    row_width = max(1, int(display_config.get("width_px", 128)) // max(1, font_width))
    check_state = config.get("check_state") if isinstance(config.get("check_state"), dict) else {}

    runtime_checks_module.set_probe_activity_callback((lambda: _feed_watchdog(watchdog)) if watchdog is not None else None)

    app = RuntimeApp(
        definitions=definitions,
        executor=executor,
        display=display,
        button_reader=None,
        input_controller=InputController(),
        page_interval_s=int(display_config.get("page_interval_s", 20)),
        page_size=page_size,
        row_width=row_width,
        display_mode=DisplayMode(str(display_config.get("mode", str(DisplayMode.STANDARD)))),
        overview_columns=int(display_config.get("columns", 1)),
        column_separator=str(display_config.get("column_separator", " ")),
        transition_thresholds=TransitionThresholds(
            failures_to_degraded=int(check_state.get("failures_to_degraded", 1)),
            failures_to_failed=int(check_state.get("failures_to_failed", 2)),
            successes_to_recover=int(check_state.get("successes_to_recover", 1)),
        ),
        visible_degraded=bool(check_state.get("visible_degraded", True)),
        sleep_ms=_sleep_ms,
        probe_time_provider=_steady_now_s,
        version=str(dict(config.get("project", {})).get("version", "")),
        build_time=str(dict(config.get("project", {})).get("build_time", "")),
        display_liveness=dict(display_config.get("liveness", {})),
        display_family="eink",
    )
    app.background_workers_enabled = False

    _connect_wifi(config, watchdog=watchdog)
    now_s = _steady_now_s()
    _feed_watchdog(watchdog)
    app.run_all_checks(now_s)
    _feed_watchdog(watchdog)
    display.draw_frame(_build_summary_frame(app))
    while True:
        _feed_watchdog(watchdog)
        _sleep_ms(20)
