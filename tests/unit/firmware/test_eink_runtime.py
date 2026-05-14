from types import ModuleType, SimpleNamespace
import sys

import pytest

import firmware.eink_runtime as eink_runtime
from vivipi.core.models import CheckRuntime, Status


def test_build_summary_frame_reserves_indicator_band_and_draws_bottom_pixels():
    app = SimpleNamespace(
        state=SimpleNamespace(
            row_width=16,
            checks=(
                CheckRuntime(identifier="u64-rest", name="U64 REST", status=Status.OK),
                CheckRuntime(identifier="c64u-rest", name="C64U REST", status=Status.FAIL),
                CheckRuntime(identifier="pixel4-adb", name="PIXEL4 ADB", status=Status.OK),
            ),
        ),
        display=SimpleNamespace(width=250, height=122, font_height=15),
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


def test_build_summary_frame_sorts_probe_rows_alphabetically_by_display_name():
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
        display=SimpleNamespace(width=250, height=160, font_height=15),
        display_liveness={"bottom_heartbeat": {"enabled": False}},
    )

    frame = eink_runtime._build_summary_frame(app)

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
    fake_state_module = ModuleType("vivipi.runtime.state")

    class FakeRuntimeApp:
        def __init__(self, **kwargs):
            events.append("runtime_app")
            captured["page_size"] = kwargs["page_size"]
            captured["display_liveness"] = kwargs["display_liveness"]
            captured["runtime_app"] = self
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
            self.last_rendered_frame = None

        def run_all_checks(self, now_s):
            events.append(("run_all_checks", now_s))

    fake_app_module.RuntimeApp = FakeRuntimeApp
    fake_state_module.bind_app = lambda app: events.append(("bind_app", app is not None))

    monkeypatch.setitem(sys.modules, "display", fake_display_module)
    monkeypatch.setitem(sys.modules, "vivipi.runtime.checks", fake_checks_module)
    monkeypatch.setitem(sys.modules, "vivipi.runtime.app", fake_app_module)
    monkeypatch.setitem(sys.modules, "vivipi.runtime.state", fake_state_module)
    import vivipi.runtime as runtime_package
    monkeypatch.setattr(runtime_package, "state", fake_state_module, raising=False)
    monkeypatch.setattr(eink_runtime, "_build_watchdog", lambda: events.append("build_watchdog") or SimpleNamespace(feed=lambda: events.append("feed")))
    monkeypatch.setattr(eink_runtime, "_connect_wifi", lambda config, watchdog=None, timeout_s=eink_runtime.DEFAULT_WIFI_CONNECT_TIMEOUT_S: events.append(("connect_wifi", watchdog is not None)))
    monkeypatch.setattr(eink_runtime, "_steady_now_s", lambda: 12.5)
    monkeypatch.setattr(eink_runtime.gc, "collect", lambda: events.append("gc.collect"))
    monkeypatch.setattr(eink_runtime, "_sleep_ms", lambda value_ms: (_ for _ in ()).throw(sentinel))

    with pytest.raises(RuntimeError, match="stop static hold"):
        eink_runtime.run_forever(str(config_path))

    assert events.index("create_display") < events.index("build_watchdog")
    assert ("bind_app", True) in events
    assert "feed" in events
    drawn_frame = next(event[1] for event in events if isinstance(event, tuple) and event[0] == "draw_frame")
    assert tuple(row.split()[0] for row in drawn_frame.rows) == ("C64U", "PIXEL4", "U64")
    assert drawn_frame.bottom_pixels == (8,)
    assert captured["runtime_app"].last_rendered_frame == drawn_frame
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


def test_run_forever_reruns_checks_and_moves_bottom_heartbeat_after_refresh_interval(tmp_path, monkeypatch):
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
      "page_interval": 1,
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

    sentinel = RuntimeError("stop after second render")
    drawn_frames = []

    def draw_frame(frame):
        drawn_frames.append(frame)
        if len(drawn_frames) >= 2:
            raise sentinel

    fake_display = SimpleNamespace(
        font_width=15,
        font_height=15,
        width=250,
        height=122,
        draw_frame=draw_frame,
    )

    fake_display_module = ModuleType("display")
    fake_display_module.create_display = lambda config: fake_display

    fake_checks_module = ModuleType("vivipi.runtime.checks")
    fake_checks_module.set_probe_activity_callback = lambda callback: None
    fake_checks_module.build_executor = lambda: object()
    fake_checks_module.build_runtime_definitions = lambda config: (
        SimpleNamespace(identifier="u64", name="U64 REST"),
        SimpleNamespace(identifier="c64u", name="C64U REST"),
    )

    fake_app_module = ModuleType("vivipi.runtime.app")
    fake_state_module = ModuleType("vivipi.runtime.state")

    class FakeRuntimeApp:
        def __init__(self, **kwargs):
            self.display = kwargs["display"]
            self.state = SimpleNamespace(row_width=16)
            self.definitions = kwargs["definitions"]
            self.display_liveness = kwargs["display_liveness"]
            self.bottom_heartbeat_step = 0
            self.registered_results = {
                "u64": {"name": "U64 REST", "status": "OK"},
                "c64u": {"name": "C64U REST", "status": "FAIL"},
            }
            self.background_workers_enabled = False
            self.last_rendered_frame = None
            self.run_times = []

        def run_all_checks(self, now_s):
            self.run_times.append(now_s)
            self.bottom_heartbeat_step += 1

    fake_app_module.RuntimeApp = FakeRuntimeApp
    fake_state_module.bind_app = lambda app: app

    monkeypatch.setitem(sys.modules, "display", fake_display_module)
    monkeypatch.setitem(sys.modules, "vivipi.runtime.checks", fake_checks_module)
    monkeypatch.setitem(sys.modules, "vivipi.runtime.app", fake_app_module)
    monkeypatch.setitem(sys.modules, "vivipi.runtime.state", fake_state_module)
    import vivipi.runtime as runtime_package
    monkeypatch.setattr(runtime_package, "state", fake_state_module, raising=False)
    monkeypatch.setattr(eink_runtime, "_build_watchdog", lambda: None)
    monkeypatch.setattr(eink_runtime, "_connect_wifi", lambda config, watchdog=None, timeout_s=eink_runtime.DEFAULT_WIFI_CONNECT_TIMEOUT_S: None)
    steady_times = iter((10.0, 11.1, 11.1))
    monkeypatch.setattr(eink_runtime, "_steady_now_s", lambda: next(steady_times))
    monkeypatch.setattr(eink_runtime.gc, "collect", lambda: None)

    with pytest.raises(RuntimeError, match="stop after second render"):
        eink_runtime.run_forever(str(config_path))

    assert len(drawn_frames) == 2
    assert drawn_frames[0].bottom_pixels != drawn_frames[1].bottom_pixels
