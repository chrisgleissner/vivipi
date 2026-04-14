#!/usr/bin/env python3

import atexit
import argparse
import enum
import ftplib
import http.client
import io
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field, replace


HOST = os.getenv("HOST", "192.168.1.13")
HTTP_PATH = os.getenv("HTTP_PATH", "v1/version")
HTTP_PORT = int(os.getenv("HTTP_PORT", "80"))
TELNET_PORT = int(os.getenv("TELNET_PORT", "23"))
FTP_PORT = int(os.getenv("FTP_PORT", "21"))
FTP_USER = os.getenv("FTP_USER", "anonymous")
FTP_PASS = os.getenv("FTP_PASS", "")
INTER_CALL_DELAY_MS = int(os.getenv("INTER_CALL_DELAY_MS", "0"))
LOG_EVERY_N_ITERATIONS = int(os.getenv("LOG_EVERY_N_ITERATIONS", "10"))
CURRENT_ITERATION = 0
LATENCY_SAMPLES = {"ping": [], "http": [], "ftp": [], "telnet": []}
TELNET_IDLE_TIMEOUT_S = 0.20
TELNET_MAX_EMPTY_READS = 3
SURFACE_OPERATION_RETRY_DELAYS_S = (0.05, 0.10, 0.20)
IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240
FTP_TEMP_DIR = "/Temp"
FTP_SELF_FILE_PREFIX = "u64test_"
HTTP_AUDIO_MIXER_CATEGORY_PATH = "/v1/configs/Audio%20Mixer"
HTTP_VOLUME_ULTISID_1_PATH = f"{HTTP_AUDIO_MIXER_CATEGORY_PATH}/Vol%20UltiSid%201"
AUDIO_MIXER_WRITE_ITEM = "Vol UltiSid 1"
AUDIO_MIXER_WRITE_TARGET_VALUES = ("0 dB", "+1 dB")
AUDIO_MIXER_WRITE_VALUE_PATTERN = re.compile(r"Vol UltiSid 1\s+(OFF|[+-]?\d+ dB|\d+ dB)")
TELNET_KEY_F2 = b"\x1b[12~"
TELNET_KEY_DOWN = b"\x1b[B"
TELNET_KEY_LEFT = b"\x1b[D"
TELNET_KEY_RIGHT = b"\x1b[C"
TELNET_KEY_ESC = b"\x1b"
TELNET_KEY_ENTER = b"\r"
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


class ProbeSurface(enum.StrEnum):
    SMOKE = "smoke"
    READ = "read"
    READWRITE = "readwrite"


PROBE_SURFACE_CHOICES = {
    "ping": (ProbeSurface.SMOKE,),
    "http": (ProbeSurface.SMOKE, ProbeSurface.READ, ProbeSurface.READWRITE),
    "ftp": (ProbeSurface.SMOKE, ProbeSurface.READ, ProbeSurface.READWRITE),
    "telnet": (ProbeSurface.SMOKE, ProbeSurface.READ, ProbeSurface.READWRITE),
}
PROBE_SURFACE_ORDER = (ProbeSurface.SMOKE, ProbeSurface.READ, ProbeSurface.READWRITE)
PROBE_CORRECTNESS_ORDER = (ProbeCorrectness.CORRECT, ProbeCorrectness.INCOMPLETE, ProbeCorrectness.INVALID)


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
            "summary": "Historical Pico-side FTP probe verified only the greeting path, then sent QUIT without login, PWD, PASV, or NLST.",
        }
    },
    "telnet": {
        ProbeCorrectness.INCOMPLETE.value: {
            "commit": "37314b1",
            "path": "src/vivipi/runtime/checks.py",
            "summary": "Historical Pico-side Telnet runner performed a single initial read and classified login failure, banner-ready output, or a quiet connected session.",
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
    "surface",
    "mode",
    "http_surface",
    "ftp_surface",
    "telnet_surface",
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
    probe_surfaces: dict[str, ProbeSurface] = field(default_factory=lambda: {protocol: ProbeSurface.SMOKE for protocol in DEFAULT_PROBES})


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
    probe_selection_lock: threading.Lock = field(default_factory=threading.Lock)
    probe_operation_counts: dict[tuple[int, str, str], int] = field(default_factory=dict)

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

    def next_probe_operation_index(self, protocol: str, runner_id: int, surface: ProbeSurface, pool_size: int) -> int:
        if pool_size < 1:
            raise ValueError("pool_size must be >= 1")
        key = (runner_id, protocol, surface.value)
        with self.probe_selection_lock:
            counter = self.probe_operation_counts.get(key, 0)
            self.probe_operation_counts[key] = counter + 1
        return counter % pool_size

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


@dataclass(frozen=True)
class ProbeRuntimeContext:
    config: ExecutionConfig
    state: ExecutionState | None
    protocol: str
    runner_id: int
    iteration: int


@dataclass
class TelnetRunnerSession:
    sock: object
    view_state: str = "unknown"
    last_text: str = ""
    menu_focus: str = "unknown"


_PROBE_RUNTIME = threading.local()
_FTP_TRACKING_LOCK = threading.Lock()
_FTP_TRACKED_FILES: set[str] = set()
_FTP_SELF_FILE_COUNTER = 0
_FTP_CLEANUP_SETTINGS: RuntimeSettings | None = None
_FTP_CLEANUP_REGISTERED = False
_TELNET_SESSION_LOCK = threading.Lock()
_TELNET_RUNNER_SESSIONS: dict[int, TelnetRunnerSession] = {}
_TELNET_CLEANUP_REGISTERED = False


def _current_probe_context() -> ProbeRuntimeContext | None:
    return getattr(_PROBE_RUNTIME, "context", None)


def _set_probe_context(context: ProbeRuntimeContext) -> ProbeRuntimeContext | None:
    previous = _current_probe_context()
    _PROBE_RUNTIME.context = context
    return previous


def _restore_probe_context(previous: ProbeRuntimeContext | None) -> None:
    if previous is None:
        if hasattr(_PROBE_RUNTIME, "context"):
            delattr(_PROBE_RUNTIME, "context")
        return
    _PROBE_RUNTIME.context = previous


def _current_probe_surface(protocol: str) -> ProbeSurface:
    context = _current_probe_context()
    if context is None:
        return ProbeSurface.SMOKE
    return context.config.probe_surfaces.get(protocol, ProbeSurface.SMOKE)


def _select_probe_operation_index(protocol: str, surface: ProbeSurface, pool_size: int) -> int:
    context = _current_probe_context()
    if context is None or context.state is None:
        return 0
    return context.state.next_probe_operation_index(protocol, context.runner_id, surface, pool_size)


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


def _fallback_surface(protocol: str, requested: ProbeSurface) -> ProbeSurface:
    available = PROBE_SURFACE_CHOICES[protocol]
    if requested in available:
        return requested
    requested_index = PROBE_SURFACE_ORDER.index(requested)
    for candidate in reversed(PROBE_SURFACE_ORDER[: requested_index + 1]):
        if candidate in available:
            return candidate
    return available[0]


def _fallback_correctness(protocol: str, requested: ProbeCorrectness) -> ProbeCorrectness:
    available = PROBE_CORRECTNESS_CHOICES[protocol]
    if requested in available:
        return requested
    requested_index = PROBE_CORRECTNESS_ORDER.index(requested)
    for candidate in reversed(PROBE_CORRECTNESS_ORDER[: requested_index + 1]):
        if candidate in available:
            return candidate
    return available[0]


def profile_overrides_help() -> str:
    return (
        "Profile precedence: if --profile is supplied, explicit --probes, --schedule, --runners, --surface, --mode, --*-surface, and --*-mode values override the profile.\n\n"
        "Examples:\n"
        "  ./u64_connection_test.py\n"
        "  ./u64_connection_test.py --profile soak\n"
        "  ./u64_connection_test.py --profile stress\n"
        "  ./u64_connection_test.py --profile soak --duration-s 300\n"
        f"  ./u64_connection_test.py --surface {ProbeSurface.READWRITE.value}\n"
        f"  ./u64_connection_test.py --mode {ProbeCorrectness.INCOMPLETE.value}\n"
        "  ./u64_connection_test.py --probes ping,http,ftp,telnet\n"
        "  ./u64_connection_test.py --probes ping,http\n"
        "  ./u64_connection_test.py --schedule concurrent\n"
        "  ./u64_connection_test.py --schedule concurrent --runners 3\n"
        f"  ./u64_connection_test.py --http-surface {ProbeSurface.READ.value}\n"
        f"  ./u64_connection_test.py --ftp-surface {ProbeSurface.READWRITE.value} --telnet-surface {ProbeSurface.READ.value}\n"
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
        help="Preset profile. Explicit --probes, --schedule, --runners, --surface, --mode, --*-surface, and --*-mode flags override the profile.",
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
    parser.add_argument(
        "--surface",
        choices=[value.value for value in ProbeSurface],
        default=None,
        help="Apply the same surface to all probes, falling back per protocol to the nearest supported lower surface.",
    )
    parser.add_argument(
        "--mode",
        choices=[value.value for value in ProbeCorrectness],
        default=None,
        help="Apply the same correctness mode to all probes, falling back per protocol to the nearest supported lower mode.",
    )
    parser.add_argument("--http-surface", choices=[value.value for value in ProbeSurface], default=None, help="HTTP probe surface.")
    parser.add_argument("--ftp-surface", choices=[value.value for value in ProbeSurface], default=None, help="FTP probe surface.")
    parser.add_argument("--telnet-surface", choices=[value.value for value in ProbeSurface], default=None, help="Telnet probe surface.")
    parser.add_argument("--ping-mode", choices=[value.value for value in ProbeCorrectness], default=None, help="Ping probe correctness.")
    parser.add_argument("--http-mode", choices=[value.value for value in ProbeCorrectness], default=None, help="HTTP probe correctness.")
    parser.add_argument("--ftp-mode", choices=[value.value for value in ProbeCorrectness], default=None, help="FTP probe correctness.")
    parser.add_argument("--telnet-mode", choices=[value.value for value in ProbeCorrectness], default=None, help="Telnet probe correctness.")
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
    if (value > 0):
        time.sleep(value / 1000.0)


def _register_ftp_cleanup(settings: RuntimeSettings) -> None:
    global _FTP_CLEANUP_REGISTERED, _FTP_CLEANUP_SETTINGS
    with _FTP_TRACKING_LOCK:
        _FTP_CLEANUP_SETTINGS = settings
        if not _FTP_CLEANUP_REGISTERED:
            atexit.register(_cleanup_ftp_self_files)
            _FTP_CLEANUP_REGISTERED = True


def _register_telnet_cleanup() -> None:
    global _TELNET_CLEANUP_REGISTERED
    with _TELNET_SESSION_LOCK:
        if not _TELNET_CLEANUP_REGISTERED:
            atexit.register(_cleanup_telnet_sessions)
            _TELNET_CLEANUP_REGISTERED = True


def _track_ftp_self_file(settings: RuntimeSettings, path: str) -> None:
    _register_ftp_cleanup(settings)
    with _FTP_TRACKING_LOCK:
        _FTP_TRACKED_FILES.add(path)


def _forget_ftp_self_file(path: str) -> None:
    with _FTP_TRACKING_LOCK:
        _FTP_TRACKED_FILES.discard(path)


def _known_ftp_self_files() -> tuple[str, ...]:
    with _FTP_TRACKING_LOCK:
        return tuple(sorted(_FTP_TRACKED_FILES))


def _next_ftp_self_file_path() -> str:
    global _FTP_SELF_FILE_COUNTER
    with _FTP_TRACKING_LOCK:
        _FTP_SELF_FILE_COUNTER += 1
        counter = _FTP_SELF_FILE_COUNTER
    return f"{FTP_TEMP_DIR}/{FTP_SELF_FILE_PREFIX}{os.getpid()}_{counter}.txt"


def _cleanup_ftp_self_files() -> None:
    settings = _FTP_CLEANUP_SETTINGS
    paths = _known_ftp_self_files()
    if settings is None or not paths:
        return
    ftp = ftplib.FTP()
    try:
        ftp.connect(settings.host, settings.ftp_port, timeout=8)
        ftp.login(settings.ftp_user, settings.ftp_pass)
        ftp.set_pasv(True)
        for path in paths:
            try:
                ftp.delete(path)
            except Exception:
                continue
            _forget_ftp_self_file(path)
        try:
            ftp.quit()
        except Exception:
            pass
    except Exception:
        return
    finally:
        try:
            ftp.close()
        except OSError:
            pass


def _close_telnet_socket(sock) -> None:
    try:
        sock.close()
    except OSError:
        pass


def _cleanup_telnet_sessions() -> None:
    with _TELNET_SESSION_LOCK:
        sessions = tuple(_TELNET_RUNNER_SESSIONS.values())
        _TELNET_RUNNER_SESSIONS.clear()
    for session in sessions:
        _close_telnet_socket(session.sock)


def _drop_telnet_session(runner_id: int) -> None:
    with _TELNET_SESSION_LOCK:
        session = _TELNET_RUNNER_SESSIONS.pop(runner_id, None)
    if session is not None:
        _close_telnet_socket(session.sock)


def _peek_telnet_session(runner_id: int):
    with _TELNET_SESSION_LOCK:
        return _TELNET_RUNNER_SESSIONS.get(runner_id)


def _get_telnet_session(settings: RuntimeSettings, runner_id: int):
    with _TELNET_SESSION_LOCK:
        existing = _TELNET_RUNNER_SESSIONS.get(runner_id)
    if existing is not None:
        return existing
    sock = _telnet_connect(settings)
    session = TelnetRunnerSession(sock=sock)
    _register_telnet_cleanup()
    with _TELNET_SESSION_LOCK:
        existing = _TELNET_RUNNER_SESSIONS.get(runner_id)
        if existing is not None:
            _close_telnet_socket(sock)
            return existing
        _TELNET_RUNNER_SESSIONS[runner_id] = session
    return session


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


def _normalize_telnet_text(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def _strip_telnet_vt_text(value: bytes) -> str:
    text = value.decode("utf-8", "ignore")
    cleaned: list[str] = []
    index = 0
    alt_charset = False
    while index < len(text):
        char = text[index]
        if char == "\x1b":
            if index + 1 >= len(text):
                break
            next_char = text[index + 1]
            if next_char == "[":
                index += 2
                while index < len(text) and not ("@" <= text[index] <= "~"):
                    index += 1
                index += 1
                continue
            if next_char == "(":
                if index + 2 < len(text):
                    alt_charset = text[index + 2] == "0"
                    index += 3
                    continue
                break
            if next_char == "c":
                index += 2
                continue
            index += 2
            continue
        if ord(char) < 32:
            cleaned.append(" ")
            index += 1
            continue
        if alt_charset and char in {"l", "k", "m", "j", "x", "q", "t", "u", "v", "w", "n"}:
            cleaned.append(" ")
            index += 1
            continue
        cleaned.append(char)
        index += 1
    return " ".join("".join(cleaned).split())


def _surface_detail(surface: ProbeSurface, op_name: str, detail: str) -> str:
    if detail:
        return f"surface={surface.value} op={op_name} {detail}"
    return f"surface={surface.value} op={op_name}"


def _is_retryable_surface_error(error: Exception) -> bool:
    if isinstance(error, (ConnectionResetError, BrokenPipeError, TimeoutError, socket.timeout)):
        return True
    if isinstance(error, OSError) and getattr(error, "errno", None) in {104, 110, 111}:
        return True
    if isinstance(error, RuntimeError):
        detail = str(error).lower()
        return "empty telnet text" in detail or "timed out" in detail or "missing audio mixer write value" in detail
    return False


def _is_expected_incomplete_disconnect(error: Exception) -> bool:
    if isinstance(error, (ConnectionResetError, BrokenPipeError)):
        return True
    if isinstance(error, OSError) and getattr(error, "errno", None) == 104:
        return True
    detail = str(error).lower()
    return "connection reset by peer" in detail or "broken pipe" in detail


def _run_incomplete_surface_operation(protocol: str, surface: ProbeSurface, op_name: str, operation: callable, settings: RuntimeSettings) -> ProbeOutcome:
    started_at = time.perf_counter_ns()
    try:
        detail = _run_surface_operation(protocol, operation, settings)
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("OK", _surface_detail(surface, op_name, detail), elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        if _is_expected_incomplete_disconnect(error):
            return ProbeOutcome("OK", _surface_detail(surface, op_name, "expected_disconnect_after_abort"), elapsed_ms)
        return ProbeOutcome("FAIL", _surface_detail(surface, op_name, str(error)), elapsed_ms)


def _run_surface_operation(protocol: str, operation: callable, *args):
    attempts = len(SURFACE_OPERATION_RETRY_DELAYS_S) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation(*args)
        except Exception as error:
            last_error = error
            if not _is_retryable_surface_error(error) or attempt + 1 >= attempts:
                raise
            time.sleep(SURFACE_OPERATION_RETRY_DELAYS_S[attempt])
    raise RuntimeError(f"{protocol} surface operation failed without error") from last_error


def _http_request_bytes(settings: RuntimeSettings, method: str, path: str) -> tuple[int, bytes, dict[str, str]]:
    conn = http.client.HTTPConnection(settings.host, settings.http_port, timeout=3)
    try:
        conn.request(method, path, headers={"Connection": "close"})
        response = conn.getresponse()
        body = response.read()
        headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, body, headers
    finally:
        conn.close()


def _http_json_request(settings: RuntimeSettings, method: str, path: str) -> tuple[int, object, int]:
    status, body, _headers = _http_request_bytes(settings, method, path)
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


def _http_safe_read(settings: RuntimeSettings, path: str) -> str:
    status, body, headers = _http_request_bytes(settings, "GET", path)
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


def _http_extract_first_byte(payload: object) -> int | None:
    if isinstance(payload, dict):
        if "data" in payload:
            return _http_extract_first_byte(payload["data"])
        if "value" in payload:
            return _http_extract_first_byte(payload["value"])
        return None
    if isinstance(payload, int):
        return payload & 0xFF
    if isinstance(payload, list):
        if not payload:
            return None
        return _http_extract_first_byte(payload[0])
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


def _http_generic_read(settings: RuntimeSettings, path: str) -> str:
    return _http_safe_read(settings, path)


def _normalize_audio_mixer_value(value: str) -> str:
    return " ".join(value.split())


def _resolve_audio_mixer_value(values: tuple[str, ...], target: str) -> str:
    normalized_target = _normalize_audio_mixer_value(target)
    for value in values:
        if _normalize_audio_mixer_value(value) == normalized_target:
            return value
    raise RuntimeError(f"unsupported target value: {target}")


def _http_audio_mixer_item_state(settings: RuntimeSettings) -> tuple[str, tuple[str, ...], int]:
    status, payload, body_bytes = _http_json_request(settings, "GET", HTTP_VOLUME_ULTISID_1_PATH)
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


def _http_read_audio_mixer_item(settings: RuntimeSettings) -> str:
    current, values, body_bytes = _http_audio_mixer_item_state(settings)
    return f"body_bytes={body_bytes} current={_normalize_audio_mixer_value(current)} options={len(values)}"


def _http_write_audio_mixer_item(settings: RuntimeSettings, target: str) -> str:
    current, values, _body_bytes = _http_audio_mixer_item_state(settings)
    resolved_target = _resolve_audio_mixer_value(values, target)
    if current != resolved_target:
        encoded_target = urllib.parse.quote(resolved_target, safe="")
        status, _body, _headers = _http_request_bytes(settings, "PUT", f"{HTTP_VOLUME_ULTISID_1_PATH}?value={encoded_target}")
        if not 200 <= status < 300:
            raise RuntimeError(f"expected HTTP 2xx, got {status}")
    updated, _updated_values, _body_bytes = _http_audio_mixer_item_state(settings)
    if _normalize_audio_mixer_value(updated) != _normalize_audio_mixer_value(resolved_target):
        raise RuntimeError(
            f"verification mismatch expected={_normalize_audio_mixer_value(resolved_target)} got={_normalize_audio_mixer_value(updated)}"
        )
    return f"from={_normalize_audio_mixer_value(current)} to={_normalize_audio_mixer_value(updated)}"


def _http_memory_read(settings: RuntimeSettings, address: str, length: int) -> str:
    status, body, _headers = _http_request_bytes(settings, "GET", f"/v1/machine:readmem?address={address}&length={length}")
    if not 200 <= status < 300:
        raise RuntimeError(f"expected HTTP 2xx, got {status}")
    if not body:
        raise RuntimeError("empty memory read body")
    expected_length = max(1, length)
    if len(body) < expected_length:
        raise RuntimeError(f"short memory read: expected at least {expected_length} bytes, got {len(body)}")
    return f"http_status={status} body_bytes={len(body)} byte=0x{body[0]:02X}"


def _http_memory_write_verify(settings: RuntimeSettings, address: str, data_hex: str) -> str:
    write_status, _body, _headers = _http_request_bytes(settings, "PUT", f"/v1/machine:writemem?address={address}&data={data_hex}")
    if not 200 <= write_status < 300:
        raise RuntimeError(f"expected HTTP 2xx, got {write_status}")
    read_status, read_body, _headers = _http_request_bytes(settings, "GET", f"/v1/machine:readmem?address={address}&length=1")
    if not 200 <= read_status < 300:
        raise RuntimeError(f"expected HTTP 2xx, got {read_status}")
    if len(read_body) < 1:
        raise RuntimeError("empty write verification body")
    value = read_body[0]
    expected = int(data_hex, 16)
    if value != expected:
        raise RuntimeError(f"verification mismatch expected=0x{expected:02X} got=0x{value:02X}")
    return f"http_status={write_status} verified=0x{value:02X}"


def _http_surface_operations(surface: ProbeSurface) -> tuple[tuple[str, callable], ...]:
    read_operations = (
        ("get_version", lambda settings: _http_generic_read(settings, "/v1/version")),
        ("get_info", lambda settings: _http_generic_read(settings, "/v1/info")),
        ("get_configs", lambda settings: _http_generic_read(settings, "/v1/configs")),
        ("get_config_audio_mixer", lambda settings: _http_generic_read(settings, HTTP_AUDIO_MIXER_CATEGORY_PATH)),
        ("get_vol_ultisid_1", lambda settings: _http_read_audio_mixer_item(settings)),
        ("get_drives", lambda settings: _http_generic_read(settings, "/v1/drives")),
        ("get_files_temp", lambda settings: _http_generic_read(settings, "/v1/files?path=/Temp")),
        ("mem_read_zero_page", lambda settings: _http_memory_read(settings, "0x0000", 16)),
        ("mem_read_screen_ram", lambda settings: _http_memory_read(settings, "0x0400", 16)),
        ("mem_read_io_area", lambda settings: _http_memory_read(settings, "0xD000", 16)),
        ("mem_read_debug_register", lambda settings: _http_memory_read(settings, "0xD7FF", 1)),
    )
    if surface == ProbeSurface.SMOKE:
        return (("get_version_smoke", lambda settings: _http_generic_read(settings, "/v1/version")),)
    if surface == ProbeSurface.READ:
        return read_operations
    return read_operations + (
        ("mem_write_screen_space", lambda settings: _http_memory_write_verify(settings, "0x0400", "20")),
        ("mem_write_screen_exclam", lambda settings: _http_memory_write_verify(settings, "0x0400", "21")),
        ("set_vol_ultisid_1_0_db", lambda settings: _http_write_audio_mixer_item(settings, "0 dB")),
        ("set_vol_ultisid_1_plus_1_db", lambda settings: _http_write_audio_mixer_item(settings, "+1 dB")),
    )


def _ftp_connect(settings: RuntimeSettings) -> ftplib.FTP:
    ftp = ftplib.FTP()
    greeting = ftp.connect(settings.host, settings.ftp_port, timeout=3)
    if not greeting.startswith("220"):
        raise RuntimeError(f"expected FTP 220, got {greeting}")
    login = ftp.login(settings.ftp_user, settings.ftp_pass)
    if not login.startswith("230"):
        raise RuntimeError(f"expected FTP 230, got {login}")
    ftp.set_pasv(True)
    return ftp


def _ftp_close(ftp: ftplib.FTP) -> None:
    try:
        ftp.quit()
    except Exception:
        pass
    finally:
        try:
            ftp.close()
        except OSError:
            pass


def _ftp_collect_temp_entries(ftp: ftplib.FTP) -> tuple[str, ...]:
    try:
        return tuple(ftp.nlst(FTP_TEMP_DIR))
    except ftplib.Error as error:
        raise RuntimeError(f"{FTP_TEMP_DIR} missing or unavailable: {error}") from error


def _ftp_collect_temp_entries_if_available(ftp: ftplib.FTP) -> tuple[str, ...]:
    try:
        return _ftp_collect_temp_entries(ftp)
    except RuntimeError:
        return ()


def _ftp_readable_self_files(entries: tuple[str, ...]) -> tuple[str, ...]:
    candidates = []
    for entry in entries:
        basename = entry.rsplit("/", 1)[-1]
        if basename.startswith(FTP_SELF_FILE_PREFIX):
            if "/" not in entry:
                candidates.append(f"{FTP_TEMP_DIR}/{entry}")
            else:
                candidates.append(entry)
    return tuple(sorted(candidates))


def _ftp_pick_known_self_file(entries: tuple[str, ...]) -> str | None:
    readable = _ftp_readable_self_files(entries)
    if readable:
        return readable[0]
    owned = _known_ftp_self_files()
    if owned:
        return owned[0]
    return None


def _ftp_retr_binary(ftp: ftplib.FTP, path: str) -> int:
    buffer = bytearray()
    ftp.retrbinary(f"RETR {path}", buffer.extend)
    return len(buffer)


def _ftp_list_lines(ftp: ftplib.FTP, path: str) -> int:
    lines: list[str] = []
    ftp.retrlines(f"LIST {path}", lines.append)
    return len(lines)


def _ftp_seed_self_file(settings: RuntimeSettings, ftp: ftplib.FTP, ordinal: int) -> str:
    path = _next_ftp_self_file_path()
    payload_bytes = f"{FTP_SELF_FILE_PREFIX}{os.getpid()}_{ordinal}\n".encode("utf-8")
    payload = io.BytesIO(payload_bytes)
    ftp.storbinary(f"STOR {path}", payload)
    _track_ftp_self_file(settings, path)
    return path


def _ftp_ensure_small_self_files(settings: RuntimeSettings, ftp: ftplib.FTP, minimum_count: int = 2) -> tuple[str, ...]:
    readable = list(_ftp_readable_self_files(_ftp_collect_temp_entries_if_available(ftp)))
    for path in readable:
        _track_ftp_self_file(settings, path)
    while len(readable) < minimum_count:
        readable.append(_ftp_seed_self_file(settings, ftp, len(readable) + 1))
    return tuple(sorted(readable))


def _ftp_prime_temp_dir(settings: RuntimeSettings, minimum_count: int = 1) -> tuple[str, ...]:
    ftp = _ftp_connect(settings)
    try:
        seeded_paths = []
        for ordinal in range(1, minimum_count + 1):
            seeded_paths.append(_ftp_seed_self_file(settings, ftp, ordinal))
        return tuple(seeded_paths)
    finally:
        _ftp_close(ftp)


def _try_ftp_prime_temp_dir(settings: RuntimeSettings, minimum_count: int = 1) -> tuple[str, ...]:
    try:
        return _ftp_prime_temp_dir(settings, minimum_count=minimum_count)
    except Exception as error:
        log("ftp", "INFO", f"prime_temp_dir_failed detail={error} continuing=1")
        return ()


def _ftp_list_temp_entries(settings: RuntimeSettings, ftp: ftplib.FTP) -> str:
    del settings
    entries = _ftp_collect_temp_entries(ftp)
    return f"entries={len(entries)} path={FTP_TEMP_DIR}"


def _ftp_read_small_self_file(settings: RuntimeSettings, ftp: ftplib.FTP, index: int) -> str:
    readable = _ftp_ensure_small_self_files(settings, ftp, minimum_count=index + 1)
    path = readable[index]
    byte_count = _ftp_retr_binary(ftp, path)
    if byte_count < 1:
        raise RuntimeError(f"empty FTP self file: {path}")
    return f"path={path} bytes={byte_count}"


def _ftp_create_self_file(settings: RuntimeSettings, ftp: ftplib.FTP) -> str:
    path = _next_ftp_self_file_path()
    payload = io.BytesIO(f"{FTP_SELF_FILE_PREFIX}{os.getpid()}\n".encode("utf-8"))
    ftp.storbinary(f"STOR {path}", payload)
    _track_ftp_self_file(settings, path)
    return f"path={path} bytes={payload.getbuffer().nbytes}"


def _ftp_rename_self_file(settings: RuntimeSettings, ftp: ftplib.FTP) -> str:
    owned = _known_ftp_self_files()
    if not owned:
        return "skip=no_self_file"
    source = owned[0]
    target = _next_ftp_self_file_path()
    ftp.rename(source, target)
    _forget_ftp_self_file(source)
    _track_ftp_self_file(settings, target)
    return f"from={source} to={target}"


def _ftp_delete_self_file(settings: RuntimeSettings, ftp: ftplib.FTP) -> str:
    del settings
    owned = _known_ftp_self_files()
    if not owned:
        return "skip=no_self_file"
    path = owned[0]
    ftp.delete(path)
    _forget_ftp_self_file(path)
    return f"path={path}"


def _ftp_pasv_only_abort(settings: RuntimeSettings) -> str:
    ftp = _ftp_connect(settings)
    try:
        response = ftp.sendcmd("PASV")
        if not response.startswith("227"):
            raise RuntimeError(f"expected FTP 227, got {response}")
        return f"reply={response.split(' ', 1)[0]}"
    finally:
        try:
            ftp.close()
        except OSError:
            pass


def _ftp_greeting_only_quit(settings: RuntimeSettings) -> str:
    ftp = ftplib.FTP()
    try:
        greeting = ftp.connect(settings.host, settings.ftp_port, timeout=3)
        if not greeting.startswith("220"):
            raise RuntimeError(f"expected FTP 220, got {greeting}")
        goodbye = ftp.quit()
        if not goodbye.startswith("221"):
            raise RuntimeError(f"expected FTP 221, got {goodbye}")
        return "ftp greeting ready"
    finally:
        try:
            ftp.close()
        except OSError:
            pass


def _ftp_login_only_abort(settings: RuntimeSettings) -> str:
    ftp = ftplib.FTP()
    try:
        greeting = ftp.connect(settings.host, settings.ftp_port, timeout=3)
        if not greeting.startswith("220"):
            raise RuntimeError(f"expected FTP 220, got {greeting}")
        login = ftp.login(settings.ftp_user, settings.ftp_pass)
        if not login.startswith("230"):
            raise RuntimeError(f"expected FTP 230, got {login}")
        return "phase=login_abort"
    finally:
        try:
            ftp.close()
        except OSError:
            pass


def _close_socket_quietly(sock) -> None:
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass


def _ftp_partial_transfer_abort(settings: RuntimeSettings, command: str, *, payload: bytes | None = None, read_limit: int = 64) -> str:
    ftp = _ftp_connect(settings)
    data_sock = None
    try:
        data_sock = ftp.transfercmd(command)
        if payload is None:
            chunk = data_sock.recv(read_limit)
            return f"command={command} bytes={len(chunk)}"
        data_sock.sendall(payload)
        return f"command={command} sent={len(payload)}"
    finally:
        _close_socket_quietly(data_sock)
        try:
            ftp.close()
        except OSError:
            pass


def _ftp_partial_stor_temp(settings: RuntimeSettings) -> str:
    path = _next_ftp_self_file_path()
    _track_ftp_self_file(settings, path)
    return _ftp_partial_transfer_abort(settings, f"STOR {path}", payload=b"vivipi-partial\n")


def _ftp_incomplete_operations(surface: ProbeSurface) -> tuple[tuple[str, callable], ...]:
    if surface == ProbeSurface.SMOKE:
        return (("ftp_greeting_only_quit", lambda settings: _ftp_greeting_only_quit(settings)),)
    operations = (
        ("ftp_pasv_only_abort", lambda settings: _ftp_pasv_only_abort(settings)),
        ("ftp_partial_list_root", lambda settings: _ftp_partial_transfer_abort(settings, "LIST .")),
        ("ftp_pasv_only_abort", lambda settings: _ftp_pasv_only_abort(settings)),
        ("ftp_partial_nlst_root", lambda settings: _ftp_partial_transfer_abort(settings, "NLST .")),
    )
    if surface == ProbeSurface.READ:
        return operations
    return operations + (
        ("ftp_partial_stor_temp", lambda settings: _ftp_partial_stor_temp(settings)),
        ("ftp_pasv_only_abort", lambda settings: _ftp_pasv_only_abort(settings)),
        ("ftp_partial_list_root", lambda settings: _ftp_partial_transfer_abort(settings, "LIST .")),
    )


def _ftp_surface_operations(surface: ProbeSurface) -> tuple[tuple[str, callable], ...]:
    read_operations = (
        ("ftp_pwd", lambda settings, ftp, entries: f"pwd={ftp.pwd()}"),
        ("ftp_nlst_root", lambda settings, ftp, entries: f"entries={len(tuple(ftp.nlst('.')))} path=."),
        ("ftp_list_root", lambda settings, ftp, entries: f"lines={_ftp_list_lines(ftp, '.')} path=."),
        ("ftp_nlst_temp", lambda settings, ftp, entries: _ftp_list_temp_entries(settings, ftp)),
    )
    if surface == ProbeSurface.SMOKE:
        return (("ftp_smoke_pwd", lambda settings, ftp, entries: f"pwd={ftp.pwd()}"),)
    if surface == ProbeSurface.READ:
        return read_operations
    return read_operations + (
        ("ftp_create_self_file", lambda settings, ftp, entries: _ftp_create_self_file(settings, ftp)),
        ("ftp_read_self_file", lambda settings, ftp, entries: _ftp_read_small_self_file(settings, ftp, 0)),
        ("ftp_rename_self_file", lambda settings, ftp, entries: _ftp_rename_self_file(settings, ftp)),
        ("ftp_delete_self_file", lambda settings, ftp, entries: _ftp_delete_self_file(settings, ftp)),
    )


def _telnet_connect(settings: RuntimeSettings):
    sock = socket.create_connection((settings.host, settings.telnet_port), timeout=2)
    sock.settimeout(TELNET_IDLE_TIMEOUT_S)
    return sock


def _telnet_read_until_idle(sock, *, max_empty_reads: int = TELNET_MAX_EMPTY_READS) -> str:
    visible = bytearray()
    empty_reads = 0
    while empty_reads < max_empty_reads:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            empty_reads += 1
            continue
        if not chunk:
            break
        empty_reads = 0
        visible.extend(_collect_telnet_visible(sock, chunk))
    text = _strip_telnet_vt_text(bytes(visible))
    if text and any(marker.decode("utf-8") in text.lower() for marker in TELNET_FAILURE_MARKERS):
        raise RuntimeError("telnet failure marker present")
    return text


def _telnet_require_text(text: str, *markers: str) -> str:
    if not text:
        raise RuntimeError("empty telnet text")
    lowered = text.lower()
    missing = [marker for marker in markers if marker.lower() not in lowered]
    if missing:
        raise RuntimeError(f"missing telnet text: {', '.join(missing)}")
    return text


def _telnet_open_menu(sock) -> str:
    _telnet_read_until_idle(sock)
    last_text = ""
    for _ in range(2):
        sock.sendall(TELNET_KEY_F2)
        text = _telnet_read_until_idle(sock)
        last_text = text
        lowered = text.lower()
        if "audio mixer" in lowered and "speaker settings" in lowered:
            return f"visible_bytes={len(text.encode())}"
    text = _telnet_require_text(last_text, "Audio Mixer", "Speaker Settings")
    return f"visible_bytes={len(text.encode())}"


def _telnet_banner(sock) -> str:
    initial_text = _telnet_read_until_idle(sock)
    if initial_text:
        return f"banner_bytes={len(_telnet_require_text(initial_text).encode())}"
    sock.sendall(b"\r\n")
    text = _telnet_require_text(_telnet_read_until_idle(sock))
    return f"banner_bytes={len(text.encode())}"


def _telnet_smoke_connect(sock) -> str:
    text = _telnet_read_until_idle(sock, max_empty_reads=1)
    if not text:
        return "connected"
    return f"visible_bytes={len(text.encode())}"


def _telnet_session_capture(session: TelnetRunnerSession, text: str, view_state: str | None = None) -> str:
    if text:
        session.last_text = text
    if view_state is not None:
        session.view_state = view_state
    return text


def _telnet_session_read(session: TelnetRunnerSession, *, max_empty_reads: int = 1, view_state: str | None = None) -> str:
    text = _telnet_read_until_idle(session.sock, max_empty_reads=max_empty_reads)
    return _telnet_session_capture(session, text, view_state=view_state)


def _telnet_session_send(session: TelnetRunnerSession, payload: bytes, *, view_state: str | None = None) -> str:
    session.sock.sendall(payload)
    return _telnet_session_read(session, max_empty_reads=1, view_state=view_state)


def _telnet_session_has_menu(session: TelnetRunnerSession) -> bool:
    lowered = session.last_text.lower()
    return "audio mixer" in lowered and "speaker settings" in lowered


def _telnet_session_has_audio_mixer(session: TelnetRunnerSession) -> bool:
    return "vol ultisid 1" in session.last_text.lower()


def _telnet_session_smoke_connect(session: TelnetRunnerSession) -> str:
    text = _telnet_session_read(session, max_empty_reads=1, view_state=session.view_state)
    if not text:
        if session.last_text:
            return f"visible_bytes={len(session.last_text.encode())}"
        session.view_state = "home"
        return "connected"
    return f"visible_bytes={len(text.encode())}"


def _telnet_session_open_menu(session: TelnetRunnerSession) -> str:
    if session.view_state == "menu" and _telnet_session_has_menu(session):
        return f"visible_bytes={len(session.last_text.encode())}"
    if session.view_state == "audio_mixer" and _telnet_session_has_audio_mixer(session):
        text = _telnet_session_send(session, TELNET_KEY_LEFT)
        text = _telnet_require_text(text, "Audio Mixer", "Speaker Settings")
        session.last_text = text
        session.view_state = "menu"
        session.menu_focus = "audio_mixer"
        return f"visible_bytes={len(text.encode())}"
    _telnet_session_read(session, max_empty_reads=1, view_state=session.view_state)
    last_text = session.last_text
    for _ in range(2):
        text = _telnet_session_send(session, TELNET_KEY_F2)
        last_text = text or last_text
        if text and _telnet_session_has_menu(session):
            session.view_state = "menu"
            session.menu_focus = "video_configuration"
            return f"visible_bytes={len(text.encode())}"
    text = _telnet_require_text(last_text, "Audio Mixer", "Speaker Settings")
    session.last_text = text
    session.view_state = "menu"
    session.menu_focus = "video_configuration"
    return f"visible_bytes={len(text.encode())}"


def _telnet_session_open_audio_mixer(session: TelnetRunnerSession) -> str:
    if session.view_state == "audio_mixer" and _telnet_session_has_audio_mixer(session):
        return session.last_text
    _telnet_session_open_menu(session)
    if session.menu_focus == "video_configuration":
        text = _telnet_session_send(session, TELNET_KEY_DOWN)
        _telnet_require_text(text, "Audio Mixer")
        session.view_state = "menu"
        session.menu_focus = "audio_mixer"
        text = _telnet_session_send(session, TELNET_KEY_ENTER)
    elif session.menu_focus == "audio_mixer":
        text = _telnet_session_send(session, TELNET_KEY_ENTER)
    else:
        text = _telnet_session_send(session, TELNET_KEY_ENTER)
        if "vol ultisid 1" not in text.lower():
            text = _telnet_session_send(session, TELNET_KEY_LEFT)
            text = _telnet_require_text(text, "Audio Mixer", "Speaker Settings")
            session.view_state = "menu"
            session.menu_focus = "video_configuration"
            text = _telnet_session_send(session, TELNET_KEY_DOWN)
            _telnet_require_text(text, "Audio Mixer")
            session.view_state = "menu"
            session.menu_focus = "audio_mixer"
            text = _telnet_session_send(session, TELNET_KEY_ENTER)
    text = _telnet_require_text(text, "Vol UltiSid 1")
    session.last_text = text
    session.view_state = "audio_mixer"
    return text


def _telnet_session_refresh_audio_mixer(session: TelnetRunnerSession) -> str:
    if session.view_state == "audio_mixer" and _telnet_session_has_audio_mixer(session):
        _telnet_session_open_menu(session)
    return _telnet_session_open_audio_mixer(session)


def _telnet_session_extract_audio_mixer_value(session: TelnetRunnerSession, text: str) -> tuple[str, str]:
    try:
        return text, _telnet_extract_audio_mixer_write_value(text)
    except RuntimeError:
        tail = _telnet_session_read(session, max_empty_reads=2, view_state=session.view_state)
        combined = text + tail if tail else text
        try:
            session.last_text = combined
            return combined, _telnet_extract_audio_mixer_write_value(combined)
        except RuntimeError:
            session.view_state = "unknown"
            session.menu_focus = "unknown"
            reopened = _telnet_session_open_audio_mixer(session)
            return reopened, _telnet_extract_audio_mixer_write_value(reopened)


def _telnet_session_read_audio_mixer_item(session: TelnetRunnerSession) -> str:
    text = _telnet_session_refresh_audio_mixer(session)
    text, current = _telnet_session_extract_audio_mixer_value(session, text)
    return f"current={current}"


def _telnet_session_write_audio_mixer_item(settings: RuntimeSettings, session: TelnetRunnerSession, target: str) -> str:
    text = _telnet_session_refresh_audio_mixer(session)
    text, current = _telnet_session_extract_audio_mixer_value(session, text)
    steps = _telnet_audio_mixer_write_right_steps(settings, current, target)
    for _ in range(steps):
        text = _telnet_session_send(session, TELNET_KEY_RIGHT, view_state="audio_mixer")
    text, updated = _telnet_session_extract_audio_mixer_value(session, text)
    if updated != _normalize_audio_mixer_value(target):
        raise RuntimeError(f"verification mismatch expected={target} got={updated}")
    session.last_text = text
    session.view_state = "audio_mixer"
    return f"from={current} to={updated} right_steps={steps}"


def _telnet_abort_after_sequence(settings: RuntimeSettings, *payloads: bytes, read_initial: bool = True) -> str:
    sock = _telnet_connect(settings)
    try:
        if read_initial:
            _telnet_read_until_idle(sock, max_empty_reads=1)
        for payload in payloads:
            sock.sendall(payload)
        if not payloads:
            return "phase=connect_abort"
        return f"steps={len(payloads)} bytes={sum(len(payload) for payload in payloads)}"
    finally:
        _close_telnet_socket(sock)


def _telnet_initial_read_classify(settings: RuntimeSettings) -> str:
    sock = _telnet_connect(settings)
    try:
        try:
            initial_raw = sock.recv(4096)
        except socket.timeout:
            initial_raw = b""
        transcript = _collect_telnet_visible(sock, initial_raw) if initial_raw else b""
        if _contains_any(transcript, TELNET_FAILURE_MARKERS):
            raise RuntimeError("login failed")
        if transcript:
            cleaned = transcript.decode("utf-8", "replace")
            if _looks_like_telnet_output(cleaned):
                return "banner ready"
        return "connected"
    finally:
        _close_telnet_socket(sock)


def _telnet_incomplete_operations(surface: ProbeSurface) -> tuple[tuple[str, callable], ...]:
    if surface == ProbeSurface.SMOKE:
        return (("telnet_initial_read_classify", lambda settings: _telnet_initial_read_classify(settings)),)
    operations = (
        ("telnet_f2_abort", lambda settings: _telnet_abort_after_sequence(settings, TELNET_KEY_F2)),
        ("telnet_partial_f2_prefix_abort", lambda settings: _telnet_abort_after_sequence(settings, TELNET_KEY_F2[:2])),
    )
    if surface == ProbeSurface.READ:
        return operations
    return operations + (
        (
            "telnet_audio_mixer_abort",
            lambda settings: _telnet_abort_after_sequence(settings, TELNET_KEY_F2, TELNET_KEY_DOWN, TELNET_KEY_ENTER),
        ),
        ("telnet_right_arrow_abort", lambda settings: _telnet_abort_after_sequence(settings, TELNET_KEY_F2, TELNET_KEY_RIGHT)),
        ("telnet_f2_abort", lambda settings: _telnet_abort_after_sequence(settings, TELNET_KEY_F2)),
    )


def _telnet_reset_to_home(sock) -> None:
    for _ in range(2):
        try:
            sock.sendall(TELNET_KEY_ESC)
        except OSError:
            raise
        try:
            _telnet_read_until_idle(sock, max_empty_reads=1)
        except RuntimeError:
            continue


def _telnet_send_and_read(sock, payload: bytes, *, require_change: bool = False) -> str:
    before = _normalize_telnet_text(_telnet_read_until_idle(sock)) if require_change else ""
    last_text = ""
    for _ in range(2):
        sock.sendall(payload)
        text = _telnet_read_until_idle(sock)
        last_text = text
        if not require_change:
            return text
        if _normalize_telnet_text(text) and _normalize_telnet_text(text) != before:
            return text
    return last_text


def _telnet_nav_down(sock) -> str:
    _telnet_open_menu(sock)
    text = _telnet_send_and_read(sock, TELNET_KEY_DOWN)
    text = _telnet_send_and_read(sock, TELNET_KEY_DOWN) or text
    text = _telnet_require_text(text, "Audio Mixer", "Speaker Settings")
    return f"visible_bytes={len(text.encode())}"


def _telnet_open_audio_mixer(sock) -> str:
    _telnet_open_menu(sock)
    _telnet_send_and_read(sock, TELNET_KEY_DOWN, require_change=True)
    text = _telnet_send_and_read(sock, TELNET_KEY_ENTER)
    text = _telnet_require_text(text, "Vol UltiSid 1")
    return text


def _telnet_extract_audio_mixer_write_value(text: str) -> str:
    match = AUDIO_MIXER_WRITE_VALUE_PATTERN.search(text)
    if match is None:
        raise RuntimeError("missing Audio Mixer write value")
    return _normalize_audio_mixer_value(match.group(1))


def _telnet_focus_audio_mixer_write_item(sock) -> tuple[str, str]:
    text = _telnet_open_audio_mixer(sock)
    return text, _telnet_extract_audio_mixer_write_value(text)


def _telnet_audio_mixer_write_right_steps(settings: RuntimeSettings, current: str, target: str) -> int:
    _current_value, values, _body_bytes = _http_audio_mixer_item_state(settings)
    normalized_values = tuple(_normalize_audio_mixer_value(value) for value in values)
    normalized_current = _normalize_audio_mixer_value(current)
    normalized_target = _normalize_audio_mixer_value(target)
    if normalized_current not in normalized_values:
        raise RuntimeError(f"unsupported Audio Mixer write current value: {current}")
    if normalized_target not in normalized_values:
        raise RuntimeError(f"unsupported Audio Mixer write target value: {target}")
    current_index = normalized_values.index(normalized_current)
    target_index = normalized_values.index(normalized_target)
    return (target_index - current_index) % len(normalized_values)


def _telnet_read_audio_mixer_item(sock) -> str:
    _text, current = _telnet_focus_audio_mixer_write_item(sock)
    return f"current={current}"


def _telnet_write_audio_mixer_item(settings: RuntimeSettings, sock, target: str) -> str:
    text, current = _telnet_focus_audio_mixer_write_item(sock)
    steps = _telnet_audio_mixer_write_right_steps(settings, current, target)
    for _ in range(steps):
        text = _telnet_send_and_read(sock, TELNET_KEY_RIGHT, require_change=True)
    updated = _telnet_extract_audio_mixer_write_value(text)
    if updated != _normalize_audio_mixer_value(target):
        raise RuntimeError(f"verification mismatch expected={target} got={updated}")
    return f"from={current} to={updated} right_steps={steps}"


def _telnet_enter_speaker_settings(sock) -> str:
    _telnet_open_menu(sock)
    _telnet_send_and_read(sock, TELNET_KEY_DOWN)
    _telnet_send_and_read(sock, TELNET_KEY_DOWN)
    text = _telnet_send_and_read(sock, TELNET_KEY_ENTER)
    text = _telnet_require_text(text, "Speaker Enable")
    return f"visible_bytes={len(text.encode())}"


def _telnet_exit_menu(sock) -> str:
    _telnet_open_menu(sock)
    text = _telnet_send_and_read(sock, TELNET_KEY_LEFT)
    if "audio mixer" in text.lower() or "speaker settings" in text.lower():
        text = _telnet_send_and_read(sock, TELNET_KEY_ESC)
    if text:
        _telnet_require_text(text)
    return f"visible_bytes={len(text.encode())}"


def _telnet_surface_operations(surface: ProbeSurface) -> tuple[tuple[str, callable], ...]:
    read_operations = (
        ("telnet_smoke_connect", lambda settings, session: _telnet_session_smoke_connect(session)),
        ("telnet_open_menu", lambda settings, session: _telnet_session_open_menu(session)),
        ("telnet_open_audio_mixer", lambda settings, session: f"visible_bytes={len(_telnet_session_open_audio_mixer(session).encode())}"),
        ("telnet_read_vol_ultisid_1", lambda settings, session: _telnet_session_read_audio_mixer_item(session)),
    )
    if surface == ProbeSurface.SMOKE:
        return (("telnet_smoke_connect", lambda settings, session: _telnet_session_smoke_connect(session)),)
    if surface == ProbeSurface.READ:
        return read_operations
    return read_operations + (
        ("set_vol_ultisid_1_0_db", lambda settings, session: _telnet_session_write_audio_mixer_item(settings, session, "0 dB")),
        ("set_vol_ultisid_1_plus_1_db", lambda settings, session: _telnet_session_write_audio_mixer_item(settings, session, "+1 dB")),
    )


def _collect_telnet_visible(handle, chunk: bytes) -> bytes:
    visible = bytearray()
    index = 0
    while index < len(chunk):
        byte = chunk[index]
        if byte == IAC:
            if index + 1 >= len(chunk):
                break
            command = chunk[index + 1]
            if command == IAC:
                visible.append(IAC)
                index += 2
                continue
            if command in (DO, DONT, WILL, WONT):
                if index + 2 >= len(chunk):
                    break
                option = chunk[index + 2]
                reply = bytes([IAC, WONT if command in (DO, DONT) else DONT, option])
                handle.sendall(reply)
                index += 3
                continue
            if command == SB:
                index += 2
                while index + 1 < len(chunk):
                    if chunk[index] == IAC and chunk[index + 1] == SE:
                        index += 2
                        break
                    index += 1
                continue
            index += 2
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
    if _current_probe_context() is not None:
        surface = _current_probe_surface("http")
        operations = _http_surface_operations(surface)
        index = _select_probe_operation_index("http", surface, len(operations))
        op_name, operation = operations[index]
        started_at = time.perf_counter_ns()
        try:
            detail = _run_surface_operation("http", operation, settings)
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("OK", _surface_detail(surface, op_name, detail), elapsed_ms)
        except Exception as error:
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("FAIL", _surface_detail(surface, op_name, str(error)), elapsed_ms)
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
    if _current_probe_context() is not None:
        if correctness == ProbeCorrectness.INCOMPLETE:
            surface = _current_probe_surface("telnet")
            operations = _telnet_incomplete_operations(surface)
            index = _select_probe_operation_index("telnet", surface, len(operations))
            op_name, operation = operations[index]
            return _run_incomplete_surface_operation("telnet", surface, op_name, operation, settings)
        surface = _current_probe_surface("telnet")
        operations = _telnet_surface_operations(surface)
        index = _select_probe_operation_index("telnet", surface, len(operations))
        op_name, operation = operations[index]
        started_at = time.perf_counter_ns()
        context = _current_probe_context()
        runner_id = 0 if context is None else context.runner_id
        try:
            detail = None
            attempts = len(SURFACE_OPERATION_RETRY_DELAYS_S) + 1
            for attempt in range(attempts):
                try:
                    session = _get_telnet_session(settings, runner_id)
                    detail = operation(settings, session)
                    break
                except Exception as error:
                    _drop_telnet_session(runner_id)
                    if not _is_retryable_surface_error(error) or attempt + 1 >= attempts:
                        raise
                    time.sleep(SURFACE_OPERATION_RETRY_DELAYS_S[attempt])
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("OK", _surface_detail(surface, op_name, detail), elapsed_ms)
        except Exception as error:
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("FAIL", _surface_detail(surface, op_name, str(error)), elapsed_ms)
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
    started_at = time.perf_counter_ns()
    try:
        detail = _telnet_initial_read_classify(settings)
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        return ProbeOutcome("OK", detail, elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        if str(error) == "login failed":
            return ProbeOutcome("FAIL", "login failed", elapsed_ms)
        return ProbeOutcome("FAIL", f"telnet failed: {error}", elapsed_ms)


def run_ftp_probe(settings: RuntimeSettings, correctness: ProbeCorrectness) -> ProbeOutcome:
    if _current_probe_context() is not None:
        if correctness == ProbeCorrectness.INVALID:
            return run_ftp_probe_invalid(settings)
        surface = _current_probe_surface("ftp")
        if correctness == ProbeCorrectness.INCOMPLETE:
            operations = _ftp_incomplete_operations(surface)
            index = _select_probe_operation_index("ftp", surface, len(operations))
            op_name, operation = operations[index]
            return _run_incomplete_surface_operation("ftp", surface, op_name, operation, settings)
        operations = _ftp_surface_operations(surface)
        index = _select_probe_operation_index("ftp", surface, len(operations))
        op_name, operation = operations[index]
        started_at = time.perf_counter_ns()
        try:
            detail = None
            attempts = len(SURFACE_OPERATION_RETRY_DELAYS_S) + 1
            for attempt in range(attempts):
                ftp = None
                try:
                    ftp = _ftp_connect(settings)
                    entries = ()
                    detail = operation(settings, ftp, entries)
                    break
                except Exception as error:
                    if not _is_retryable_surface_error(error) or attempt + 1 >= attempts:
                        raise
                    time.sleep(SURFACE_OPERATION_RETRY_DELAYS_S[attempt])
                finally:
                    if ftp is not None:
                        _ftp_close(ftp)
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("OK", _surface_detail(surface, op_name, detail), elapsed_ms)
        except Exception as error:
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("FAIL", _surface_detail(surface, op_name, str(error)), elapsed_ms)
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
        probe_surfaces={protocol: ProbeSurface.SMOKE for protocol in DEFAULT_PROBES},
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
            probe_surfaces={
                "ping": ProbeSurface.SMOKE,
                "http": ProbeSurface.READ,
                "ftp": ProbeSurface.READ,
                "telnet": ProbeSurface.READ,
            },
        )
    if profile == PROFILE_STRESS:
        return ExecutionConfig(
            profile=PROFILE_STRESS,
            probes=("ftp", "telnet", "http", "ftp", "telnet", "ping"),
            schedule=SCHEDULE_CONCURRENT,
            runners=1,
            duration_s=DEFAULT_PROFILE_DURATION_S,
            probe_correctness={
                "ping": ProbeCorrectness.CORRECT,
                "http": ProbeCorrectness.CORRECT,
                "ftp": ProbeCorrectness.INCOMPLETE,
                "telnet": ProbeCorrectness.INCOMPLETE,
            },
            uses_extended_flags=True,
            overrides=(),
            probe_surfaces={
                "ping": ProbeSurface.SMOKE,
                "http": ProbeSurface.READWRITE,
                "ftp": ProbeSurface.READWRITE,
                "telnet": ProbeSurface.READWRITE,
            },
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
    probe_surfaces = dict(resolved.probe_surfaces)
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
            probe_surfaces=probe_surfaces,
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
            probe_surfaces=probe_surfaces,
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
            probe_surfaces=probe_surfaces,
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
            probe_surfaces=probe_surfaces,
        )
        overrides.append("duration-s")

    if args.surface is not None:
        requested_surface = ProbeSurface(args.surface)
        for protocol in DEFAULT_PROBES:
            probe_surfaces[protocol] = _fallback_surface(protocol, requested_surface)
        overrides.append("surface")

    if args.mode is not None:
        requested_mode = ProbeCorrectness(args.mode)
        for protocol in DEFAULT_PROBES:
            probe_correctness[protocol] = _fallback_correctness(protocol, requested_mode)
        overrides.append("mode")

    surface_arguments = {
        "http": args.http_surface,
        "ftp": args.ftp_surface,
        "telnet": args.telnet_surface,
    }
    for protocol, raw_value in surface_arguments.items():
        if raw_value is None:
            continue
        probe_surfaces[protocol] = _fallback_surface(protocol, ProbeSurface(raw_value))
        overrides.append(f"{protocol}-surface")

    mode_arguments = {
        "ping": args.ping_mode,
        "http": args.http_mode,
        "ftp": args.ftp_mode,
        "telnet": args.telnet_mode,
    }
    for protocol, raw_value in mode_arguments.items():
        if raw_value is None:
            continue
        probe_correctness[protocol] = _fallback_correctness(protocol, ProbeCorrectness(raw_value))
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
        probe_surfaces=probe_surfaces,
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
        "surfaces=" + ",".join(f"{protocol}:{config.probe_surfaces[protocol].value}" for protocol in DEFAULT_PROBES),
        "correctness=" + ",".join(f"{protocol}:{config.probe_correctness[protocol].value}" for protocol in DEFAULT_PROBES),
    ]
    if config.profile is not None and config.overrides:
        parts.append(f"overrides={','.join(config.overrides)}")
    try:
        print(f'{ts()} protocol=config result=INFO detail="{' '.join(parts)}"', flush=True)
    except BrokenPipeError:
        raise SystemExit(0)


def execute_probe(
    protocol: str,
    settings: RuntimeSettings,
    config: ExecutionConfig,
    probe_runners: dict[str, callable] | None = None,
    *,
    state: ExecutionState | None = None,
    runner_id: int = 1,
    iteration: int = 1,
) -> ProbeOutcome:
    runners = PROBE_RUNNERS if probe_runners is None else probe_runners
    previous = _set_probe_context(
        ProbeRuntimeContext(
            config=config,
            state=state,
            protocol=protocol,
            runner_id=runner_id,
            iteration=iteration,
        )
    )
    try:
        return runners[protocol](settings, config.probe_correctness[protocol])
    finally:
        _restore_probe_context(previous)


def execute_probe_safely(
    protocol: str,
    settings: RuntimeSettings,
    config: ExecutionConfig,
    probe_runners: dict[str, callable] | None = None,
    *,
    state: ExecutionState | None = None,
    runner_id: int = 1,
    iteration: int = 1,
) -> ProbeOutcome:
    started_at = time.perf_counter_ns()
    try:
        return execute_probe(
            protocol,
            settings,
            config,
            probe_runners,
            state=state,
            runner_id=runner_id,
            iteration=iteration,
        )
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
            outcome = execute_probe_safely(
                protocol,
                settings,
                config,
                probe_runners,
                state=state,
                runner_id=runner_id,
                iteration=iteration,
            )
            results.append((protocol, outcome))
            state.emit_probe_outcome(protocol, outcome, iteration=iteration, runner_id=runner_id)
            if index < len(config.probes) - 1:
                sleep_fn(settings.delay_ms)
        return tuple(results)

    ordered_results: list[tuple[str, ProbeOutcome] | None] = [None] * len(config.probes)
    threads: list[threading.Thread] = []

    def worker(index: int, protocol: str) -> None:
        ordered_results[index] = (
            protocol,
            execute_probe_safely(
                protocol,
                settings,
                config,
                probe_runners,
                state=state,
                runner_id=runner_id,
                iteration=iteration,
            ),
        )

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
    if "ftp" in config.probes and config.probe_surfaces.get("ftp", ProbeSurface.SMOKE) in (ProbeSurface.READ, ProbeSurface.READWRITE):
        _try_ftp_prime_temp_dir(settings)
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