from types import ModuleType, SimpleNamespace
import sys

import pytest

import firmware.eink_runtime as eink_runtime


def test_run_forever_builds_watchdog_after_display_creation(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        """
{
  "device": {
    "display": {
      "family": "eink",
      "type": "waveshare-pico-epaper-2.13-b-v4",
      "width_px": 250,
      "height_px": 122,
      "font": {"width_px": 16, "height_px": 16}
    }
  }
}
""".strip()
    )

    events = []
    sentinel = RuntimeError("stop static hold")
    fake_display = SimpleNamespace(
        font_width=16,
        font_height=16,
        width=250,
        height=122,
        draw_frame=lambda frame: events.append(("draw_frame", tuple(frame.rows))),
    )

    fake_display_module = ModuleType("display")
    fake_display_module.create_display = lambda config: events.append("create_display") or fake_display

    fake_checks_module = ModuleType("vivipi.runtime.checks")
    fake_checks_module.set_probe_activity_callback = lambda callback: events.append(("probe_callback", callback is not None))
    fake_checks_module.build_executor = lambda: events.append("build_executor") or object()
    fake_checks_module.build_runtime_definitions = lambda config: events.append("build_runtime_definitions") or ()

    fake_app_module = ModuleType("vivipi.runtime.app")

    class FakeRuntimeApp:
        def __init__(self, **kwargs):
            events.append("runtime_app")
            self.display = kwargs["display"]
            self.state = SimpleNamespace(row_width=15)
            self.definitions = ()
            self.registered_results = {}
            self.background_workers_enabled = False

        def run_all_checks(self, now_s):
            events.append(("run_all_checks", now_s))

    fake_app_module.RuntimeApp = FakeRuntimeApp

    monkeypatch.setitem(sys.modules, "display", fake_display_module)
    monkeypatch.setitem(sys.modules, "vivipi.runtime.checks", fake_checks_module)
    monkeypatch.setitem(sys.modules, "vivipi.runtime.app", fake_app_module)
    monkeypatch.setattr(eink_runtime, "_build_watchdog", lambda: events.append("build_watchdog") or SimpleNamespace(feed=lambda: events.append("feed")))
    monkeypatch.setattr(eink_runtime, "_connect_wifi", lambda config, watchdog=None, timeout_s=eink_runtime.DEFAULT_WIFI_CONNECT_TIMEOUT_S: events.append(("connect_wifi", watchdog is not None)))
    monkeypatch.setattr(eink_runtime, "_steady_now_s", lambda: 12.5)
    monkeypatch.setattr(eink_runtime.gc, "collect", lambda: events.append("gc.collect"))
    monkeypatch.setattr(eink_runtime, "_sleep_ms", lambda value_ms: (_ for _ in ()).throw(sentinel))

    with pytest.raises(RuntimeError, match="stop static hold"):
        eink_runtime.run_forever(str(config_path))

    assert events.index("create_display") < events.index("build_watchdog")
    assert "feed" in events
    assert any(event[0] == "draw_frame" for event in events if isinstance(event, tuple))
