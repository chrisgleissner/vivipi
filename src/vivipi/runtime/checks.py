from __future__ import annotations

import json
import gc
try:
    import importlib
except ImportError:  # pragma: no cover - MicroPython fallback
    importlib = None
import re
import select
import socket
import time
from urllib.parse import urlparse

try:
    TimeoutError
except NameError:  # pragma: no cover - MicroPython fallback
    class TimeoutError(OSError):
        pass

from vivipi.core.execution import HttpResponseResult, PingProbeResult, execute_check
from vivipi.core.logging import bound_text
from vivipi.core.models import CheckDefinition, CheckType, Status


PING_LATENCY_PATTERN = re.compile(r"time[=<]([0-9.]+)")
FTP_PASV_PATTERN = re.compile(r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)")
TELNET_FAILURE_MARKERS = (b"incorrect", b"failed", b"denied", b"invalid")
TELNET_LOGIN_MARKERS = (b"login:", b"username:", b"user:")
TELNET_PASSWORD_MARKERS = (b"password:",)
TELNET_PROMPT_MARKERS = (b">", b"#", b"$", b"%")
TELNET_IAC = 255
TELNET_DONT = 254
TELNET_DO = 253
TELNET_WONT = 252
TELNET_WILL = 251
TELNET_IDLE_TIMEOUT_S = 0.12
TELNET_POST_DATA_IDLE_TIMEOUT_S = 0.1
TELNET_STABLE_OPEN_THRESHOLD_MS = 500
TELNET_EARLY_CLOSE_THRESHOLD_MS = 100
DEVICE_SOCKET_RECV_CHUNK_SIZE = 512
TELNET_RECV_CHUNK_SIZE = 512
TELNET_SB = 250
TELNET_SE = 240
TELNET_FAILURE_SCAN_TAIL_BYTES = max(len(marker) for marker in TELNET_FAILURE_MARKERS)
POLLIN = getattr(select, "POLLIN", 0x0001)
POLLOUT = getattr(select, "POLLOUT", 0x0004)
POLLERR = getattr(select, "POLLERR", 0x0008)
POLLHUP = getattr(select, "POLLHUP", 0x0010)
SOCKET_CONNECT_IN_PROGRESS_ERRNOS = frozenset({11, 36, 114, 115, 10035})
SOCKET_ALREADY_CONNECTED_ERRNOS = frozenset({56, 106, 127})
SOCKET_WOULD_BLOCK_ERRNOS = frozenset({11, 35, 36, 10035})
SOCKET_TIMEOUT_ERRNOS = frozenset({110})

PROBE_MAX_SOCKET_OPS = 48
PROBE_IO_PACING_MS = 2
PROBE_OPERATION_LIMIT = 48
TELNET_MAX_RECV_CHUNKS = 8
SOCKET_WAIT_SLICE_MS = 1000


_PROBE_ACTIVITY_CALLBACK = None


def set_probe_activity_callback(callback) -> None:
    global _PROBE_ACTIVITY_CALLBACK
    _PROBE_ACTIVITY_CALLBACK = callback


def _emit_probe_activity() -> None:
    callback = _PROBE_ACTIVITY_CALLBACK
    if callback is None:
        return
    callback()


class _ProbeBudget:
    __slots__ = ("remaining", "pacing_ms")

    def __init__(self, max_ops: int = PROBE_MAX_SOCKET_OPS, pacing_ms: int = PROBE_IO_PACING_MS):
        self.remaining = int(max_ops)
        self.pacing_ms = int(pacing_ms)

    def charge(self, count: int = 1) -> None:
        if count < 1:
            return
        if self.remaining < count:
            raise TimeoutError("probe io budget exhausted")
        self.remaining -= count
        if self.pacing_ms > 0:
            _sleep_ms(self.pacing_ms)


def _bounded_operation(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    if len(text) <= PROBE_OPERATION_LIMIT:
        return text
    if PROBE_OPERATION_LIMIT == 1:
        return text[:1]
    return text[: PROBE_OPERATION_LIMIT - 1] + "…"


def _emit_socket_send(trace, *, stage: str, bytes_sent: int, operation=None, target=None) -> None:
    descriptor = _bounded_operation(operation)
    fields = {"stage": stage, "bytes_sent": bytes_sent}
    if descriptor is not None:
        fields["operation"] = descriptor
    if target is not None:
        fields["target"] = target
    _emit_probe_trace(trace, "socket-send", **fields)


def _emit_socket_recv(trace, *, stage: str, bytes_received: int, operation=None, target=None) -> None:
    descriptor = _bounded_operation(operation)
    fields = {"stage": stage, "bytes_received": bytes_received}
    if descriptor is not None:
        fields["operation"] = descriptor
    if target is not None:
        fields["target"] = target
    _emit_probe_trace(trace, "socket-recv", **fields)


def _charge_budget(budget, count: int = 1) -> None:
    if budget is None:
        return
    budget.charge(count)


def _fold(value: object) -> str:
    return str(value).strip().lower()


def _import_module(name: str):
    if importlib is not None:
        return importlib.import_module(name)
    return __import__(name, None, None, ("*",))


def _start_timer() -> tuple[float, bool]:
    if hasattr(time, "ticks_ms"):
        return float(time.ticks_ms()), True
    return time.perf_counter(), False


def _elapsed_ms(started_at: float, uses_ticks_ms: bool) -> float:
    if uses_ticks_ms:
        return float(time.ticks_diff(time.ticks_ms(), int(started_at)))
    return (time.perf_counter() - started_at) * 1000.0


def _sleep_ms(value_ms: int):
    if value_ms <= 0:
        return
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value_ms)
        return
    if hasattr(time, "sleep"):
        time.sleep(value_ms / 1000.0)


def _is_micropython_runtime() -> bool:
    return hasattr(time, "ticks_ms")


def _maybe_collect_gc(min_free_bytes: int = TELNET_RECV_CHUNK_SIZE * 4):
    if not _is_micropython_runtime() or not hasattr(gc, "collect"):
        return
    free_bytes = None
    if hasattr(gc, "mem_free"):
        try:
            free_bytes = int(gc.mem_free())
        except Exception:
            free_bytes = None
    if free_bytes is not None and free_bytes >= min_free_bytes:
        return
    gc.collect()


def _deadline_after_s(timeout_s: int | float):
    timeout_ms = max(1, int(float(timeout_s) * 1000.0))
    if hasattr(time, "ticks_add") and hasattr(time, "ticks_ms"):
        return ("ticks", time.ticks_add(time.ticks_ms(), timeout_ms))
    return ("perf", time.perf_counter() + (timeout_ms / 1000.0))


def _deadline_remaining_ms(deadline) -> int:
    kind, value = deadline
    if kind == "ticks":
        return max(0, int(time.ticks_diff(value, time.ticks_ms())))
    return max(0, int((float(value) - time.perf_counter()) * 1000.0))


def _deadline_remaining_s(deadline) -> float:
    remaining_ms = _deadline_remaining_ms(deadline)
    if remaining_ms <= 0:
        raise TimeoutError("timed out")
    return max(0.001, remaining_ms / 1000.0)


def _emit_probe_trace(trace, event: str, **fields):
    if trace is None:
        return
    trace(event, **fields)


def _check_type_name(definition: CheckDefinition) -> str:
    candidate = getattr(definition.check_type, "value", None)
    if isinstance(candidate, str) and candidate and candidate != "<property>":
        return candidate.strip().upper()
    name = getattr(definition.check_type, "name", None)
    if isinstance(name, str) and name:
        return name.strip().upper()
    return str(definition.check_type).strip().upper() or "UNKNOWN"


def _status_text(value: object) -> str:
    candidate = getattr(value, "value", None)
    if isinstance(candidate, str) and candidate and candidate != "<property>":
        return candidate
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    return str(value)


def _probe_end_status(definition: CheckDefinition, result) -> str:
    for observation in getattr(result, "observations", ()):
        if getattr(observation, "identifier", None) == definition.identifier:
            return _status_text(getattr(observation, "status", "?"))
    if definition.check_type == CheckType.SERVICE:
        return "OK"
    observations = getattr(result, "observations", ())
    if observations:
        return _status_text(getattr(observations[0], "status", "?"))
    return "?"


def _probe_end_detail(definition: CheckDefinition, result) -> str:
    for observation in getattr(result, "observations", ()):
        if getattr(observation, "identifier", None) == definition.identifier:
            return str(getattr(observation, "details", "") or "")
    if definition.check_type == CheckType.SERVICE:
        return ""
    observations = getattr(result, "observations", ())
    if observations:
        return str(getattr(observations[0], "details", "") or "")
    return ""


def _probe_end_latency_ms(definition: CheckDefinition, result):
    probe_latency_ms = getattr(result, "probe_latency_ms", None)
    if probe_latency_ms is not None:
        return probe_latency_ms
    for observation in getattr(result, "observations", ()):
        if getattr(observation, "identifier", None) == definition.identifier:
            return getattr(observation, "latency_ms", None)
    if definition.check_type == CheckType.SERVICE:
        return None
    observations = getattr(result, "observations", ())
    if observations:
        return getattr(observations[0], "latency_ms", None)
    return None


def _format_socket_address(address) -> str:
    try:
        host = address[0]
        port = address[1]
        return f"{host}:{port}"
    except Exception:
        return str(address)


def _normalize_error_text(error: BaseException) -> str:
    return " ".join(str(error).split()).strip() or type(error).__name__


def _error_errno(error: BaseException) -> int | None:
    errno = getattr(error, "errno", None)
    if isinstance(errno, int):
        return errno
    if getattr(error, "args", None):
        first = error.args[0]
        if isinstance(first, int):
            return first
    return None


def _error_text(error: BaseException) -> str:
    return _fold(_normalize_error_text(error))


def _is_connect_in_progress(error: BaseException) -> bool:
    errno = _error_errno(error)
    message = _error_text(error)
    return errno in SOCKET_CONNECT_IN_PROGRESS_ERRNOS or "in progress" in message or "would block" in message


def _is_already_connected(error: BaseException) -> bool:
    errno = _error_errno(error)
    message = _error_text(error)
    return errno in SOCKET_ALREADY_CONNECTED_ERRNOS or "already connected" in message or "is connected" in message


def _is_would_block(error: BaseException) -> bool:
    errno = _error_errno(error)
    message = _error_text(error)
    return errno in SOCKET_WOULD_BLOCK_ERRNOS or "would block" in message or "temporarily unavailable" in message


def _classify_network_error(error: BaseException) -> str:
    message = _fold(_normalize_error_text(error))
    errno = _error_errno(error)
    if isinstance(error, TimeoutError) or errno in SOCKET_TIMEOUT_ERRNOS or "timeout" in message or "timed out" in message or "etimedout" in message:
        return "timeout"
    if errno in {-2, -3} or "name or service not known" in message or "name resolution" in message:
        return "dns"
    if errno in {111, 61} or "refused" in message:
        return "refused"
    if errno in {101, 113} or "unreachable" in message:
        return "network"
    if "reset" in message or "broken pipe" in message:
        return "reset"
    return "io"


def _format_network_error(error: BaseException) -> str:
    category = _classify_network_error(error)
    detail = bound_text(_normalize_error_text(error), 40)
    if _fold(detail) == category:
        return category
    return f"{category}: {detail}"


def _runtime_check_type(value: object) -> CheckType:
    normalized = str(value).strip().upper()
    if normalized == "REST":
        normalized = "HTTP"
    return CheckType(normalized)


def _runtime_optional_auth(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _resolve_target_alias(target: object, host_aliases: object) -> str:
    raw_target = str(target).strip()
    if not raw_target:
        return raw_target
    if not isinstance(host_aliases, dict) or not host_aliases:
        return raw_target

    if "://" in raw_target:
        parsed = urlparse(raw_target)
        hostname = getattr(parsed, "hostname", None)
        if not hostname or hostname not in host_aliases:
            return raw_target
        alias = str(host_aliases[hostname]).strip()
        if not alias:
            return raw_target
        prefix = raw_target.split("://", 1)[0] + "://"
        suffix = raw_target.split(hostname, 1)[1]
        return prefix + alias + suffix

    alias = host_aliases.get(raw_target)
    if alias is None:
        return raw_target
    return str(alias).strip() or raw_target


def build_runtime_definitions(config: dict[str, object]) -> tuple[CheckDefinition, ...]:
    raw_checks = config.get("checks")
    if not isinstance(raw_checks, list):
        raise ValueError("runtime config must contain a checks list")

    wifi = config.get("wifi") if isinstance(config.get("wifi"), dict) else {}
    host_aliases = wifi.get("host_aliases") if isinstance(wifi, dict) else None

    definitions: list[CheckDefinition] = []
    for item in raw_checks:
        if not isinstance(item, dict):
            raise ValueError("runtime checks must be objects")
        definitions.append(
            CheckDefinition(
                identifier=str(item["id"]),
                name=str(item["name"]),
                check_type=_runtime_check_type(item["type"]),
                target=_resolve_target_alias(item["target"], host_aliases),
                interval_s=int(item.get("interval_s", 15)),
                timeout_s=int(item.get("timeout_s", 10)),
                method=str(item.get("method", "GET")).upper(),
                username=_runtime_optional_auth(item.get("username")),
                password=_runtime_optional_auth(item.get("password")),
                service_prefix=(
                    str(item["service_prefix"])
                    if isinstance(item.get("service_prefix"), str) and str(item["service_prefix"]).strip()
                    else None
                ),
            )
        )
    return tuple(definitions)


def load_runtime_checks(path, env=None) -> tuple[CheckDefinition, ...]:
    from vivipi.core.config import load_checks_config

    return load_checks_config(path, env=env)


def portable_ping_runner(target: str, timeout_s: int, trace=None) -> PingProbeResult:
    del trace

    def _single_ping() -> PingProbeResult:
        _emit_probe_activity()
        try:
            import uping  # type: ignore
        except ImportError:
            try:
                import subprocess
            except ImportError:
                return PingProbeResult(
                    ok=False,
                    latency_ms=None,
                    details="ICMP unsupported on device",
                )

            started_at, uses_ticks_ms = _start_timer()
            completed = subprocess.run(
                ["ping", "-c", "1", "-W", str(timeout_s), target],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_s + 1,
            )
            latency_match = PING_LATENCY_PATTERN.search(completed.stdout)
            latency_ms = float(latency_match.group(1)) if latency_match else None
            ok = completed.returncode == 0
            details = completed.stderr.strip() or "timeout"
            return PingProbeResult(
                ok=ok,
                latency_ms=latency_ms if latency_ms is not None else (_elapsed_ms(started_at, uses_ticks_ms) if ok else None),
                details="reachable" if ok else details,
            )

        started_at, uses_ticks_ms = _start_timer()
        response = uping.ping(target, count=1, timeout=timeout_s * 1000, quiet=True)
        packets_received = int(response[1]) if len(response) > 1 else 0
        latency_ms = float(response[-1]) if response else None
        return PingProbeResult(
            ok=packets_received > 0,
            latency_ms=latency_ms if latency_ms is not None else _elapsed_ms(started_at, uses_ticks_ms),
            details="reachable" if packets_received > 0 else "timeout",
        )

    return _single_ping()


def _probe_error_detail(error: BaseException) -> str:
    if isinstance(error, (OSError, TimeoutError)):
        return _format_network_error(error)
    return _normalize_error_text(error)


def portable_http_runner(
    method: str,
    target: str,
    timeout_s: int,
    username: str | None = None,
    password: str | None = None,
    trace=None,
) -> HttpResponseResult:
    del username, password
    if trace is not None or _is_micropython_runtime():
        return _portable_http_runner_socket(method, target, timeout_s, trace=trace)

    try:
        http_client = _import_module("http.client")
    except ImportError:
        return _portable_http_runner_socket(method, target, timeout_s, trace=trace)

    scheme, host, port, path = _parse_http_target(target)
    connection_class = http_client.HTTPSConnection if scheme == "https" else http_client.HTTPConnection
    connection = connection_class(host, port, timeout=timeout_s)
    started_at, uses_ticks_ms = _start_timer()
    try:
        connection.request(method, path, headers={"Connection": "close"})
        response = connection.getresponse()
        body_bytes = response.read()
        return HttpResponseResult(
            status_code=int(response.status),
            body=_decode_http_body(body_bytes),
            latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
            details=f"HTTP {response.status}",
        )
    except Exception as error:
        return HttpResponseResult(
            status_code=None,
            body=None,
            latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
            details=_probe_error_detail(error),
        )
    finally:
        connection.close()


def _parse_socket_target(target: str, default_port: int, expected_scheme: str | None = None) -> tuple[str, int]:
    raw_target = str(target).strip()
    if "://" in raw_target:
        parsed = urlparse(raw_target)
        if expected_scheme is not None and parsed.scheme and _fold(parsed.scheme) != expected_scheme:
            raise ValueError(f"expected {expected_scheme} target")
        if not parsed.hostname:
            raise ValueError("target must include a host")
        return parsed.hostname, parsed.port or default_port

    host, separator, port_text = raw_target.rpartition(":")
    if separator and host and port_text.isdigit():
        return host, int(port_text)
    if not raw_target:
        raise ValueError("target must include a host")
    return raw_target, default_port


def _set_socket_timeout(handle, timeout_s: float):
    if hasattr(handle, "settimeout"):
        handle.settimeout(timeout_s)


def _set_nonblocking_socket(handle, enabled: bool) -> bool:
    try:
        if hasattr(handle, "setblocking"):
            handle.setblocking(not enabled)
            return True
        if enabled and hasattr(handle, "settimeout"):
            handle.settimeout(0)
            return True
    except Exception:
        return False
    return False


def _socket_wait(handle, deadline, *, writable: bool, trace=None, stage: str):
    remaining_ms = _deadline_remaining_ms(deadline)
    if remaining_ms <= 0:
        _emit_probe_trace(trace, "socket-timeout", stage=stage, remain_ms=0)
        raise TimeoutError("timed out")

    if not hasattr(select, "poll"):
        _emit_probe_activity()
        _set_socket_timeout(handle, min(_deadline_remaining_s(deadline), float(SOCKET_WAIT_SLICE_MS) / 1000.0))
        return

    poller = select.poll()
    flags = POLLOUT if writable else POLLIN
    flags |= POLLERR | POLLHUP
    try:
        poller.register(handle, flags)
    except Exception:
        _emit_probe_activity()
        _set_socket_timeout(handle, min(_deadline_remaining_s(deadline), float(SOCKET_WAIT_SLICE_MS) / 1000.0))
        return

    while remaining_ms > 0:
        _emit_probe_activity()
        wait_ms = min(remaining_ms, SOCKET_WAIT_SLICE_MS)
        events = poller.poll(wait_ms)
        if events:
            return
        updated_remaining_ms = _deadline_remaining_ms(deadline)
        if updated_remaining_ms >= remaining_ms:
            remaining_ms -= wait_ms
            continue
        remaining_ms = updated_remaining_ms

    _emit_probe_trace(trace, "socket-timeout", stage=stage, remain_ms=0)
    raise TimeoutError("timed out")


def _connect_socket(handle, address, timeout_s: int, deadline, trace=None):
    if not _set_nonblocking_socket(handle, True):
        _set_socket_timeout(handle, _deadline_remaining_s(deadline))
        handle.connect(address)
        _set_socket_timeout(handle, timeout_s)
        return

    _emit_probe_trace(trace, "socket-open", stage="connect", target=f"{address[0]}:{address[1]}", timeout_s=timeout_s)
    try:
        handle.connect(address)
        _set_socket_timeout(handle, timeout_s)
        return
    except OSError as error:
        if not _is_connect_in_progress(error) and not _is_would_block(error):
            raise

    while True:
        _socket_wait(handle, deadline, writable=True, trace=trace, stage="connect")
        try:
            if hasattr(handle, "getsockopt") and hasattr(socket, "SOL_SOCKET") and hasattr(socket, "SO_ERROR"):
                sock_error = handle.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if isinstance(sock_error, int) and sock_error != 0:
                    raise OSError(sock_error, "connect failed")
            handle.connect(address)
            _set_socket_timeout(handle, timeout_s)
            return
        except OSError as error:
            if _is_already_connected(error):
                _set_socket_timeout(handle, timeout_s)
                return
            if _is_connect_in_progress(error) or _is_would_block(error):
                continue
            raise


def _open_socket_compat(host: str, port: int, timeout_s: int, deadline, trace=None):
    try:
        return _open_socket(host, port, timeout_s, deadline=deadline, trace=trace)
    except TypeError as error:
        if "deadline" not in str(error) and "trace" not in str(error):
            raise
        return _open_socket(host, port, timeout_s)


def _recv_telnet_chunk_compat(handle, size: int, deadline=None, trace=None, budget=None) -> bytes:
    try:
        return _recv_telnet_chunk(handle, size, deadline=deadline, trace=trace, budget=budget)
    except TypeError as error:
        if "deadline" not in str(error) and "trace" not in str(error) and "budget" not in str(error):
            raise
        try:
            return _recv_telnet_chunk(handle, size)
        except OSError as nested_error:
            if _classify_network_error(nested_error) == "timeout":
                return b""
            raise


def _open_socket(host: str, port: int, timeout_s: int, *, deadline=None, trace=None):
    endpoint = f"{host}:{port}"
    _emit_probe_trace(trace, "dns-start", host=host, port=port, target=endpoint)
    try:
        addresses = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except Exception as error:
        _emit_probe_trace(trace, "dns-error", host=host, port=port, target=endpoint, detail=_format_network_error(error))
        raise
    _emit_probe_trace(
        trace,
        "dns-result",
        host=host,
        port=port,
        target=endpoint,
        addresses=tuple(_format_socket_address(address) for _, _, _, _, address in addresses),
    )
    last_error: OSError | None = None
    deadline = _deadline_after_s(timeout_s) if deadline is None else deadline
    for family, socktype, proto, _, address in addresses:
        handle = socket.socket(family, socktype, proto)
        try:
            _connect_socket(handle, address, timeout_s, deadline, trace=trace)
            _emit_probe_trace(trace, "socket-ready", stage="connect", target=f"{address[0]}:{address[1]}", remain_ms=_deadline_remaining_ms(deadline))
            return handle
        except (OSError, TimeoutError) as error:
            _emit_probe_trace(trace, "socket-error", stage="connect", target=f"{address[0]}:{address[1]}", detail=_format_network_error(error))
            last_error = error
            handle.close()
    raise last_error or OSError("unable to open socket")


def _close_socket(handle, trace=None, *, target=None):
    if handle is None:
        return
    try:
        shutdown = getattr(handle, "shutdown", None)
        shutdown_flag = getattr(socket, "SHUT_RDWR", None)
        if callable(shutdown) and shutdown_flag is not None:
            try:
                shutdown(shutdown_flag)
            except OSError:
                pass
        handle.close()
    except OSError:
        return
    _emit_probe_trace(trace, "socket-close", stage="close", target=target)


def _socket_sendall(handle, payload: bytes, deadline, trace=None, stage: str = "send", operation=None, target=None, budget=None):
    if not payload:
        return
    sender = getattr(handle, "send", None)
    if callable(sender):
        view = memoryview(payload)
        while len(view):
            _socket_wait(handle, deadline, writable=True, trace=trace, stage=stage)
            _charge_budget(budget)
            try:
                sent = sender(view)
            except OSError as error:
                if _is_would_block(error):
                    continue
                raise
            if sent is None:
                return
            if sent <= 0:
                raise OSError("send failed")
            _emit_socket_send(trace, stage=stage, bytes_sent=sent, operation=operation, target=target)
            view = view[sent:]
        return

    while True:
        _socket_wait(handle, deadline, writable=True, trace=trace, stage=stage)
        _charge_budget(budget)
        try:
            handle.sendall(payload)
        except OSError as error:
            if _is_would_block(error):
                continue
            raise
        _emit_socket_send(trace, stage=stage, bytes_sent=len(payload), operation=operation, target=target)
        return
def _socket_recv(handle, size: int, deadline, trace=None, stage: str = "recv", operation=None, target=None, budget=None) -> bytes:
    if _is_micropython_runtime():
        size = min(int(size), DEVICE_SOCKET_RECV_CHUNK_SIZE)
    while True:
        _socket_wait(handle, deadline, writable=False, trace=trace, stage=stage)
        _charge_budget(budget)
        try:
            chunk = handle.recv(size)
        except OSError as error:
            if _classify_network_error(error) == "timeout":
                _emit_probe_trace(trace, "socket-timeout", stage=stage, remain_ms=_deadline_remaining_ms(deadline))
                raise TimeoutError("timed out") from error
            if _is_would_block(error):
                continue
            raise
        _emit_socket_recv(trace, stage=stage, bytes_received=len(chunk), operation=operation, target=target)
        return chunk


def _ftp_read_response(handle, deadline=None, trace=None, operation=None, target=None, budget=None) -> tuple[int, str]:
    buffer = bytearray()
    while not buffer.endswith(b"\n"):
        if deadline is not None:
            chunk = _socket_recv(handle, 4096, deadline, trace=trace, stage="ftp-recv", operation=operation, target=target, budget=budget)
        else:
            chunk = handle.recv(4096)
        if not chunk:
            break
        buffer.extend(chunk)
    response = bytes(buffer).decode("utf-8", "replace").strip()
    if len(response) < 3 or not response[:3].isdigit():
        raise ValueError("invalid FTP response")
    return int(response[:3]), response


def _ftp_command(handle, value: str):
    handle.sendall((value + "\r\n").encode("utf-8"))


def _ftp_operation_descriptor(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "FTP"
    head, _, _ = text.partition(" ")
    if head.upper() == "PASS":
        return "PASS ***"
    return text


def _ftp_command_with_deadline(handle, value: str, deadline, trace=None, target=None, budget=None):
    operation = _ftp_operation_descriptor(value)
    _socket_sendall(
        handle,
        (value + "\r\n").encode("utf-8"),
        deadline,
        trace=trace,
        stage="ftp-send",
        operation=operation,
        target=target,
        budget=budget,
    )


def _ftp_parse_pasv(response: str) -> tuple[str, int]:
    match = FTP_PASV_PATTERN.search(response)
    if match is None:
        raise ValueError("invalid FTP passive response")
    host = ".".join(match.group(index) for index in range(1, 5))
    port = (int(match.group(5)) * 256) + int(match.group(6))
    return host, port


def _ftp_parse_pwd(response: str) -> str:
    if '"' in response:
        parts = response.split('"')
        if len(parts) >= 3 and parts[1]:
            return parts[1]
    if response.startswith("257 "):
        remainder = response[4:].strip()
        if remainder:
            return remainder
    raise ValueError("invalid FTP PWD response")


def _ftp_nlst_names(payload: bytes) -> list[str]:
    return [line.strip() for line in payload.decode("utf-8", "replace").splitlines() if line.strip()]


def _recv_all(handle) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = handle.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _recv_until_closed(handle, deadline, trace=None, stage: str = "recv-all", operation=None, target=None, budget=None) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = _socket_recv(handle, 4096, deadline, trace=trace, stage=stage, operation=operation, target=target, budget=budget)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def portable_ftp_runner(target: str, timeout_s: int, username: str | None = None, password: str | None = None, trace=None) -> PingProbeResult:
    host, port = _parse_socket_target(target, 21, expected_scheme="ftp")
    endpoint = f"{host}:{port}"
    if trace is None and not _is_micropython_runtime():
        try:
            import ftplib
        except ImportError:
            pass
        else:
            ftp = ftplib.FTP()
            started_at, uses_ticks_ms = _start_timer()
            try:
                greeting = ftp.connect(host, port, timeout=timeout_s)
                if not greeting.startswith("220"):
                    raise RuntimeError(f"expected FTP 220, got {greeting}")
                login = ftp.login(username or "anonymous", password or "")
                if not login.startswith("230"):
                    raise RuntimeError(f"expected FTP 230, got {login}")
                working_directory = ftp.pwd()
                if not working_directory:
                    raise RuntimeError("empty FTP PWD response")
                goodbye = ftp.quit()
                if not goodbye.startswith("221"):
                    raise RuntimeError(f"expected FTP 221, got {goodbye}")
                return PingProbeResult(
                    ok=True,
                    latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
                    details=f"pwd={working_directory}",
                )
            except Exception as error:
                return PingProbeResult(
                    ok=False,
                    latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
                    details=_probe_error_detail(error),
                )
            finally:
                try:
                    ftp.close()
                except OSError:
                    pass

    started_at, uses_ticks_ms = _start_timer()
    deadline = _deadline_after_s(timeout_s)
    budget = _ProbeBudget()

    control_socket = None
    try:
        _maybe_collect_gc()
        control_socket = _open_socket_compat(host, port, timeout_s, deadline, trace=trace)
        code, response = _ftp_read_response(control_socket, deadline=deadline, trace=trace, operation="server-greeting", target=endpoint, budget=budget)
        if code != 220:
            raise RuntimeError(f"expected FTP 220, got {response}")

        login_username = username or "anonymous"
        login_password = password or ""

        user_operation = _ftp_operation_descriptor(f"USER {login_username}")
        _ftp_command_with_deadline(control_socket, f"USER {login_username}", deadline, trace=trace, target=endpoint, budget=budget)
        code, response = _ftp_read_response(control_socket, deadline=deadline, trace=trace, operation=user_operation, target=endpoint, budget=budget)
        if code == 331:
            pass_operation = _ftp_operation_descriptor(f"PASS {login_password}")
            _ftp_command_with_deadline(control_socket, f"PASS {login_password}", deadline, trace=trace, target=endpoint, budget=budget)
            code, response = _ftp_read_response(control_socket, deadline=deadline, trace=trace, operation=pass_operation, target=endpoint, budget=budget)
        if code != 230:
            raise RuntimeError(f"expected FTP 230, got {response}")

        pwd_operation = _ftp_operation_descriptor("PWD")
        _ftp_command_with_deadline(control_socket, "PWD", deadline, trace=trace, target=endpoint, budget=budget)
        code, response = _ftp_read_response(control_socket, deadline=deadline, trace=trace, operation=pwd_operation, target=endpoint, budget=budget)
        if code != 257:
            raise RuntimeError(f"expected FTP 257, got {response}")
        working_directory = _ftp_parse_pwd(response)

        quit_operation = _ftp_operation_descriptor("QUIT")
        _ftp_command_with_deadline(control_socket, "QUIT", deadline, trace=trace, target=endpoint, budget=budget)
        code, response = _ftp_read_response(control_socket, deadline=deadline, trace=trace, operation=quit_operation, target=endpoint, budget=budget)
        if code != 221:
            raise RuntimeError(f"expected FTP 221, got {response}")
        return PingProbeResult(
            ok=True,
            latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
            details=f"pwd={working_directory}",
        )
    except Exception as error:
        return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details=_probe_error_detail(error))
    finally:
        _close_socket(control_socket, trace=trace, target=endpoint)
        _maybe_collect_gc()


def _telnet_strip_negotiation(handle, chunk: bytes, trace=None, target=None, budget=None) -> bytes:
    output = bytearray()
    index = 0
    while index < len(chunk):
        value = chunk[index]
        if value != TELNET_IAC:
            output.append(value)
            index += 1
            continue

        if index + 1 >= len(chunk):
            break
        command = chunk[index + 1]
        if command in {TELNET_DO, TELNET_DONT, TELNET_WILL, TELNET_WONT}:
            if index + 2 >= len(chunk):
                break
            option = chunk[index + 2]
            if command in {TELNET_DO, TELNET_DONT}:
                _telnet_send_best_effort(
                    handle,
                    bytes((TELNET_IAC, TELNET_WONT, option)),
                    trace=trace,
                    operation="telnet-iac",
                    target=target,
                    budget=budget,
                )
            else:
                _telnet_send_best_effort(
                    handle,
                    bytes((TELNET_IAC, TELNET_DONT, option)),
                    trace=trace,
                    operation="telnet-iac",
                    target=target,
                    budget=budget,
                )
            index += 3
            continue
        if command == TELNET_SB:
            index += 2
            while index + 1 < len(chunk) and not (chunk[index] == TELNET_IAC and chunk[index + 1] == TELNET_SE):
                index += 1
            index += 2
            continue
        index += 2
    return bytes(output)


def _contains_any(value: bytes, markers: tuple[bytes, ...]) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in markers)


def _has_alnum_ascii(value: str) -> bool:
    for character in value:
        codepoint = ord(character)
        if 48 <= codepoint <= 57 or 65 <= codepoint <= 90 or 97 <= codepoint <= 122:
            return True
    return False


def _read_until_markers(handle, markers: tuple[bytes, ...], deadline=None, trace=None, budget=None) -> bytes:
    buffer = bytearray()
    lowered_markers = tuple(marker.lower() for marker in markers)
    while True:
        chunk = _recv_telnet_chunk_compat(handle, 4096, deadline=deadline, trace=trace, budget=budget)
        if not chunk:
            break
        buffer.extend(_telnet_strip_negotiation(handle, chunk, trace=trace, budget=budget))
        lowered = bytes(buffer).lower()
        if any(marker in lowered for marker in lowered_markers):
            break
    return bytes(buffer)


def _recv_telnet_chunk(handle, size: int = 4096, deadline=None, trace=None, operation=None, target=None, budget=None) -> bytes:
    try:
        if deadline is not None:
            try:
                return _socket_recv(
                    handle,
                    size,
                    deadline,
                    trace=trace,
                    stage="telnet-recv",
                    operation=operation,
                    target=target,
                    budget=budget,
                )
            except TypeError as error:
                if "operation" not in str(error) and "target" not in str(error):
                    raise
                return _socket_recv(handle, size, deadline, trace=trace, stage="telnet-recv", budget=budget)
        _charge_budget(budget)
        return handle.recv(size)
    except TimeoutError as error:
        if _normalize_error_text(error) == "probe io budget exhausted":
            raise
        return b""
    except OSError as error:
        if _classify_network_error(error) == "timeout":
            return b""
        raise
def _recv_telnet_chunk_into(handle, buffer, trace=None, target=None, budget=None):
    receiver = getattr(handle, "recv_into", None)
    if callable(receiver):
        _charge_budget(budget)
        size = receiver(buffer)
        _emit_socket_recv(trace, stage="telnet-recv", bytes_received=size, operation="read-visible", target=target)
        return memoryview(buffer)[:size]
    _charge_budget(budget)
    chunk = handle.recv(len(buffer))
    _emit_socket_recv(trace, stage="telnet-recv", bytes_received=len(chunk), operation="read-visible", target=target)
    return chunk


def _telnet_send_best_effort(handle, payload: bytes, trace=None, operation=None, target=None, budget=None) -> bool:
    _charge_budget(budget)
    try:
        handle.sendall(payload)
    except OSError as error:
        if _classify_network_error(error) == "timeout":
            return False
        raise
    _emit_socket_send(trace, stage="telnet-send", bytes_sent=len(payload), operation=operation, target=target)
    return True


def _looks_like_telnet_output(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if any(marker.decode("utf-8") in lowered for marker in TELNET_FAILURE_MARKERS):
        return False
    return _has_alnum_ascii(stripped) or stripped[-1:] in ">#$%"


def _ascii_lower(byte: int) -> int:
    if 65 <= byte <= 90:
        return byte + 32
    return byte


def _is_ascii_whitespace(byte: int) -> bool:
    return byte in (9, 10, 11, 12, 13, 32)


def _skip_terminal_escape_sequence(chunk: bytes, index: int) -> int:
    next_index = index + 1
    if next_index >= len(chunk):
        return len(chunk)
    introducer = chunk[next_index]
    if introducer == 91:
        next_index += 1
        while next_index < len(chunk) and not 64 <= chunk[next_index] <= 126:
            next_index += 1
        return min(len(chunk), next_index + 1)
    if introducer in (40, 41, 42, 43):
        return min(len(chunk), next_index + 2)
    return min(len(chunk), next_index + 1)


def _telnet_collect_visible(handle, chunk: bytes, trace=None, target=None, budget=None) -> tuple[bytes, bool]:
    visible = bytearray()
    handshake_detected = False
    index = 0
    while index < len(chunk):
        byte = chunk[index]
        if byte == TELNET_IAC and index + 2 < len(chunk) and chunk[index + 1] in (TELNET_DO, TELNET_DONT, TELNET_WILL, TELNET_WONT):
            command = chunk[index + 1]
            option = chunk[index + 2]
            handshake_detected = True
            reply = bytes((TELNET_IAC, TELNET_WONT if command in (TELNET_DO, TELNET_DONT) else TELNET_DONT, option))
            _telnet_send_best_effort(handle, reply, trace=trace, operation="telnet-iac", target=target, budget=budget)
            index += 3
            continue
        if byte == 27:
            index = _skip_terminal_escape_sequence(chunk, index)
            continue
        visible.append(byte)
        index += 1
    return visible, handshake_detected


def _update_telnet_text_state(
    visible,
    *,
    visible_bytes: int,
    has_visible_text: bool,
    pending_trailing_whitespace: int,
    failure_window: bytearray,
) -> tuple[int, bool, int, bytearray, bool]:
    for byte in visible:
        if byte < 32 and not _is_ascii_whitespace(byte):
            continue
        if byte > 126:
            continue
        failure_window.append(_ascii_lower(byte))
        if len(failure_window) > TELNET_FAILURE_SCAN_TAIL_BYTES:
            failure_window = bytearray(failure_window[-TELNET_FAILURE_SCAN_TAIL_BYTES:])
        if any(marker in failure_window for marker in TELNET_FAILURE_MARKERS):
            return visible_bytes, has_visible_text, pending_trailing_whitespace, failure_window, True

        if not has_visible_text and _is_ascii_whitespace(byte):
            continue
        if not has_visible_text:
            has_visible_text = True

        if _is_ascii_whitespace(byte):
            pending_trailing_whitespace += 1
            continue

        visible_bytes += pending_trailing_whitespace + 1
        pending_trailing_whitespace = 0

    return visible_bytes, has_visible_text, pending_trailing_whitespace, failure_window, False


def _telnet_result_metadata(
    close_reason: str,
    session_duration_ms: float,
    handshake_detected: bool,
    response_received: bool,
) -> dict[str, object]:
    return {
        "close_reason": close_reason,
        "session_duration_ms": round(float(session_duration_ms), 1),
        "handshake_detected": bool(handshake_detected),
        "response_received": bool(response_received),
    }


def _telnet_failure_detail(session: dict[str, object]) -> str:
    close_reason = str(session["close_reason"])
    if close_reason in {"remote-close", "reset"} and float(session["session_duration_ms"]) < TELNET_EARLY_CLOSE_THRESHOLD_MS:
        return "closed immediately"
    if close_reason == "failure-marker":
        return "telnet failure marker present"
    if close_reason in {"remote-close", "reset", "idle-timeout", "stable-open"}:
        return "no telnet response"
    if close_reason in {"chunk-limit", "deadline"}:
        return "response not fully consumed" if bool(session.get("has_visible_text", False)) else "no telnet response"
    return close_reason


def _classify_telnet_session(session: dict[str, object]) -> tuple[Status, str]:
    session_duration_ms = float(session["session_duration_ms"])
    close_reason = str(session["close_reason"])
    handshake_detected = bool(session["handshake_detected"])
    has_visible_text = bool(session["has_visible_text"])
    early_close = close_reason in {"remote-close", "reset"} and session_duration_ms < TELNET_EARLY_CLOSE_THRESHOLD_MS

    if bool(session["failure_detected"]):
        return Status.FAIL, "telnet failure marker present"
    if has_visible_text:
        return Status.OK, "response-received"
    if early_close:
        return Status.FAIL, "closed immediately"
    if handshake_detected or close_reason in {"idle-timeout", "stable-open", "remote-close", "reset"}:
        return Status.FAIL, "no telnet response"
    return Status.FAIL, _telnet_failure_detail(session)


def _read_telnet_until_idle(
    handle,
    *,
    initial_timeout_s: float = TELNET_IDLE_TIMEOUT_S,
    quiet_timeout_s: float = TELNET_POST_DATA_IDLE_TIMEOUT_S,
    trace=None,
    deadline=None,
    target=None,
    budget=None,
    max_chunks: int = TELNET_MAX_RECV_CHUNKS,
) -> dict[str, object]:
    started_at, uses_ticks_ms = _start_timer()
    waited_timeout_ms = 0.0

    def session_duration_ms() -> float:
        return max(_elapsed_ms(started_at, uses_ticks_ms), waited_timeout_ms)
    visible_bytes = 0
    has_visible_text = False
    handshake_detected = False
    pending_trailing_whitespace = 0
    failure_window = bytearray()
    saw_data = False
    chunks_received = 0
    read_buffer = bytearray(TELNET_RECV_CHUNK_SIZE) if _is_micropython_runtime() else None

    def snapshot(close_reason: str, *, failure_detected: bool = False) -> dict[str, object]:
        return {
            "visible_bytes": visible_bytes,
            "has_visible_text": has_visible_text,
            "handshake_detected": handshake_detected,
            "failure_detected": failure_detected,
            "close_reason": close_reason,
            "session_duration_ms": session_duration_ms(),
        }

    while True:
        current_timeout_s = quiet_timeout_s if saw_data else initial_timeout_s
        current_duration_ms = session_duration_ms()
        meaningful_interaction = handshake_detected or has_visible_text
        stable_without_interaction = (not meaningful_interaction) and (not saw_data)
        if stable_without_interaction and current_duration_ms >= TELNET_STABLE_OPEN_THRESHOLD_MS:
            return snapshot("stable-open")
        if chunks_received >= max_chunks:
            return snapshot("chunk-limit")
        if deadline is not None and _deadline_remaining_ms(deadline) <= 0:
            return snapshot("deadline")
        _set_socket_timeout(handle, current_timeout_s)
        try:
            if read_buffer is not None:
                chunk = _recv_telnet_chunk_into(handle, read_buffer, trace=trace, target=target, budget=budget)
            else:
                _charge_budget(budget)
                chunk = handle.recv(TELNET_RECV_CHUNK_SIZE)
                _emit_socket_recv(trace, stage="telnet-recv", bytes_received=len(chunk), operation="read-visible", target=target)
        except TimeoutError as error:
            if _normalize_error_text(error) == "probe io budget exhausted":
                raise
            waited_timeout_ms += current_timeout_s * 1000.0
            current_duration_ms = session_duration_ms()
            meaningful_interaction = handshake_detected or has_visible_text
            stable_without_interaction = (not meaningful_interaction) and (not saw_data)
            if meaningful_interaction and current_duration_ms < TELNET_STABLE_OPEN_THRESHOLD_MS:
                continue
            if stable_without_interaction and current_duration_ms < TELNET_STABLE_OPEN_THRESHOLD_MS:
                continue
            return snapshot("idle-timeout")
        except OSError as error:
            category = _classify_network_error(error)
            if category == "timeout":
                waited_timeout_ms += current_timeout_s * 1000.0
            current_duration_ms = session_duration_ms()
            meaningful_interaction = handshake_detected or has_visible_text
            stable_without_interaction = (not meaningful_interaction) and (not saw_data)
            if category == "timeout":
                if meaningful_interaction and current_duration_ms < TELNET_STABLE_OPEN_THRESHOLD_MS:
                    continue
                if stable_without_interaction and current_duration_ms < TELNET_STABLE_OPEN_THRESHOLD_MS:
                    continue
                return snapshot("idle-timeout")
            return snapshot(category)
        if not chunk:
            return snapshot("remote-close")
        saw_data = True
        chunks_received += 1
        try:
            visible, chunk_handshake = _telnet_collect_visible(handle, chunk, trace=trace, target=target, budget=budget)
        except TimeoutError as error:
            if _normalize_error_text(error) == "probe io budget exhausted":
                raise
            return snapshot(_classify_network_error(error))
        except OSError as error:
            return snapshot(_classify_network_error(error))
        handshake_detected = handshake_detected or chunk_handshake
        if not visible:
            continue
        visible_bytes, has_visible_text, pending_trailing_whitespace, failure_window, failure_detected = _update_telnet_text_state(
            visible,
            visible_bytes=visible_bytes,
            has_visible_text=has_visible_text,
            pending_trailing_whitespace=pending_trailing_whitespace,
            failure_window=failure_window,
        )
        if failure_detected:
            return snapshot("failure-marker", failure_detected=True)
    return snapshot("idle-timeout")


def _telnet_result_from_session(session: dict[str, object], latency_ms: float) -> PingProbeResult:
    status, details = _classify_telnet_session(session)
    return PingProbeResult(
        ok=status == Status.OK,
        status=status,
        latency_ms=latency_ms,
        details=details,
        metadata=_telnet_result_metadata(
            str(session["close_reason"]),
            float(session["session_duration_ms"]),
            bool(session["handshake_detected"]),
            bool(session.get("has_visible_text", False)),
        ),
    )


def portable_telnet_runner(target: str, timeout_s: int, username: str | None = None, password: str | None = None, trace=None) -> PingProbeResult:
    del username, password
    host, port = _parse_socket_target(target, 23, expected_scheme="telnet")
    endpoint = f"{host}:{port}"
    if trace is None and not _is_micropython_runtime() and hasattr(socket, "create_connection"):
        started_at, uses_ticks_ms = _start_timer()
        handle = None
        try:
            deadline = _deadline_after_s(timeout_s)
            budget = _ProbeBudget()
            handle = socket.create_connection((host, port), timeout=timeout_s)
            session = _read_telnet_until_idle(handle, deadline=deadline, target=endpoint, budget=budget)
            return _telnet_result_from_session(session, _elapsed_ms(started_at, uses_ticks_ms))
        except Exception as error:
            return PingProbeResult(
                ok=False,
                status=Status.FAIL,
                latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
                details=_probe_error_detail(error),
                metadata=_telnet_result_metadata(_classify_network_error(error), 0.0, False, False),
            )
        finally:
            _close_socket(handle, trace=trace, target=endpoint)

    started_at, uses_ticks_ms = _start_timer()
    handle = None
    try:
        _maybe_collect_gc()
        deadline = _deadline_after_s(timeout_s)
        budget = _ProbeBudget()
        handle = _open_socket_compat(host, port, timeout_s, deadline, trace=trace)
        session = _read_telnet_until_idle(handle, trace=trace, deadline=deadline, target=endpoint, budget=budget)
        return _telnet_result_from_session(session, _elapsed_ms(started_at, uses_ticks_ms))
    except Exception as error:
        return PingProbeResult(
            ok=False,
            status=Status.FAIL,
            latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
            details=_probe_error_detail(error),
            metadata=_telnet_result_metadata(_classify_network_error(error), 0.0, False, False),
        )
    finally:
        _close_socket(handle, trace=trace, target=endpoint)
        _maybe_collect_gc()


def _parse_http_target(target: str) -> tuple[str, str, int, str]:
    raw_target = str(target).strip()
    parsed = urlparse(raw_target)
    scheme = _fold(parsed.scheme or "http")
    if scheme not in {"http", "https"}:
        raise ValueError("expected http target")
    if not parsed.hostname:
        raise ValueError("target must include a host")

    remainder = raw_target.split("://", 1)[1] if "://" in raw_target else raw_target
    split_index = len(remainder)
    for marker in ("/", "?"):
        marker_index = remainder.find(marker)
        if marker_index >= 0:
            split_index = min(split_index, marker_index)
    suffix = remainder[split_index:] if split_index < len(remainder) else ""
    path = suffix or "/"
    if not path.startswith("/"):
        path = "/" + path

    default_port = 443 if scheme == "https" else 80
    return scheme, parsed.hostname, parsed.port or default_port, path


def _decode_http_body(body: bytes):
    text = body.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _parse_http_response(payload: bytes) -> tuple[int, object]:
    header_end = payload.find(b"\r\n\r\n")
    if header_end < 0:
        raise ValueError("invalid HTTP response")
    header_block = payload[:header_end].decode("iso-8859-1", "replace")
    status_line = header_block.split("\r\n", 1)[0]
    parts = status_line.split()
    if len(parts) < 2 or not parts[1].isdigit():
        raise ValueError("invalid HTTP status")
    return int(parts[1]), _decode_http_body(payload[header_end + 4 :])


def _portable_http_runner_socket(method: str, target: str, timeout_s: int, trace=None) -> HttpResponseResult:
    scheme, host, port, path = _parse_http_target(target)
    if scheme == "https":
        raise OSError("https unsupported on device")

    started_at, uses_ticks_ms = _start_timer()
    deadline = _deadline_after_s(timeout_s)
    budget = _ProbeBudget()
    host_header = host if port == 80 else f"{host}:{port}"
    request_lines = [
        f"{method} {path} HTTP/1.1",
        f"Host: {host_header}",
        "Connection: close",
    ]
    request_lines.extend(("", ""))
    request_payload = "\r\n".join(request_lines).encode("utf-8")
    http_operation = f"{method} {path}"

    handle = None
    try:
        _maybe_collect_gc()
        handle = _open_socket_compat(host, port, timeout_s, deadline, trace=trace)
        endpoint = f"{host}:{port}"
        _socket_sendall(handle, request_payload, deadline, trace=trace, stage="http-send", operation=http_operation, target=endpoint, budget=budget)
        payload = _recv_until_closed(handle, deadline, trace=trace, stage="http-recv", operation=http_operation, target=endpoint, budget=budget)
        status_code, body = _parse_http_response(payload)
        return HttpResponseResult(
            status_code=status_code,
            body=body,
            latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
            details=f"HTTP {status_code}",
        )
    except Exception as error:
        return HttpResponseResult(
            status_code=None,
            body=None,
            latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
            details=_probe_error_detail(error),
        )
    finally:
        _close_socket(handle, trace=trace, target=f"{host}:{port}")
        _maybe_collect_gc()


def build_executor(ping_runner=None, http_runner=None, ftp_runner=None, telnet_runner=None, trace_sink=None):
    probe_counts_by_type: dict[str, dict[str, int]] = {}

    def _type_counts_for(definition: CheckDefinition) -> dict[str, int]:
        type_name = _check_type_name(definition)
        counts = probe_counts_by_type.get(type_name)
        if counts is None:
            counts = {"issued": 0, "succeeded": 0, "failed": 0}
            probe_counts_by_type[type_name] = counts
        return counts

    if ping_runner is None:
        def ping(target: str, timeout_s: int, trace=None):
            return portable_ping_runner(target, timeout_s)
    else:
        def ping(target: str, timeout_s: int, trace=None):
            return ping_runner(target, timeout_s)

    if http_runner is None:
        def http(method: str, target: str, timeout_s: int, username: str | None = None, password: str | None = None, trace=None):
            return portable_http_runner(method, target, timeout_s, username, password, trace=trace)
    else:
        def http(method: str, target: str, timeout_s: int, username: str | None = None, password: str | None = None, trace=None):
            if username is not None or password is not None:
                return http_runner(method, target, timeout_s, username, password)
            return http_runner(method, target, timeout_s)

    if ftp_runner is None:
        def ftp(target: str, timeout_s: int, username: str | None = None, password: str | None = None, trace=None):
            return portable_ftp_runner(target, timeout_s, username=username, password=password, trace=trace)
    else:
        def ftp(target: str, timeout_s: int, username: str | None = None, password: str | None = None, trace=None):
            return ftp_runner(target, timeout_s, username=username, password=password)

    if telnet_runner is None:
        def telnet(target: str, timeout_s: int, username: str | None = None, password: str | None = None, trace=None):
            return portable_telnet_runner(target, timeout_s, username=username, password=password, trace=trace)
    else:
        def telnet(target: str, timeout_s: int, username: str | None = None, password: str | None = None, trace=None):
            return telnet_runner(target, timeout_s, username=username, password=password)

    def executor(definition: CheckDefinition, now_s: float):
        counts = _type_counts_for(definition)
        counts["issued"] += 1
        trace = None
        if trace_sink is not None:
            def trace(event, **fields):
                if "target" not in fields:
                    fields["target"] = definition.target
                return trace_sink(definition, event, fields)
        if trace is not None:
            trace("probe-start", timeout_s=definition.timeout_s)
        try:
            result = execute_check(
                definition,
                now_s,
                lambda target, timeout_s: ping(target, timeout_s, trace=trace),
                lambda method, target, timeout_s, username=None, password=None: http(
                    method,
                    target,
                    timeout_s,
                    username,
                    password,
                    trace=trace,
                ),
                lambda target, timeout_s, username=None, password=None: ftp(
                    target,
                    timeout_s,
                    username=username,
                    password=password,
                    trace=trace,
                ),
                lambda target, timeout_s, username=None, password=None: telnet(
                    target,
                    timeout_s,
                    username=username,
                    password=password,
                    trace=trace,
                ),
            )
        except Exception as error:
            counts["failed"] += 1
            if trace is not None:
                trace(
                    "probe-error",
                    detail=_normalize_error_text(error),
                    probe_type=_check_type_name(definition),
                    issued=counts["issued"],
                    succeeded=counts["succeeded"],
                    failed=counts["failed"],
                )
            raise
        status = _probe_end_status(definition, result)
        if status == "OK":
            counts["succeeded"] += 1
        else:
            counts["failed"] += 1
        if trace is not None:
            fields = {
                "status": status,
                "detail": _probe_end_detail(definition, result),
                "latency_ms": _probe_end_latency_ms(definition, result),
                "observations": len(result.observations),
                "replace_source": result.replace_source,
                "probe_type": _check_type_name(definition),
                "issued": counts["issued"],
                "succeeded": counts["succeeded"],
                "failed": counts["failed"],
            }
            for field_name in ("close_reason", "session_duration_ms", "handshake_detected"):
                value = result.probe_metadata.get(field_name)
                if value is not None:
                    fields[field_name] = value
            trace("probe-end", **fields)
        return result

    return executor
