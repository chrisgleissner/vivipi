from __future__ import annotations

import json
import re
import time

from vivipi.core.execution import HttpResponseResult, PingProbeResult, execute_check
from vivipi.core.models import CheckDefinition, CheckType


PING_LATENCY_PATTERN = re.compile(r"time[=<]([0-9.]+)")


def _start_timer() -> tuple[float, bool]:
    if hasattr(time, "ticks_ms"):
        return float(time.ticks_ms()), True
    return time.perf_counter(), False


def _elapsed_ms(started_at: float, uses_ticks_ms: bool) -> float:
    if uses_ticks_ms:
        return float(time.ticks_diff(time.ticks_ms(), int(started_at)))
    return (time.perf_counter() - started_at) * 1000.0


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
                check_type=CheckType(str(item["type"])),
                target=str(item["target"]),
                interval_s=int(item.get("interval_s", 15)),
                timeout_s=int(item.get("timeout_s", 10)),
                method=str(item.get("method", "GET")).upper(),
                service_prefix=(
                    str(item["service_prefix"])
                    if isinstance(item.get("service_prefix"), str) and str(item["service_prefix"]).strip()
                    else None
                ),
            )
        )
    return tuple(definitions)


def portable_ping_runner(target: str, timeout_s: int) -> PingProbeResult:
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
        return PingProbeResult(
            ok=ok,
            latency_ms=latency_ms if latency_ms is not None else (_elapsed_ms(started_at, uses_ticks_ms) if ok else None),
            details="reachable" if ok else (completed.stderr.strip() or "timeout"),
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


def portable_http_runner(method: str, target: str, timeout_s: int) -> HttpResponseResult:
    try:
        import urequests  # type: ignore
    except ImportError:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(url=target, method=method)
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

    started_at, uses_ticks_ms = _start_timer()
    response = urequests.request(method, target, timeout=timeout_s)
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


def build_executor(ping_runner=None, http_runner=None):
    ping = ping_runner or portable_ping_runner
    http = http_runner or portable_http_runner

    def executor(definition: CheckDefinition, now_s: float):
        return execute_check(definition, now_s, ping, http)

    return executor