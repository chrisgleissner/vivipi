#!/usr/bin/env python3
"""Deterministic FTP performance tester for Ultimate 64 devices.

Single-file CLI. Standard library only. Measures FTP upload/download
throughput across configurable file sizes and concurrency modes and emits
line-oriented metrics to stdout.
"""

from __future__ import annotations

import argparse
import ftplib
import io
import json
import math
import re
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field


DEFAULT_HOST = "u64"
DEFAULT_FTP_PORT = 21
DEFAULT_FTP_USER = ""
DEFAULT_FTP_PASS = ""
DEFAULT_PASSIVE = True
DEFAULT_TIMEOUT_S = 10
DEFAULT_REMOTE_DIR = "/USB2/test/FTP"
DEFAULT_SIZES = "20K,200K,1M"
DEFAULT_TARGET_BYTES = "600K"
DEFAULT_CONCURRENCY = 3
DEFAULT_MODE = "both"
DEFAULT_VERIFY = True
DEFAULT_FAIL_FAST = False
DEFAULT_MAX_RUNTIME_S = (
    0  # 0 disables the runtime cap; the full matrix always runs to completion by default
)
DEFAULT_FORMAT = "text"
DEFAULT_ENSURE_REMOTE_DIR = True

MODES = ("single", "multi", "both")
FORMATS = ("text", "json")

PAYLOAD_SEED = b"u64ftp-deterministic-payload-00\n"  # 32 bytes
OPS_TEMP_DIR = "__perf_cd"
OPS_XMKD_DIR = "__perf_xmkd"
OPS_CD_LEVEL_1 = "level1"
OPS_CD_LEVEL_2 = "level2"
TEST_FILE_RENAME_SUFFIX = ".rn"
TEST_FILENAME_RE = re.compile(r"^u64ftp_[^/\\]+_[1-9][0-9]*_[1-9][0-9]*\.bin(?:\.rn)?$")


# ------------------------------ parsers ---------------------------------


def parse_size_token(token: str) -> tuple[str, int]:
    """Parse a single size like '20K', '200K', '1M', '1G', '512' into (label, bytes)."""
    raw = token.strip()
    if not raw:
        raise argparse.ArgumentTypeError("size token must not be empty")
    suffix = raw[-1].upper()
    if suffix in ("K", "M", "G"):
        number_part = raw[:-1]
        multiplier = {"K": 1024, "M": 1024 * 1024, "G": 1024 * 1024 * 1024}[suffix]
    else:
        number_part = raw
        multiplier = 1
    try:
        value = float(number_part)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid size: {token!r}") from error
    if value <= 0:
        raise argparse.ArgumentTypeError(f"size must be > 0: {token!r}")
    byte_count = int(round(value * multiplier))
    if byte_count < 1:
        raise argparse.ArgumentTypeError(f"size must be >= 1 byte: {token!r}")
    return raw, byte_count


def parse_sizes(value: str) -> tuple[tuple[str, int], ...]:
    raw = value.strip()
    if not raw:
        raise argparse.ArgumentTypeError("--sizes must be non-empty")
    tokens = [part.strip() for part in raw.split(",") if part.strip()]
    if not tokens:
        raise argparse.ArgumentTypeError("--sizes must contain at least one entry")
    return tuple(parse_size_token(token) for token in tokens)


def parse_byte_size(value: str) -> int:
    _label, byte_count = parse_size_token(value)
    return byte_count


def parse_mode(value: str) -> str:
    lowered = value.strip().lower()
    if lowered not in MODES:
        raise argparse.ArgumentTypeError(f"--mode must be one of {','.join(MODES)}")
    return lowered


def parse_format(value: str) -> str:
    lowered = value.strip().lower()
    if lowered not in FORMATS:
        raise argparse.ArgumentTypeError(f"--format must be one of {','.join(FORMATS)}")
    return lowered


def _contains_unsafe_ftp_chars(value: str) -> bool:
    return any(ord(ch) < 32 for ch in value)


def validate_ftp_basename(value: str, *, label: str = "path") -> str:
    name = value.strip()
    if not name:
        raise ValueError(f"{label} must be non-empty")
    if name in (".", ".."):
        raise ValueError(f"{label} must not be '.' or '..'")
    if "/" in name or "\\" in name:
        raise ValueError(f"{label} must not contain path separators")
    if _contains_unsafe_ftp_chars(name):
        raise ValueError(f"{label} must not contain control characters")
    return name


def parse_remote_dir(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise argparse.ArgumentTypeError("--remote-dir must be non-empty")
    if not raw.startswith("/"):
        raise argparse.ArgumentTypeError("--remote-dir must be an absolute FTP path")
    if _contains_unsafe_ftp_chars(raw):
        raise argparse.ArgumentTypeError("--remote-dir must not contain control characters")

    normalized_parts: list[str] = []
    for part in raw.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise argparse.ArgumentTypeError("--remote-dir must not contain '..'")
        try:
            normalized_parts.append(validate_ftp_basename(part, label="remote-dir segment"))
        except ValueError as error:
            raise argparse.ArgumentTypeError(str(error)) from error

    if not normalized_parts:
        return "/"
    return "/" + "/".join(normalized_parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="u64_ftp_test",
        description="Deterministic FTP performance tester for Ultimate 64 devices.",
    )
    parser.add_argument("-H", "--host", default=DEFAULT_HOST)
    parser.add_argument("--ftp-port", type=int, default=DEFAULT_FTP_PORT)
    parser.add_argument("-u", "--ftp-user", default=DEFAULT_FTP_USER)
    parser.add_argument("-P", "--ftp-pass", default=DEFAULT_FTP_PASS)
    passive_group = parser.add_mutually_exclusive_group()
    passive_group.add_argument(
        "--passive", dest="passive", action="store_true", default=DEFAULT_PASSIVE
    )
    passive_group.add_argument("--no-passive", dest="passive", action="store_false")
    parser.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S)
    parser.add_argument(
        "--remote-dir", type=parse_remote_dir, default=parse_remote_dir(DEFAULT_REMOTE_DIR)
    )
    parser.add_argument("--sizes", type=parse_sizes, default=parse_sizes(DEFAULT_SIZES))
    parser.add_argument(
        "--target-bytes", type=parse_byte_size, default=parse_byte_size(DEFAULT_TARGET_BYTES)
    )
    parser.add_argument("--files-per-stage", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--mode", type=parse_mode, default=DEFAULT_MODE)
    verify_group = parser.add_mutually_exclusive_group()
    verify_group.add_argument(
        "--verify", dest="verify", action="store_true", default=DEFAULT_VERIFY
    )
    verify_group.add_argument("--no-verify", dest="verify", action="store_false")
    fail_fast_group = parser.add_mutually_exclusive_group()
    fail_fast_group.add_argument(
        "--fail-fast", dest="fail_fast", action="store_true", default=DEFAULT_FAIL_FAST
    )
    fail_fast_group.add_argument("--no-fail-fast", dest="fail_fast", action="store_false")
    parser.add_argument("--max-runtime-s", type=int, default=DEFAULT_MAX_RUNTIME_S)
    parser.add_argument("--format", type=parse_format, default=DEFAULT_FORMAT)
    parser.add_argument("-v", "--verbose", action="store_true", default=False)
    ensure_group = parser.add_mutually_exclusive_group()
    ensure_group.add_argument(
        "--ensure-remote-dir",
        dest="ensure_remote_dir",
        action="store_true",
        default=DEFAULT_ENSURE_REMOTE_DIR,
    )
    ensure_group.add_argument(
        "--no-ensure-remote-dir", dest="ensure_remote_dir", action="store_false"
    )
    return parser


# ------------------------------ payload ---------------------------------


def build_payload(size_bytes: int) -> bytes:
    if size_bytes < 1:
        raise ValueError("size_bytes must be >= 1")
    seed = PAYLOAD_SEED
    repeats, remainder = divmod(size_bytes, len(seed))
    return seed * repeats + seed[:remainder]


def build_filename(size_label: str, worker_index: int, iteration: int) -> str:
    return f"u64ftp_{size_label}_{worker_index}_{iteration}.bin"


def safe_build_filename(size_label: str, worker_index: int, iteration: int) -> str:
    return validate_ftp_basename(
        build_filename(size_label, worker_index, iteration),
        label="generated filename",
    )


def is_managed_test_filename(value: str) -> bool:
    return bool(TEST_FILENAME_RE.fullmatch(value))


def validate_managed_test_filename(value: str, *, label: str = "filename") -> str:
    name = validate_ftp_basename(value, label=label)
    if not is_managed_test_filename(name):
        raise ValueError(f"{label} is not a managed test file")
    return name


def build_renamed_test_filename(filename: str) -> str:
    return validate_managed_test_filename(
        f"{validate_managed_test_filename(filename, label='filename')}{TEST_FILE_RENAME_SUFFIX}",
        label="rename target",
    )


def normalize_managed_test_filenames(filenames: list[str]) -> set[str]:
    return {validate_managed_test_filename(name) for name in filenames}


# ------------------------------ logging ---------------------------------


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ProgressBar:
    """Single-line, TTY-only progress indicator.

    Uses carriage-return + ANSI erase to occupy exactly one line. When stdout
    is not a TTY (pipe, file, CI) the bar is a no-op so batch consumers see
    clean line-oriented output. Callers must hold the shared output lock
    around draw/clear operations to avoid interleaving with log lines.
    """

    BAR_WIDTH = 30
    MIN_REDRAW_INTERVAL_S = 0.05

    def __init__(self, enabled: bool, stream=None) -> None:
        self.enabled = enabled
        self.stream = stream if stream is not None else sys.stdout
        self.label: str | None = None
        self.total = 0
        self.completed = 0
        self.failed = 0
        self.started_at = 0.0
        self._last_line = ""
        self._last_draw_at = 0.0

    def start(self, label: str, total: int) -> None:
        if not self.enabled:
            return
        self.label = label
        self.total = max(1, total)
        self.completed = 0
        self.failed = 0
        self.started_at = time.perf_counter()
        self._last_draw_at = 0.0
        self._draw(force=True)

    def tick(self, success: bool) -> None:
        if not self.enabled or self.label is None:
            return
        self.completed += 1
        if not success:
            self.failed += 1
        # Always redraw on tick: the bar only ticks when a transfer finishes,
        # which is typically >10 ms apart; visible feedback beats micro-flicker
        # concerns.
        self._draw(force=True)

    def finish(self) -> None:
        if not self.enabled or self.label is None:
            return
        self._clear()
        self.label = None
        self.total = 0
        self.completed = 0
        self.failed = 0

    def pause(self) -> bool:
        if not self.enabled or self.label is None:
            return False
        if not self._last_line:
            return False
        self._clear()
        return True

    def resume(self) -> None:
        if not self.enabled or self.label is None:
            return
        self._draw(force=True)

    def _clear(self) -> None:
        if not self._last_line:
            return
        try:
            self.stream.write("\r\x1b[2K")
            self.stream.flush()
        except (OSError, ValueError):
            pass
        self._last_line = ""

    def _draw(self, *, force: bool) -> None:
        now = time.perf_counter()
        if not force and (now - self._last_draw_at) < self.MIN_REDRAW_INTERVAL_S:
            return
        completed = min(self.completed, self.total)
        ratio = completed / self.total if self.total else 1.0
        filled = int(self.BAR_WIDTH * ratio)
        bar = "#" * filled + "-" * (self.BAR_WIDTH - filled)
        percent = int(round(100 * ratio))
        elapsed = max(0.0, now - self.started_at)
        rate = completed / elapsed if elapsed > 0 and completed > 0 else 0.0
        remaining = max(0, self.total - completed)
        eta = int(remaining / rate) if rate > 0 else 0
        line = (
            f"[{bar}] {percent:3d}% {completed}/{self.total} "
            f"{self.label} fail={self.failed} eta={eta}s"
        )
        pad = ""
        if len(line) < len(self._last_line):
            pad = " " * (len(self._last_line) - len(line))
        try:
            self.stream.write("\r" + line + pad)
            self.stream.flush()
        except (OSError, ValueError):
            return
        self._last_line = line
        self._last_draw_at = now


class Emitter:
    """Emit log lines (text mode) or capture structured events (json mode)."""

    def __init__(self, json_mode: bool, *, progress: ProgressBar | None = None) -> None:
        self.json_mode = json_mode
        self.lock = threading.Lock()
        self.stages: list[dict] = []
        self.config: dict | None = None
        self.summary: dict | None = None
        self.progress = progress or ProgressBar(enabled=False)

    def emit_text(self, protocol: str, result: str, detail_pairs: list[tuple[str, object]]) -> None:
        if self.json_mode:
            return
        detail = " ".join(f"{k}={_format_value(v)}" for k, v in detail_pairs)
        safe_detail = detail.replace('"', "'")
        line = f'{ts()} protocol={protocol} result={result} detail="{safe_detail}"'
        with self.lock:
            was_drawn = self.progress.pause()
            try:
                print(line, flush=True)
            except BrokenPipeError:
                raise SystemExit(0)
            if was_drawn:
                self.progress.resume()

    def progress_start(self, label: str, total: int) -> None:
        with self.lock:
            self.progress.start(label, total)

    def progress_tick(self, success: bool) -> None:
        with self.lock:
            self.progress.tick(success)

    def progress_finish(self) -> None:
        with self.lock:
            self.progress.finish()


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "0"
        return f"{value:.2f}".rstrip("0").rstrip(".") or "0"
    return str(value)


# ------------------------------ data model ------------------------------


@dataclass
class TransferResult:
    size_label: str
    size_bytes: int
    worker: int
    iteration: int
    success: bool
    failure_type: str | None = None
    failure_detail: str | None = None
    upload_time_s: float = 0.0
    download_time_s: float = 0.0
    upload_bytes: int = 0
    download_bytes: int = 0
    verify_ok: bool = False
    verify_checked: bool = False

    @property
    def upload_KBps(self) -> float:
        if self.upload_time_s <= 0:
            return 0.0
        return (self.upload_bytes / 1024.0) / self.upload_time_s

    @property
    def download_KBps(self) -> float:
        if self.download_time_s <= 0:
            return 0.0
        return (self.download_bytes / 1024.0) / self.download_time_s


@dataclass
class OpsCommandResult:
    cmd: str
    success: bool
    time_s: float
    reply: str | None = None
    error: str | None = None


@dataclass
class OpsResult:
    commands: list[OpsCommandResult] = field(default_factory=list)
    cd_count: int = 0
    cd_time_s: float = 0.0
    list_count: int = 0
    list_time_s: float = 0.0
    delete_count: int = 0
    delete_time_s: float = 0.0
    success: bool = True
    error_detail: str | None = None


@dataclass
class StageResult:
    size_label: str
    size_bytes: int
    mode: str  # "single" or "multi"
    workers: int
    files_per_worker: int
    total_files: int
    transfers: list[TransferResult] = field(default_factory=list)
    started_at_s: float = 0.0
    ended_at_s: float = 0.0
    aborted: bool = False

    def total_bytes_up(self) -> int:
        return sum(t.upload_bytes for t in self.transfers if t.success)

    def total_bytes_down(self) -> int:
        return sum(t.download_bytes for t in self.transfers if t.success)

    def success_count(self) -> int:
        return sum(1 for t in self.transfers if t.success)

    def failure_count(self) -> int:
        return sum(1 for t in self.transfers if not t.success)

    def verify_failures(self) -> int:
        return sum(1 for t in self.transfers if t.verify_checked and not t.verify_ok)

    def aggregate_upload_KBps(self) -> float:
        """Aggregate stage upload throughput, summed across all workers.

        Computed as total successful upload bytes divided by the stage's
        wall-clock duration, in kilobytes/sec (1024-based). Returns 0 when
        the stage produced no successful transfers or has no measurable
        duration.
        """
        duration = self.total_time_s()
        if duration <= 0:
            return 0.0
        return (self.total_bytes_up() / 1024.0) / duration

    def aggregate_download_KBps(self) -> float:
        duration = self.total_time_s()
        if duration <= 0:
            return 0.0
        return (self.total_bytes_down() / 1024.0) / duration

    def total_time_s(self) -> float:
        if self.ended_at_s > self.started_at_s:
            return self.ended_at_s - self.started_at_s
        return 0.0


# ------------------------------ FTP session -----------------------------


class FtpOpenError(Exception):
    """Raised when establishing a session fails. Carries a phase tag."""

    def __init__(self, phase: str, cause: BaseException) -> None:
        super().__init__(f"{phase}: {cause}")
        self.phase = phase
        self.cause = cause


def open_session(args: argparse.Namespace) -> ftplib.FTP:
    ftp = ftplib.FTP()
    try:
        ftp.connect(args.host, args.ftp_port, timeout=args.timeout_s)
    except (OSError, ftplib.Error) as error:
        try:
            ftp.close()
        except OSError:
            pass
        raise FtpOpenError("connect_failed", error) from error
    try:
        ftp.login(args.ftp_user, args.ftp_pass)
    except (OSError, ftplib.Error) as error:
        try:
            ftp.close()
        except OSError:
            pass
        raise FtpOpenError("login_failed", error) from error
    try:
        ftp.set_pasv(bool(args.passive))
        ftp.cwd(args.remote_dir)
    except (OSError, ftplib.Error) as error:
        try:
            ftp.close()
        except OSError:
            pass
        raise FtpOpenError("cwd_failed", error) from error
    try:
        ftp.sock.settimeout(args.timeout_s)
    except (AttributeError, OSError):
        pass
    return ftp


def ensure_remote_dir(args: argparse.Namespace) -> tuple[bool, str | None]:
    """Create the remote directory if missing. Returns (ok, error_detail)."""
    ftp = ftplib.FTP()
    try:
        try:
            ftp.connect(args.host, args.ftp_port, timeout=args.timeout_s)
            ftp.login(args.ftp_user, args.ftp_pass)
            ftp.set_pasv(bool(args.passive))
        except (OSError, ftplib.Error) as error:
            return False, f"connect_or_login: {error}"
        segments = [seg for seg in args.remote_dir.split("/") if seg]
        path = ""
        for seg in segments:
            path = f"{path}/{seg}"
            try:
                ftp.cwd(path)
            except ftplib.error_perm:
                try:
                    ftp.mkd(path)
                except ftplib.error_perm as mkd_error:
                    # If the directory was created in parallel or already exists, try cwd again.
                    try:
                        ftp.cwd(path)
                    except ftplib.error_perm:
                        return False, f"mkd {path}: {mkd_error}"
                except (OSError, ftplib.Error) as mkd_error:
                    return False, f"mkd {path}: {mkd_error}"
        return True, None
    finally:
        try:
            ftp.quit()
        except (OSError, ftplib.Error, EOFError):
            pass
        try:
            ftp.close()
        except OSError:
            pass


def close_session(ftp: ftplib.FTP | None) -> None:
    if ftp is None:
        return
    try:
        ftp.quit()
    except (OSError, ftplib.Error, EOFError):
        pass
    finally:
        try:
            ftp.close()
        except OSError:
            pass


def safe_sendcmd(ftp: ftplib.FTP, cmd_name: str, argument: str | None = None) -> str:
    safe_cmd = cmd_name.strip().upper()
    if not safe_cmd or any(ch.isspace() for ch in safe_cmd):
        raise ValueError("FTP command name must be a single token")
    if argument is None:
        return ftp.sendcmd(safe_cmd)
    return ftp.sendcmd(f"{safe_cmd} {validate_ftp_basename(argument, label='command argument')}")


# ------------------------------ transfer --------------------------------


def run_single_transfer(
    ftp: ftplib.FTP,
    size_label: str,
    size_bytes: int,
    worker: int,
    iteration: int,
    payload: bytes,
    verify: bool,
) -> TransferResult:
    result = TransferResult(
        size_label=size_label,
        size_bytes=size_bytes,
        worker=worker,
        iteration=iteration,
        success=False,
    )
    try:
        filename = safe_build_filename(size_label, worker, iteration)
    except ValueError as error:
        result.failure_type = "filename_invalid"
        result.failure_detail = str(error)
        return result

    upload_start = time.perf_counter()
    try:
        buffer = io.BytesIO(payload)
        ftp.storbinary(f"STOR {filename}", buffer)
    except (OSError, ftplib.Error, EOFError, socket.timeout) as error:
        result.failure_type = "upload_failed"
        result.failure_detail = str(error) or error.__class__.__name__
        return result
    result.upload_time_s = time.perf_counter() - upload_start
    result.upload_bytes = len(payload)

    received = bytearray()
    download_start = time.perf_counter()
    try:
        ftp.retrbinary(f"RETR {filename}", received.extend)
    except (OSError, ftplib.Error, EOFError, socket.timeout) as error:
        result.failure_type = "download_failed"
        result.failure_detail = str(error) or error.__class__.__name__
        return result
    result.download_time_s = time.perf_counter() - download_start
    result.download_bytes = len(received)

    if verify:
        result.verify_checked = True
        if bytes(received) == payload:
            result.verify_ok = True
            result.success = True
        else:
            result.failure_type = "verify_mismatch"
            result.failure_detail = f"expected_bytes={len(payload)} got_bytes={len(received)}"
            result.success = False
    else:
        result.success = True
    return result


# ------------------------------ stage runner ----------------------------


@dataclass
class StageContext:
    args: argparse.Namespace
    emitter: Emitter
    deadline_s: float | None
    abort_flag: threading.Event


def worker_loop(
    worker_index: int,
    files_per_worker: int,
    size_label: str,
    size_bytes: int,
    payload: bytes,
    context: StageContext,
) -> list[TransferResult]:
    results: list[TransferResult] = []
    ftp: ftplib.FTP | None = None
    try:
        try:
            ftp = open_session(context.args)
        except FtpOpenError as error:
            for iteration in range(1, files_per_worker + 1):
                failure = TransferResult(
                    size_label=size_label,
                    size_bytes=size_bytes,
                    worker=worker_index,
                    iteration=iteration,
                    success=False,
                    failure_type=error.phase,
                    failure_detail=str(error.cause),
                )
                results.append(failure)
                log_transfer(context, failure)
                if context.args.fail_fast:
                    context.abort_flag.set()
                    return results
                if context.abort_flag.is_set():
                    return results
            return results

        for iteration in range(1, files_per_worker + 1):
            if context.abort_flag.is_set():
                break
            transfer = run_single_transfer(
                ftp,
                size_label,
                size_bytes,
                worker_index,
                iteration,
                payload,
                context.args.verify,
            )
            results.append(transfer)
            log_transfer(context, transfer)
            if not transfer.success and context.args.fail_fast:
                context.abort_flag.set()
                break
    finally:
        close_session(ftp)
    return results


def log_transfer(context: StageContext, transfer: TransferResult) -> None:
    context.emitter.progress_tick(transfer.success)
    should_emit = context.args.verbose or not transfer.success
    if not should_emit:
        return
    if transfer.success:
        detail = [
            ("size", transfer.size_label),
            ("worker", transfer.worker),
            ("iter", transfer.iteration),
            ("up_KBps", int(round(transfer.upload_KBps))),
            ("down_KBps", int(round(transfer.download_KBps))),
            (
                "verify",
                "OK" if transfer.verify_ok else ("SKIP" if not transfer.verify_checked else "FAIL"),
            ),
        ]
        context.emitter.emit_text("transfer", "OK", detail)
    else:
        detail = [
            ("size", transfer.size_label),
            ("worker", transfer.worker),
            ("iter", transfer.iteration),
            ("phase", transfer.failure_type or "unknown"),
            ("error", (transfer.failure_detail or "").replace(" ", "_")[:200]),
        ]
        context.emitter.emit_text("transfer", "FAIL", detail)


def compute_files_per_worker(size_bytes: int, target_bytes: int, override: int | None) -> int:
    if override is not None:
        return max(1, int(override))
    return max(1, math.ceil(target_bytes / size_bytes))


def run_stage(
    args: argparse.Namespace,
    emitter: Emitter,
    size_label: str,
    size_bytes: int,
    mode: str,
    workers: int,
    deadline_s: float | None,
) -> StageResult:
    files_per_worker = compute_files_per_worker(size_bytes, args.target_bytes, args.files_per_stage)
    total_files = files_per_worker * workers

    stage = StageResult(
        size_label=size_label,
        size_bytes=size_bytes,
        mode=mode,
        workers=workers,
        files_per_worker=files_per_worker,
        total_files=total_files,
    )

    emitter.emit_text(
        "stage",
        "START",
        [
            ("size", size_label),
            ("mode", mode),
            ("workers", workers),
            ("files_per_worker", files_per_worker),
            ("total_files", total_files),
            ("target", _format_bytes_short(args.target_bytes)),
        ],
    )

    emitter.progress_start(f"size={size_label} mode={mode} workers={workers}", total_files)

    payload = build_payload(size_bytes)
    abort_flag = threading.Event()
    context = StageContext(args=args, emitter=emitter, deadline_s=deadline_s, abort_flag=abort_flag)

    stage.started_at_s = time.perf_counter()

    if workers == 1:
        stage.transfers = worker_loop(1, files_per_worker, size_label, size_bytes, payload, context)
    else:
        ordered: list[list[TransferResult]] = [[] for _ in range(workers)]
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="u64ftp") as pool:
            futures = {
                pool.submit(
                    worker_loop, idx + 1, files_per_worker, size_label, size_bytes, payload, context
                ): idx
                for idx in range(workers)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    ordered[idx] = future.result()
                except Exception as error:  # defensive; worker_loop already catches
                    ordered[idx] = [
                        TransferResult(
                            size_label=size_label,
                            size_bytes=size_bytes,
                            worker=idx + 1,
                            iteration=1,
                            success=False,
                            failure_type="connect_failed",
                            failure_detail=str(error),
                        )
                    ]
        for bucket in ordered:
            stage.transfers.extend(bucket)

    stage.ended_at_s = time.perf_counter()
    stage.aborted = abort_flag.is_set()

    emitter.progress_finish()

    emitter.emit_text(
        "stage",
        "END",
        [
            ("size", size_label),
            ("mode", mode),
            ("stage_s", round(stage.total_time_s(), 2)),
            ("agg_up_KBps", int(round(stage.aggregate_upload_KBps()))),
            ("agg_down_KBps", int(round(stage.aggregate_download_KBps()))),
            ("success", stage.success_count()),
            ("fail", stage.failure_count()),
            ("verify_fail", stage.verify_failures()),
        ],
    )
    return stage


def _format_bytes_short(value: int) -> str:
    if value % (1024 * 1024 * 1024) == 0 and value >= 1024 * 1024 * 1024:
        return f"{value // (1024 * 1024 * 1024)}G"
    if value % (1024 * 1024) == 0 and value >= 1024 * 1024:
        return f"{value // (1024 * 1024)}M"
    if value % 1024 == 0 and value >= 1024:
        return f"{value // 1024}K"
    return str(value)


# ------------------------------ driver ----------------------------------


def modes_for(mode: str) -> tuple[str, ...]:
    if mode == "single":
        return ("single",)
    if mode == "multi":
        return ("multi",)
    return ("single", "multi")


def workers_for(mode: str, concurrency: int) -> int:
    if mode == "single":
        return 1
    return max(1, concurrency)


def emit_config(args: argparse.Namespace, emitter: Emitter) -> dict:
    sizes_str = ",".join(label for label, _ in args.sizes)
    target_str = _format_bytes_short(args.target_bytes)
    config_dict = {
        "host": args.host,
        "ftp_port": args.ftp_port,
        "ftp_user": args.ftp_user,
        "passive": bool(args.passive),
        "timeout_s": args.timeout_s,
        "remote_dir": args.remote_dir,
        "sizes": sizes_str,
        "target_bytes": args.target_bytes,
        "files_per_stage": args.files_per_stage,
        "concurrency": args.concurrency,
        "mode": args.mode,
        "verify": bool(args.verify),
        "fail_fast": bool(args.fail_fast),
        "max_runtime_s": args.max_runtime_s,
        "format": args.format,
        "verbose": bool(args.verbose),
    }
    emitter.config = config_dict
    emitter.emit_text(
        "config",
        "INFO",
        [
            ("host", args.host),
            ("dir", args.remote_dir),
            ("sizes", sizes_str),
            ("target", target_str),
            ("concurrency", args.concurrency),
            ("mode", args.mode),
            ("verify", args.verify),
        ],
    )
    return config_dict


def build_stage_record(stage: StageResult) -> dict:
    return {
        "size": stage.size_label,
        "size_bytes": stage.size_bytes,
        "mode": stage.mode,
        "workers": stage.workers,
        "files_per_worker": stage.files_per_worker,
        "total_files": stage.total_files,
        "agg_upload_KBps": round(stage.aggregate_upload_KBps(), 2),
        "agg_download_KBps": round(stage.aggregate_download_KBps(), 2),
        "success_count": stage.success_count(),
        "failure_count": stage.failure_count(),
        "verify_failures": stage.verify_failures(),
        "total_bytes_up": stage.total_bytes_up(),
        "total_bytes_down": stage.total_bytes_down(),
        "total_time_s": round(stage.total_time_s(), 3),
        "aborted": stage.aborted,
        "throughput_basis": "aggregate_across_all_workers",
        "throughput_unit": "KBps_1024_based",
        "transfers": [
            {
                "worker": t.worker,
                "iter": t.iteration,
                "success": t.success,
                "failure_type": t.failure_type,
                "failure_detail": t.failure_detail,
                "upload_time_s": round(t.upload_time_s, 6),
                "download_time_s": round(t.download_time_s, 6),
                "upload_bytes": t.upload_bytes,
                "download_bytes": t.download_bytes,
                "upload_KBps": round(t.upload_KBps, 2),
                "download_KBps": round(t.download_KBps, 2),
                "verify_ok": t.verify_ok,
                "verify_checked": t.verify_checked,
            }
            for t in stage.transfers
        ],
    }


def _is_interactive_stdout() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def run_ops_stage(args: argparse.Namespace, emitter: Emitter, filenames: list[str]) -> OpsResult:
    ops = OpsResult()
    try:
        managed_files = sorted(normalize_managed_test_filenames(filenames))
    except ValueError as error:
        ops.success = False
        ops.error_detail = str(error)
        emitter.emit_text(
            "ops",
            "FAIL",
            [("phase", "validate_files"), ("error", ops.error_detail.replace(" ", "_")[:200])],
        )
        return ops

    emitter.emit_text("ops", "START", [("files", len(managed_files))])

    ftp = ftplib.FTP()
    try:
        ftp.connect(args.host, args.ftp_port, timeout=args.timeout_s)
        ftp.login(args.ftp_user, args.ftp_pass)
        ftp.set_pasv(bool(args.passive))
        ftp.cwd(args.remote_dir)
    except Exception as error:
        ops.success = False
        ops.error_detail = f"connect_or_login: {error}"
        emitter.emit_text(
            "ops",
            "FAIL",
            [("phase", "connect"), ("error", ops.error_detail.replace(" ", "_")[:200])],
        )
        return ops

    def time_cmd(
        label: str,
        argument: str | None = None,
        expect_error: bool = False,
        *,
        command: str | None = None,
    ):
        start = time.perf_counter()
        try:
            resp = safe_sendcmd(ftp, command or label, argument)
            dur = time.perf_counter() - start
            ops.commands.append(OpsCommandResult(cmd=label, success=True, time_s=dur, reply=resp))
        except Exception as error:
            dur = time.perf_counter() - start
            ops.commands.append(
                OpsCommandResult(cmd=label, success=expect_error, time_s=dur, error=str(error))
            )
            if not expect_error:
                ops.success = False

    time_cmd("FEAT")
    time_cmd("SYST")
    time_cmd("NOOP")
    time_cmd("TYPE", "I")
    time_cmd("MODE", "S", expect_error=True)
    time_cmd("PWD")
    time_cmd("XPWD")
    time_cmd("XMKD", OPS_XMKD_DIR)
    time_cmd("XRMD", OPS_XMKD_DIR)
    time_cmd("PORT", "127,0,0,1,1,1")

    cd_count = 10
    cd_start = time.perf_counter()
    try:
        safe_sendcmd(ftp, "MKD", OPS_TEMP_DIR)
        safe_sendcmd(ftp, "CWD", OPS_TEMP_DIR)
        safe_sendcmd(ftp, "MKD", OPS_CD_LEVEL_1)
        safe_sendcmd(ftp, "CWD", OPS_CD_LEVEL_1)
        safe_sendcmd(ftp, "MKD", OPS_CD_LEVEL_2)
        ftp.cwd(args.remote_dir)
        for _ in range(cd_count):
            try:
                safe_sendcmd(ftp, "CWD", OPS_TEMP_DIR)
                safe_sendcmd(ftp, "CWD", OPS_CD_LEVEL_1)
                safe_sendcmd(ftp, "CWD", OPS_CD_LEVEL_2)
                safe_sendcmd(ftp, "CDUP")
                safe_sendcmd(ftp, "CDUP")
                safe_sendcmd(ftp, "CDUP")
                ops.cd_count += 1
            except Exception:
                ops.success = False
                break

        # Ensure we are back in the correct directory before removing
        ftp.cwd(args.remote_dir)
        safe_sendcmd(ftp, "CWD", OPS_TEMP_DIR)
        safe_sendcmd(ftp, "CWD", OPS_CD_LEVEL_1)
        safe_sendcmd(ftp, "RMD", OPS_CD_LEVEL_2)
        safe_sendcmd(ftp, "CDUP")
        safe_sendcmd(ftp, "RMD", OPS_CD_LEVEL_1)
        safe_sendcmd(ftp, "CDUP")
        safe_sendcmd(ftp, "RMD", OPS_TEMP_DIR)
    except Exception:
        ops.success = False

    ops.cd_time_s = time.perf_counter() - cd_start
    cd_avg = ops.cd_time_s / ops.cd_count if ops.cd_count else 0.0
    emitter.emit_text("ops", "CD_PERF", [("count", ops.cd_count), ("avg_s", round(cd_avg, 4))])

    list_count = 5
    list_ops = ["LIST", "NLST", "MLSD"]
    list_start = time.perf_counter()
    for op in list_ops:
        op_start = time.perf_counter()
        op_success = 0
        for _ in range(list_count):
            try:
                lines: list[str] = []
                ftp.retrlines(op, lines.append)
                op_success += 1
                ops.list_count += 1
            except Exception:
                ops.success = False
                break
        op_dur = time.perf_counter() - op_start
        op_avg = op_dur / op_success if op_success else 0.0
        emitter.emit_text("ops", f"{op}_PERF", [("count", op_success), ("avg_s", round(op_avg, 4))])

    ops.list_time_s = time.perf_counter() - list_start

    # Info & Rename test on a single file if available
    if managed_files:
        test_file = managed_files[0]
        renamed_test_file = build_renamed_test_filename(test_file)
        try:
            time_cmd("SIZE", test_file)

            # RNFR returns 350
            start = time.perf_counter()
            try:
                resp = safe_sendcmd(ftp, "RNFR", test_file)
                if resp.startswith("350"):
                    ops.commands.append(
                        OpsCommandResult(
                            cmd="RNFR", success=True, time_s=time.perf_counter() - start, reply=resp
                        )
                    )
                else:
                    ops.commands.append(
                        OpsCommandResult(
                            cmd="RNFR",
                            success=False,
                            time_s=time.perf_counter() - start,
                            error="Expected 350 but got " + resp,
                        )
                    )
                    ops.success = False
            except Exception as e:
                ops.commands.append(
                    OpsCommandResult(
                        cmd="RNFR", success=False, time_s=time.perf_counter() - start, error=str(e)
                    )
                )
                ops.success = False

            time_cmd("RNTO", renamed_test_file)
            time_cmd("MLST", renamed_test_file)

            start = time.perf_counter()
            try:
                resp = safe_sendcmd(ftp, "RNFR", renamed_test_file)
                if resp.startswith("350"):
                    ops.commands.append(
                        OpsCommandResult(
                            cmd="RNFR2",
                            success=True,
                            time_s=time.perf_counter() - start,
                            reply=resp,
                        )
                    )
                else:
                    ops.commands.append(
                        OpsCommandResult(
                            cmd="RNFR2",
                            success=False,
                            time_s=time.perf_counter() - start,
                            error="Expected 350 but got " + resp,
                        )
                    )
                    ops.success = False
            except Exception as e:
                ops.commands.append(
                    OpsCommandResult(
                        cmd="RNFR2", success=False, time_s=time.perf_counter() - start, error=str(e)
                    )
                )
                ops.success = False

            time_cmd("RNTO2", test_file, command="RNTO")
        except Exception:
            ops.success = False

    del_start = time.perf_counter()
    for f in managed_files:
        try:
            ftp.delete(f)
            ops.delete_count += 1
        except Exception:
            ops.success = False

    ops.delete_time_s = time.perf_counter() - del_start
    del_avg = ops.delete_time_s / ops.delete_count if ops.delete_count else 0.0
    emitter.emit_text(
        "ops", "DELETE_PERF", [("count", ops.delete_count), ("avg_s", round(del_avg, 4))]
    )

    try:
        ftp.sock.settimeout(1)
        time_cmd("ABOR", expect_error=True)
    finally:
        try:
            ftp.sock.settimeout(args.timeout_s)
        except Exception:
            pass

    close_session(ftp)

    emitter.emit_text(
        "ops",
        "END",
        [
            ("success", ops.success),
            ("cmds", len(ops.commands)),
            ("cd_count", ops.cd_count),
            ("list_count", ops.list_count),
            ("del_count", ops.delete_count),
        ],
    )

    return ops


def run(args: argparse.Namespace) -> int:
    progress_enabled = args.format == "text" and _is_interactive_stdout()
    progress = ProgressBar(enabled=progress_enabled)
    emitter = Emitter(json_mode=(args.format == "json"), progress=progress)
    config_dict = emit_config(args, emitter)

    start_monotonic = time.monotonic()
    deadline_s = (
        start_monotonic + args.max_runtime_s
        if args.max_runtime_s and args.max_runtime_s > 0
        else None
    )

    stages: list[StageResult] = []
    partial = False
    stages_failed = 0

    if args.ensure_remote_dir:
        ok, detail = ensure_remote_dir(args)
        if not ok:
            emitter.emit_text(
                "setup",
                "FAIL",
                [("dir", args.remote_dir), ("error", (detail or "").replace(" ", "_")[:200])],
            )

    for size_label, size_bytes in args.sizes:
        for mode in modes_for(args.mode):
            if deadline_s is not None and time.monotonic() >= deadline_s:
                partial = True
                break
            workers = workers_for(mode, args.concurrency)
            stage = run_stage(args, emitter, size_label, size_bytes, mode, workers, deadline_s)
            stages.append(stage)
            if stage.failure_count() > 0 or stage.verify_failures() > 0:
                stages_failed += 1
            if args.fail_fast and (stage.failure_count() > 0 or stage.verify_failures() > 0):
                break
        else:
            continue
        break

    total_runtime = time.monotonic() - start_monotonic
    total_bytes_up = sum(s.total_bytes_up() for s in stages)
    total_bytes_down = sum(s.total_bytes_down() for s in stages)
    total_stage_seconds = sum(s.total_time_s() for s in stages)
    agg_up_kbps = (
        (total_bytes_up / 1024.0) / total_stage_seconds if total_stage_seconds > 0 else 0.0
    )
    agg_down_kbps = (
        (total_bytes_down / 1024.0) / total_stage_seconds if total_stage_seconds > 0 else 0.0
    )
    overall_success = stages_failed == 0 and not partial and len(stages) > 0

    ops_result: OpsResult | None = None
    if overall_success and not partial:
        # Collect created filenames to perform ops testing and deletion
        created_files = set()
        for s in stages:
            for t in s.transfers:
                if t.success:
                    created_files.add(safe_build_filename(t.size_label, t.worker, t.iteration))

        # We need to make sure we don't break out of the directory.
        ops_result = run_ops_stage(args, emitter, list(created_files))
        if not ops_result.success:
            overall_success = False

    summary_dict = {
        "runtime_s": round(total_runtime, 3),
        "stage_seconds_total": round(total_stage_seconds, 3),
        "stages_run": len(stages),
        "stages_failed": stages_failed,
        "total_bytes_up": total_bytes_up,
        "total_bytes_down": total_bytes_down,
        "total_up_KB": total_bytes_up // 1024,
        "total_down_KB": total_bytes_down // 1024,
        "agg_up_KBps": round(agg_up_kbps, 2),
        "agg_down_KBps": round(agg_down_kbps, 2),
        "ops_success": ops_result.success if ops_result else None,
        "ops_cd_count": ops_result.cd_count if ops_result else 0,
        "ops_list_count": ops_result.list_count if ops_result else 0,
        "ops_del_count": ops_result.delete_count if ops_result else 0,
        "success": overall_success,
        "partial": partial,
    }

    summary_result = "PARTIAL" if partial else ("OK" if overall_success else "FAIL")
    emitter.summary = dict(summary_dict, result=summary_result)
    emitter.emit_text(
        "summary",
        summary_result,
        [
            ("runtime_s", int(round(total_runtime))),
            ("stages", len(stages)),
            ("failed", stages_failed),
            ("total_up_KB", total_bytes_up // 1024),
            ("total_down_KB", total_bytes_down // 1024),
            ("agg_up_KBps", int(round(agg_up_kbps))),
            ("agg_down_KBps", int(round(agg_down_kbps))),
        ],
    )

    if args.format == "json":
        document = {
            "config": config_dict,
            "stages": [build_stage_record(s) for s in stages],
            "ops": {
                "success": ops_result.success if ops_result else None,
                "cd_count": ops_result.cd_count if ops_result else 0,
                "list_count": ops_result.list_count if ops_result else 0,
                "delete_count": ops_result.delete_count if ops_result else 0,
            }
            if ops_result
            else None,
            "summary": dict(summary_dict, result=summary_result),
        }
        try:
            print(json.dumps(document, indent=2, sort_keys=True), flush=True)
        except BrokenPipeError:
            raise SystemExit(0)

    if partial:
        return 2
    if overall_success:
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
