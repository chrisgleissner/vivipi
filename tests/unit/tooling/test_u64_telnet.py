from __future__ import annotations

import socket
import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from _script_loader import load_script_module


def load_runtime():
    return load_script_module("u64_connection_runtime")


def load_telnet():
    return load_script_module("u64_telnet")


def make_settings(runtime):
    return runtime.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)


class RotatingState:
    def __init__(self):
        self.counts = {}

    def next_probe_operation_index(self, protocol, runner_id, surface, pool_size):
        key = (runner_id, protocol, surface.value)
        counter = self.counts.get(key, 0)
        self.counts[key] = counter + 1
        return counter % pool_size


def test_telnet_normal_mode_sends_newline_drains_banner_and_closes(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()
    calls = []

    class FakeSocket:
        def settimeout(self, timeout):
            calls.append(("settimeout", timeout))

        def sendall(self, payload):
            calls.append(("sendall", payload))

        def recv(self, size):
            calls.append(("recv", size))
            if calls.count(("recv", size)) == 1:
                return b"READY>"
            raise socket.timeout()

        def close(self):
            calls.append("close")

    monkeypatch.setattr(module.socket, "create_connection", lambda address, timeout: FakeSocket())

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.CORRECT)

    assert outcome.result == "OK"
    assert ("sendall", b"\r\n") in calls
    assert calls[-1] == "close"


def test_telnet_incomplete_correctness_accepts_blank_session(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()
    calls = []

    class FakeSocket:
        def settimeout(self, timeout):
            calls.append(("settimeout", timeout))

        def recv(self, size):
            calls.append(("recv", size))
            raise socket.timeout()

        def sendall(self, payload):
            calls.append(("sendall", payload))

        def close(self):
            calls.append("close")

    monkeypatch.setattr(module.socket, "create_connection", lambda address, timeout: FakeSocket())

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.INCOMPLETE)

    assert outcome.result == "OK"
    assert outcome.detail == "connected"
    assert not any(call == ("sendall", b"\r\n") for call in calls)
    assert calls[-1] == "close"


def test_telnet_initial_read_classify_returns_banner_ready(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()
    calls = []

    class FakeSocket:
        def settimeout(self, timeout):
            calls.append(("settimeout", timeout))

        def recv(self, size):
            calls.append(("recv", size))
            return b"READY>"

        def close(self):
            calls.append("close")

    monkeypatch.setattr(module.socket, "create_connection", lambda address, timeout: FakeSocket())

    detail = module.initial_read_classify(make_settings(runtime))

    assert detail == "banner ready"
    assert calls[-1] == "close"


def test_telnet_smoke_incomplete_operations_match_historical_probe():
    runtime = load_runtime()
    module = load_telnet()

    operations = module.incomplete_operations(runtime.ProbeSurface.SMOKE)

    assert [name for name, _ in operations] == ["telnet_initial_read_classify"]


def test_telnet_open_menu_retries_with_f2_sequence():
    module = load_telnet()
    calls = []

    class FakeSocket:
        def __init__(self):
            self.frames = [
                [socket.timeout(), socket.timeout(), socket.timeout()],
                [socket.timeout(), socket.timeout(), socket.timeout()],
                [b"Audio Mixer\nSpeaker Settings", socket.timeout(), socket.timeout(), socket.timeout()],
            ]
            self.frame_index = 0
            self.chunk_index = 0

        def sendall(self, payload):
            calls.append(("sendall", payload))

        def recv(self, size):
            del size
            if self.frame_index >= len(self.frames):
                raise socket.timeout()
            frame = self.frames[self.frame_index]
            item = frame[self.chunk_index]
            self.chunk_index += 1
            if self.chunk_index >= len(frame):
                self.frame_index += 1
                self.chunk_index = 0
            if isinstance(item, BaseException):
                raise item
            return item

    detail = module.open_menu(FakeSocket())

    assert detail == "visible_bytes=28"
    assert calls == [("sendall", module.TELNET_KEY_F2), ("sendall", module.TELNET_KEY_F2)]


def test_telnet_session_open_audio_mixer_uses_down_after_f2_menu(monkeypatch):
    module = load_telnet()
    calls = []
    menu_text = "-- Audio / Video -- Video Configuration Audio Mixer Speaker Settings"
    down_text = "Video Configuration Audio Mixer"
    audio_text = "Vol UltiSid 1 0 dB"
    session = module.TelnetRunnerSession(sock=object())

    monkeypatch.setattr(module, "session_read", lambda current_session, **kwargs: "")

    def fake_send(current_session, payload, *, view_state=None):
        calls.append(payload)
        if payload == module.TELNET_KEY_F2:
            current_session.last_text = menu_text
            return menu_text
        if payload == module.TELNET_KEY_DOWN:
            current_session.last_text = down_text
            return down_text
        if payload == module.TELNET_KEY_ENTER:
            current_session.last_text = audio_text
            return audio_text
        raise AssertionError(payload)

    monkeypatch.setattr(module, "session_send", fake_send)

    text = module.session_open_audio_mixer(session)

    assert text == audio_text
    assert calls == [module.TELNET_KEY_F2, module.TELNET_KEY_DOWN, module.TELNET_KEY_ENTER]
    assert session.view_state == "audio_mixer"
    assert session.menu_focus == "audio_mixer"


def test_telnet_session_write_audio_mixer_item_recovers_from_partial_screen(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()
    session = module.TelnetRunnerSession(sock=object(), view_state="audio_mixer", last_text="Vol UltiSid 1")

    monkeypatch.setattr(module, "session_refresh_audio_mixer", lambda current_session: "Vol UltiSid 1")
    monkeypatch.setattr(module, "session_read", lambda current_session, **kwargs: " 0 dBVol UltiSid 2 0 dB")
    monkeypatch.setattr(module, "audio_mixer_write_right_steps", lambda settings, current, target: 0)

    detail = module.session_write_audio_mixer_item(make_settings(runtime), session, "0 dB")

    assert detail == "from=0 dB to=0 dB right_steps=0"
    assert session.last_text == "Vol UltiSid 1 0 dBVol UltiSid 2 0 dB"
    assert session.view_state == "audio_mixer"


def test_extended_telnet_incomplete_mode_treats_connection_reset_as_expected_disconnect(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()
    state = RotatingState()

    monkeypatch.setattr(
        module,
        "incomplete_operations",
        lambda surface: (("telnet_f2_abort", lambda settings: (_ for _ in ()).throw(ConnectionResetError(104, "Connection reset by peer"))),),
    )

    context = runtime.ProbeExecutionContext(
        protocol="telnet",
        runner_id=1,
        iteration=1,
        surface=runtime.ProbeSurface.READWRITE,
        state=state,
    )
    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.INCOMPLETE, context=context)

    assert outcome.result == "OK"
    assert outcome.detail == "surface=readwrite op=telnet_f2_abort expected_disconnect_after_abort"


def test_collect_telnet_visible_ignores_subnegotiation_and_keeps_literal_iac():
    module = load_telnet()
    replies = []

    class FakeHandle:
        def sendall(self, payload):
            replies.append(payload)

    chunk = bytes(
        [
            module.IAC,
            module.DO,
            1,
            ord("A"),
            module.IAC,
            module.SB,
            24,
            1,
            ord("x"),
            module.IAC,
            module.SE,
            module.IAC,
            module.IAC,
            ord("B"),
        ]
    )

    visible = module.collect_visible(FakeHandle(), chunk)

    assert visible == b"A\xffB"
    assert replies == [bytes([module.IAC, module.WONT, 1])]
