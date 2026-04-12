#!/usr/bin/env python3

import argparse
import ftplib
import http.client
import math
import os
import re
import socket
import subprocess
import sys
import time

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


def parse_bool(value: str) -> bool:
    return value.strip().lower() not in {"", "0", "false", "no"}


VERBOSE = parse_bool(os.getenv("VERBOSE", "0"))


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repeated U64 connectivity checks")
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
    for protocol in ("ping", "http", "ftp", "telnet"):
        parts.append(f"{protocol}_median_ms={percentile_ms(protocol, 50)}")
        parts.append(f"{protocol}_p90_ms={percentile_ms(protocol, 90)}")
        parts.append(f"{protocol}_p99_ms={percentile_ms(protocol, 99)}")
    try:
        print(f'{ts()} protocol=iteration result=INFO detail="{" ".join(parts)}"', flush=True)
    except BrokenPipeError:
        raise SystemExit(0)


def sleep_ms(value: int) -> None:
    time.sleep(value / 1000.0)


def ping_check() -> None:
    started_at = time.perf_counter_ns()
    try:
        result = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", "2", HOST],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        if result.returncode == 0:
            match = re.search(r"time=([0-9.]+)", result.stdout)
            if match:
                log_check("ping", "OK", f"icmp_reply_ms={match.group(1)}", elapsed_ms)
            else:
                log_check("ping", "OK", "icmp reply", elapsed_ms)
            return
        detail = next((line for line in (result.stderr + "\n" + result.stdout).splitlines() if line.strip()), "ping failed")
        log_check("ping", "FAIL", detail, elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        log_check("ping", "FAIL", f"ping failed: {error}", elapsed_ms)


def http_check() -> None:
    conn = http.client.HTTPConnection(HOST, HTTP_PORT, timeout=8)
    started_at = time.perf_counter_ns()
    try:
        conn.request("GET", f"/{HTTP_PATH}", headers={"Connection": "close"})
        response = conn.getresponse()
        body = response.read()
        if not 200 <= response.status < 300:
            raise RuntimeError(f"expected HTTP 2xx, got {response.status}")
        if not body:
            raise RuntimeError("empty HTTP body")
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        log_check("http", "OK", f"HTTP {response.status} body_bytes={len(body)}", elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        log_check("http", "FAIL", f"http failed: {error}", elapsed_ms)
    finally:
        conn.close()


def telnet_check() -> None:
    sock = None
    started_at = time.perf_counter_ns()
    try:
        sock = socket.create_connection((HOST, TELNET_PORT), timeout=2)
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
            index = 0
            while index < len(chunk):
                byte = chunk[index]
                if byte == IAC and index + 2 < len(chunk) and chunk[index + 1] in (DO, DONT, WILL, WONT):
                    command = chunk[index + 1]
                    option = chunk[index + 2]
                    reply = bytes([IAC, WONT if command in (DO, DONT) else DONT, option])
                    sock.sendall(reply)
                    index += 3
                    continue
                visible.append(byte)
                index += 1
        text = bytes(visible).decode("utf-8", "ignore").strip()
        if not text:
            raise RuntimeError("empty telnet banner")
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        log_check("telnet", "OK", f"banner_bytes={len(text.encode())}", elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        log_check("telnet", "FAIL", f"telnet failed: {error}", elapsed_ms)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def ftp_check() -> None:
    ftp = ftplib.FTP()
    started_at = time.perf_counter_ns()
    try:
        greeting = ftp.connect(HOST, FTP_PORT, timeout=8)
        if not greeting.startswith("220"):
            raise RuntimeError(f"expected FTP 220, got {greeting}")
        login = ftp.login(FTP_USER, FTP_PASS)
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
        log_check("ftp", "OK", f"NLST bytes={sum(len(name) for name in names)}", elapsed_ms)
    except Exception as error:
        elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000.0
        log_check("ftp", "FAIL", f"ftp failed: {error}", elapsed_ms)
        try:
            ftp.close()
        except OSError:
            pass


def main(argv: list[str]) -> int:
    global HOST, HTTP_PATH, HTTP_PORT, TELNET_PORT, FTP_PORT, FTP_USER, FTP_PASS, INTER_CALL_DELAY_MS, LOG_EVERY_N_ITERATIONS, VERBOSE, CURRENT_ITERATION
    parser = build_parser()
    args = parser.parse_args(argv)
    HOST = args.host
    HTTP_PATH = args.http_path
    HTTP_PORT = args.http_port
    TELNET_PORT = args.telnet_port
    FTP_PORT = args.ftp_port
    FTP_USER = args.ftp_user
    FTP_PASS = args.ftp_pass
    INTER_CALL_DELAY_MS = args.delay_ms
    LOG_EVERY_N_ITERATIONS = args.log_every
    VERBOSE = args.verbose

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


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(0)