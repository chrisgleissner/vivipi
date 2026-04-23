from __future__ import annotations

from tests.unit.tooling._script_loader import load_script_module


def load_module():
    return load_script_module("u64_stream")


def make_snapshot(module, kind, packets_received):
    return module.StreamSnapshot(
        kind=kind,
        status="OK" if packets_received else "STARTING",
        packets_received=packets_received,
        lost_packets=0,
        reordered_packets=0,
        size_errors=0,
        header_errors=0,
        structure_errors=0,
        timeout_errors=0,
        first_packet_at=None,
        last_packet_at=None,
        last_sequence=None,
        last_error="",
    )


def test_parse_stream_selection_defaults_to_all_streams():
    module = load_module()

    assert module.parse_stream_selection(()) == module.STREAM_KIND_ORDER


def test_parse_stream_selection_preserves_order_and_deduplicates():
    module = load_module()

    assert module.parse_stream_selection(["video", "audio", "video"]) == (
        module.StreamKind.VIDEO,
        module.StreamKind.AUDIO,
    )


def test_stream_summary_parts_formats_health_snapshot():
    module = load_module()

    snapshots = (
        module.StreamSnapshot(
            kind=module.StreamKind.VIDEO,
            status="OK",
            packets_received=12,
            lost_packets=0,
            reordered_packets=0,
            size_errors=0,
            header_errors=0,
            structure_errors=0,
            timeout_errors=0,
            first_packet_at=1.0,
            last_packet_at=2.0,
            last_sequence=11,
            last_error="",
        ),
    )

    assert module.stream_summary_parts(snapshots) == (
        "stream_video=OK,packets:12,lost:0,reordered:0,size_errs:0,header_errs:0,structure_errs:0,timeout_errs:0",
    )


def test_stream_packet_tracker_marks_startup_timeout():
    module = load_module()
    tracker = module.StreamPacketTracker(module.StreamKind.VIDEO, startup_grace_s=1.0, packet_timeout_s=1.0, logger=lambda *args: None)

    tracker.note_idle(now=tracker.started_at + 1.5)
    snapshot = tracker.snapshot(now=tracker.started_at + 1.5)

    assert snapshot.status == "FAIL"
    assert snapshot.timeout_errors == 1
    assert snapshot.last_error == "no packets received before startup grace expired"


def test_stream_monitor_retries_audio_start_when_startup_stays_silent(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "_resolve_local_ip", lambda host, port: "127.0.0.1")
    monkeypatch.setattr(module, "_resolve_peer_ips", lambda host, port: ("127.0.0.1",))
    sleep_calls = []
    monkeypatch.setattr(module.time, "sleep", lambda delay_s: sleep_calls.append(delay_s))

    class SilentTracker:
        def snapshot(self, now=None):
            return make_snapshot(module, module.StreamKind.AUDIO, 0)

    monitor = module.StreamMonitor(module.StreamRuntimeSettings(host="host"), (module.StreamKind.AUDIO,), logger=lambda *args: None)
    monitor._trackers[module.StreamKind.AUDIO] = SilentTracker()
    enable_calls = []
    monitor._enable_stream = lambda kind, retry=False: enable_calls.append((kind, retry))

    monitor._retry_audio_start_if_silent()

    assert sleep_calls == [module.AUDIO_START_RETRY_DELAY_S]
    assert enable_calls == [(module.StreamKind.AUDIO, True)]


def test_stream_monitor_skips_audio_retry_once_packets_arrive(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "_resolve_local_ip", lambda host, port: "127.0.0.1")
    monkeypatch.setattr(module, "_resolve_peer_ips", lambda host, port: ("127.0.0.1",))
    sleep_calls = []
    monkeypatch.setattr(module.time, "sleep", lambda delay_s: sleep_calls.append(delay_s))

    class StartedTracker:
        def snapshot(self, now=None):
            return make_snapshot(module, module.StreamKind.AUDIO, 1)

    monitor = module.StreamMonitor(module.StreamRuntimeSettings(host="host"), (module.StreamKind.AUDIO,), logger=lambda *args: None)
    monitor._trackers[module.StreamKind.AUDIO] = StartedTracker()
    enable_calls = []
    monitor._enable_stream = lambda kind, retry=False: enable_calls.append((kind, retry))

    monitor._retry_audio_start_if_silent()

    assert sleep_calls == [module.AUDIO_START_RETRY_DELAY_S]
    assert enable_calls == []


def test_stream_packet_tracker_marks_lost_audio_packets():
    module = load_module()
    tracker = module.StreamPacketTracker(module.StreamKind.AUDIO, logger=lambda *args: None)
    payload1 = (1).to_bytes(2, "little") + (b"\x00" * (module.AUDIO_PACKET_SIZE - 2))
    payload3 = (3).to_bytes(2, "little") + (b"\x00" * (module.AUDIO_PACKET_SIZE - 2))

    tracker.note_packet(payload1, now=1.0)
    tracker.note_packet(payload3, now=2.0)
    snapshot = tracker.snapshot(now=2.0)

    assert snapshot.status == "FAIL"
    assert snapshot.lost_packets == 1
    assert snapshot.last_sequence == 3


def test_stream_packet_tracker_detects_video_size_error():
    module = load_module()
    tracker = module.StreamPacketTracker(module.StreamKind.VIDEO, logger=lambda *args: None)

    tracker.note_packet(b"\x00" * 10, now=1.0)
    snapshot = tracker.snapshot(now=1.0)

    assert snapshot.status == "FAIL"
    assert snapshot.size_errors == 1


def test_send_command_authenticates_with_shared_network_password(monkeypatch):
    module = load_module()
    calls = []

    class FakeSocket:
        def sendall(self, payload):
            calls.append(("sendall", payload))

        def close(self):
            calls.append("close")

    fake_socket = FakeSocket()
    monkeypatch.setattr(module.socket, "create_connection", lambda address, timeout: fake_socket)
    monkeypatch.setattr(module.u64_raw64, "authenticate_socket", lambda sock, password: calls.append(("auth", password)))

    module._send_command(
        module.StreamRuntimeSettings(host="host", control_port=64, network_password="secret"),
        b"payload",
    )

    assert calls == [
        ("auth", "secret"),
        ("sendall", b"payload"),
        "close",
    ]


def test_send_command_retries_transient_auth_reset(monkeypatch):
    module = load_module()
    calls = []

    class FakeSocket:
        def __init__(self, label):
            self.label = label

        def sendall(self, payload):
            calls.append((self.label, "sendall", payload))

        def close(self):
            calls.append((self.label, "close"))

    sockets = iter((FakeSocket("first"), FakeSocket("second")))
    monkeypatch.setattr(module.socket, "create_connection", lambda address, timeout: next(sockets))
    monkeypatch.setattr(module.time, "sleep", lambda delay_s: calls.append(("sleep", delay_s)))

    def authenticate(sock, password):
        calls.append((sock.label, "auth", password))
        if sock.label == "first":
            raise ConnectionResetError(104, "Connection reset by peer")

    monkeypatch.setattr(module.u64_raw64, "authenticate_socket", authenticate)

    module._send_command(
        module.StreamRuntimeSettings(host="host", control_port=64, network_password="secret"),
        b"payload",
    )

    assert calls == [
        ("first", "auth", "secret"),
        ("first", "close"),
        ("sleep", module.STREAM_CONTROL_RETRY_DELAYS_S[0]),
        ("second", "auth", "secret"),
        ("second", "sendall", b"payload"),
        ("second", "close"),
    ]
