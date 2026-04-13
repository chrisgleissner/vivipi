#!/usr/bin/env python3

import argparse
import enum
import ftplib
import http.client
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace


HOST = os.getenv("HOST", "192.168.1.13")
HTTP_PATH = os.getenv("HTTP_PATH", "v1/version")
HTTP_PORT = int(os.getenv("HTTP_PORT", "80"))
TELNET_PORT = int(os.getenv("TELNET_PORT", "23"))
FTP_PORT = int(os.getenv("FTP_PORT", "21"))
FTP_USER = os.getenv("FTP_USER", "anonymous")
FTP_PASS = os.getenv("FTP_PASS", "")
INTER_CALL_DELAY_MS = int(os.getenv("INTER_CALL_DELAY_MS", "1"))
LOG_EVERY_N_ITERATIONS = int(os.getenv("LOG_EVERY_N_ITERATIONS", "10"))
CURRENT_ITERATION = 0
LATENCY_SAMPLES = {"ping": [], "http": [], "ftp": [], "telnet": []}
TELNET_IDLE_TIMEOUT_S = 0.20
IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
DEFAULT_PROBES = ("ping", "http", "ftp", "telnet")
DEFAULT_SCHEDULE = "sequential"
DEFAULT_RUNNERS = 1
DEFAULT_PROFILE_HOST = "u64"
DEFAULT_PROFILE_DURATION_S = 120
PROFILE_SOAK = "soak"
PROFILE_STRESS = "stress"
SCHEDULE_SEQUENTIAL = "sequential"
SCHEDULE_CONCURRENT = "concurrent"


class ProbeCorrectness(enum.StrEnum):
    CORRECT = "correct"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


PROBE_CORRECTNESS_CHOICES = {
    "ping": (ProbeCorrectness.CORRECT,),
    "http": (ProbeCorrectness.CORRECT,),
    "ftp": (ProbeCorrectness.CORRECT, ProbeCorrectness.INCOMPLETE, ProbeCorrectness.INVALID),
    "telnet": (ProbeCorrectness.CORRECT, ProbeCorrectness.INCOMPLETE),
}
HISTORICAL_CORRECTNESS_EVIDENCE = {
    "ftp": {
        ProbeCorrectness.INCOMPLETE.value: {
            "commit": "37314b1",
            "path": "src/vivipi/runtime/checks.py",
            "summary": "Historical Pico-side FTP probe stopped after the greeting path and skipped the later passive listing and graceful teardown path.",
        }
    },
    "telnet": {
        ProbeCorrectness.INCOMPLETE.value: {
            "commit": "37314b1",
            "path": "src/vivipi/runtime/checks.py",
            "summary": "Historical Pico-side Telnet runner performed only an initial read and treated blank or delayed output as connected.",
        }
    },
}
TELNET_FAILURE_MARKERS = (b"incorrect", b"failed", b"denied", b"invalid")
NEW_FEATURE_ARGUMENT_NAMES = (
    "profile",
    "probes",
    "schedule",
    "runners",
    "duration_s",
    "ping_mode",
    "http_mode",
    "ftp_mode",
    "telnet_mode",
)


def parse_bool(value: str) -> bool:
    return value.strip().lower() not in {"", "0", "false", "no"}


VERBOSE = parse_bool(os.getenv("VERBOSE", "0"))


@dataclass(frozen=True)
class RuntimeSettings:
    host: str
    http_path: str
    http_port: int
    telnet_port: int
    ftp_port: int
    ftp_user: str
    ftp_pass: str
    delay_ms: int
    log_every: int
    verbose: bool


@dataclass(frozen=True)
class ExecutionConfig:
    profile: str | None
    probes: tuple[str, ...]
    schedule: str
    runners: int
    duration_s: int | None
    probe_correctness: dict[str, ProbeCorrectness]
    uses_extended_flags: bool
    overrides: tuple[str, ...]


@dataclass(frozen=True)
class ProbeOutcome:
    result: str
    detail: str
    elapsed_ms: float


@dataclass
class ExecutionState:
    settings: RuntimeSettings
    include_runner_context: bool
    latency_samples: dict[str, list[float]] = field(default_factory=lambda: {protocol: [] for protocol in DEFAULT_PROBES})
    sample_lock: threading.Lock = field(default_factory=threading.Lock)
    output_lock: threading.Lock = field(default_factory=threading.Lock)

    def record_latency(self, protocol: str, elapsed_ms: float) -> None:
        with self.sample_lock:
            self.latency_samples[protocol].append(elapsed_ms)

    def percentile_ms(self, protocol: str, percentile: int) -> int:
        with self.sample_lock:
            samples = tuple(self.latency_samples[protocol])
        if not samples:
            return 0
        ordered = sorted(samples)
        rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
        return int(round(ordered[rank - 1]))

    def emit_log(self, protocol: str, result: str, detail: str, *, iteration: int, runner_id: int) -> None:
        if result != "FAIL" and not self.settings.verbose and self.settings.log_every > 1 and iteration % self.settings.log_every != 0:
            return
        if self.include_runner_context:
            detail = f"runner={runner_id} iteration={iteration} {detail}"
        with self.output_lock:
            try:
                print(f'{ts()} protocol={protocol} result={result} detail="{detail.replace(chr(34), chr(39))}"', flush=True)
            except BrokenPipeError:
                raise SystemExit(0)

    def emit_probe_outcome(self, protocol: str, outcome: ProbeOutcome, *, iteration: int, runner_id: int) -> None:
        self.record_latency(protocol, outcome.elapsed_ms)
        self.emit_log(
            protocol,
            outcome.result,
            f"{outcome.detail} latency_ms={int(round(outcome.elapsed_ms))}",
            iteration=iteration,
            runner_id=runner_id,
        )

    def emit_iteration_summary(self, started_at: float, iteration: int, runner_id: int) -> None:
        if not self.settings.verbose and self.settings.log_every > 1 and iteration % self.settings.log_every != 0:
            return
        parts = [f"iteration={iteration}", f"runtime_s={int(time.time() - started_at)}", f"host={self.settings.host}"]
        if self.include_runner_context:
            parts.insert(0, f"runner={runner_id}")
        for protocol in DEFAULT_PROBES:
            parts.append(f"{protocol}_median_ms={self.percentile_ms(protocol, 50)}")
            parts.append(f"{protocol}_p90_ms={self.percentile_ms(protocol, 90)}")
            parts.append(f"{protocol}_p99_ms={self.percentile_ms(protocol, 99)}")
        with self.output_lock:
            try:
                print(f'{ts()} protocol=iteration result=INFO detail="{' '.join(parts)}"', flush=True)
            except BrokenPipeError:
                raise SystemExit(0)


def usage() -> None:
    parser = build_parser()
    parser.print_help()


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def should_log(result: str) -> bool:
    if result == "FAIL" or VERBOSE:
        return True
    if LOG_EVERY_N_ITERATIONS <= 1:
        return True
    return CURRENT_ITERATION % LOG_EVERY_N_ITERATIONS == 0


def should_log_iteration() -> bool:
    if VERBOSE:
        return True
    if LOG_EVERY_N_ITERATIONS <= 1:
        return True
    return CURRENT_ITERATION % LOG_EVERY_N_ITERATIONS == 0


def log(protocol: str, result: str, detail: str) -> None:
    if not should_log(result):
        return
    try:
        print(f'{ts()} protocol={protocol} result={result} detail="{detail.replace(chr(34), chr(39))}"', flush=True)
    except BrokenPipeError:
        raise SystemExit(0)


def log_check(protocol: str, result: str, detail: str, elapsed_ms: float) -> None:
    LATENCY_SAMPLES[protocol].append(elapsed_ms)
    log(protocol, result, f"{detail} latency_ms={int(round(elapsed_ms))}")


def parse_probes(value: str) -> tuple[str, ...]:
    raw_value = value.strip()
    if not raw_value:
        raise argparse.ArgumentTypeError("--probes must be a non-empty comma-separated list")
    probes = tuple(part.strip() for part in raw_value.split(","))
    if any(not probe for probe in probes):
        raise argparse.ArgumentTypeError("--probes must not contain empty entries")
    invalid = [probe for probe in probes if probe not in DEFAULT_PROBES]
    if invalid:
        invalid_list = ", ".join(sorted(set(invalid)))
        raise argparse.ArgumentTypeError(f"unknown probe name(s): {invalid_list}")
    if len(set(probes)) != len(probes):
        raise argparse.ArgumentTypeError("--probes must not contain duplicates")
    return probes


def parse_runners(value: str) -> int:
    try:
        runners = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--runners must be an integer >= 1") from error
    if runners < 1:
        raise argparse.ArgumentTypeError("--runners must be >= 1")
    return runners


def parse_duration_s(value: str) -> int:
    try:
        duration_s = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--duration-s must be an integer >= 1") from error
    if duration_s < 1:
        raise argparse.ArgumentTypeError("--duration-s must be >= 1")
    return duration_s


def profile_overrides_help() -> str:
    return (
        "Profile precedence: if --profile is supplied, explicit --probes, --schedule, --runners, and --*-mode values override the profile.\n\n"
        "Examples:\n"
        "  ./u64_connection_test.py\n"
        "  ./u64_connection_test.py --profile soak\n"
        "  ./u64_connection_test.py --profile stress\n"
        "  ./u64_connection_test.py --profile soak --duration-s 300\n"
        "  ./u64_connection_test.py --probes ping,http,ftp,telnet\n"
        "  ./u64_connection_test.py --probes ping,http\n"
        "  ./u64_connection_test.py --schedule concurrent\n"
        "  ./u64_connection_test.py --schedule concurrent --runners 3\n"
        f"  ./u64_connection_test.py --telnet-mode {ProbeCorrectness.INCOMPLETE.value}\n"
        f"  ./u64_connection_test.py --schedule concurrent --runners 2 --ftp-mode {ProbeCorrectness.INVALID.value} --telnet-mode {ProbeCorrectness.INCOMPLETE.value}\n"
        "  ./u64_connection_test.py --profile stress --runners 4\n"
        "  ./u64_connection_test.py --profile soak --probes ping,http"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repeated U64 connectivity checks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=profile_overrides_help(),
    )
    parser.add_argument("-H", "--host", default=HOST, help="Target host or IP")
    parser.add_argument("-d", "--delay-ms", type=int, default=INTER_CALL_DELAY_MS, help="Delay between checks in milliseconds")
    parser.add_argument("-n", "--log-every", type=int, default=LOG_EVERY_N_ITERATIONS, help="Log every Nth successful iteration")
    parser.add_argument("-u", "--ftp-user", default=FTP_USER, help="FTP username")
    parser.add_argument("-P", "--ftp-pass", default=FTP_PASS, help="FTP password")
    parser.add_argument("--http-path", default=HTTP_PATH, help="HTTP path")
    parser.add_argument("--http-port", type=int, default=HTTP_PORT, help="HTTP port")
    parser.add_argument("--ftp-port", type=int, default=FTP_PORT, help="FTP port")
    parser.add_argument("--telnet-port", type=int, default=TELNET_PORT, help="Telnet port")
    parser.add_argument("-v", "--verbose", action="store_true", default=VERBOSE, help="Log every successful check")
    parser.add_argument(
        "--profile",
        choices=(PROFILE_SOAK, PROFILE_STRESS),
        default=None,
        help="Preset profile. Explicit --probes, --schedule, --runners, and --*-mode flags override the profile.",
    )
    parser.add_argument(
        "--probes",
        type=parse_probes,
        default=None,
        help="Ordered non-empty comma-separated probe list using ping,http,ftp,telnet.",
    )
    parser.add_argument(
        "--schedule",
        choices=(SCHEDULE_SEQUENTIAL, SCHEDULE_CONCURRENT),
        default=None,
        help="Per-runner scheduling mode.",
    )
    parser.add_argument(
        "--runners",
        type=parse_runners,
        default=None,
        help="Logical runner count >= 1.",
    )
    parser.add_argument(
        "--duration-s",
        type=parse_duration_s,
        default=None,
        help="Optional total run duration in seconds. Profiles default to 120 seconds when not overridden.",
    )
    parser.add_argument("--ping-mode", choices=[value.value for value in PROBE_CORRECTNESS_CHOICES["ping"]], default=None, help="Ping probe correctness.")
    parser.add_argument("--http-mode", choices=[value.value for value in PROBE_CORRECTNESS_CHOICES["http"]], default=None, help="HTTP probe correctness.")
    parser.add_argument("--ftp-mode", choices=[value.value for value in PROBE_CORRECTNESS_CHOICES["ftp"]], default=None, help="FTP probe correctness.")
    parser.add_argument("--telnet-mode", choices=[value.value for value in PROBE_CORRECTNESS_CHOICES["telnet"]], default=None, help="Telnet probe correctness.")
    return parser


def percentile_ms(protocol: str, percentile: int) -> int:
    samples = LATENCY_SAMPLES[protocol]
    if not samples:
        return 0
    ordered = sorted(samples)
    rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
    return int(round(ordered[rank - 1]))


def log_iteration_summary(started_at: float, iteration: int) -> None:
    if not should_log_iteration():
        return
    parts = [f"iteration={iteration}", f"runtime_s={int(time.time() - started_at)}", f"host={HOST}"]
    for protocol in DEFAULT_PROBES:
        parts.append(f"{protocol}_median_ms={percentile_ms(protocol, 50)}")
        parts.append(f"{protocol}_p90_ms={percentile_ms(protocol, 90)}")
        parts.append(f"{protocol}_p99_ms={percentile_ms(protocol, 99)}")
    try:
        print(f'{ts()} protocol=iteration result=INFO detail="{' '.join(parts)}"', flush=True)
    except BrokenPipeError:
        raise SystemExit(0)


def sleep_ms(value: int) -> None:
    time.sleep(value / 1000.0)


def http_request_path(http_path: str) -> str:
    return f"/{http_path}"


def _first_non_empty_line(text: str, fallback: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), fallback)


def _parse_http_response(payload: bytes) -> tuple[int, bytes]:
    header_end = payload.find(b"\r\n\r\n")
    if header_end < 0:
        raise RuntimeError("invalid HTTP response")
    header_block = payload[:header_end].decode("iso-8859-1", "replace")
    status_line = header_block.split("\r\n", 1)[0]
    parts = status_line.split()
    if len(parts) < 2 or not parts[1].isdigit():
        raise RuntimeError("invalid HTTP status")
    return int(parts[1]), payload[header_end + 4 :]


def _looks_like_telnet_output(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if any(marker.decode("utf-8") in lowered for marker in TELNET_FAILURE_MARKERS):
        return False
    return any(character.isalnum() for character in stripped) or stripped[-1:] in ">#$%"


def _contains_any(value: bytes, markers: tuple[bytes, ...]) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in markers)


def _collect_telnet_visible(handle, chunk: bytes) -> bytes:
    visible = bytearray()
    index = 0
    while index < len(chunk):
        byte = chunk[index]
        if byte == IAC and index + 2 < len(chunk) and chunk[index + 1] in (DO, DONT, WILL, WONT):
            command = chunk[index + 1]
            option = chunk[index + 2]
            reply = bytes([IAC, WONT if command in (DO, DONT) else DONT, option])
            handle.sendall(reply)
            index += 3
            continue
        visible.append(byte)
        index += 1
    return bytes(visible)


def run_ping_probe(settings: RuntimeSettings, correctness: ProbeCorrectness) -> ProbeOutcome:
    del correctness
    started_at = time.perf_counter_ns()
    try:
        result = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", "2", settings.host],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        if result.returncode == 0:
            match = re.search(r"time=([0-9.]+)", result.stdout)
            if match:
                return ProbeOutcome("OK", f"ping_reply_ms={match.group(1)}", elapsed_ms)
            return ProbeOutcome("OK", "ping reply", elapsed_ms)
        detail = _first_non_empty_line(result.stderr + "\n" + result.stdout, "ping failed")
        return ProbeOutcome("FAIL", detail, elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("FAIL", f"ping failed: {error}", elapsed_ms)


def run_http_probe(settings: RuntimeSettings, correctness: ProbeCorrectness) -> ProbeOutcome:
    del correctness
    conn = http.client.HTTPConnection(settings.host, settings.http_port, timeout=8)
    started_at = time.perf_counter_ns()
    try:
        conn.request("GET", http_request_path(settings.http_path), headers={"Connection": "close"})
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


def run_telnet_probe(settings: RuntimeSettings, correctness: ProbeCorrectness) -> ProbeOutcome:
    if correctness == ProbeCorrectness.INCOMPLETE:
        return run_telnet_probe_incomplete(settings)
    sock = None
    started_at = time.perf_counter_ns()
    try:
        sock = socket.create_connection((settings.host, settings.telnet_port), timeout=2)
        sock.settimeout(TELNET_IDLE_TIMEOUT_S)
        sock.sendall(b"\r\n")
        visible = bytearray()
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            visible.extend(_collect_telnet_visible(sock, chunk))
        text = bytes(visible).decode("utf-8", "ignore").strip()
        if not text:
            raise RuntimeError("empty telnet banner")
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("OK", f"banner_bytes={len(text.encode())}", elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("FAIL", f"telnet failed: {error}", elapsed_ms)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def run_telnet_probe_incomplete(settings: RuntimeSettings) -> ProbeOutcome:
    sock = None
    started_at = time.perf_counter_ns()
    try:
        sock = socket.create_connection((settings.host, settings.telnet_port), timeout=2)
        sock.settimeout(TELNET_IDLE_TIMEOUT_S)
        try:
            initial_raw = sock.recv(4096)
        except socket.timeout:
            initial_raw = b""
        transcript = _collect_telnet_visible(sock, initial_raw) if initial_raw else b""
        if _contains_any(transcript, TELNET_FAILURE_MARKERS):
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("FAIL", "login failed", elapsed_ms)
        if transcript:
            cleaned = transcript.decode("utf-8", "replace")
            if _looks_like_telnet_output(cleaned):
                elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
                return ProbeOutcome("OK", "banner ready", elapsed_ms)
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("OK", "connected", elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("FAIL", f"telnet failed: {error}", elapsed_ms)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def run_ftp_probe(settings: RuntimeSettings, correctness: ProbeCorrectness) -> ProbeOutcome:
    if correctness == ProbeCorrectness.INCOMPLETE:
        return run_ftp_probe_incomplete(settings)
    if correctness == ProbeCorrectness.INVALID:
        return run_ftp_probe_invalid(settings)
    ftp = ftplib.FTP()
    started_at = time.perf_counter_ns()
    try:
        greeting = ftp.connect(settings.host, settings.ftp_port, timeout=8)
        if not greeting.startswith("220"):
            raise RuntimeError(f"expected FTP 220, got {greeting}")
        login = ftp.login(settings.ftp_user, settings.ftp_pass)
        if not login.startswith("230"):
            raise RuntimeError(f"expected FTP 230, got {login}")
        ftp.set_pasv(True)
        names = ftp.nlst(".")
        if not names:
            raise RuntimeError("empty FTP NLST data")
        goodbye = ftp.quit()
        if not goodbye.startswith("221"):
            raise RuntimeError(f"expected FTP 221, got {goodbye}")
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("OK", f"NLST bytes={sum(len(name) for name in names)}", elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("FAIL", f"ftp failed: {error}", elapsed_ms)
    finally:
        try:
            ftp.close()
        except OSError:
            pass


def run_ftp_probe_incomplete(settings: RuntimeSettings) -> ProbeOutcome:
    ftp = ftplib.FTP()
    started_at = time.perf_counter_ns()
    try:
        greeting = ftp.connect(settings.host, settings.ftp_port, timeout=8)
        if not greeting.startswith("220"):
            raise RuntimeError(f"expected FTP 220, got {greeting}")
        login = ftp.login(settings.ftp_user, settings.ftp_pass)
        if not login.startswith("230"):
            raise RuntimeError(f"expected FTP 230, got {login}")
        ftp.set_pasv(False)
        names = ftp.nlst(".")
        if not names:
            raise RuntimeError("empty FTP NLST data")
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("OK", f"NLST bytes={sum(len(name) for name in names)}", elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("FAIL", f"ftp failed: {error}", elapsed_ms)
    finally:
        try:
            ftp.close()
        except OSError:
            pass


def run_ftp_probe_invalid(settings: RuntimeSettings) -> ProbeOutcome:
    ftp = ftplib.FTP()
    started_at = time.perf_counter_ns()
    try:
        greeting = ftp.connect(settings.host, settings.ftp_port, timeout=8)
        if not greeting.startswith("220"):
            raise RuntimeError(f"expected FTP 220, got {greeting}")
        login = ftp.login(settings.ftp_user, settings.ftp_pass)
        if not login.startswith("230"):
            raise RuntimeError(f"expected FTP 230, got {login}")
        try:
            response = ftp.sendcmd("VIVIPI-WRONG")
        except ftplib.Error as error:
            response = str(error)
        if not response:
            raise RuntimeError("empty FTP invalid-command response")
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("OK", f"invalid_reply={response}", elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("FAIL", f"ftp failed: {error}", elapsed_ms)
    finally:
        try:
            ftp.close()
        except OSError:
            pass


PROBE_RUNNERS = {
    "ping": run_ping_probe,
    "http": run_http_probe,
    "ftp": run_ftp_probe,
    "telnet": run_telnet_probe,
}


def ping_check() -> None:
    outcome = run_ping_probe(build_runtime_settings_from_globals(), ProbeCorrectness.CORRECT)
    log_check("ping", outcome.result, outcome.detail, outcome.elapsed_ms)


def http_check() -> None:
    outcome = run_http_probe(build_runtime_settings_from_globals(), ProbeCorrectness.CORRECT)
    log_check("http", outcome.result, outcome.detail, outcome.elapsed_ms)


def telnet_check() -> None:
    outcome = run_telnet_probe(build_runtime_settings_from_globals(), ProbeCorrectness.CORRECT)
    log_check("telnet", outcome.result, outcome.detail, outcome.elapsed_ms)


def ftp_check() -> None:
    outcome = run_ftp_probe(build_runtime_settings_from_globals(), ProbeCorrectness.CORRECT)
    log_check("ftp", outcome.result, outcome.detail, outcome.elapsed_ms)


def build_runtime_settings(args: argparse.Namespace) -> RuntimeSettings:
    return RuntimeSettings(
        host=args.host,
        http_path=args.http_path,
        http_port=args.http_port,
        telnet_port=args.telnet_port,
        ftp_port=args.ftp_port,
        ftp_user=args.ftp_user,
        ftp_pass=args.ftp_pass,
        delay_ms=args.delay_ms,
        log_every=args.log_every,
        verbose=args.verbose,
    )


def build_runtime_settings_from_globals() -> RuntimeSettings:
    return RuntimeSettings(
        host=HOST,
        http_path=HTTP_PATH,
        http_port=HTTP_PORT,
        telnet_port=TELNET_PORT,
        ftp_port=FTP_PORT,
        ftp_user=FTP_USER,
        ftp_pass=FTP_PASS,
        delay_ms=INTER_CALL_DELAY_MS,
        log_every=LOG_EVERY_N_ITERATIONS,
        verbose=VERBOSE,
    )


def default_execution_config() -> ExecutionConfig:
    return ExecutionConfig(
        profile=None,
        probes=DEFAULT_PROBES,
        schedule=DEFAULT_SCHEDULE,
        runners=DEFAULT_RUNNERS,
        duration_s=None,
        probe_correctness={protocol: PROBE_CORRECTNESS_CHOICES[protocol][0] for protocol in DEFAULT_PROBES},
        uses_extended_flags=False,
        overrides=(),
    )


def profile_execution_config(profile: str) -> ExecutionConfig:
    if profile == PROFILE_SOAK:
        return ExecutionConfig(
            profile=PROFILE_SOAK,
            probes=DEFAULT_PROBES,
            schedule=SCHEDULE_SEQUENTIAL,
            runners=1,
            duration_s=DEFAULT_PROFILE_DURATION_S,
            probe_correctness={protocol: PROBE_CORRECTNESS_CHOICES[protocol][0] for protocol in DEFAULT_PROBES},
            uses_extended_flags=True,
            overrides=(),
        )
    if profile == PROFILE_STRESS:
        return ExecutionConfig(
            profile=PROFILE_STRESS,
            probes=DEFAULT_PROBES,
            schedule=SCHEDULE_CONCURRENT,
            runners=4,
            duration_s=DEFAULT_PROFILE_DURATION_S,
            probe_correctness={
                "ping": ProbeCorrectness.CORRECT,
                "http": ProbeCorrectness.CORRECT,
                "ftp": ProbeCorrectness.INCOMPLETE,
                "telnet": ProbeCorrectness.INCOMPLETE,
            },
            uses_extended_flags=True,
            overrides=(),
        )
    raise ValueError(f"unsupported profile: {profile}")


def uses_extended_flags(args: argparse.Namespace) -> bool:
    return any(getattr(args, name) is not None for name in NEW_FEATURE_ARGUMENT_NAMES)


def resolve_execution_config(args: argparse.Namespace) -> ExecutionConfig:
    if args.profile is None:
        resolved = default_execution_config()
    else:
        resolved = profile_execution_config(args.profile)

    probe_correctness = dict(resolved.probe_correctness)
    overrides: list[str] = []

    if args.probes is not None:
        resolved = ExecutionConfig(
            profile=resolved.profile,
            probes=args.probes,
            schedule=resolved.schedule,
            runners=resolved.runners,
            duration_s=resolved.duration_s,
            probe_correctness=probe_correctness,
            uses_extended_flags=True,
            overrides=resolved.overrides,
        )
        overrides.append("probes")
    if args.schedule is not None:
        resolved = ExecutionConfig(
            profile=resolved.profile,
            probes=resolved.probes,
            schedule=args.schedule,
            runners=resolved.runners,
            duration_s=resolved.duration_s,
            probe_correctness=probe_correctness,
            uses_extended_flags=True,
            overrides=resolved.overrides,
        )
        overrides.append("schedule")
    if args.runners is not None:
        resolved = ExecutionConfig(
            profile=resolved.profile,
            probes=resolved.probes,
            schedule=resolved.schedule,
            runners=args.runners,
            duration_s=resolved.duration_s,
            probe_correctness=probe_correctness,
            uses_extended_flags=True,
            overrides=resolved.overrides,
        )
        overrides.append("runners")
    if args.duration_s is not None:
        resolved = ExecutionConfig(
            profile=resolved.profile,
            probes=resolved.probes,
            schedule=resolved.schedule,
            runners=resolved.runners,
            duration_s=args.duration_s,
            probe_correctness=probe_correctness,
            uses_extended_flags=True,
            overrides=resolved.overrides,
        )
        overrides.append("duration-s")

    mode_arguments = {
        "ping": args.ping_mode,
        "http": args.http_mode,
        "ftp": args.ftp_mode,
        "telnet": args.telnet_mode,
    }
    for protocol, raw_value in mode_arguments.items():
        if raw_value is None:
            continue
        probe_correctness[protocol] = ProbeCorrectness(raw_value)
        overrides.append(f"{protocol}-mode")

    return ExecutionConfig(
        profile=resolved.profile,
        probes=resolved.probes,
        schedule=resolved.schedule,
        runners=resolved.runners,
        duration_s=resolved.duration_s,
        probe_correctness=probe_correctness,
        uses_extended_flags=True,
        overrides=tuple(overrides),
    )


def log_startup() -> None:
    try:
        print(
            f'{ts()} protocol=config result=INFO detail="host={HOST} http={HTTP_PORT}/{HTTP_PATH} '
            f'telnet={TELNET_PORT} ftp={FTP_PORT} user={FTP_USER} sample_every={LOG_EVERY_N_ITERATIONS} '
            f'verbose={int(VERBOSE)} call_gap_ms={INTER_CALL_DELAY_MS}"',
            flush=True,
        )
    except BrokenPipeError:
        raise SystemExit(0)


def log_resolved_startup(settings: RuntimeSettings, config: ExecutionConfig) -> None:
    profile_name = config.profile or "custom"
    parts = [
        f"host={settings.host}",
        f"http={settings.http_port}/{settings.http_path}",
        f"telnet={settings.telnet_port}",
        f"ftp={settings.ftp_port}",
        f"user={settings.ftp_user}",
        f"sample_every={settings.log_every}",
        f"verbose={int(settings.verbose)}",
        f"call_gap_ms={settings.delay_ms}",
        f"profile={profile_name}",
        f"probes={','.join(config.probes)}",
        f"schedule={config.schedule}",
        f"runners={config.runners}",
        f"duration_s={config.duration_s if config.duration_s is not None else 'infinite'}",
        "correctness=" + ",".join(f"{protocol}:{config.probe_correctness[protocol].value}" for protocol in DEFAULT_PROBES),
    ]
    if config.profile is not None and config.overrides:
        parts.append(f"overrides={','.join(config.overrides)}")
    try:
        print(f'{ts()} protocol=config result=INFO detail="{' '.join(parts)}"', flush=True)
    except BrokenPipeError:
        raise SystemExit(0)


def execute_probe(protocol: str, settings: RuntimeSettings, config: ExecutionConfig, probe_runners: dict[str, callable] | None = None) -> ProbeOutcome:
    runners = PROBE_RUNNERS if probe_runners is None else probe_runners
    return runners[protocol](settings, config.probe_correctness[protocol])


def execute_probe_safely(
    protocol: str,
    settings: RuntimeSettings,
    config: ExecutionConfig,
    probe_runners: dict[str, callable] | None = None,
) -> ProbeOutcome:
    started_at = time.perf_counter_ns()
    try:
        return execute_probe(protocol, settings, config, probe_runners)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("FAIL", f"{protocol} failed: {error}", elapsed_ms)


def run_runner_iteration(
    runner_id: int,
    iteration: int,
    config: ExecutionConfig,
    settings: RuntimeSettings,
    state: ExecutionState,
    *,
    sleep_fn=sleep_ms,
    probe_runners: dict[str, callable] | None = None,
) -> tuple[tuple[str, ProbeOutcome], ...]:
    if config.schedule == SCHEDULE_SEQUENTIAL:
        results: list[tuple[str, ProbeOutcome]] = []
        for index, protocol in enumerate(config.probes):
            outcome = execute_probe_safely(protocol, settings, config, probe_runners)
            results.append((protocol, outcome))
            state.emit_probe_outcome(protocol, outcome, iteration=iteration, runner_id=runner_id)
            if index < len(config.probes) - 1:
                sleep_fn(settings.delay_ms)
        return tuple(results)

    ordered_results: list[tuple[str, ProbeOutcome] | None] = [None] * len(config.probes)
    threads: list[threading.Thread] = []

    def worker(index: int, protocol: str) -> None:
        ordered_results[index] = (protocol, execute_probe_safely(protocol, settings, config, probe_runners))

    for index, protocol in enumerate(config.probes):
        thread = threading.Thread(target=worker, args=(index, protocol), daemon=False)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    results = tuple(item for item in ordered_results if item is not None)
    for protocol, outcome in results:
        state.emit_probe_outcome(protocol, outcome, iteration=iteration, runner_id=runner_id)
    return results


def run_runner_loop(
    runner_id: int,
    config: ExecutionConfig,
    settings: RuntimeSettings,
    state: ExecutionState,
    stop_event: threading.Event,
    *,
    sleep_fn=sleep_ms,
    probe_runners: dict[str, callable] | None = None,
    max_iterations: int | None = None,
    deadline_s: float | None = None,
) -> int:
    started_at = time.time()
    iteration = 0
    while not stop_event.is_set():
        if deadline_s is not None and time.monotonic() >= deadline_s:
            stop_event.set()
            return iteration
        iteration += 1
        run_runner_iteration(
            runner_id,
            iteration,
            config,
            settings,
            state,
            sleep_fn=sleep_fn,
            probe_runners=probe_runners,
        )
        state.emit_iteration_summary(started_at, iteration, runner_id)
        if max_iterations is not None and iteration >= max_iterations:
            return iteration
        if deadline_s is not None and time.monotonic() >= deadline_s:
            stop_event.set()
            return iteration
        sleep_fn(settings.delay_ms)
    return iteration


def run_extended(config: ExecutionConfig, settings: RuntimeSettings) -> int:
    state = ExecutionState(settings=settings, include_runner_context=True)
    stop_event = threading.Event()
    deadline_s = None if config.duration_s is None else time.monotonic() + config.duration_s
    if config.runners == 1:
        run_runner_loop(1, config, settings, state, stop_event, deadline_s=deadline_s)
        return 0
    threads: list[threading.Thread] = []
    for runner_id in range(1, config.runners + 1):
        thread = threading.Thread(
            target=run_runner_loop,
            args=(runner_id, config, settings, state, stop_event),
            kwargs={"deadline_s": deadline_s},
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    while True:
        for thread in threads:
            thread.join(timeout=0.2)
        if not any(thread.is_alive() for thread in threads):
            return 0


def run_legacy(settings: RuntimeSettings) -> int:
    global HOST, HTTP_PATH, HTTP_PORT, TELNET_PORT, FTP_PORT, FTP_USER, FTP_PASS, INTER_CALL_DELAY_MS, LOG_EVERY_N_ITERATIONS, VERBOSE, CURRENT_ITERATION
    HOST = settings.host
    HTTP_PATH = settings.http_path
    HTTP_PORT = settings.http_port
    TELNET_PORT = settings.telnet_port
    FTP_PORT = settings.ftp_port
    FTP_USER = settings.ftp_user
    FTP_PASS = settings.ftp_pass
    INTER_CALL_DELAY_MS = settings.delay_ms
    LOG_EVERY_N_ITERATIONS = settings.log_every
    VERBOSE = settings.verbose

    started_at = time.time()
    iteration = 0
    log_startup()
    while True:
        iteration += 1
        CURRENT_ITERATION = iteration
        ping_check()
        sleep_ms(INTER_CALL_DELAY_MS)
        http_check()
        sleep_ms(INTER_CALL_DELAY_MS)
        ftp_check()
        sleep_ms(INTER_CALL_DELAY_MS)
        telnet_check()
        log_iteration_summary(started_at, iteration)
        sleep_ms(INTER_CALL_DELAY_MS)


def has_explicit_host(argv: list[str]) -> bool:
    return any(argument in {"-H", "--host"} or argument.startswith("--host=") for argument in argv)


def apply_profile_runtime_defaults(settings: RuntimeSettings, config: ExecutionConfig, argv: list[str]) -> RuntimeSettings:
    if config.profile is None:
        return settings
    if has_explicit_host(argv):
        return settings
    return replace(settings, host=DEFAULT_PROFILE_HOST)


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = build_runtime_settings(args)
    if not uses_extended_flags(args):
        return run_legacy(settings)
    config = resolve_execution_config(args)
    settings = apply_profile_runtime_defaults(settings, config, argv)
    log_resolved_startup(settings, config)
    return run_extended(config, settings)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(0)