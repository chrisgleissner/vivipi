#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import u64_connection_test as stress_types

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

stress = importlib.import_module("u64_connection_test")


PROTOCOLS = ("ping", "http", "ftp", "telnet")
DEFAULT_POST_CHECK_SECONDS = (5,)
STRESS_SCRIPT = SCRIPT_DIR / "u64_connection_test.py"
TRANSIENT_FAILURE_MARKERS = (
    "errno 104",
    "errno 110",
    "errno 111",
    "connection reset by peer",
    "connection refused",
    "timed out",
    "timeout",
    "broken pipe",
)


@dataclass(frozen=True)
class CrashCheckConfig:
    host: str
    ftp_user: str
    ftp_pass: str
    http_path: str
    http_port: int
    ftp_port: int
    telnet_port: int
    stress_duration_s: int
    post_check_seconds: tuple[int, ...]
    forwarded_args: tuple[str, ...]


def sanitize(value: str) -> str:
    return value.replace('"', "'")


def log(result: str, detail: str) -> None:
    print(f'{stress.ts()} protocol=crash-check result={result} detail="{sanitize(detail)}"', flush=True)


def parse_post_check_seconds(value: str) -> tuple[int, ...]:
    if not value.strip():
        raise argparse.ArgumentTypeError("post-check seconds must not be blank")
    checkpoints = []
    previous = 0
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part.isdigit():
            raise argparse.ArgumentTypeError("post-check seconds must be comma-separated positive integers")
        checkpoint = int(part)
        if checkpoint < 1 or checkpoint <= previous:
            raise argparse.ArgumentTypeError("post-check seconds must be strictly increasing positive integers")
        checkpoints.append(checkpoint)
        previous = checkpoint
    return tuple(checkpoints)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the U64 stress profile and verify persistent full-surface degradation.")
    parser.add_argument("-H", "--host", default=stress.DEFAULT_PROFILE_HOST, help="Target host or IP")
    parser.add_argument("-u", "--ftp-user", default=stress.FTP_USER, help="FTP username")
    parser.add_argument("-P", "--ftp-pass", default=stress.FTP_PASS, help="FTP password")
    parser.add_argument("-p", "--http-path", default=stress.HTTP_PATH, help="HTTP path")
    parser.add_argument("-w", "--http-port", type=int, default=stress.HTTP_PORT, help="HTTP port")
    parser.add_argument("-f", "--ftp-port", type=int, default=stress.FTP_PORT, help="FTP port")
    parser.add_argument("-t", "--telnet-port", type=int, default=stress.TELNET_PORT, help="Telnet port")
    parser.add_argument(
        "-D",
        "--stress-duration-s",
        type=int,
        default=5,
        help="Stress duration in seconds unless full degradation is detected sooner",
    )
    parser.add_argument(
        "-c",
        "--post-check-seconds",
        type=parse_post_check_seconds,
        default=DEFAULT_POST_CHECK_SECONDS,
        help="Comma-separated post-stress checkpoints in seconds",
    )
    return parser


def parse_args(argv: list[str]) -> CrashCheckConfig:
    parser = build_parser()
    args, forwarded = parser.parse_known_args(argv)
    forwarded_args = tuple(argument for argument in forwarded if argument != "--")
    return CrashCheckConfig(
        host=args.host,
        ftp_user=args.ftp_user,
        ftp_pass=args.ftp_pass,
        http_path=args.http_path,
        http_port=args.http_port,
        ftp_port=args.ftp_port,
        telnet_port=args.telnet_port,
        stress_duration_s=args.stress_duration_s,
        post_check_seconds=args.post_check_seconds,
        forwarded_args=forwarded_args,
    )


def build_runtime_settings(config: CrashCheckConfig) -> stress_types.RuntimeSettings:
    return stress.RuntimeSettings(
        host=config.host,
        http_path=config.http_path,
        http_port=config.http_port,
        telnet_port=config.telnet_port,
        ftp_port=config.ftp_port,
        ftp_user=config.ftp_user,
        ftp_pass=config.ftp_pass,
        delay_ms=0,
        log_every=1,
        verbose=False,
    )


def find_python() -> str:
    return shutil.which("python3") or shutil.which("python") or sys.executable


def build_stress_command(config: CrashCheckConfig) -> list[str]:
    return [
        find_python(),
        str(STRESS_SCRIPT),
        *config.forwarded_args,
        "-H",
        config.host,
        "--http-path",
        config.http_path,
        "--http-port",
        str(config.http_port),
        "--ftp-port",
        str(config.ftp_port),
        "--telnet-port",
        str(config.telnet_port),
        "-u",
        config.ftp_user,
        "-P",
        config.ftp_pass,
        "--profile",
        stress.PROFILE_STRESS,
        "--duration-s",
        str(config.stress_duration_s),
    ]


def run_probe_round(settings: stress_types.RuntimeSettings) -> dict[str, stress_types.ProbeOutcome]:
    return {
        protocol: stress.PROBE_RUNNERS[protocol](settings, stress.ProbeCorrectness.CORRECT)
        for protocol in PROTOCOLS
    }


def all_probes_failed(outcomes: dict[str, stress_types.ProbeOutcome]) -> bool:
    return all(outcome.result == "FAIL" for outcome in outcomes.values())


def is_transient_failure(protocol: str, outcome: stress_types.ProbeOutcome) -> bool:
    if outcome.result != "FAIL":
        return False
    if protocol == "ping":
        return False
    detail = outcome.detail.lower()
    return any(marker in detail for marker in TRANSIENT_FAILURE_MARKERS)


def is_persistent_failure_candidate(outcomes: dict[str, stress_types.ProbeOutcome]) -> bool:
    if not all_probes_failed(outcomes):
        return False
    return not any(is_transient_failure(protocol, outcome) for protocol, outcome in outcomes.items())


def survivors(outcomes: dict[str, stress_types.ProbeOutcome]) -> list[str]:
    return [protocol for protocol, outcome in outcomes.items() if outcome.result == "OK"]


def log_probe_summary(phase: str, *, after_s: int | None = None, outcomes: dict[str, stress_types.ProbeOutcome]) -> None:
    parts = [f"phase={phase}"]
    if after_s is not None:
        parts.append(f"after_s={after_s}")
    parts.extend(f"{protocol}={outcomes[protocol].result}" for protocol in PROTOCOLS)
    log("INFO", " ".join(parts))


def log_probe_details(*, checkpoint: int, outcomes: dict[str, stress_types.ProbeOutcome]) -> None:
    for protocol in PROTOCOLS:
        outcome = outcomes[protocol]
        log(
            "INFO",
            f"phase=post-check after_s={checkpoint} protocol={protocol} state={outcome.result} detail={outcome.detail}",
        )


def monitor_stress_process(process: subprocess.Popen[bytes], settings: stress_types.RuntimeSettings) -> bool:
    consecutive_full_failures = 0
    while process.poll() is None:
        outcomes = run_probe_round(settings)
        if is_persistent_failure_candidate(outcomes):
            consecutive_full_failures += 1
        else:
            consecutive_full_failures = 0
        if consecutive_full_failures >= 2:
            log_probe_summary("stress-check", outcomes=outcomes)
            log("OK", "phase=stress full_degradation_detected stopping_stress=1")
            process.terminate()
            return True
        if process.poll() is None:
            time.sleep(1)
    return False


def stop_process(process: subprocess.Popen[bytes]) -> int | None:
    if process.poll() is not None:
        return process.wait()
    process.terminate()
    try:
        return process.wait(timeout=2)
    except TypeError:
        return process.wait()
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait()


def run_post_checks(checkpoints: tuple[int, ...], settings: stress_types.RuntimeSettings) -> int:
    previous = 0
    survivor_lines: list[str] = []
    all_failed_every_time = True

    for checkpoint in checkpoints:
        wait_s = checkpoint - previous
        if wait_s > 0:
            log("INFO", f"phase=post-check waiting_s={wait_s} target_s={checkpoint}")
            time.sleep(wait_s)

        outcomes = run_probe_round(settings)
        log_probe_summary("post-check", after_s=checkpoint, outcomes=outcomes)
        log_probe_details(checkpoint=checkpoint, outcomes=outcomes)

        checkpoint_survivors = survivors(outcomes)
        if checkpoint_survivors:
            all_failed_every_time = False
            survivor_lines.append(f"after_s={checkpoint}:{','.join(checkpoint_survivors)}")
        previous = checkpoint

    if all_failed_every_time:
        checkpoints_text = ",".join(str(value) for value in checkpoints)
        log("OK", f"crash_detected checkpoints_s={checkpoints_text}")
        return 0

    detail = " ".join(survivor_lines) if survivor_lines else "none"
    log("FAIL", f"crash_not_detected survivors={detail}")
    return 1


def main(argv: list[str]) -> int:
    config = parse_args(argv)
    settings = build_runtime_settings(config)
    command = build_stress_command(config)
    log("INFO", f"phase=stress command={shlex.join(command)}")

    process = subprocess.Popen(command)
    log("INFO", f"phase=stress pid={process.pid}")

    stopped_early = False
    stress_status = None
    try:
        stopped_early = monitor_stress_process(process, settings)
        stress_status = process.wait()
    except KeyboardInterrupt:
        log("INFO", "cancelled")
        stop_process(process)
        return 0
    finally:
        if process.poll() is None:
            stop_process(process)

    if stopped_early:
        log("INFO", f"phase=stress returncode={stress_status} stopped_early=1")
    else:
        log("INFO", f"phase=stress returncode={stress_status}")

    if not stopped_early and stress_status != 0:
        log("FAIL", f"phase=stress failed_returncode={stress_status}")
        return 2

    return run_post_checks(config.post_check_seconds, settings)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(0)