from __future__ import annotations

import atexit
import ftplib
import io
import os
import threading
import time
from typing import Callable

from u64_connection_runtime import (
    ProbeCorrectness,
    ProbeExecutionContext,
    ProbeOutcome,
    ProbeSurface,
    RuntimeSettings,
    run_incomplete_surface_operation,
    run_surface_operation,
    select_operation_index,
    surface_detail,
)


FTP_TEMP_DIR = "/Temp"
FTP_SELF_FILE_PREFIX = "u64test_"

_FTP_TRACKING_LOCK = threading.Lock()
_FTP_TRACKED_FILES: set[str] = set()
_FTP_SELF_FILE_COUNTER = 0
_FTP_CLEANUP_SETTINGS: RuntimeSettings | None = None
_FTP_CLEANUP_REGISTERED = False


def register_cleanup(settings: RuntimeSettings) -> None:
    global _FTP_CLEANUP_REGISTERED, _FTP_CLEANUP_SETTINGS
    with _FTP_TRACKING_LOCK:
        _FTP_CLEANUP_SETTINGS = settings
        if not _FTP_CLEANUP_REGISTERED:
            atexit.register(cleanup_self_files)
            _FTP_CLEANUP_REGISTERED = True


def track_self_file(settings: RuntimeSettings, path: str) -> None:
    register_cleanup(settings)
    with _FTP_TRACKING_LOCK:
        _FTP_TRACKED_FILES.add(path)


def forget_self_file(path: str) -> None:
    with _FTP_TRACKING_LOCK:
        _FTP_TRACKED_FILES.discard(path)


def known_self_files() -> tuple[str, ...]:
    with _FTP_TRACKING_LOCK:
        return tuple(sorted(_FTP_TRACKED_FILES))


def next_self_file_path() -> str:
    global _FTP_SELF_FILE_COUNTER
    with _FTP_TRACKING_LOCK:
        _FTP_SELF_FILE_COUNTER += 1
        counter = _FTP_SELF_FILE_COUNTER
    return f"{FTP_TEMP_DIR}/{FTP_SELF_FILE_PREFIX}{os.getpid()}_{counter}.txt"


def cleanup_self_files() -> None:
    settings = _FTP_CLEANUP_SETTINGS
    paths = known_self_files()
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
            forget_self_file(path)
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


def connect(settings: RuntimeSettings) -> ftplib.FTP:
    ftp = ftplib.FTP()
    greeting = ftp.connect(settings.host, settings.ftp_port, timeout=3)
    if not greeting.startswith("220"):
        raise RuntimeError(f"expected FTP 220, got {greeting}")
    login = ftp.login(settings.ftp_user, settings.ftp_pass)
    if not login.startswith("230"):
        raise RuntimeError(f"expected FTP 230, got {login}")
    ftp.set_pasv(True)
    return ftp


def close(ftp: ftplib.FTP | None) -> None:
    if ftp is None:
        return
    try:
        ftp.quit()
    except Exception:
        pass
    finally:
        try:
            ftp.close()
        except OSError:
            pass


def collect_temp_entries(ftp: ftplib.FTP) -> tuple[str, ...]:
    try:
        return tuple(ftp.nlst(FTP_TEMP_DIR))
    except ftplib.Error as error:
        raise RuntimeError(f"{FTP_TEMP_DIR} missing or unavailable: {error}") from error


def collect_temp_entries_if_available(ftp: ftplib.FTP) -> tuple[str, ...]:
    try:
        return collect_temp_entries(ftp)
    except RuntimeError:
        return ()


def readable_self_files(entries: tuple[str, ...]) -> tuple[str, ...]:
    candidates = []
    for entry in entries:
        basename = entry.rsplit("/", 1)[-1]
        if basename.startswith(FTP_SELF_FILE_PREFIX):
            candidates.append(entry if "/" in entry else f"{FTP_TEMP_DIR}/{entry}")
    return tuple(sorted(candidates))


def pick_known_self_file(entries: tuple[str, ...]) -> str | None:
    readable = readable_self_files(entries)
    if readable:
        return readable[0]
    owned = known_self_files()
    if owned:
        return owned[0]
    return None


def retr_binary(ftp: ftplib.FTP, path: str) -> int:
    buffer = bytearray()
    ftp.retrbinary(f"RETR {path}", buffer.extend)
    return len(buffer)


def list_lines(ftp: ftplib.FTP, path: str) -> int:
    lines: list[str] = []
    ftp.retrlines(f"LIST {path}", lines.append)
    return len(lines)


def seed_self_file(settings: RuntimeSettings, ftp: ftplib.FTP, ordinal: int) -> str:
    path = next_self_file_path()
    payload_bytes = f"{FTP_SELF_FILE_PREFIX}{os.getpid()}_{ordinal}\n".encode("utf-8")
    ftp.storbinary(f"STOR {path}", io.BytesIO(payload_bytes))
    track_self_file(settings, path)
    return path


def ensure_small_self_files(settings: RuntimeSettings, ftp: ftplib.FTP, minimum_count: int = 2) -> tuple[str, ...]:
    readable = list(readable_self_files(collect_temp_entries_if_available(ftp)))
    for path in readable:
        track_self_file(settings, path)
    while len(readable) < minimum_count:
        readable.append(seed_self_file(settings, ftp, len(readable) + 1))
    return tuple(sorted(readable))


def prime_temp_dir(settings: RuntimeSettings, minimum_count: int = 1) -> tuple[str, ...]:
    ftp = connect(settings)
    try:
        return tuple(seed_self_file(settings, ftp, ordinal) for ordinal in range(1, minimum_count + 1))
    finally:
        close(ftp)


def try_prime_temp_dir(
    settings: RuntimeSettings,
    minimum_count: int = 1,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[str, ...]:
    try:
        return prime_temp_dir(settings, minimum_count=minimum_count)
    except Exception as error:
        if log_fn is not None:
            log_fn(f"prime_temp_dir_failed detail={error} continuing=1")
        return ()


def list_temp_entries(settings: RuntimeSettings, ftp: ftplib.FTP) -> str:
    del settings
    entries = collect_temp_entries(ftp)
    return f"entries={len(entries)} path={FTP_TEMP_DIR}"


def read_small_self_file(settings: RuntimeSettings, ftp: ftplib.FTP, index: int) -> str:
    readable = ensure_small_self_files(settings, ftp, minimum_count=index + 1)
    path = readable[index]
    byte_count = retr_binary(ftp, path)
    if byte_count < 1:
        raise RuntimeError(f"empty FTP self file: {path}")
    return f"path={path} bytes={byte_count}"


def create_self_file(settings: RuntimeSettings, ftp: ftplib.FTP) -> str:
    path = next_self_file_path()
    payload = io.BytesIO(f"{FTP_SELF_FILE_PREFIX}{os.getpid()}\n".encode("utf-8"))
    ftp.storbinary(f"STOR {path}", payload)
    track_self_file(settings, path)
    return f"path={path} bytes={payload.getbuffer().nbytes}"


def rename_self_file(settings: RuntimeSettings, ftp: ftplib.FTP) -> str:
    owned = known_self_files()
    if not owned:
        return "skip=no_self_file"
    source = owned[0]
    target = next_self_file_path()
    ftp.rename(source, target)
    forget_self_file(source)
    track_self_file(settings, target)
    return f"from={source} to={target}"


def delete_self_file(settings: RuntimeSettings, ftp: ftplib.FTP) -> str:
    del settings
    owned = known_self_files()
    if not owned:
        return "skip=no_self_file"
    path = owned[0]
    ftp.delete(path)
    forget_self_file(path)
    return f"path={path}"


def pasv_only_abort(settings: RuntimeSettings) -> str:
    ftp = connect(settings)
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


def greeting_only_quit(settings: RuntimeSettings) -> str:
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


def login_only_abort(settings: RuntimeSettings) -> str:
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


def close_socket_quietly(sock) -> None:
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass


def partial_transfer_abort(
    settings: RuntimeSettings,
    command: str,
    *,
    payload: bytes | None = None,
    read_limit: int = 64,
) -> str:
    ftp = connect(settings)
    data_sock = None
    try:
        data_sock = ftp.transfercmd(command)
        if payload is None:
            chunk = data_sock.recv(read_limit)
            return f"command={command} bytes={len(chunk)}"
        data_sock.sendall(payload)
        return f"command={command} sent={len(payload)}"
    finally:
        close_socket_quietly(data_sock)
        try:
            ftp.close()
        except OSError:
            pass


def partial_stor_temp(settings: RuntimeSettings) -> str:
    path = next_self_file_path()
    track_self_file(settings, path)
    return partial_transfer_abort(settings, f"STOR {path}", payload=b"vivipi-partial\n")


def incomplete_operations(surface: ProbeSurface) -> tuple[tuple[str, Callable[[RuntimeSettings], str]], ...]:
    if surface == ProbeSurface.SMOKE:
        return (("ftp_greeting_only_quit", greeting_only_quit),)
    operations = (
        ("ftp_pasv_only_abort", pasv_only_abort),
        ("ftp_partial_list_root", lambda settings: partial_transfer_abort(settings, "LIST .")),
        ("ftp_pasv_only_abort", pasv_only_abort),
        ("ftp_partial_nlst_root", lambda settings: partial_transfer_abort(settings, "NLST .")),
    )
    if surface == ProbeSurface.READ:
        return operations
    return operations + (
        ("ftp_partial_stor_temp", partial_stor_temp),
        ("ftp_pasv_only_abort", pasv_only_abort),
        ("ftp_partial_list_root", lambda settings: partial_transfer_abort(settings, "LIST .")),
    )


def surface_operations(
    surface: ProbeSurface,
) -> tuple[tuple[str, Callable[[RuntimeSettings, ftplib.FTP], str]], ...]:
    read_operations = (
        ("ftp_pwd", lambda settings, ftp: f"pwd={ftp.pwd()}"),
        ("ftp_nlst_root", lambda settings, ftp: f"entries={len(tuple(ftp.nlst('.')))} path=."),
        ("ftp_list_root", lambda settings, ftp: f"lines={list_lines(ftp, '.')} path=."),
        ("ftp_nlst_temp", list_temp_entries),
    )
    if surface == ProbeSurface.SMOKE:
        return (("ftp_smoke_pwd", lambda settings, ftp: f"pwd={ftp.pwd()}"),)
    if surface == ProbeSurface.READ:
        return read_operations
    return read_operations + (
        ("ftp_create_self_file", create_self_file),
        ("ftp_read_self_file", lambda settings, ftp: read_small_self_file(settings, ftp, 0)),
        ("ftp_rename_self_file", rename_self_file),
        ("ftp_delete_self_file", delete_self_file),
    )


def run_probe(
    settings: RuntimeSettings,
    correctness: ProbeCorrectness,
    *,
    context: ProbeExecutionContext | None = None,
) -> ProbeOutcome:
    if context is not None:
        if correctness == ProbeCorrectness.INVALID:
            return run_probe_invalid(settings)
        surface = context.surface
        if correctness == ProbeCorrectness.INCOMPLETE:
            operations = incomplete_operations(surface)
            index = select_operation_index(context, len(operations))
            op_name, operation = operations[index]
            return run_incomplete_surface_operation("ftp", surface, op_name, operation, settings)
        operations = surface_operations(surface)
        index = select_operation_index(context, len(operations))
        op_name, operation = operations[index]
        started_at = time.perf_counter_ns()
        try:
            def surface_operation(current_settings: RuntimeSettings) -> str:
                ftp = None
                try:
                    ftp = connect(current_settings)
                    return operation(current_settings, ftp)
                finally:
                    close(ftp)

            detail = run_surface_operation("ftp", surface_operation, settings)
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("OK", surface_detail(surface, op_name, detail), elapsed_ms)
        except Exception as error:
            elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
            return ProbeOutcome("FAIL", surface_detail(surface, op_name, str(error)), elapsed_ms)

    if correctness == ProbeCorrectness.INCOMPLETE:
        return run_probe_incomplete(settings)
    if correctness == ProbeCorrectness.INVALID:
        return run_probe_invalid(settings)

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


def run_probe_incomplete(settings: RuntimeSettings) -> ProbeOutcome:
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


def run_probe_invalid(settings: RuntimeSettings) -> ProbeOutcome:
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
