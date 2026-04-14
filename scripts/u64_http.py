from __future__ import annotations

import http.client
import json
import re
import time
import urllib.parse

from u64_connection_runtime import (
    ProbeExecutionContext,
    ProbeOutcome,
    ProbeSurface,
    RuntimeSettings,
    run_surface_operation,
    select_operation_index,
    surface_detail,
)


HTTP_AUDIO_MIXER_CATEGORY_PATH = "/v1/configs/Audio%20Mixer"
HTTP_VOLUME_ULTISID_1_PATH = f"{HTTP_AUDIO_MIXER_CATEGORY_PATH}/Vol%20UltiSid%201"
AUDIO_MIXER_WRITE_ITEM = "Vol UltiSid 1"
AUDIO_MIXER_WRITE_TARGET_VALUES = ("0 dB", "+1 dB")


def request_path(http_path: str) -> str:
    return f"/{http_path}"


def parse_response(payload: bytes) -> tuple[int, bytes]:
    header_end = payload.find(b"\r\n\r\n")
    if header_end < 0:
        raise RuntimeError("invalid HTTP response")
    header_block = payload[:header_end].decode("iso-8859-1", "replace")
    status_line = header_block.split("\r\n", 1)[0]
    parts = status_line.split()
    if len(parts) < 2 or not parts[1].isdigit():
        raise RuntimeError("invalid HTTP status")
    return int(parts[1]), payload[header_end + 4 :]


def request_bytes(settings: RuntimeSettings, method: str, path: str) -> tuple[int, bytes, dict[str, str]]:
    conn = http.client.HTTPConnection(settings.host, settings.http_port, timeout=3)
    try:
        conn.request(method, path, headers={"Connection": "close"})
        response = conn.getresponse()
        body = response.read()
        headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, body, headers
    finally:
        conn.close()


def json_request(settings: RuntimeSettings, method: str, path: str) -> tuple[int, object, int]:
    status, body, _headers = request_bytes(settings, method, path)
    if not 200 <= status < 300:
        raise RuntimeError(f"expected HTTP 2xx, got {status}")
    if not body:
        raise RuntimeError("empty JSON body")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as error:
        raise RuntimeError(f"invalid JSON body: {error}") from error
    if payload in (None, "", [], {}):
        raise RuntimeError("empty JSON payload")
    return status, payload, len(body)


def safe_read(settings: RuntimeSettings, path: str) -> str:
    status, body, headers = request_bytes(settings, "GET", path)
    if path.startswith("/v1/files") and status == 404:
        return "skip=files_endpoint_unavailable"
    if not 200 <= status < 300:
        raise RuntimeError(f"expected HTTP 2xx, got {status}")
    if not body:
        raise RuntimeError("empty HTTP body")
    content_type = headers.get("content-type", "")
    if "json" in content_type.lower():
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as error:
            raise RuntimeError(f"invalid JSON body: {error}") from error
        if payload in (None, "", [], {}):
            raise RuntimeError("empty JSON payload")
        return f"http_status={status} body_bytes={len(body)} json_type={type(payload).__name__}"
    return f"http_status={status} body_bytes={len(body)}"


def extract_first_byte(payload: object) -> int | None:
    if isinstance(payload, dict):
        if "data" in payload:
            return extract_first_byte(payload["data"])
        if "value" in payload:
            return extract_first_byte(payload["value"])
        return None
    if isinstance(payload, int):
        return payload & 0xFF
    if isinstance(payload, list):
        if not payload:
            return None
        return extract_first_byte(payload[0])
    if isinstance(payload, str):
        raw = payload.strip()
        if not raw:
            return None
        tokens = [token for token in re.split(r"[\s,]+", raw) if token]
        if not tokens:
            return None
        token = tokens[0]
        if token.lower().startswith("0x"):
            token = token[2:]
        for base in (16, 10):
            try:
                return int(token, base) & 0xFF
            except ValueError:
                continue
    return None


def generic_read(settings: RuntimeSettings, path: str) -> str:
    return safe_read(settings, path)


def normalize_audio_mixer_value(value: str) -> str:
    return " ".join(value.split())


def resolve_audio_mixer_value(values: tuple[str, ...], target: str) -> str:
    normalized_target = normalize_audio_mixer_value(target)
    for value in values:
        if normalize_audio_mixer_value(value) == normalized_target:
            return value
    raise RuntimeError(f"unsupported target value: {target}")


def audio_mixer_item_state(settings: RuntimeSettings) -> tuple[str, tuple[str, ...], int]:
    _status, payload, body_bytes = json_request(settings, "GET", HTTP_VOLUME_ULTISID_1_PATH)
    category_payload = payload.get("Audio Mixer") if isinstance(payload, dict) else None
    if not isinstance(category_payload, dict):
        raise RuntimeError("missing Audio Mixer payload")
    item_payload = category_payload.get(AUDIO_MIXER_WRITE_ITEM)
    if not isinstance(item_payload, dict):
        raise RuntimeError("missing Audio Mixer write payload")
    current = item_payload.get("current")
    values = item_payload.get("values")
    if not isinstance(current, str) or not current.strip():
        raise RuntimeError("missing Audio Mixer write current value")
    if not isinstance(values, list) or not values:
        raise RuntimeError("missing Audio Mixer write values")
    normalized_values = tuple(str(value) for value in values if str(value).strip())
    if not normalized_values:
        raise RuntimeError("empty Audio Mixer write values")
    return current, normalized_values, body_bytes


def read_audio_mixer_item(settings: RuntimeSettings) -> str:
    current, values, body_bytes = audio_mixer_item_state(settings)
    return f"body_bytes={body_bytes} current={normalize_audio_mixer_value(current)} options={len(values)}"


def write_audio_mixer_item(settings: RuntimeSettings, target: str) -> str:
    current, values, _body_bytes = audio_mixer_item_state(settings)
    resolved_target = resolve_audio_mixer_value(values, target)
    if current != resolved_target:
        encoded_target = urllib.parse.quote(resolved_target, safe="")
        status, _body, _headers = request_bytes(settings, "PUT", f"{HTTP_VOLUME_ULTISID_1_PATH}?value={encoded_target}")
        if not 200 <= status < 300:
            raise RuntimeError(f"expected HTTP 2xx, got {status}")
    updated, _updated_values, _body_bytes = audio_mixer_item_state(settings)
    if normalize_audio_mixer_value(updated) != normalize_audio_mixer_value(resolved_target):
        raise RuntimeError(
            f"verification mismatch expected={normalize_audio_mixer_value(resolved_target)} got={normalize_audio_mixer_value(updated)}"
        )
    return f"from={normalize_audio_mixer_value(current)} to={normalize_audio_mixer_value(updated)}"


def memory_read(settings: RuntimeSettings, address: str, length: int) -> str:
    status, body, _headers = request_bytes(settings, "GET", f"/v1/machine:readmem?address={address}&length={length}")
    if not 200 <= status < 300:
        raise RuntimeError(f"expected HTTP 2xx, got {status}")
    if not body:
        raise RuntimeError("empty memory read body")
    expected_length = max(1, length)
    if len(body) < expected_length:
        raise RuntimeError(f"short memory read: expected at least {expected_length} bytes, got {len(body)}")
    return f"http_status={status} body_bytes={len(body)} byte=0x{body[0]:02X}"


def memory_write_verify(settings: RuntimeSettings, address: str, data_hex: str) -> str:
    write_status, _body, _headers = request_bytes(settings, "PUT", f"/v1/machine:writemem?address={address}&data={data_hex}")
    if not 200 <= write_status < 300:
        raise RuntimeError(f"expected HTTP 2xx, got {write_status}")
    read_status, read_body, _headers = request_bytes(settings, "GET", f"/v1/machine:readmem?address={address}&length=1")
    if not 200 <= read_status < 300:
        raise RuntimeError(f"expected HTTP 2xx, got {read_status}")
    if len(read_body) < 1:
        raise RuntimeError("empty write verification body")
    value = read_body[0]
    expected = int(data_hex, 16)
    if value != expected:
        raise RuntimeError(f"verification mismatch expected=0x{expected:02X} got=0x{value:02X}")
    return f"http_status={write_status} verified=0x{value:02X}"


def surface_operations(surface: ProbeSurface) -> tuple[tuple[str, callable], ...]:
    read_operations = (
        ("get_version", lambda settings: generic_read(settings, "/v1/version")),
        ("get_info", lambda settings: generic_read(settings, "/v1/info")),
        ("get_configs", lambda settings: generic_read(settings, "/v1/configs")),
        ("get_config_audio_mixer", lambda settings: generic_read(settings, HTTP_AUDIO_MIXER_CATEGORY_PATH)),
        ("get_vol_ultisid_1", lambda settings: read_audio_mixer_item(settings)),
        ("get_drives", lambda settings: generic_read(settings, "/v1/drives")),
        ("get_files_temp", lambda settings: generic_read(settings, "/v1/files?path=/Temp")),
        ("mem_read_zero_page", lambda settings: memory_read(settings, "0x0000", 16)),
        ("mem_read_screen_ram", lambda settings: memory_read(settings, "0x0400", 16)),
        ("mem_read_io_area", lambda settings: memory_read(settings, "0xD000", 16)),
        ("mem_read_debug_register", lambda settings: memory_read(settings, "0xD7FF", 1)),
    )
    if surface == ProbeSurface.SMOKE:
        return (("get_version_smoke", lambda settings: generic_read(settings, "/v1/version")),)
    if surface == ProbeSurface.READ:
        return read_operations
    return read_operations + (
        ("mem_write_screen_space", lambda settings: memory_write_verify(settings, "0x0400", "20")),
        ("mem_write_screen_exclam", lambda settings: memory_write_verify(settings, "0x0400", "21")),
        ("set_vol_ultisid_1_0_db", lambda settings: write_audio_mixer_item(settings, "0 dB")),
        ("set_vol_ultisid_1_plus_1_db", lambda settings: write_audio_mixer_item(settings, "+1 dB")),
    )


def run_probe(settings: RuntimeSettings, correctness, *, context: ProbeExecutionContext | None = None) -> ProbeOutcome:
    if context is not None:
        operations = surface_operations(context.surface)
        index = select_operation_index(context, len(operations))
        op_name, operation = operations[index]
        started_at = time.perf_counter_ns()
        try:
            detail = run_surface_operation("http", operation, settings)
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("OK", surface_detail(context.surface, op_name, detail), elapsed_ms)
        except Exception as error:
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("FAIL", surface_detail(context.surface, op_name, str(error)), elapsed_ms)

    del correctness
    conn = http.client.HTTPConnection(settings.host, settings.http_port, timeout=8)
    started_at = time.perf_counter_ns()
    try:
        conn.request("GET", request_path(settings.http_path), headers={"Connection": "close"})
        response = conn.getresponse()
        body = response.read()
        if not 200 <= response.status < 300:
            raise RuntimeError(f"expected HTTP 2xx, got {response.status}")
        if not body:
            raise RuntimeError("empty HTTP body")
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("OK", f"HTTP {response.status} body_bytes={len(body)}", elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("FAIL", f"http failed: {error}", elapsed_ms)
    finally:
        conn.close()
