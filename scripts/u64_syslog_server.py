#!/usr/bin/env python3

from __future__ import annotations

import argparse
import signal
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 514
DEFAULT_LOG_FILE = Path(__file__).resolve().parent / "logs" / "u64-syslog.log"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Receive UDP syslog packets and tail them to stdout.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host or IP address to bind.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="UDP port to bind.")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE, help="File where received messages are appended.")
    return parser


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_packet(payload: bytes, source_host: str, source_port: int) -> str:
    message = payload.decode("utf-8", "replace").replace("\r", "\\r").replace("\n", "\\n")
    return f"{utc_timestamp()} from={source_host}:{source_port} {message}"


def main() -> int:
    args = build_parser().parse_args()
    args.log_file.parent.mkdir(parents=True, exist_ok=True)

    should_stop = False

    def handle_signal(signum, _frame):
        nonlocal should_stop
        should_stop = True
        print(f"{utc_timestamp()} listener=stopping signal={signum}", flush=True)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    sock.settimeout(0.5)

    print(
        f"{utc_timestamp()} listener=ready bind={args.host}:{args.port} log_file={args.log_file}",
        flush=True,
    )

    with args.log_file.open("a", encoding="utf-8") as handle:
        while not should_stop:
            try:
                payload, (source_host, source_port) = sock.recvfrom(65535)
            except TimeoutError:
                continue
            line = format_packet(payload, source_host, source_port)
            print(line, flush=True)
            handle.write(line + "\n")
            handle.flush()

    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())