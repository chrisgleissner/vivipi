import firmware.main as firmware_main
from types import SimpleNamespace


def test_main_delegates_non_eink_to_run_forever(monkeypatch):
    called = {"count": 0}
    original_import = __import__

    monkeypatch.setattr(firmware_main, "_display_family_from_config", lambda path="config.json": "oled")

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "runtime":
            return SimpleNamespace(run_forever=lambda: called.__setitem__("count", called["count"] + 1))
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    firmware_main.main()

    assert called == {"count": 1}


def test_main_routes_eink_to_lightweight_runner(monkeypatch):
    called = {"count": 0}
    original_import = __import__

    monkeypatch.setattr(firmware_main, "_display_family_from_config", lambda path="config.json": "eink")

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "eink_runtime":
            return SimpleNamespace(run_forever=lambda: called.__setitem__("count", called["count"] + 1))
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    firmware_main.main()

    assert called == {"count": 1}