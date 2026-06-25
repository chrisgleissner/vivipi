#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
import random
import socket
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_TRAFFIC_CONFIG = "config/u64_dma_rest_benchmark_traffic.json"
DEFAULT_TRAFFIC_NAME = "c64cast"
TOOL_NAME = "u64_dma_rest_benchmark"
WRITEMEM_PATH = "/v1/machine:writemem"

# Socket-DMA command constants (verified-from-source, socket_dma.cc L32-56).
SOCKET_CMD_DMAWRITE = 0xFF06
SOCKET_CMD_REUWRITE = 0xFF07
SOCKET_CMD_IDENTIFY = 0xFF0E
SOCKET_CMD_AUTHENTICATE = 0xFF1F
SOCKET_CMD_DEBUG_REG = 0xFF76

# Single-frame payload ceilings (verified-from-source).
DMAWRITE_MAX_DATA = 65533  # 65535 - 2 address bytes
REUWRITE_MAX_DATA = 65532  # 65535 - 3 offset bytes
REST_PUT_MAX_BYTES = 128
C64_ADDR_MAX = 0xFFFF
C64_ADDR_SPACE_MAX = 0x10000
REU_ADDR_SPACE_MAX = 0x1000000  # 24-bit REU offset space ($000000-$FFFFFF)

DMA_DEFAULT_TIMEOUT_S = 2.0
DMA_RECV_TIMEOUT_S = 1.0
HTTP_DEFAULT_TIMEOUT_S = 8.0

DEFAULT_HOST = os.getenv("HOST", "u64")
DEFAULT_HTTP_PORT = int(os.getenv("HTTP_PORT", "80"))
DEFAULT_DMA_PORT = int(os.getenv("DMA_PORT", "64"))
NETWORK_PASSWORD = os.getenv("NETWORK_PASSWORD", "")
FTP_PASS = os.getenv("FTP_PASS", "")
DEFAULT_SEED = 1
DEFAULT_LOG_EVERY = 1
DEFAULT_DELAY_MS = 0
DEFAULT_PROBES = ("rest", "dma")
PROBE_CHOICES = ("rest", "dma")
SCHEDULE_SEQUENTIAL = "sequential"
SCHEDULE_CONCURRENT = "concurrent"
REST_METHOD_AUTO = "auto"
REST_METHOD_POST = "post"
REST_METHOD_PUT = "put"
HTTP_CONN_CLOSE = "close"
HTTP_CONN_PERSISTENT = "persistent"
DMA_ACK_BARRIER = "barrier"
DMA_ACK_SEND_ONLY = "send-only"
DMA_BARRIER_DEBUGREG = "debugreg"
DMA_BARRIER_IDENTIFY = "identify"
DMA_CONN_PERSISTENT = "persistent"
DMA_CONN_PER_REQUEST = "per-request"
PAYLOAD_ZERO = "zero"
PAYLOAD_INCREMENT = "increment"
PAYLOAD_RANDOM = "random"
PAYLOAD_FRAME_COUNTER = "frame-counter"
PAYLOAD_CHOICES = (PAYLOAD_ZERO, PAYLOAD_INCREMENT, PAYLOAD_RANDOM, PAYLOAD_FRAME_COUNTER)
SPACE_C64 = "c64"
SPACE_REU = "reu"
SPACES = (SPACE_C64, SPACE_REU)
WRITE_KIND_DMAWRITE = "dmawrite"
WRITE_KIND_REUWRITE = "reuwrite"
WRITE_KIND_REST = "rest-writemem"
WRITE_KINDS = (WRITE_KIND_DMAWRITE, WRITE_KIND_REUWRITE, WRITE_KIND_REST)
DIRTY_POLICIES = ("full", "one-span", "slabs", "skip-repeated")
REST_POLICY_WRITE = "write"
REST_POLICY_SKIP = "skip"
REST_POLICY_SHADOW = "c64-shadow"
REST_POLICIES = (REST_POLICY_WRITE, REST_POLICY_SKIP, REST_POLICY_SHADOW)

FIRMWARE_CHECKOUT_ENV = "VIVIPI_1541ULTIMATE_PATH"
_DEFAULT_FIRMWARE_CHECKOUT = REPO_ROOT / "1541ultimate"


class SupportsClose(Protocol):
    def close(self) -> None: ...


def now_iso() -> str:
    # Read the wall clock once so the seconds and milliseconds fields cannot
    # straddle a second boundary (two independent time calls could disagree by
    # up to 1 s right at the rollover).
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def firmware_source_info(checkout: Path | None = None) -> dict[str, Any]:
    path = checkout or Path(os.getenv(FIRMWARE_CHECKOUT_ENV, str(_DEFAULT_FIRMWARE_CHECKOUT)))
    info: dict[str, Any] = {"checkout_path": str(path), "commit": None, "dirty": None}
    if not path.exists():
        info["checkout_path"] = str(path)
        return info
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        info["commit"] = completed.stdout.strip() or None
    except Exception:
        info["commit"] = None
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        info["dirty"] = bool(completed.stdout.strip())
    except Exception:
        info["dirty"] = None
    return info


# --------------------------------------------------------------------------- #
# Traffic configuration model
# --------------------------------------------------------------------------- #


def parse_address(raw: str, *, space: str) -> int:
    text = str(raw).strip()
    if not text:
        raise ValueError("address must be a non-empty hex string")
    if text.lower().startswith("0x"):
        text = text[2:]
    try:
        value = int(text, 16)
    except ValueError as error:
        raise ValueError(f"invalid hex address: {raw!r}") from error
    if value < 0:
        raise ValueError(f"address must be non-negative: {raw!r}")
    if space == SPACE_C64 and value > C64_ADDR_MAX:
        raise ValueError(f"c64 address exceeds $FFFF: {raw!r}")
    if space == SPACE_REU and value > 0xFFFFFF:
        raise ValueError(f"reu offset exceeds 24 bits: {raw!r}")
    return value


def format_address(address: int) -> str:
    return f"{address:04X}"


@dataclass(frozen=True)
class WriteTemplate:
    index: int
    space: str
    address: int
    bytes_count: int
    write_kind: str
    label: str | None = None
    enabled: bool = True
    repeat: int = 1
    bank_cycle: tuple[int, ...] = ()
    dirty_policy: str = "full"
    dirty_span: int = 0
    dirty_full_every: int = 1
    rest_policy: str = REST_POLICY_WRITE
    split_policy: str = "reject"
    split_size: int = 0
    write_id: str | None = None


@dataclass(frozen=True)
class LogicalWrite:
    logical_write_id: str
    space: str
    address: int
    bytes_count: int
    write_kind: str
    label: str | None
    rest_policy: str
    emitted_write_index: int
    dirty_offset: int
    template_index: int


@dataclass(frozen=True)
class TrafficProfile:
    name: str
    description: str
    unit: str
    rate_hz: float | None
    iterations: int | None
    duration_s: float | None
    pacing: str
    inter_write_delay_ms: int
    payload_pattern: str
    seed: int
    writes: tuple[WriteTemplate, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


def _require_dict(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _require_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context} must be an integer")
    return value


def _parse_dirty(raw: Any) -> tuple[str, int, int]:
    if raw is None:
        return ("full", 0, 1)
    if not isinstance(raw, dict):
        raise ValueError("dirty must be an object")
    policy = str(raw.get("policy", "full"))
    if policy not in DIRTY_POLICIES:
        raise ValueError(f"invalid dirty policy: {policy!r}")
    span = int(raw.get("span", 0) or 0)
    if span < 0:
        raise ValueError("dirty span must be >= 0")
    full_every = int(raw.get("full_every", 1) or 1)
    if full_every < 1:
        raise ValueError("dirty full_every must be >= 1")
    return (policy, span, full_every)


def _parse_split(raw: Any) -> tuple[str, int]:
    if raw is None:
        return ("reject", 0)
    if not isinstance(raw, dict):
        raise ValueError("split must be an object")
    policy = str(raw.get("policy", "reject"))
    if policy not in ("reject", "chunk"):
        raise ValueError(f"invalid split policy: {policy!r}")
    size = int(raw.get("size", 0) or 0)
    if policy == "chunk" and size < 1:
        raise ValueError("split chunk size must be >= 1")
    return (policy, size)


def parse_write_template(raw: Any, index: int) -> WriteTemplate:
    obj = _require_dict(raw, f"writes[{index}]")
    space = str(obj.get("space", ""))
    if space not in SPACES:
        raise ValueError(f"writes[{index}] has invalid space: {space!r}")
    address = parse_address(obj.get("address"), space=space)
    bytes_count = obj.get("bytes")
    if isinstance(bytes_count, bool) or not isinstance(bytes_count, int) or bytes_count <= 0:
        raise ValueError(f"writes[{index}] bytes must be a positive integer")
    write_kind = str(obj.get("write_kind", ""))
    if write_kind not in WRITE_KINDS:
        raise ValueError(f"writes[{index}] has invalid write_kind: {write_kind!r}")
    if write_kind == WRITE_KIND_REUWRITE and space != SPACE_REU:
        raise ValueError(f"writes[{index}] reuwrite requires space=reu")
    if write_kind in (WRITE_KIND_DMAWRITE, WRITE_KIND_REST) and space != SPACE_C64:
        raise ValueError(f"writes[{index}] {write_kind} requires space=c64")
    label = obj.get("label")
    if label is not None:
        label = str(label)
    enabled = bool(obj.get("enabled", True))
    repeat = int(obj.get("repeat", 1) or 1)
    if repeat < 1:
        raise ValueError(f"writes[{index}] repeat must be >= 1")
    bank_cycle_raw = obj.get("bank_cycle")
    bank_cycle: tuple[int, ...] = ()
    if bank_cycle_raw is not None:
        if not isinstance(bank_cycle_raw, list) or not bank_cycle_raw:
            raise ValueError(f"writes[{index}] bank_cycle must be a non-empty list")
        bank_cycle = tuple(parse_address(item, space=space) for item in bank_cycle_raw)
    dirty_policy, dirty_span, dirty_full_every = _parse_dirty(obj.get("dirty"))
    rest_policy = str(obj.get("rest_policy", REST_POLICY_SHADOW if space == SPACE_REU else REST_POLICY_WRITE))
    if rest_policy not in REST_POLICIES:
        raise ValueError(f"writes[{index}] invalid rest_policy: {rest_policy!r}")
    if space == SPACE_REU and rest_policy == REST_POLICY_WRITE:
        rest_policy = REST_POLICY_SKIP
    split_policy, split_size = _parse_split(obj.get("split"))
    write_id = obj.get("write_id")
    if write_id is not None:
        write_id = str(write_id)
    return WriteTemplate(
        index=index,
        space=space,
        address=address,
        bytes_count=bytes_count,
        write_kind=write_kind,
        label=label,
        enabled=enabled,
        repeat=repeat,
        bank_cycle=bank_cycle,
        dirty_policy=dirty_policy,
        dirty_span=dirty_span,
        dirty_full_every=dirty_full_every,
        rest_policy=rest_policy,
        split_policy=split_policy,
        split_size=split_size,
        write_id=write_id,
    )


def _validate_template_limits(template: WriteTemplate) -> None:
    max_data = REUWRITE_MAX_DATA if template.space == SPACE_REU else DMAWRITE_MAX_DATA
    if template.split_policy == "reject" and template.bytes_count > max_data:
        raise ValueError(
            f"writes[{template.index}] bytes={template.bytes_count} exceeds single-frame max {max_data}; "
            "add an explicit split policy"
        )
    addr_space_max = REU_ADDR_SPACE_MAX if template.space == SPACE_REU else C64_ADDR_SPACE_MAX
    limit_label = "$FFFFFF" if template.space == SPACE_REU else "$FFFF"
    # When bank_cycle is set those entries are the bases actually written each
    # unit (the static address is unused); otherwise the static address applies.
    # Every base spans up to bytes_count, so check each against the space limit.
    base_addresses = template.bank_cycle or (template.address,)
    for base in base_addresses:
        if base + template.bytes_count > addr_space_max:
            raise ValueError(f"writes[{template.index}] write range exceeds {limit_label} at ${base:X}")


def parse_traffic_profile(raw: Any) -> TrafficProfile:
    obj = _require_dict(raw, "traffic profile")
    name = str(obj.get("name", "")).strip()
    if not name:
        raise ValueError("traffic profile name must be a non-empty string")
    description = str(obj.get("description", "")).strip()
    unit = str(obj.get("unit", "unit")).strip() or "unit"
    rate_hz = obj.get("rate_hz")
    if rate_hz is not None:
        rate_hz = float(rate_hz)
        if rate_hz <= 0:
            raise ValueError(f"traffic {name!r} rate_hz must be > 0")
    iterations = obj.get("iterations")
    if iterations is not None:
        iterations = _require_int(iterations, f"traffic {name!r} iterations")
        if iterations < 1:
            raise ValueError(f"traffic {name!r} iterations must be >= 1")
    duration_s = obj.get("duration_s")
    if duration_s is not None:
        duration_s = float(duration_s)
        if duration_s <= 0:
            raise ValueError(f"traffic {name!r} duration_s must be > 0")
    pacing = str(obj.get("pacing", "none"))
    if pacing not in ("unit", "none"):
        raise ValueError(f"traffic {name!r} invalid pacing: {pacing!r}")
    inter_write_delay_ms = int(obj.get("inter_write_delay_ms", 0) or 0)
    if inter_write_delay_ms < 0:
        raise ValueError(f"traffic {name!r} inter_write_delay_ms must be >= 0")
    payload_pattern = str(obj.get("payload_pattern", PAYLOAD_ZERO))
    if payload_pattern not in PAYLOAD_CHOICES:
        raise ValueError(f"traffic {name!r} invalid payload_pattern: {payload_pattern!r}")
    seed = int(obj.get("seed", DEFAULT_SEED) or DEFAULT_SEED)
    writes_raw = obj.get("writes")
    if not isinstance(writes_raw, list) or not writes_raw:
        raise ValueError(f"traffic {name!r} writes must be a non-empty list")
    writes = tuple(parse_write_template(item, idx) for idx, item in enumerate(writes_raw))
    enabled = [w for w in writes if w.enabled]
    if not enabled:
        raise ValueError(f"traffic {name!r} has no enabled writes")
    for write in writes:
        _validate_template_limits(write)
    seen_ids: set[str] = set()
    for write in writes:
        if write.write_id is not None and write.write_id in seen_ids:
            raise ValueError(f"traffic {name!r} duplicate write_id: {write.write_id!r}")
        if write.write_id is not None:
            seen_ids.add(write.write_id)
    metadata = obj.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(f"traffic {name!r} metadata must be an object")
    return TrafficProfile(
        name=name,
        description=description,
        unit=unit,
        rate_hz=rate_hz,
        iterations=iterations,
        duration_s=duration_s,
        pacing=pacing,
        inter_write_delay_ms=inter_write_delay_ms,
        payload_pattern=payload_pattern,
        seed=seed,
        writes=writes,
        metadata=dict(metadata),
    )


class TrafficConfig:
    def __init__(self, version: int, default: str, profiles: tuple[TrafficProfile, ...]):
        self.version = version
        self.default = default
        self.profiles = profiles
        self._by_name = {profile.name: profile for profile in profiles}

    def select(self, name: str | None) -> TrafficProfile:
        key = name or self.default
        if key not in self._by_name:
            available = ", ".join(sorted(self._by_name)) or "<none>"
            raise ValueError(f"traffic profile {key!r} not found; available: {available}")
        return self._by_name[key]


def load_traffic_config(path: str | Path) -> TrafficConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("traffic config must be a JSON object")
    version = int(raw.get("version", 1) or 1)
    traffic_raw = raw.get("traffic")
    if not isinstance(traffic_raw, list) or not traffic_raw:
        raise ValueError("traffic config 'traffic' must be a non-empty list")
    profiles = tuple(parse_traffic_profile(item) for item in traffic_raw)
    seen: set[str] = set()
    for profile in profiles:
        if profile.name in seen:
            raise ValueError(f"duplicate traffic name: {profile.name!r}")
        seen.add(profile.name)
    default = str(raw.get("default", profiles[0].name))
    if default not in seen:
        raise ValueError(f"default traffic {default!r} not present in traffic list")
    return TrafficConfig(version=version, default=default, profiles=profiles)


# --------------------------------------------------------------------------- #
# Unit expansion (repeat, bank_cycle, dirty, split)
# --------------------------------------------------------------------------- #


def _chunk_ranges(total: int, span: int) -> list[tuple[int, int]]:
    if total <= 0 or span <= 0:
        return [(0, total)]
    ranges: list[tuple[int, int]] = []
    offset = 0
    while offset < total:
        size = min(span, total - offset)
        ranges.append((offset, size))
        offset += size
    return ranges


def expand_template(
    template: WriteTemplate, unit_index: int, emitted_start: int
) -> list[LogicalWrite]:
    """Expand one template into ordered logical writes for a single unit."""
    if not template.enabled:
        return []
    base_address = template.address
    if template.bank_cycle:
        base_address = template.bank_cycle[unit_index % len(template.bank_cycle)]
    total = template.bytes_count
    policy = template.dirty_policy

    if policy == "skip-repeated":
        if unit_index % max(1, template.dirty_full_every) != 0:
            return []

    if policy == "one-span":
        span = template.dirty_span or total
        span = min(span, total)
        if span <= 0:
            span = total
        cycle_extent = total - span + 1
        offset = (unit_index * span) % cycle_extent if cycle_extent > 0 else 0
        spans: list[tuple[int, int]] = [(offset, span)]
    elif policy == "slabs":
        span = template.dirty_span or total
        spans = _chunk_ranges(total, span)
    else:  # full or skip-repeated (full write on active units)
        spans = [(0, total)]

    logical_writes: list[LogicalWrite] = []
    emitted = emitted_start
    for span_offset, span_size in spans:
        chunks: list[tuple[int, int]]
        if template.split_policy == "chunk" and span_size > template.split_size > 0:
            chunks = _chunk_ranges(span_size, template.split_size)
        else:
            chunks = [(0, span_size)]
        for chunk_offset, chunk_size in chunks:
            address = base_address + span_offset + chunk_offset
            dirty_offset = span_offset + chunk_offset
            for _ in range(template.repeat):
                logical_writes.append(
                    LogicalWrite(
                        logical_write_id=f"u{unit_index:06d}e{emitted:03d}",
                        space=template.space,
                        address=address,
                        bytes_count=chunk_size,
                        write_kind=template.write_kind,
                        label=template.label,
                        rest_policy=template.rest_policy,
                        emitted_write_index=emitted,
                        dirty_offset=dirty_offset,
                        template_index=template.index,
                    )
                )
                emitted += 1
    return logical_writes


def expand_unit(profile: TrafficProfile, unit_index: int) -> list[LogicalWrite]:
    """Expand all enabled templates for one logical unit, preserving order.

    The emitted-write index accumulates across templates within the unit so that
    each logical write gets a stable, label-independent identity.
    """
    emitted = 0
    out: list[LogicalWrite] = []
    for template in profile.writes:
        if not template.enabled:
            continue
        produced = expand_template(template, unit_index, emitted)
        emitted += len(produced)
        out.extend(produced)
    return out


def per_unit_bytes(profile: TrafficProfile, unit_index: int = 0) -> int:
    return sum(lw.bytes_count for lw in expand_unit(profile, unit_index))


# --------------------------------------------------------------------------- #
# Deterministic payload generation (label-independent)
# --------------------------------------------------------------------------- #


def _identity_digest(
    *,
    traffic_name: str,
    unit_index: int,
    emitted_write_index: int,
    space: str,
    address: int,
    byte_count: int,
    dirty_offset: int,
    seed: int,
    pattern: str,
) -> int:
    digest = hashlib.blake2b(digest_size=8)
    for part in (
        traffic_name, pattern, space, seed, unit_index, emitted_write_index,
        address, byte_count, dirty_offset,
    ):
        digest.update(b"\0")
        digest.update(str(part).encode("utf-8"))
    return int.from_bytes(digest.digest(), "big")


def generate_payload(
    *,
    traffic_name: str,
    unit_index: int,
    emitted_write_index: int,
    space: str,
    address: int,
    byte_count: int,
    dirty_offset: int,
    seed: int,
    pattern: str,
) -> bytes:
    if byte_count <= 0:
        return b""
    if pattern == PAYLOAD_ZERO:
        return b"\x00" * byte_count
    if pattern == PAYLOAD_INCREMENT:
        return bytes((i + dirty_offset) & 0xFF for i in range(byte_count))
    if pattern == PAYLOAD_FRAME_COUNTER:
        return bytes((unit_index + i + dirty_offset) & 0xFF for i in range(byte_count))
    # random
    seed_value = _identity_digest(
        traffic_name=traffic_name,
        unit_index=unit_index,
        emitted_write_index=emitted_write_index,
        space=space,
        address=address,
        byte_count=byte_count,
        dirty_offset=dirty_offset,
        seed=seed,
        pattern=pattern,
    )
    rng = random.Random(seed_value)
    return bytes(rng.randrange(256) for _ in range(byte_count))


# --------------------------------------------------------------------------- #
# DMA framing
# --------------------------------------------------------------------------- #


def command_frame(command: int, payload: bytes = b"") -> bytes:
    return struct.pack("<HH", command, len(payload)) + payload


def dma_write_frame(logical: LogicalWrite, data: bytes) -> tuple[int, bytes]:
    if logical.space == SPACE_REU:
        prefix = struct.pack("<I", logical.address & 0xFFFFFF)[:3]
        return SOCKET_CMD_REUWRITE, prefix + data
    prefix = struct.pack("<H", logical.address & 0xFFFF)
    return SOCKET_CMD_DMAWRITE, prefix + data


def authenticate_frame(password: str) -> bytes:
    return command_frame(SOCKET_CMD_AUTHENTICATE, password.encode("utf-8"))


def command_label(command: int) -> str:
    return f"0x{command:04X}"


def validate_dma_payload(logical: LogicalWrite, data: bytes) -> None:
    if logical.space == SPACE_REU and len(data) > REUWRITE_MAX_DATA:
        raise ValueError(f"reu write payload {len(data)} exceeds single-frame max {REUWRITE_MAX_DATA}")
    if logical.space == SPACE_C64 and len(data) > DMAWRITE_MAX_DATA:
        raise ValueError(f"dmawrite payload {len(data)} exceeds single-frame max {DMAWRITE_MAX_DATA}")


def validate_rest_payload(logical: LogicalWrite, data: bytes, *, method: str) -> None:
    if logical.space == SPACE_REU:
        raise ValueError("rest cannot represent reu address space")
    if logical.address + len(data) > C64_ADDR_SPACE_MAX:
        raise ValueError(f"rest write range exceeds $FFFF at ${logical.address:04X}")
    if method.upper() == "PUT" and len(data) > REST_PUT_MAX_BYTES:
        raise ValueError(f"rest put payload {len(data)} exceeds {REST_PUT_MAX_BYTES} bytes")


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise RuntimeError(f"short dma read expected={size} got={len(chunks)}")
        chunks.extend(chunk)
    return bytes(chunks)


# --------------------------------------------------------------------------- #
# REST framing
# --------------------------------------------------------------------------- #


def rest_request_path_put(address: int, data: bytes) -> str:
    if len(data) > REST_PUT_MAX_BYTES:
        raise ValueError(f"rest put payload {len(data)} exceeds {REST_PUT_MAX_BYTES} bytes")
    if len(data) < 1:
        raise ValueError("rest put payload must be at least one byte")
    hex_text = data.hex().upper()
    if len(hex_text) % 2 != 0 or not all(c in "0123456789ABCDEF" for c in hex_text):
        raise ValueError("rest put hex encoding produced an invalid even-length hex string")
    return f"{WRITEMEM_PATH}?address={address:04X}&data={hex_text}"


def rest_request_path_post(address: int) -> str:
    return f"{WRITEMEM_PATH}?address={address:04X}"


def resolve_rest_method(method: str, data_len: int) -> str:
    # The 1541Ultimate HTTP server matches API routes case-sensitively and rejects
    # lowercase methods with HTTP 404, so always return uppercase HTTP verbs.
    if method == REST_METHOD_AUTO:
        return "PUT" if data_len <= REST_PUT_MAX_BYTES else "POST"
    return method.upper()


# --------------------------------------------------------------------------- #
# Benchmark context + executors
# --------------------------------------------------------------------------- #


@dataclass
class BenchmarkContext:
    host: str
    http_port: int
    dma_port: int
    network_password: str
    rest_method: str
    http_connection: str
    dma_ack_mode: str
    dma_barrier: str
    dma_connection: str
    run_id: str
    traffic: TrafficProfile
    seed: int
    rounds: int
    verbose: bool
    delay_ms: int
    log_every: int
    http_timeout_s: float = HTTP_DEFAULT_TIMEOUT_S
    dma_timeout_s: float = DMA_DEFAULT_TIMEOUT_S
    now_fn: Callable[[], str] = field(default=now_iso)
    perf_fn: Callable[[], float] = field(default=time.perf_counter)
    sleep_fn: Callable[[float], None] = field(default=time.sleep)
    emit: Callable[[dict[str, Any]], None] = field(default=lambda event: print(json.dumps(event), flush=True))
    diag: Callable[[str], None] = field(default=lambda message: print(message, file=sys.stderr, flush=True))
    http_connection_factory: Callable[..., Any] = field(
        default=lambda host, port, timeout: http.client.HTTPConnection(host, port, timeout=timeout)
    )
    socket_factory: Callable[..., Any] = field(
        default=lambda address, timeout: socket.create_connection(address, timeout=timeout)
    )


class BenchmarkError(Exception):
    pass


def _rest_headers(ctx: BenchmarkContext) -> dict[str, str]:
    # Match the HTTP Connection header to the selected mode so a keep-alive-capable
    # firmware is actually exercised in persistent mode instead of being told to
    # close after every response.
    connection = "keep-alive" if ctx.http_connection == HTTP_CONN_PERSISTENT else "close"
    headers = {"Connection": connection}
    if ctx.network_password:
        headers["X-Password"] = ctx.network_password
    return headers


class RestExecutor:
    def __init__(self, ctx: BenchmarkContext):
        self.ctx = ctx
        self.conn: Any = None

    def open(self) -> None:
        if self.ctx.http_connection == HTTP_CONN_PERSISTENT:
            self.conn = self.ctx.http_connection_factory(
                self.ctx.host, self.ctx.http_port, self.ctx.http_timeout_s
            )

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def _new_connection(self) -> Any:
        return self.ctx.http_connection_factory(
            self.ctx.host, self.ctx.http_port, self.ctx.http_timeout_s
        )

    def execute(self, logical: LogicalWrite, data: bytes) -> dict[str, Any]:
        method = resolve_rest_method(self.ctx.rest_method, len(data))
        validate_rest_payload(logical, data, method=method)
        persistent = self.ctx.http_connection == HTTP_CONN_PERSISTENT and self.conn is not None
        conn = self.conn if persistent else None
        owned = conn is None
        body: bytes | None
        if method == "PUT":
            path = rest_request_path_put(logical.address, data)
            body = None
        else:
            path = rest_request_path_post(logical.address)
            body = data
        headers = dict(_rest_headers(self.ctx))
        if method == "POST":
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-Length"] = str(len(data))
        status: int | None = None
        resp_bytes = 0
        error: str | None = None
        error_type: str | None = None
        t0 = self.ctx.perf_fn()
        try:
            if owned:
                conn = self._new_connection()
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            response_body = response.read()
            status = response.status
            resp_bytes = len(response_body)
            ok = 200 <= status < 300
            if not ok:
                text = response_body.decode("utf-8", "replace").strip()
                error = f"HTTP {status}: {text[:200]}" if text else f"HTTP {status}"
                error_type = "http_status"
        except Exception as exc:
            ok = False
            error = str(exc)
            error_type = type(exc).__name__
        finally:
            latency_ms = (self.ctx.perf_fn() - t0) * 1000.0
            if owned and conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        event: dict[str, Any] = {
            "method": method,
            "path": path,
            "http_connection": self.ctx.http_connection,
            "latency_ms": latency_ms,
            "ok": ok if "ok" in locals() else False,
        }
        if status is not None:
            event["status"] = status
        event["response_bytes"] = resp_bytes
        if error is not None:
            event["error"] = error
            event["error_type"] = error_type
        return event


class DmaExecutor:
    def __init__(self, ctx: BenchmarkContext):
        self.ctx = ctx
        self.sock: Any = None
        self.authenticated = False
        self.send_only_warned = False

    def _open_socket(self) -> Any:
        sock = self.ctx.socket_factory((self.ctx.host, self.ctx.dma_port), self.ctx.dma_timeout_s)
        try:
            sock.settimeout(DMA_RECV_TIMEOUT_S)
        except Exception:
            pass
        # Disable Nagle so small DMAWRITE frames (7 bytes for an empty
        # barrier, ~10 bytes for a small write) ship immediately instead
        # of waiting ~40 ms for the kernel to coalesce. Without this, every
        # barrier-mode DMAWRITE is dominated by Nagle, not device-side
        # completion — and that is not what we want to measure. See
        # c64cast/c64cast/socket_dma.py:114-117 for the same rationale.
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        return sock

    def _authenticate(self, sock: Any) -> None:
        if not self.ctx.network_password:
            return
        sock.sendall(authenticate_frame(self.ctx.network_password))
        response = recv_exact(sock, 1)
        if response != b"\x01":
            raise BenchmarkError("dma authentication failed (0x00)")

    def open(self) -> None:
        if self.ctx.dma_connection == DMA_CONN_PERSISTENT:
            self.sock = self._open_socket()
            self._authenticate(self.sock)
            self.authenticated = True

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _barrier_response(self, sock: Any) -> str:
        if self.ctx.dma_barrier == DMA_BARRIER_IDENTIFY:
            sock.sendall(command_frame(SOCKET_CMD_IDENTIFY))
            title_len = recv_exact(sock, 1)[0]
            if title_len:
                recv_exact(sock, title_len)
            return "identify"
        # default debugreg: zero-length returns one byte, no state change
        sock.sendall(command_frame(SOCKET_CMD_DEBUG_REG, b""))
        recv_exact(sock, 1)
        return "debugreg"

    def execute(self, logical: LogicalWrite, data: bytes) -> dict[str, Any]:
        validate_dma_payload(logical, data)
        command, payload = dma_write_frame(logical, data)
        persistent = self.ctx.dma_connection == DMA_CONN_PERSISTENT and self.sock is not None
        sock = self.sock if persistent else None
        owned = sock is None
        error: str | None = None
        error_type: str | None = None
        barrier_name: str | None = None
        ok = True
        t0 = self.ctx.perf_fn()
        try:
            if owned:
                sock = self._open_socket()
                self._authenticate(sock)
            sock.sendall(command_frame(command, payload))
            if self.ctx.dma_ack_mode == DMA_ACK_BARRIER:
                barrier_name = self._barrier_response(sock)
            # send-only: nothing more to wait for
        except Exception as exc:
            ok = False
            error = str(exc)
            error_type = type(exc).__name__
        latency_ms = (self.ctx.perf_fn() - t0) * 1000.0
        if owned and sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        if self.ctx.dma_ack_mode == DMA_ACK_SEND_ONLY and not self.send_only_warned:
            self.send_only_warned = True
            self.ctx.emit(
                {
                    "event": "warning",
                    "run_id": self.ctx.run_id,
                    "code": "DMA_SEND_ONLY_LATENCY",
                    "message": "send-only DMA timing measures host/socket-buffer latency, not device-side completion",
                }
            )
        event: dict[str, Any] = {
            "command": command_label(command),
            "ack_mode": self.ctx.dma_ack_mode,
            "dma_connection": self.ctx.dma_connection,
            "latency_ms": latency_ms,
            "ok": ok,
        }
        if barrier_name is not None:
            event["barrier"] = barrier_name
        if error is not None:
            event["error"] = error
            event["error_type"] = error_type
        return event


# --------------------------------------------------------------------------- #
# Summary statistics
# --------------------------------------------------------------------------- #


def percentile(sorted_samples: list[float], pct: float) -> float:
    if not sorted_samples:
        return 0.0
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_samples)))
    return sorted_samples[min(rank, len(sorted_samples)) - 1]


def summarize_phase(events: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [e for e in events if e.get("event") == "request" and not e.get("warmup")]
    latencies = sorted(float(e["latency_ms"]) for e in measured if e.get("ok"))
    failed = sum(1 for e in measured if not e.get("ok"))
    request_bytes = int(sum(int(e["request_bytes"]) for e in measured))
    elapsed_values = [float(e.get("elapsed_ms", 0.0)) for e in measured if e.get("elapsed_ms") is not None]
    elapsed_s = (max(elapsed_values) - min(elapsed_values)) / 1000.0 if elapsed_values else 0.0
    requests = len(measured)
    summary: dict[str, Any] = {
        "requests": requests,
        "failed_requests": failed,
        "request_bytes": request_bytes,
        "elapsed_s": round(elapsed_s, 6),
        "requests_per_s": round(requests / elapsed_s, 6) if elapsed_s > 0 else 0.0,
        "payload_bytes_per_s": round(request_bytes / elapsed_s, 6) if elapsed_s > 0 else 0.0,
        "min_ms": round(latencies[0], 6) if latencies else 0.0,
        "median_ms": round(percentile(latencies, 50), 6),
        "p90_ms": round(percentile(latencies, 90), 6),
        "p95_ms": round(percentile(latencies, 95), 6),
        "p99_ms": round(percentile(latencies, 99), 6),
        "max_ms": round(latencies[-1], 6) if latencies else 0.0,
        "warmup_requests": sum(1 for e in events if e.get("event") == "request" and e.get("warmup")),
    }
    return summary


# --------------------------------------------------------------------------- #
# Phase runner
# --------------------------------------------------------------------------- #


def _request_event(
    *,
    ctx: BenchmarkContext,
    logical: LogicalWrite,
    payload: bytes,
    probe: str,
    round_index: int,
    phase_index: int,
    unit_index: int,
    measured_iter: int,
    warmup: bool,
    extra: dict[str, Any],
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ts": ctx.now_fn(),
        "run_id": ctx.run_id,
        "event": "request",
        "round": round_index,
        "phase_index": phase_index,
        "phase_probe": probe,
        "traffic": ctx.traffic.name,
        "unit": ctx.traffic.unit,
        "unit_index": unit_index,
        "iter": measured_iter,
        "logical_write_id": logical.logical_write_id,
        "space": logical.space,
        "write_kind": logical.write_kind,
        "type": probe,
        "operation": "writemem",
        "address": format_address(logical.address),
        "request_bytes": logical.bytes_count,
        "warmup": warmup,
    }
    if logical.label is not None:
        base["label"] = logical.label
    base.update(extra)
    # remove protocol-supplied latency/ok placement; merge extras after
    return base


def run_phase(
    *,
    ctx: BenchmarkContext,
    probe: str,
    round_index: int,
    phase_index: int,
    start_perf: float,
    deadline_perf: float | None,
    max_units: int | None,
    warmup_units: int,
    firmware_source: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    profile = ctx.traffic

    if probe == "rest":
        executor: Any = RestExecutor(ctx)
    else:
        executor = DmaExecutor(ctx)

    executor_open_ok = True
    try:
        executor.open()
    except Exception as exc:
        executor_open_ok = False
        ctx.emit(
            {
                "ts": ctx.now_fn(),
                "event": "warning",
                "run_id": ctx.run_id,
                "code": f"{probe.upper()}_OPEN_FAILED",
                "message": f"{probe} phase open failed: {exc}",
                "phase_probe": probe,
                "error_type": type(exc).__name__,
            }
        )

    # --delay-ms (CLI) overrides the profile's inter_write_delay_ms; both pace the
    # gap between individual logical writes, which is what "--delay-ms" documents
    # and what the JSON inter_write_delay_ms field names. Duration/rate budgets are
    # still enforced separately via deadline_perf.
    per_write_delay_ms = ctx.delay_ms or profile.inter_write_delay_ms

    unit_index = 0
    measured_iter = 0
    try:
        if executor_open_ok:
            # warmup
            for _ in range(warmup_units):
                if deadline_perf is not None and ctx.perf_fn() >= deadline_perf:
                    break
                for logical in expand_unit(profile, unit_index):
                    _emit_logical_write(
                        ctx, executor, probe, round_index, phase_index, unit_index,
                        measured_iter, warmup=True, logical=logical, events=events,
                    )
                    if per_write_delay_ms:
                        ctx.sleep_fn(per_write_delay_ms / 1000.0)
                unit_index += 1
            # measured
            while True:
                if deadline_perf is not None and ctx.perf_fn() >= deadline_perf:
                    break
                if max_units is not None and measured_iter >= max_units:
                    break
                for logical in expand_unit(profile, unit_index):
                    if deadline_perf is not None and ctx.perf_fn() >= deadline_perf:
                        break
                    _emit_logical_write(
                        ctx, executor, probe, round_index, phase_index, unit_index,
                        measured_iter, warmup=False, logical=logical, events=events,
                    )
                    if per_write_delay_ms:
                        ctx.sleep_fn(per_write_delay_ms / 1000.0)
                if deadline_perf is not None and ctx.perf_fn() >= deadline_perf:
                    break
                measured_iter += 1
                unit_index += 1
    finally:
        try:
            executor.close()
        except Exception:
            pass

    # stamp elapsed_ms on each event relative to phase start for summary window
    for event in events:
        if "perf_ts" in event:
            event["elapsed_ms"] = (event.pop("perf_ts") - start_perf) * 1000.0
    return events


def _emit_logical_write(
    ctx: BenchmarkContext,
    executor: Any,
    probe: str,
    round_index: int,
    phase_index: int,
    unit_index: int,
    measured_iter: int,
    warmup: bool,
    logical: LogicalWrite,
    events: list[dict[str, Any]],
) -> None:
    profile = ctx.traffic
    if probe == "rest" and logical.rest_policy == REST_POLICY_SKIP:
        skip_event = {
            "ts": ctx.now_fn(),
            "run_id": ctx.run_id,
            "event": "skip",
            "round": round_index,
            "phase_index": phase_index,
            "phase_probe": probe,
            "traffic": profile.name,
            "unit": profile.unit,
            "unit_index": unit_index,
            "iter": measured_iter,
            "logical_write_id": logical.logical_write_id,
            "space": logical.space,
            "address": format_address(logical.address),
            "request_bytes": logical.bytes_count,
            "warmup": warmup,
            "reason": "rest_unsupported_space",
        }
        if logical.label is not None:
            skip_event["label"] = logical.label
        ctx.emit(skip_event)
        return

    payload = generate_payload(
        traffic_name=profile.name,
        unit_index=unit_index,
        emitted_write_index=logical.emitted_write_index,
        space=logical.space,
        address=logical.address,
        byte_count=logical.bytes_count,
        dirty_offset=logical.dirty_offset,
        seed=profile.seed,
        pattern=profile.payload_pattern,
    )
    try:
        extra = executor.execute(logical, payload)
    except Exception as exc:
        extra = {
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "latency_ms": 0.0,
        }
    event = _request_event(
        ctx=ctx,
        logical=logical,
        payload=payload,
        probe=probe,
        round_index=round_index,
        phase_index=phase_index,
        unit_index=unit_index,
        measured_iter=measured_iter,
        warmup=warmup,
        extra=extra,
    )
    event["perf_ts"] = ctx.perf_fn()
    ctx.emit(event)
    events.append(event)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _workload_summary(profile: TrafficProfile) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for template in profile.writes:
        if not template.enabled:
            continue
        max_data = REUWRITE_MAX_DATA if template.space == SPACE_REU else DMAWRITE_MAX_DATA
        summary.append(
            {
                "template_index": template.index,
                "label": template.label,
                "space": template.space,
                "address": format_address(template.address),
                "bytes": template.bytes_count,
                "write_kind": template.write_kind,
                "repeat": template.repeat,
                "rest_support": template.rest_policy != REST_POLICY_SKIP,
                "dma_support": True,
                "within_single_frame": template.bytes_count <= max_data,
            }
        )
    return summary


def build_start_event(
    *,
    ctx: BenchmarkContext,
    probes: tuple[str, ...],
    schedule: str,
    runners: int,
    rounds: int,
    duration_s: float | None,
    iterations: int | None,
    traffic_config_path: str,
    firmware_source: dict[str, Any],
    primary: bool,
) -> dict[str, Any]:
    profile = ctx.traffic
    event: dict[str, Any] = {
        "run": "start",
        "tool": TOOL_NAME,
        "run_id": ctx.run_id,
        "traffic": profile.name,
        "traffic_config": traffic_config_path,
        "traffic_description": profile.description,
        "traffic_unit": profile.unit,
        "traffic_metadata": dict(profile.metadata),
        "probes": list(probes),
        "schedule": schedule,
        "primary": primary,
        "runners": runners,
        "rounds": rounds,
        "host": ctx.host,
        "http_port": ctx.http_port,
        "dma_port": ctx.dma_port,
        "rest_method": ctx.rest_method,
        "http_connection": ctx.http_connection,
        "dma_ack_mode": ctx.dma_ack_mode,
        "dma_barrier": ctx.dma_barrier,
        "dma_connection": ctx.dma_connection,
        "seed": ctx.seed,
        "rate_hz": profile.rate_hz,
        "pacing": profile.pacing,
        "payload_pattern": profile.payload_pattern,
        "firmware_source": firmware_source,
        "workload": _workload_summary(profile),
    }
    if duration_s is not None:
        event["duration_s"] = duration_s
    if iterations is not None:
        event["iterations"] = iterations
    return event


def run_benchmark(
    *,
    ctx: BenchmarkContext,
    probes: tuple[str, ...],
    schedule: str,
    runners: int,
    rounds: int,
    duration_s: float | None,
    iterations: int | None,
    warmup_iterations: int,
    traffic_config_path: str,
    firmware_source: dict[str, Any] | None,
) -> int:
    profile = ctx.traffic
    fw_source = firmware_source if firmware_source is not None else firmware_source_info()
    primary = schedule == SCHEDULE_SEQUENTIAL
    if runners > 1:
        # The value is accepted and recorded in the start event for provenance,
        # but runner fan-out is not implemented yet. Warn loudly so a multi-runner
        # invocation is never silently measured as a single runner.
        ctx.diag(
            f"warning: --runners {runners} requested but runner fan-out is not yet "
            "implemented; measuring a single runner."
        )
    ctx.emit(
        build_start_event(
            ctx=ctx,
            probes=probes,
            schedule=schedule,
            runners=runners,
            rounds=rounds,
            duration_s=duration_s,
            iterations=iterations,
            traffic_config_path=traffic_config_path,
            firmware_source=fw_source,
            primary=primary,
        )
    )

    max_units = None if duration_s is not None else (iterations or profile.iterations or 1)
    if max_units is None and duration_s is None:
        max_units = 1

    start_perf = ctx.perf_fn()
    # Sequential schedule: each phase gets its own --duration-s budget so REST and DMA are
    # compared on equal wallclock. Concurrent schedule shares a single overall deadline.
    sequential = schedule == SCHEDULE_SEQUENTIAL
    phase_budget_s = duration_s if (sequential and duration_s is not None) else None
    overall_deadline = None if (sequential and duration_s is not None) else (
        None if duration_s is None else start_perf + duration_s
    )

    all_events: list[dict[str, Any]] = []
    overall_ok = True

    if schedule == SCHEDULE_CONCURRENT and len(probes) > 1:
        import threading

        per_probe_events: dict[str, list[dict[str, Any]]] = {probe: [] for probe in probes}
        emit_lock = threading.Lock()
        original_emit = ctx.emit

        def locked_emit(event: dict[str, Any]) -> None:
            with emit_lock:
                original_emit(event)

        ctx.emit = locked_emit  # type: ignore[assignment]
        threads: list[threading.Thread] = []
        phase_index = 0
        for probe in probes:
            for round_index in range(1, rounds + 1):
                thread = threading.Thread(
                    target=_run_single_phase_collect,
                    args=(ctx, probe, round_index, phase_index, start_perf, overall_deadline,
                          max_units, warmup_iterations, fw_source, per_probe_events, probe),
                    daemon=True,
                )
                phase_index += 1
                threads.append(thread)
                thread.start()
        for thread in threads:
            thread.join()
        ctx.emit = original_emit  # type: ignore[assignment]
        for probe in probes:
            all_events.extend(per_probe_events[probe])
    else:
        phase_index = 0
        for round_index in range(1, rounds + 1):
            for probe in probes:
                # Each sequential phase gets its own start/clock so the per-phase budget is honored.
                phase_start = ctx.perf_fn()
                phase_deadline = (
                    phase_start + phase_budget_s if phase_budget_s is not None else overall_deadline
                )
                phase_events = run_phase(
                    ctx=ctx,
                    probe=probe,
                    round_index=round_index,
                    phase_index=phase_index,
                    start_perf=phase_start,
                    deadline_perf=phase_deadline,
                    max_units=max_units,
                    warmup_units=warmup_iterations,
                    firmware_source=fw_source,
                )
                all_events.extend(phase_events)
                phase_index += 1
                if any(e.get("event") == "request" and not e.get("ok") for e in phase_events):
                    overall_ok = False

    summary: dict[str, Any] = {}
    for probe in probes:
        probe_events = [e for e in all_events if e.get("phase_probe") == probe and e.get("event") == "request"]
        summary[probe] = summarize_phase(probe_events)
    if not all(e["failed_requests"] == 0 for e in summary.values()):
        overall_ok = False
    if not any(summary[probe]["requests"] > 0 for probe in probes):
        overall_ok = False

    end_event = {
        "run": "end",
        "event": "summary",
        "run_id": ctx.run_id,
        "ok": overall_ok,
        "summary": summary,
    }
    ctx.emit(end_event)
    return 0 if overall_ok else 1


def _run_single_phase_collect(
    ctx: BenchmarkContext,
    probe: str,
    round_index: int,
    phase_index: int,
    start_perf: float,
    deadline_perf: float | None,
    max_units: int | None,
    warmup_units: int,
    firmware_source: dict[str, Any],
    sink: dict[str, list[dict[str, Any]]],
    probe_key: str,
) -> None:
    events = run_phase(
        ctx=ctx,
        probe=probe,
        round_index=round_index,
        phase_index=phase_index,
        start_perf=start_perf,
        deadline_perf=deadline_perf,
        max_units=max_units,
        warmup_units=warmup_units,
        firmware_source=firmware_source,
    )
    sink[probe_key].extend(events)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_probes(value: str) -> tuple[str, ...]:
    raw = value.strip()
    if not raw:
        raise argparse.ArgumentTypeError("--probes must be a non-empty comma-separated list")
    probes = tuple(part.strip() for part in raw.split(","))
    if any(not probe for probe in probes):
        raise argparse.ArgumentTypeError("--probes must not contain empty entries")
    invalid = [probe for probe in probes if probe not in PROBE_CHOICES]
    if invalid:
        raise argparse.ArgumentTypeError(f"unknown probe name(s): {', '.join(sorted(set(invalid)))}")
    if len(set(probes)) != len(probes):
        raise argparse.ArgumentTypeError("--probes must not contain duplicates")
    return probes


def parse_positive_int(name: str) -> Callable[[str], int]:
    def parser(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"{name} must be an integer >= 1") from error
        if parsed < 1:
            raise argparse.ArgumentTypeError(f"{name} must be >= 1")
        return parsed

    return parser


def epilog_text() -> str:
    return (
        "Probe precedence: --probes is an ordered list using rest,dma. In the default sequential\n"
        "schedule the benchmark runs one protocol phase at a time (REST then DMA) so the two\n"
        "transports face the identical JSON workload. Concurrent schedule is exploratory and is\n"
        "marked non-primary because it changes offered load.\n\n"
        "stdout is JSONL only; all human diagnostics go to stderr. Workload shape comes from the\n"
        "JSON traffic config selected by --traffic, not from CLI workload flags.\n\n"
        "Examples:\n"
        "  ./scripts/u64_dma_rest_benchmark.py -H u64\n"
        "  ./scripts/u64_dma_rest_benchmark.py -H u64 --traffic c64cast\n"
        "  ./scripts/u64_dma_rest_benchmark.py -H u64 --traffic c64cast --duration-s 60\n"
        "  ./scripts/u64_dma_rest_benchmark.py -H u64 --traffic-config config/u64_dma_rest_benchmark_traffic.json --traffic single-write\n"
        "  ./scripts/u64_dma_rest_benchmark.py -H u64 --probes rest --rest-method post\n"
        "  ./scripts/u64_dma_rest_benchmark.py -H u64 --probes rest --rest-method put --traffic single-write\n"
        "  ./scripts/u64_dma_rest_benchmark.py -H u64 --probes dma --dma-ack-mode barrier\n"
        "  ./scripts/u64_dma_rest_benchmark.py -H u64 --schedule sequential --runners 1 --duration-s 60\n"
    )


SAFETY_TEXT = (
    "Safety: this tool writes C64 RAM, color RAM ($D800-$DBE7), and possibly I/O / VIC / CIA / REC\n"
    "registers or REU space per the selected JSON traffic. Default traffic writes only RAM/screen/\n"
    "color/bitmap regions and does not touch VIC/CIA/vectors/REC unless a profile is explicitly\n"
    "documented as stateful. The tool does not restore modified memory."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic U64 memory-write benchmark. Default: JSON traffic 'c64cast' with sequential "
            "REST and DMA phases. rest targets /v1/machine:writemem; dma targets the DMA-capable TCP "
            "port 64 command endpoint."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_text(),
    )
    parser.add_argument("-H", "--host", default=DEFAULT_HOST, help="Target host or IP")
    parser.add_argument("-d", "--delay-ms", type=int, default=DEFAULT_DELAY_MS, help="Delay between writes in milliseconds")
    parser.add_argument("-n", "--log-every", type=int, default=DEFAULT_LOG_EVERY, help="Log every Nth request (JSONL always emits all requests)")
    parser.add_argument("-P", "--ftp-pass", default=None, help="Legacy alias for the shared device network password.")
    parser.add_argument("--network-password", default=None, help="Shared device network password used for REST X-Password and DMA 0xFF1F authentication.")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, help="REST HTTP port")
    parser.add_argument("--dma-port", type=int, default=DEFAULT_DMA_PORT, help="TCP/64 socket-DMA port")
    parser.add_argument("-v", "--verbose", action="store_true", help="Increase stderr diagnostics.")
    parser.add_argument("--traffic", default=DEFAULT_TRAFFIC_NAME, help="Named JSON traffic profile to run (default: c64cast).")
    parser.add_argument("--traffic-config", default=DEFAULT_TRAFFIC_CONFIG, help="Path to the JSON traffic config file.")
    parser.add_argument("--probes", type=parse_probes, default=",".join(DEFAULT_PROBES), help="Ordered non-empty comma-separated protocol list using rest,dma (default: rest,dma).")
    parser.add_argument("--transport", dest="probes", help=argparse.SUPPRESS, type=parse_probes)
    parser.add_argument("--schedule", choices=(SCHEDULE_SEQUENTIAL, SCHEDULE_CONCURRENT), default=SCHEDULE_SEQUENTIAL, help="sequential = one phase at a time (primary comparison); concurrent = exploratory, marked non-primary.")
    parser.add_argument("--runners", type=parse_positive_int("--runners"), default=1, help="Logical runner count >= 1.")
    parser.add_argument("--duration-s", type=parse_positive_int("--duration-s"), default=None, help="Measured workload duration in seconds. Overrides JSON iteration count.")
    parser.add_argument("--iterations", type=parse_positive_int("--iterations"), default=None, help="Measured unit count when --duration-s is omitted. Defaults to JSON iterations.")
    parser.add_argument("--warmup-iterations", type=int, default=0, help="Warmup units run before measured units (excluded from measured summary).")
    parser.add_argument("--rest-method", choices=(REST_METHOD_AUTO, REST_METHOD_POST, REST_METHOD_PUT), default=REST_METHOD_AUTO, help="REST writemem method. auto uses PUT for <=128 bytes else POST.")
    parser.add_argument("--http-connection", choices=(HTTP_CONN_CLOSE, HTTP_CONN_PERSISTENT), default=HTTP_CONN_CLOSE, help="HTTP connection mode (default: close).")
    parser.add_argument("--dma-ack-mode", choices=(DMA_ACK_BARRIER, DMA_ACK_SEND_ONLY), default=DMA_ACK_BARRIER, help="DMA timing mode. barrier (default) measures write-frame to same-socket barrier response.")
    parser.add_argument("--dma-barrier", choices=(DMA_BARRIER_DEBUGREG, DMA_BARRIER_IDENTIFY), default=DMA_BARRIER_DEBUGREG, help="DMA barrier command: debugreg (zero-length 0xFF76) or identify (0xFF0E).")
    parser.add_argument("--dma-connection", choices=(DMA_CONN_PERSISTENT, DMA_CONN_PER_REQUEST), default=DMA_CONN_PERSISTENT, help="DMA connection mode (default: persistent).")
    parser.add_argument("--payload-pattern", choices=PAYLOAD_CHOICES, default=None, help="Override the JSON payload pattern.")
    parser.add_argument("--seed", type=int, default=None, help="Override the JSON payload seed.")
    parser.add_argument("--report", default=None, help="Optional path to also write the final summary event.")
    parser.add_argument("--rounds", type=parse_positive_int("--rounds"), default=1, help=argparse.SUPPRESS)
    parser.epilog = epilog_text() + "\n" + SAFETY_TEXT
    return parser


def _resolve_network_password(args: argparse.Namespace) -> str:
    # Precedence: explicit --network-password (including an empty string to clear
    # an env-derived secret) > explicit --ftp-pass alias > env NETWORK_PASSWORD >
    # env FTP_PASS > "". argparse leaves un-passed flags as None, which lets us
    # distinguish "not passed" from "passed as empty".
    if args.network_password is not None:
        return args.network_password
    if args.ftp_pass is not None:
        return args.ftp_pass
    return NETWORK_PASSWORD or FTP_PASS


def _resolve_traffic_config_path(args: argparse.Namespace) -> Path:
    path = Path(args.traffic_config)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def build_context(args: argparse.Namespace, profile: TrafficProfile, emit: Callable[[dict[str, Any]], None] | None = None) -> BenchmarkContext:
    seed = args.seed if args.seed is not None else profile.seed
    payload_pattern = args.payload_pattern or profile.payload_pattern
    # apply CLI overrides onto a new profile copy
    overridden = TrafficProfile(
        name=profile.name,
        description=profile.description,
        unit=profile.unit,
        rate_hz=profile.rate_hz,
        iterations=profile.iterations,
        duration_s=profile.duration_s,
        pacing=profile.pacing,
        inter_write_delay_ms=profile.inter_write_delay_ms,
        payload_pattern=payload_pattern,
        seed=seed,
        writes=profile.writes,
        metadata=profile.metadata,
    )
    def diag_default(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    return BenchmarkContext(
        host=args.host,
        http_port=args.http_port,
        dma_port=args.dma_port,
        network_password=_resolve_network_password(args),
        rest_method=args.rest_method,
        http_connection=args.http_connection,
        dma_ack_mode=args.dma_ack_mode,
        dma_barrier=args.dma_barrier,
        dma_connection=args.dma_connection,
        run_id=uuid.uuid4().hex,
        traffic=overridden,
        seed=seed,
        rounds=args.rounds,
        verbose=args.verbose,
        delay_ms=args.delay_ms,
        log_every=args.log_every,
        emit=emit or (lambda event: print(json.dumps(event), flush=True)),
        diag=diag_default,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config_path = _resolve_traffic_config_path(args)
    try:
        config = load_traffic_config(config_path)
        profile = config.select(args.traffic)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))  # raises SystemExit(2); never returns

    def emit(event: dict[str, Any]) -> None:
        print(json.dumps(event), flush=True)

    report_path = Path(args.report) if args.report else None

    if report_path is not None:
        captured: list[dict[str, Any]] = []

        def emitting(event: dict[str, Any]) -> None:
            captured.append(event)
            print(json.dumps(event), flush=True)

        emit = emitting

    ctx = build_context(args, profile, emit=emit)
    duration_s = args.duration_s if args.duration_s is not None else None
    iterations = args.iterations if args.iterations is not None else profile.iterations

    try:
        result = run_benchmark(
            ctx=ctx,
            probes=args.probes,
            schedule=args.schedule,
            runners=args.runners,
            rounds=args.rounds,
            duration_s=duration_s,
            iterations=iterations,
            warmup_iterations=max(0, args.warmup_iterations),
            traffic_config_path=str(config_path),
            firmware_source=None,
        )
    except BrokenPipeError:
        raise SystemExit(0)

    if report_path is not None and captured:
        final = captured[-1]
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8") as handle:
            json.dump(final, handle, indent=2)
            handle.write("\n")

    return result


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(0)
