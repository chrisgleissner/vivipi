from __future__ import annotations

import json
import re
import socket
import time
from urllib.parse import urlparse

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
MAX_NETWORK_ATTEMPTS = 3
NETWORK_BACKOFF_BASE_MS = 100


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


def _retry_attempts(timeout_s: int) -> int:
    return max(1, min(MAX_NETWORK_ATTEMPTS, int(timeout_s)))


def _retry_backoff_ms(attempt_index: int) -> int:
    return min(800, NETWORK_BACKOFF_BASE_MS * (2**attempt_index))


def _normalize_error_text(error: BaseException) -> str:
    return " ".join(str(error).split()).strip() or type(error).__name__


def _classify_network_error(error: BaseException) -> str:
    message = _normalize_error_text(error).casefold()
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
    if detail.casefold() == category:
        return category
    return f"{category}: {detail}"


def _is_retryable_network_error(error: BaseException) -> bool:
    return isinstance(error, (OSError, TimeoutError)) or error.__class__.__name__ == "URLError"


def _retry_network_operation(operation, timeout_s: int):
    attempts = _retry_attempts(timeout_s)
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:
            if not _is_retryable_network_error(error) or attempt == attempts - 1:
                raise
            _sleep_ms(_retry_backoff_ms(attempt))


def _should_retry_probe_result(details: str) -> bool:
    normalized = str(details).casefold()
    return any(marker in normalized for marker in ("timeout", "dns", "refused", "unreachable", "network", "reset", "io:"))


def _retry_probe_result(operation, timeout_s: int):
    attempts = _retry_attempts(timeout_s)
    result = None
    for attempt in range(attempts):
        result = operation()
        if result.ok or attempt == attempts - 1 or not _should_retry_probe_result(result.details):
            return result
        _sleep_ms(_retry_backoff_ms(attempt))
    return result


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


def build_runtime_definitions(config: dict[str, object]) -> tuple[CheckDefinition, ...]:
    raw_checks = config.get("checks")
    if not isinstance(raw_checks, list):
        raise ValueError("runtime config must contain a checks list")

    definitions: list[CheckDefinition] = []
    for item in raw_checks:
        if not isinstance(item, dict):
            raise ValueError("runtime checks must be objects")
        definitions.append(
            CheckDefinition(
                identifier=str(item["id"]),
                name=str(item["name"]),
                check_type=_runtime_check_type(item["type"]),
                target=str(item["target"]),
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


def portable_ping_runner(target: str, timeout_s: int) -> PingProbeResult:
    def _single_ping() -> PingProbeResult:
        try:
            import uping  # type: ignore
        except ImportError:
            import subprocess

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

    return _retry_probe_result(_single_ping, timeout_s)


def portable_http_runner(method: str, target: str, timeout_s: int) -> HttpResponseResult:
    try:
        import urequests  # type: ignore
    except ImportError:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(url=target, method=method)
        started_at, uses_ticks_ms = _start_timer()
        try:
            with _retry_network_operation(lambda: urllib.request.urlopen(request, timeout=timeout_s), timeout_s) as response:
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

    started_at, uses_ticks_ms = _start_timer()
    try:
        response = _retry_network_operation(lambda: urequests.request(method, target, timeout=timeout_s), timeout_s)
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
        if expected_scheme is not None and parsed.scheme and parsed.scheme.casefold() != expected_scheme:
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


def _open_socket(host: str, port: int, timeout_s: int):
    def _single_attempt():
        addresses = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        last_error: OSError | None = None
        for family, socktype, proto, _, address in addresses:
            handle = socket.socket(family, socktype, proto)
            try:
                if hasattr(handle, "settimeout"):
                    handle.settimeout(timeout_s)
                handle.connect(address)
                return handle
            except OSError as error:
                last_error = error
                handle.close()
        raise last_error or OSError("unable to open socket")

    return _retry_network_operation(_single_attempt, timeout_s)


def _close_socket(handle):
    if handle is None:
        return
    try:
        handle.close()
    except OSError:
        return


def _ftp_read_response(handle) -> tuple[int, str]:
    buffer = bytearray()
    while not buffer.endswith(b"\n"):
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


def _looks_like_ftp_listing(value: str) -> bool:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return any(line[0] in "-dl" or len(line.split()) >= 2 for line in lines)


def portable_ftp_runner(target: str, timeout_s: int, username: str | None = None, password: str | None = None) -> PingProbeResult:
    host, port = _parse_socket_target(target, 21, expected_scheme="ftp")
    login_user = username or "anonymous"
    login_password = password or "vivipi@example.invalid"
    started_at, uses_ticks_ms = _start_timer()

    def _session() -> PingProbeResult:
        control_socket = None
        data_socket = None
        try:
            control_socket = _open_socket(host, port, timeout_s)
            code, response = _ftp_read_response(control_socket)
            if code != 220:
                return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details=response or "ftp unavailable")

            _ftp_command(control_socket, f"USER {login_user}")
            code, response = _ftp_read_response(control_socket)
            if code == 331:
                _ftp_command(control_socket, f"PASS {login_password}")
                code, response = _ftp_read_response(control_socket)
            if code != 230:
                return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details=response or "ftp login failed")

            _ftp_command(control_socket, "PASV")
            code, response = _ftp_read_response(control_socket)
            if code != 227:
                return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details=response or "ftp passive mode failed")

            data_host, data_port = _ftp_parse_pasv(response)
            data_socket = _open_socket(data_host, data_port, timeout_s)
            _ftp_command(control_socket, "LIST")
            code, response = _ftp_read_response(control_socket)
            if code not in {125, 150}:
                return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details=response or "ftp list failed")

            listing = _recv_all(data_socket).decode("utf-8", "replace")
            _close_socket(data_socket)
            data_socket = None

            code, response = _ftp_read_response(control_socket)
            if code not in {226, 250}:
                return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details=response or "ftp transfer incomplete")
            if not _looks_like_ftp_listing(listing):
                return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details="invalid directory listing")

            _ftp_command(control_socket, "QUIT")
            return PingProbeResult(
                ok=True,
                latency_ms=_elapsed_ms(started_at, uses_ticks_ms),
                details=f"listed {len([line for line in listing.splitlines() if line.strip()])} entries",
            )
        finally:
            _close_socket(data_socket)
            _close_socket(control_socket)

    try:
        return _retry_network_operation(_session, timeout_s)
    except (OSError, ValueError) as error:
        return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details=_format_network_error(error))


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


def _read_until_markers(handle, markers: tuple[bytes, ...]) -> bytes:
    buffer = bytearray()
    lowered_markers = tuple(marker.lower() for marker in markers)
    while True:
        chunk = handle.recv(4096)
        if not chunk:
            break
        buffer.extend(_telnet_strip_negotiation(handle, chunk))
        lowered = bytes(buffer).lower()
        if any(marker in lowered for marker in lowered_markers):
            break
    return bytes(buffer)


def _looks_like_telnet_output(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    lowered = stripped.casefold()
    if any(marker.decode("utf-8") in lowered for marker in TELNET_FAILURE_MARKERS):
        return False
    return any(character.isalnum() for character in stripped) or stripped[-1:] in ">#$%"


def portable_telnet_runner(target: str, timeout_s: int, username: str | None = None, password: str | None = None) -> PingProbeResult:
    host, port = _parse_socket_target(target, 23, expected_scheme="telnet")
    login_user = username or ""
    login_password = password or ""
    started_at, uses_ticks_ms = _start_timer()

    def _session() -> PingProbeResult:
        handle = None
        try:
            handle = _open_socket(host, port, timeout_s)
            transcript = _read_until_markers(handle, TELNET_LOGIN_MARKERS + TELNET_PASSWORD_MARKERS + TELNET_PROMPT_MARKERS + TELNET_FAILURE_MARKERS)
            if _contains_any(transcript, TELNET_FAILURE_MARKERS):
                return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details="login failed")

            if _contains_any(transcript, TELNET_LOGIN_MARKERS):
                handle.sendall((login_user + "\r\n").encode("utf-8"))
                transcript += _read_until_markers(handle, TELNET_PASSWORD_MARKERS + TELNET_PROMPT_MARKERS + TELNET_FAILURE_MARKERS)
            if _contains_any(transcript, TELNET_FAILURE_MARKERS):
                return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details="login failed")

            if _contains_any(transcript, TELNET_PASSWORD_MARKERS):
                handle.sendall((login_password + "\r\n").encode("utf-8"))
                transcript += _read_until_markers(handle, TELNET_PROMPT_MARKERS + TELNET_FAILURE_MARKERS)
            if _contains_any(transcript, TELNET_FAILURE_MARKERS):
                return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details="login failed")

            if not _contains_any(transcript, TELNET_PROMPT_MARKERS):
                handle.sendall(b"\r\n")
                transcript += _read_until_markers(handle, TELNET_PROMPT_MARKERS + TELNET_FAILURE_MARKERS)
            cleaned = transcript.decode("utf-8", "replace")
            if not _looks_like_telnet_output(cleaned):
                return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details="invalid session output")

            return PingProbeResult(ok=True, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details="session ready")
        finally:
            _close_socket(handle)

    try:
        return _retry_network_operation(_session, timeout_s)
    except (OSError, ValueError) as error:
        return PingProbeResult(ok=False, latency_ms=_elapsed_ms(started_at, uses_ticks_ms), details=_format_network_error(error))


def build_executor(ping_runner=None, http_runner=None, ftp_runner=None, telnet_runner=None):
    ping = ping_runner or portable_ping_runner
    http = http_runner or portable_http_runner
    ftp = ftp_runner or portable_ftp_runner
    telnet = telnet_runner or portable_telnet_runner

    def executor(definition: CheckDefinition, now_s: float):
        return execute_check(definition, now_s, ping, http, ftp, telnet)

    return executor