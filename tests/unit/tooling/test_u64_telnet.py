from __future__ import annotations

import socket

from tests.unit.tooling._script_loader import load_script_module


def load_runtime():
    return load_script_module("u64_connection_runtime")


def load_telnet():
    return load_script_module("u64_telnet")


def load_connection_test():
    return load_script_module("u64_connection_test")


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


class UnusedSocket:
    def sendall(self, payload: bytes) -> None:
        return None

    def recv(self, size: int) -> bytes:
        return b""

    def close(self) -> None:
        return None


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

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.COMPLETE)

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


def test_telnet_connect_authenticates_when_network_password_configured(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()
    calls = []
    prompts = iter(["Password:", "READY>"])

    class FakeSocket:
        def settimeout(self, timeout):
            calls.append(("settimeout", timeout))

        def sendall(self, payload):
            calls.append(("sendall", payload))

        def close(self):
            calls.append("close")

    monkeypatch.setattr(module.socket, "create_connection", lambda address, timeout: FakeSocket())
    monkeypatch.setattr(
        module,
        "read_until_idle",
        lambda sock, max_empty_reads=2, initial_timeout_s=None, quiet_timeout_s=None: next(prompts),
    )

    settings = runtime.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True, network_password="secret")
    sock = module.connect(settings)

    assert hasattr(sock, "recv")
    assert calls == [
        ("settimeout", module.TELNET_IDLE_TIMEOUT_S),
        ("sendall", b"secret\r\n"),
    ]


def test_telnet_connect_prefetches_post_login_screen_after_masked_echo(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()
    calls = []
    responses = iter(["Password:", "***", "Remote Home Screen"])

    class FakeSocket:
        def settimeout(self, timeout):
            calls.append(("settimeout", timeout))

        def sendall(self, payload):
            calls.append(("sendall", payload))

        def recv(self, size):
            calls.append(("recv", size))
            return b""

        def close(self):
            calls.append("close")

    monkeypatch.setattr(module.socket, "create_connection", lambda address, timeout: FakeSocket())
    monkeypatch.setattr(
        module,
        "read_until_idle",
        lambda sock, max_empty_reads=2, initial_timeout_s=None, quiet_timeout_s=None: next(responses),
    )

    settings = runtime.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True, network_password="secret")
    sock = module.connect(settings)

    assert sock.recv(4096) == b"Remote Home Screen"
    assert calls == [
        ("settimeout", module.TELNET_IDLE_TIMEOUT_S),
        ("sendall", b"secret\r\n"),
    ]


def test_read_until_idle_switches_to_short_post_data_timeout():
    module = load_telnet()
    timeouts = []

    class FakeSocket:
        def __init__(self):
            self.responses = [b"READY>", socket.timeout()]

        def settimeout(self, timeout):
            timeouts.append(timeout)

        def recv(self, size):
            del size
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

    text = module.read_until_idle(FakeSocket(), max_empty_reads=1)

    assert text == "READY>"
    assert timeouts == [module.TELNET_IDLE_TIMEOUT_S, module.TELNET_POST_DATA_IDLE_TIMEOUT_S]


def test_read_until_idle_uses_socket_readiness_checks(monkeypatch):
    module = load_telnet()
    waits = []

    class FakeSocket:
        def __init__(self):
            self.responses = [b"READY>", b"menu"]

        def fileno(self):
            return 7

        def recv(self, size):
            del size
            return self.responses.pop(0)

    sock = FakeSocket()
    readiness = [([sock], [], []), ([sock], [], []), ([], [], []), ([], [], []), ([], [], [])]

    def fake_select(readers, writers, errors, timeout):
        waits.append(timeout)
        assert readers == [sock]
        assert writers == []
        assert errors == []
        return readiness.pop(0)

    monkeypatch.setattr(module.select, "select", fake_select)

    text = module.read_until_idle(sock)

    assert text == "READY>menu"
    assert waits == [
        module.TELNET_IDLE_TIMEOUT_S,
        module.TELNET_POST_DATA_IDLE_TIMEOUT_S,
        module.TELNET_POST_DATA_IDLE_TIMEOUT_S,
    ]


def test_read_until_idle_preserves_max_empty_reads_with_select(monkeypatch):
    module = load_telnet()
    waits = []

    class FakeSocket:
        def fileno(self):
            return 7

        def recv(self, size):
            raise AssertionError("recv should not be called when select reports no data")

    sock = FakeSocket()

    def fake_select(readers, writers, errors, timeout):
        waits.append(timeout)
        assert readers == [sock]
        assert writers == []
        assert errors == []
        return ([], [], [])

    monkeypatch.setattr(module.select, "select", fake_select)

    text = module.read_until_idle(sock, max_empty_reads=2)

    assert text == ""
    assert waits == [module.TELNET_IDLE_TIMEOUT_S, module.TELNET_IDLE_TIMEOUT_S]


def test_telnet_smoke_incomplete_operations_match_historical_probe():
    runtime = load_runtime()
    module = load_telnet()

    operations = module.incomplete_operations(runtime.ProbeSurface.SMOKE)

    assert [name for name, _ in operations] == ["telnet_initial_read_classify"]


def test_telnet_open_menu_retries_with_f2_then_enter_sequence():
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
    assert calls == [
        ("sendall", module.TELNET_KEY_F2),
        ("sendall", module.TELNET_KEY_ENTER),
        ("sendall", module.TELNET_KEY_ENTER),
    ]


def test_telnet_session_open_audio_mixer_uses_enter_path_after_f2_menu(monkeypatch):
    module = load_telnet()
    calls = []
    audio_text = "Vol UltiSid 1 0 dB"
    session = module.TelnetRunnerSession(sock=UnusedSocket())

    monkeypatch.setattr(module, "session_read", lambda current_session, **kwargs: "")

    def fake_send(current_session, payload, *, view_state=None, initial_timeout_s=None):
        del initial_timeout_s
        calls.append(payload)
        if payload == module.TELNET_KEY_F2:
            current_session.last_text = ""
            return ""
        if payload == module.TELNET_KEY_DOWN:
            current_session.last_text = "Video Configuration Audio Mixer"
            current_session.view_state = "audio_video_menu"
            return current_session.last_text
        if payload == module.TELNET_KEY_ENTER:
            current_session.last_text = audio_text
            current_session.view_state = "audio_mixer"
            return audio_text
        raise AssertionError(payload)

    monkeypatch.setattr(module, "session_send", fake_send)

    text = module.session_open_audio_mixer(session)

    assert text == audio_text
    assert calls == [
        module.TELNET_KEY_F2,
        module.TELNET_KEY_DOWN,
        module.TELNET_KEY_ENTER,
    ]
    assert session.view_state == "audio_mixer"
    assert session.menu_focus == "audio_mixer"


def test_telnet_session_write_audio_mixer_item_recovers_from_partial_screen(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()
    session = module.TelnetRunnerSession(sock=UnusedSocket(), view_state="audio_mixer", last_text="Vol UltiSid 1")

    monkeypatch.setattr(module, "session_refresh_audio_mixer", lambda current_session: "Vol UltiSid 1")
    read_responses = iter([" 0 dBVol UltiSid 2 0 dB", ""])
    monkeypatch.setattr(module, "session_read", lambda current_session, **kwargs: next(read_responses, ""))
    monkeypatch.setattr(module, "audio_mixer_picker_sequence", lambda settings, current, target: (module.TELNET_KEY_DOWN, 0))
    monkeypatch.setattr(
        module.u64_http,
        "verify_audio_mixer_value",
        lambda current_settings, target, *, shared_state=None: module.u64_http.confirm_audio_mixer_value(shared_state, "0 dB"),
    )

    calls = []

    def fake_send(current_session, payload, *, view_state=None, initial_timeout_s=None):
        del current_session, view_state, initial_timeout_s
        calls.append(payload)
        if payload == module.TELNET_KEY_LEFT:
            return "Save changes to Flash? Yes No"
        return "Vol UltiSid 1 0 dB"

    monkeypatch.setattr(module, "session_send", fake_send)

    detail = module.session_write_audio_mixer_item(make_settings(runtime), session, "0 dB")

    assert detail == "from=0 dB to=0 dB picker=down steps=0"
    assert calls == []
    assert session.view_state == "unknown"


def test_telnet_session_write_audio_mixer_item_preserves_latest_known_state(monkeypatch):
    runtime = load_runtime()
    connection_test = load_connection_test()
    module = load_telnet()
    settings = make_settings(runtime)
    state = connection_test.ExecutionState(settings=settings, include_runner_context=False, random_seed=1)
    session = module.TelnetRunnerSession(sock=UnusedSocket(), view_state="audio_mixer", last_text="Vol UltiSid 1 +1 dB")

    monkeypatch.setattr(module, "session_refresh_audio_mixer", lambda current_session: "Vol UltiSid 1 +1 dB")
    monkeypatch.setattr(
        module,
        "session_extract_audio_mixer_value",
        lambda current_session, text: (text, "+1 dB") if "+1 dB" in text else (text, "0 dB"),
    )
    monkeypatch.setattr(module, "audio_mixer_picker_sequence", lambda current_settings, current, target: (module.TELNET_KEY_DOWN, 1))

    calls = []

    def fake_send(current_session, payload, *, view_state=None, initial_timeout_s=None):
        del current_session, view_state, initial_timeout_s
        calls.append(payload)
        if payload == module.TELNET_KEY_LEFT:
            return "Save changes to Flash? Yes No"
        return "Vol UltiSid 1 0 dB"

    monkeypatch.setattr(module, "session_send", fake_send)
    monkeypatch.setattr(
        module.u64_http,
        "verify_audio_mixer_value",
        lambda current_settings, target, *, shared_state=None: module.u64_http.confirm_audio_mixer_value(shared_state, "0 dB"),
    )

    detail = module.session_write_audio_mixer_item(settings, session, "0 dB", shared_state=state)

    assert detail == "from=+1 dB to=0 dB picker=down steps=1"
    assert calls == [
        module.TELNET_KEY_ENTER,
        module.TELNET_KEY_DOWN,
        module.TELNET_KEY_ENTER,
        module.TELNET_KEY_LEFT,
        module.TELNET_KEY_LEFT,
        module.TELNET_KEY_ENTER,
    ]
    assert state.get_shared_resource_value(module.u64_http.AUDIO_MIXER_SHARED_STATE_KEY) == "0 dB"
    assert session.view_state == "unknown"


def test_telnet_session_write_audio_mixer_item_accepts_authoritative_http_target(monkeypatch):
    runtime = load_runtime()
    connection_test = load_connection_test()
    module = load_telnet()
    settings = make_settings(runtime)
    state = connection_test.ExecutionState(settings=settings, include_runner_context=False, random_seed=1)
    session = module.TelnetRunnerSession(sock=UnusedSocket(), view_state="audio_mixer", last_text="Vol UltiSid 1 0 dB")

    monkeypatch.setattr(module, "session_refresh_audio_mixer", lambda current_session: "Vol UltiSid 1 0 dB")
    monkeypatch.setattr(
        module,
        "session_extract_audio_mixer_value",
        lambda current_session, text: (text, "+1 dB") if "+1 dB" in text else (text, "0 dB"),
    )
    monkeypatch.setattr(module, "audio_mixer_picker_sequence", lambda current_settings, current, target: (module.TELNET_KEY_DOWN, 1))

    calls = []

    def fake_send(current_session, payload, *, view_state=None, initial_timeout_s=None):
        del current_session, view_state, initial_timeout_s
        calls.append(payload)
        if payload == module.TELNET_KEY_LEFT:
            return "Save changes to Flash? Yes No"
        return "Vol UltiSid 1 +1 dB"

    monkeypatch.setattr(module, "session_send", fake_send)
    monkeypatch.setattr(
        module.u64_http,
        "verify_audio_mixer_value",
        lambda current_settings, target, *, shared_state=None: module.u64_http.confirm_audio_mixer_value(shared_state, "+1 dB"),
    )

    detail = module.session_write_audio_mixer_item(settings, session, "+1 dB", shared_state=state)

    assert detail == "from=0 dB to=+1 dB picker=down steps=1"
    assert calls == [
        module.TELNET_KEY_ENTER,
        module.TELNET_KEY_DOWN,
        module.TELNET_KEY_ENTER,
        module.TELNET_KEY_LEFT,
        module.TELNET_KEY_LEFT,
        module.TELNET_KEY_ENTER,
    ]
    assert session.view_state == "unknown"
    assert session.menu_focus == "unknown"
    assert state.get_shared_resource_value(module.u64_http.AUDIO_MIXER_SHARED_STATE_KEY) == "+1 dB"


def test_telnet_session_write_audio_mixer_item_does_not_double_confirm_shared_state(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()
    settings = make_settings(runtime)
    session = module.TelnetRunnerSession(sock=UnusedSocket())
    state = {}
    calls = []

    monkeypatch.setattr(module, "session_refresh_audio_mixer", lambda _session: "Vol UltiSid 1\n0 dB")
    monkeypatch.setattr(module, "session_extract_audio_mixer_value", lambda _session, text: (text, "0 dB"))
    monkeypatch.setattr(module.u64_http, "remember_audio_mixer_value", lambda shared_state, value: value)
    monkeypatch.setattr(module.u64_http, "normalize_audio_mixer_value", lambda value: value)
    monkeypatch.setattr(module, "audio_mixer_picker_sequence", lambda settings, current, target: (module.TELNET_KEY_DOWN, 0))
    monkeypatch.setattr(module.u64_http, "stage_audio_mixer_value", lambda shared_state, value: calls.append(("stage", value)))
    monkeypatch.setattr(
        module.u64_http,
        "verify_audio_mixer_value",
        lambda current_settings, target, *, shared_state=None: calls.append(("verify", target)) or target,
    )
    monkeypatch.setattr(
        module.u64_http,
        "confirm_audio_mixer_value",
        lambda shared_state, value: calls.append(("confirm", value)) or value,
    )

    detail = module.session_write_audio_mixer_item(settings, session, "0 dB", shared_state=state)

    assert detail == "from=0 dB to=0 dB picker=down steps=0"
    assert calls == [("verify", "0 dB")]


def test_audio_mixer_picker_sequence_prefers_shorter_direction(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()

    monkeypatch.setattr(module.u64_http, "audio_mixer_item_state", lambda settings: ("+1 dB", ("-1 dB", "0 dB", "+1 dB"), 123))

    direction, steps = module.audio_mixer_picker_sequence(make_settings(runtime), "+1 dB", "0 dB")

    assert direction == module.TELNET_KEY_UP
    assert steps == 1


def test_session_save_changes_to_flash_confirms_dialog(monkeypatch):
    module = load_telnet()
    session = module.TelnetRunnerSession(sock=UnusedSocket(), view_state="audio_mixer", last_text="Vol UltiSid 1 0 dB")
    calls = []

    def fake_send(current_session, payload, *, view_state=None, initial_timeout_s=None):
        del current_session, view_state, initial_timeout_s
        calls.append(payload)
        if len(calls) == 1:
            return "Save changes to Flash? Yes No"
        return "Vol UltiSid 1 0 dB"

    monkeypatch.setattr(module, "session_send", fake_send)

    text = module.session_save_changes_to_flash(session)

    assert text == "Vol UltiSid 1 0 dB"
    assert calls == [module.TELNET_KEY_LEFT, module.TELNET_KEY_LEFT, module.TELNET_KEY_ENTER]


def test_extended_telnet_incomplete_mode_uses_incomplete_operations(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()
    state = RotatingState()

    def unexpected_surface_operations(*args, **kwargs):
        raise AssertionError("surface_operations should not be used for incomplete Telnet probes")

    monkeypatch.setattr(
        module,
        "surface_operations",
        unexpected_surface_operations,
    )
    monkeypatch.setattr(
        module,
        "incomplete_operations",
        lambda surface: ((
            "telnet_audio_mixer_abort",
            lambda settings: (_ for _ in ()).throw(ConnectionResetError(104, "Connection reset by peer")),
        ),),
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
    assert outcome.detail == "surface=readwrite op=telnet_audio_mixer_abort expected_disconnect_after_abort"


def test_extended_telnet_open_mode_uses_surface_operations(monkeypatch):
    runtime = load_runtime()
    module = load_telnet()
    state = RotatingState()

    def unexpected_incomplete_operations(*args, **kwargs):
        raise AssertionError("incomplete_operations should not be used for open Telnet probes")

    monkeypatch.setattr(module, "incomplete_operations", unexpected_incomplete_operations)
    monkeypatch.setattr(
        module,
        "surface_operations",
        lambda surface, *, concurrent_multi_runner=False, shared_state=None: ((
            "set_vol_ultisid_1_0_db",
            lambda settings, session: "open_surface_path",
        ),),
    )
    monkeypatch.setattr(module, "run_open_surface_operation", lambda current_settings, runner_id, operation: operation(current_settings, None))

    context = runtime.ProbeExecutionContext(
        protocol="telnet",
        runner_id=1,
        iteration=1,
        surface=runtime.ProbeSurface.READWRITE,
        state=state,
    )

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.OPEN, context=context)

    assert outcome.result == "OK"
    assert outcome.detail == "surface=readwrite op=set_vol_ultisid_1_0_db open_surface_path"


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
