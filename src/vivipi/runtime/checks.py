from __future__ import annotations

import json
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
from vivipi.core.models import CheckDefinition, CheckType


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
TELNET_SB = 250
TELNET_SE = 240
POLLIN = getattr(select, "POLLIN", 0x0001)
POLLOUT = getattr(select, "POLLOUT", 0x0004)
POLLERR = getattr(select, "POLLERR", 0x0008)
POLLHUP = getattr(select, "POLLHUP", 0x0010)
SOCKET_CONNECT_IN_PROGRESS_ERRNOS = frozenset({11, 36, 114, 115, 10035})
SOCKET_ALREADY_CONNECTED_ERRNOS = frozenset({56, 106, 127})
SOCKET_WOULD_BLOCK_ERRNOS = frozenset({11, 35, 36, 10035})


def _fold(value: object) -> str:
    return str(value).strip().lower()


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
    time.sleep(value_ms / 1000.0)


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
    errno = getattr(error, "errno", None)
    if isinstance(error, TimeoutError) or "timeout" in message or "timed out" in message:
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


def portable_ping_runner(target: str, timeout_s: int) -> PingProbeResult:
    def _single_ping() -> PingProbeResult:
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


def _http_headers(username: str | None = None, password: str | None = None) -> dict[str, str]:
    headers = {"Connection": "close"}
    if password:
        headers["X-Password"] = password
    return headers


def portable_http_runner(
    method: str,
    target: str,
    timeout_s: int,
    username: str | None = None,
    password: str | None = None,
    trace=None,
) -> HttpResponseResult:
    headers = _http_headers(username=username, password=password)
    try:
        import urequests  # type: ignore
    except ImportError:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(url=target, method=method, headers=headers)
        started_at, uses_ticks_ms = _start_timer()
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                raw_body = response.read().decode("utf-8")
                try:
                    body = json.loads(raw_body)
                except ValueError:
                    body = raw_body
                return HttpResponseResult(
                    status_code=response.getcode(),
                    body=body,
                    latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
                    details=f"HTTP {response.getcode()}",
                )
        except urllib.error.HTTPError as error:
            raw_body = error.read().decode("utf-8")
            try:
                body = json.loads(raw_body)
            except ValueError:
                body = raw_body
            return HttpResponseResult(
                status_code=error.code,
                body=body,
                latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
                details=f"HTTP {error.code}",
            )
        except Exception as error:
            return HttpResponseResult(
                status_code=None,
                body=None,
                latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
                details=_format_network_error(error),
            )

    if hasattr(time, "ticks_ms"):
        return _portable_http_runner_socket(method, target, timeout_s, headers=headers, trace=trace)

    started_at, uses_ticks_ms = _start_timer()
    try:
        response = urequests.request(method, target, timeout=timeout_s, headers=headers)
    except Exception as error:
        return HttpResponseResult(
            status_code=None,
            body=None,
            latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
            details=_format_network_error(error),
        )
    try:
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return HttpResponseResult(
            status_code=int(response.status_code),
            body=body,
            latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
            details=f"HTTP {response.status_code}",
        )
    finally:
        response.close()


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
        _set_socket_timeout(handle, _deadline_remaining_s(deadline))
        return

    poller = select.poll()
    flags = POLLOUT if writable else POLLIN
    flags |= POLLERR | POLLHUP
    try:
        poller.register(handle, flags)
    except Exception:
        _set_socket_timeout(handle, _deadline_remaining_s(deadline))
        return

    events = poller.poll(remaining_ms)
    if not events:
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


def _recv_telnet_chunk_compat(handle, size: int, deadline=None, trace=None) -> bytes:
    try:
        return _recv_telnet_chunk(handle, size, deadline=deadline, trace=trace)
    except TypeError as error:
        if "deadline" not in str(error) and "trace" not in str(error):
            raise
        try:
            return _recv_telnet_chunk(handle, size)
        except OSError as nested_error:
            if _classify_network_error(nested_error) == "timeout":
                return b""
            raise


def _open_socket(host: str, port: int, timeout_s: int, *, deadline=None, trace=None):
    _emit_probe_trace(trace, "dns-start", host=host, port=port)
    try:
        addresses = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except Exception as error:
        _emit_probe_trace(trace, "dns-error", host=host, port=port, detail=_format_network_error(error))
        raise
    _emit_probe_trace(
        trace,
        "dns-result",
        host=host,
        port=port,
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
        handle.close()
    except OSError:
        return
    _emit_probe_trace(trace, "socket-close", stage="close", target=target)


def _socket_sendall(handle, payload: bytes, deadline, trace=None, stage: str = "send"):
    if not payload:
        return
    sender = getattr(handle, "send", None)
    if callable(sender):
        view = memoryview(payload)
        while len(view):
            _socket_wait(handle, deadline, writable=True, trace=trace, stage=stage)
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
            _emit_probe_trace(trace, "socket-send", stage=stage, bytes_sent=sent)
            view = view[sent:]
        return

    while True:
        _socket_wait(handle, deadline, writable=True, trace=trace, stage=stage)
        try:
            handle.sendall(payload)
            _emit_probe_trace(trace, "socket-send", stage=stage, bytes_sent=len(payload))
            return
        except OSError as error:
            if _is_would_block(error):
                continue
            raise


def _socket_recv(handle, size: int, deadline, trace=None, stage: str = "recv") -> bytes:
    while True:
        _socket_wait(handle, deadline, writable=False, trace=trace, stage=stage)
        try:
            chunk = handle.recv(size)
            _emit_probe_trace(trace, "socket-recv", stage=stage, bytes_received=len(chunk))
            return chunk
        except OSError as error:
            if _classify_network_error(error) == "timeout":
                _emit_probe_trace(trace, "socket-timeout", stage=stage, remain_ms=_deadline_remaining_ms(deadline))
                raise TimeoutError("timed out") from error
            if _is_would_block(error):
                continue
            raise


def _ftp_read_response(handle, deadline=None, trace=None) -> tuple[int, str]:
    buffer = bytearray()
    while not buffer.endswith(b"\n"):
        chunk = _socket_recv(handle, 4096, deadline, trace=trace, stage="ftp-recv") if deadline is not None else handle.recv(4096)
        if not chunk:
            break
        buffer.extend(chunk)
    response = bytes(buffer).decode("utf-8", "replace").strip()
    if len(response) < 3 or not response[:3].isdigit():
        raise ValueError("invalid FTP response")
    return int(response[:3]), response


def _ftp_command(handle, value: str):
    handle.sendall((value + "\r\n").encode("utf-8"))


def _ftp_command_with_deadline(handle, value: str, deadline, trace=None):
    _socket_sendall(handle, (value + "\r\n").encode("utf-8"), deadline, trace=trace, stage="ftp-send")


def _ftp_parse_pasv(response: str) -> tuple[str, int]:
    match = FTP_PASV_PATTERN.search(response)
    if match is None:
        raise ValueError("invalid FTP passive response")
    host = ".".join(match.group(index) for index in range(1, 5))
    port = (int(match.group(5)) * 256) + int(match.group(6))
    return host, port


def _recv_all(handle) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = handle.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _recv_until_closed(handle, deadline, trace=None, stage: str = "recv-all") -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = _socket_recv(handle, 4096, deadline, trace=trace, stage=stage)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _looks_like_ftp_listing(value: str) -> bool:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return any(line[0] in "-dl" or len(line.split()) >= 2 for line in lines)


def portable_ftp_runner(target: str, timeout_s: int, username: str | None = None, password: str | None = None, trace=None) -> PingProbeResult:
    host, port = _parse_socket_target(target, 21, expected_scheme="ftp")
    started_at, uses_ticks_ms = _start_timer()
    deadline = _deadline_after_s(timeout_s)

    control_socket = None
    try:
        control_socket = _open_socket_compat(host, port, timeout_s, deadline, trace=trace)
        code, response = _ftp_read_response(control_socket, deadline=deadline, trace=trace)
        if code != 220:
            return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details=response or "ftp unavailable")

        _ftp_command_with_deadline(control_socket, "QUIT", deadline, trace=trace)
        _ftp_read_response(control_socket, deadline=deadline, trace=trace)
        return PingProbeResult(
            ok=True,
            latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
            details="ftp greeting ready",
        )
    except (OSError, TimeoutError, ValueError) as error:
        return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details=_format_network_error(error))
    finally:
        _close_socket(control_socket, trace=trace, target=f"{host}:{port}")


def _telnet_strip_negotiation(handle, chunk: bytes) -> bytes:
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
                handle.sendall(bytes((TELNET_IAC, TELNET_WONT, option)))
            else:
                handle.sendall(bytes((TELNET_IAC, TELNET_DONT, option)))
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


def _read_until_markers(handle, markers: tuple[bytes, ...], deadline=None, trace=None) -> bytes:
    buffer = bytearray()
    lowered_markers = tuple(marker.lower() for marker in markers)
    while True:
        chunk = _recv_telnet_chunk_compat(handle, 4096, deadline=deadline, trace=trace)
        if not chunk:
            break
        buffer.extend(_telnet_strip_negotiation(handle, chunk))
        lowered = bytes(buffer).lower()
        if any(marker in lowered for marker in lowered_markers):
            break
    return bytes(buffer)


def _recv_telnet_chunk(handle, size: int = 4096, deadline=None, trace=None) -> bytes:
    try:
        if deadline is not None:
            return _socket_recv(handle, size, deadline, trace=trace, stage="telnet-recv")
        return handle.recv(size)
    except TimeoutError:
        return b""
    except OSError as error:
        if _classify_network_error(error) == "timeout":
            return b""
        raise


def _looks_like_telnet_output(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if any(marker.decode("utf-8") in lowered for marker in TELNET_FAILURE_MARKERS):
        return False
    return _has_alnum_ascii(stripped) or stripped[-1:] in ">#$%"


def portable_telnet_runner(target: str, timeout_s: int, username: str | None = None, password: str | None = None, trace=None) -> PingProbeResult:
    host, port = _parse_socket_target(target, 23, expected_scheme="telnet")
    started_at, uses_ticks_ms = _start_timer()
    deadline = _deadline_after_s(timeout_s)

    handle = None
    try:
        handle = _open_socket_compat(host, port, timeout_s, deadline, trace=trace)
        initial_raw = _recv_telnet_chunk_compat(handle, 4096, deadline=deadline, trace=trace)
        transcript = _telnet_strip_negotiation(handle, initial_raw)
        if _contains_any(transcript, TELNET_FAILURE_MARKERS):
            return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details="login failed")

        if transcript:
            cleaned = transcript.decode("utf-8", "replace")
            if _looks_like_telnet_output(cleaned):
                return PingProbeResult(ok=True, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details="banner ready")

        return PingProbeResult(ok=True, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details="connected")
    except (OSError, TimeoutError, ValueError) as error:
        return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details=_format_network_error(error))
    finally:
        _close_socket(handle, trace=trace, target=f"{host}:{port}")


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


def _portable_http_runner_socket(method: str, target: str, timeout_s: int, headers: dict[str, str], trace=None) -> HttpResponseResult:
    scheme, host, port, path = _parse_http_target(target)
    if scheme == "https":
        raise OSError("https unsupported on device")

    started_at, uses_ticks_ms = _start_timer()
    deadline = _deadline_after_s(timeout_s)
    host_header = host if port == 80 else f"{host}:{port}"
    request_lines = [
        f"{method} {path} HTTP/1.0",
        f"Host: {host_header}",
        "Connection: close",
    ]
    for key, value in headers.items():
        if _fold(key) == "connection":
            continue
        request_lines.append(f"{key}: {value}")
    request_lines.extend(("", ""))
    request_payload = "\r\n".join(request_lines).encode("utf-8")

    handle = None
    try:
        handle = _open_socket_compat(host, port, timeout_s, deadline, trace=trace)
        _socket_sendall(handle, request_payload, deadline, trace=trace, stage="http-send")
        payload = _recv_until_closed(handle, deadline, trace=trace, stage="http-recv")
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
            details=_format_network_error(error),
        )
    finally:
        _close_socket(handle, trace=trace, target=f"{host}:{port}")


def build_executor(ping_runner=None, http_runner=None, ftp_runner=None, telnet_runner=None, trace_sink=None):
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
        trace = None
        if trace_sink is not None:
            def trace(event, **fields):
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
            if trace is not None:
                trace("probe-error", detail=_normalize_error_text(error))
            raise
        if trace is not None:
            observation = result.observations[0] if result.observations else None
            trace(
                "probe-end",
                status=getattr(getattr(observation, "status", None), "value", getattr(observation, "status", "?")),
                detail=getattr(observation, "details", "") if observation is not None else "",
                latency_ms=getattr(observation, "latency_ms", None) if observation is not None else None,
                observations=len(result.observations),
                replace_source=result.replace_source,
            )
        return result

    return executor
