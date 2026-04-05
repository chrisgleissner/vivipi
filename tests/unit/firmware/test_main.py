import firmware.main as firmware_main


def test_main_delegates_to_run_forever(monkeypatch):
    called = {"count": 0}

    monkeypatch.setattr(firmware_main, "run_forever", lambda: called.__setitem__("count", called["count"] + 1))

    firmware_main.main()

    assert called == {"count": 1}