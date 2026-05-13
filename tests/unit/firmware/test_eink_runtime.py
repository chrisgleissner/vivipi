from types import ModuleType, SimpleNamespace
import sys

import pytest

import firmware.eink_runtime as eink_runtime


def test_build_summary_frame_reserves_indicator_band_and_draws_bottom_pixels():
    definitions = (
        SimpleNamespace(identifier="u64", name="U64 REST"),
        SimpleNamespace(identifier="c64u", name="C64U REST"),
        SimpleNamespace(identifier="pixel4", name="PIXEL4"),
    )
    app = SimpleNamespace(
        state=SimpleNamespace(row_width=16),
        display=SimpleNamespace(width=250, height=122, font_height=15),
        definitions=definitions,
        registered_results={
            "u64": {"name": "U64 REST", "status": "OK"},
            "c64u": {"name": "C64U REST", "status": "FAIL"},
            "pixel4": {"name": "PIXEL4", "status": "OK"},
        },
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
        bottom_heartbeat_step=4,
    )

    frame = eink_runtime._build_summary_frame(app)

    assert frame.shift_offset == (0, 36)
    assert frame.bottom_pixels == (8,)
    assert frame.bottom_pixel_width_px == 2
    assert frame.bottom_pixel_height_px == 2
    assert frame.rows[0] == "C64U REST   FAIL"


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
      "font": {"width_px": 15, "height_px": 15},
      "liveness": {
        "bottom_heartbeat": {
          "enabled": true,
          "period_s": 1,
          "pixel_count": 1,
          "position": "left",
          "pixel_width_px": 2,
          "pixel_height_px": 2,
          "gap_px": 3
        }
      }
    }
  }
}
""".strip()
    )

    events = []
    captured = {}
    sentinel = RuntimeError("stop static hold")
    fake_display = SimpleNamespace(
        font_width=15,
        font_height=15,
        width=250,
        height=122,
        draw_frame=lambda frame: events.append(("draw_frame", frame)),
    )

    fake_display_module = ModuleType("display")
    fake_display_module.create_display = lambda config: events.append("create_display") or fake_display

    fake_checks_module = ModuleType("vivipi.runtime.checks")
    fake_checks_module.set_probe_activity_callback = lambda callback: events.append(("probe_callback", callback is not None))
    fake_checks_module.build_executor = lambda: events.append("build_executor") or object()
    fake_checks_module.build_runtime_definitions = lambda config: events.append("build_runtime_definitions") or (
      SimpleNamespace(identifier="u64", name="U64 REST"),
      SimpleNamespace(identifier="c64u", name="C64U REST"),
      SimpleNamespace(identifier="pixel4", name="PIXEL4"),
    )

    fake_app_module = ModuleType("vivipi.runtime.app")

    class FakeRuntimeApp:
        def __init__(self, **kwargs):
            events.append("runtime_app")
            captured["page_size"] = kwargs["page_size"]
            captured["display_liveness"] = kwargs["display_liveness"]
            self.display = kwargs["display"]
            self.state = SimpleNamespace(row_width=16)
            self.definitions = kwargs["definitions"]
            self.display_liveness = kwargs["display_liveness"]
            self.bottom_heartbeat_step = 4
            self.registered_results = {
                "u64": {"name": "U64 REST", "status": "OK"},
                "c64u": {"name": "C64U REST", "status": "FAIL"},
                "pixel4": {"name": "PIXEL4", "status": "OK"},
            }
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
    drawn_frame = next(event[1] for event in events if isinstance(event, tuple) and event[0] == "draw_frame")
    assert tuple(row.split()[0] for row in drawn_frame.rows) == ("C64U", "PIXEL4", "U64")
    assert drawn_frame.bottom_pixels == (8,)
    assert captured["page_size"] == 7
    assert captured["display_liveness"]["bottom_heartbeat"] == {
        "enabled": True,
        "period_s": 1,
        "pixel_count": 1,
        "position": "left",
        "pixel_width_px": 2,
        "pixel_height_px": 2,
        "gap_px": 3,
    }
