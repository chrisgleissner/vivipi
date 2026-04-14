from __future__ import annotations

import importlib.util
import struct
import sys
import types
import uuid
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "u64_stream_test.py"


def load_module() -> types.ModuleType:
    module_name = f"test_u64_stream_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def make_video_packet(module, sequence: int, frame: int, line: int, *, terminal: bool = False) -> bytes:
    line_field = line | (0x8000 if terminal else 0)
    header = struct.pack(
        "<HHHHBBH",
        sequence,
        frame,
        line_field,
        module.VIDEO_WIDTH,
        module.VIDEO_LINES_PER_PACKET,
        module.VIDEO_BITS_PER_PIXEL,
        module.VIDEO_ENCODING,
    )
    return header + bytes(module.VIDEO_PACKET_SIZE - len(header))


def make_audio_packet(module, sequence: int) -> bytes:
    return struct.pack("<H", sequence) + bytes(module.AUDIO_PACKET_SIZE - module.AUDIO_HEADER_SIZE)


def make_debug_packet(module, sequence: int, *, reserved: int = 0) -> bytes:
    return struct.pack("<HH", sequence, reserved) + bytes(module.DEBUG_PACKET_SIZE - module.DEBUG_HEADER_SIZE)


def make_debug_packet_with_entries(module, sequence: int, entry_count: int, *, reserved: int = 0) -> bytes:
    return struct.pack("<HH", sequence, reserved) + bytes(entry_count * 4)


def test_parse_stream_selection_defaults_to_all_streams():
    module = load_module()

    assert module.parse_stream_selection(()) == (
        module.StreamKind.VIDEO,
        module.StreamKind.AUDIO,
        module.StreamKind.DEBUG,
    )


def test_parse_stream_selection_deduplicates_and_preserves_order():
    module = load_module()

    resolved = module.parse_stream_selection(("audio", "video", "audio", "debug"))

    assert resolved == (
        module.StreamKind.AUDIO,
        module.StreamKind.VIDEO,
        module.StreamKind.DEBUG,
    )


def test_video_tracker_detects_sequence_gap_without_header_errors():
    module = load_module()
    tracker = module.StreamPacketTracker(module.StreamKind.VIDEO, startup_grace_s=0.1)

    tracker.note_packet(make_video_packet(module, 10, 3, 0))
    tracker.note_packet(make_video_packet(module, 12, 3, 8))

    snapshot = tracker.snapshot(now=tracker.started_at + 1.0)
    assert snapshot.packets_received == 2
    assert snapshot.lost_packets == 1
    assert snapshot.header_errors == 0
    assert snapshot.status == "FAIL"


def test_audio_tracker_detects_invalid_packet_size():
    module = load_module()
    tracker = module.StreamPacketTracker(module.StreamKind.AUDIO, startup_grace_s=0.1)

    tracker.note_packet(make_audio_packet(module, 1)[:-4])

    snapshot = tracker.snapshot(now=tracker.started_at + 1.0)
    assert snapshot.size_errors == 1
    assert snapshot.status == "FAIL"


def test_debug_tracker_detects_reserved_header_bits():
    module = load_module()
    tracker = module.StreamPacketTracker(module.StreamKind.DEBUG, startup_grace_s=0.1)

    tracker.note_packet(make_debug_packet(module, 7, reserved=3))

    snapshot = tracker.snapshot(now=tracker.started_at + 1.0)
    assert snapshot.header_errors == 1
    assert snapshot.status == "FAIL"


def test_debug_tracker_accepts_shorter_aligned_packets_within_entry_limit():
    module = load_module()
    tracker = module.StreamPacketTracker(module.StreamKind.DEBUG, startup_grace_s=0.1)

    tracker.note_packet(make_debug_packet_with_entries(module, 7, 200))

    snapshot = tracker.snapshot(now=tracker.started_at + 1.0)
    assert snapshot.size_errors == 0
    assert snapshot.structure_errors == 0
    assert snapshot.header_errors == 0
    assert snapshot.status == "OK"


def test_tracker_transitions_from_starting_to_timeout_failure_without_packets():
    module = load_module()
    tracker = module.StreamPacketTracker(module.StreamKind.AUDIO, startup_grace_s=0.1, packet_timeout_s=0.1)

    assert tracker.snapshot(now=tracker.started_at + 0.05).status == "STARTING"
    tracker.note_idle(now=tracker.started_at + 0.2)

    snapshot = tracker.snapshot(now=tracker.started_at + 0.2)
    assert snapshot.timeout_errors == 1
    assert snapshot.status == "FAIL"


def test_stream_summary_parts_render_all_counters():
    module = load_module()
    snapshot = module.StreamSnapshot(
        kind=module.StreamKind.DEBUG,
        status="OK",
        packets_received=25,
        lost_packets=0,
        reordered_packets=0,
        size_errors=0,
        header_errors=0,
        structure_errors=0,
        timeout_errors=0,
        first_packet_at=1.0,
        last_packet_at=2.0,
        last_sequence=24,
        last_error="",
    )

    assert module.stream_summary_parts((snapshot,)) == (
        "stream_debug=OK,packets:25,lost:0,reordered:0,size_errs:0,header_errs:0,structure_errs:0,timeout_errs:0",
    )


def test_enable_command_encodes_duration_zero_and_destination_ascii():
    module = load_module()

    command = module._build_enable_command(module.StreamKind.AUDIO, "192.168.1.10:1234")

    assert command[:4] == struct.pack("<HH", 0xFF21, 19)
    assert command[4:6] == b"\x00\x00"
    assert command[6:] == b"192.168.1.10:1234"


def test_resolve_peer_ips_returns_unique_ipv4_addresses(monkeypatch):
    module = load_module()

    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda host, port, type: [
            (module.socket.AF_INET, module.socket.SOCK_DGRAM, 17, "", ("192.168.1.13", port)),
            (module.socket.AF_INET6, module.socket.SOCK_DGRAM, 17, "", ("::1", port, 0, 0)),
            (module.socket.AF_INET, module.socket.SOCK_DGRAM, 17, "", ("192.168.1.13", port)),
            (module.socket.AF_INET, module.socket.SOCK_DGRAM, 17, "", ("192.168.1.14", port)),
        ],
    )

    assert module._resolve_peer_ips("u64", 64) == ("192.168.1.13", "192.168.1.14")
