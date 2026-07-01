#!/usr/bin/env python3
"""Mixed-traffic slowloris test for the Ultimate 64 HTTP server's idle reaper.

Opens a number of silent connections (which each occupy a client slot) and holds
them while a background thread keeps issuing real requests. Constant real traffic
keeps the server busy, so this specifically exercises the *per-connection* idle
reaper: a purely global "no activity anywhere" reaper could never reap the silent
connections while other connections are active. The test passes when the server
closes the held-silent connections at ~HTTP_CONN_IDLE_TIMEOUT while the concurrent
real requests keep succeeding.
"""
from __future__ import annotations

import argparse
import os
import socket
import threading
import time
import urllib.request

HOST = os.getenv("HOST", "u64")
HTTP_PORT = int(os.getenv("HTTP_PORT", "80"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Mixed-traffic slowloris test for the Ultimate 64 HTTP server's "
            "per-connection idle reaper. Holds --hold silent connections while a "
            "background thread issues real requests, and verifies the server reaps "
            "the silent connections at about --idle-timeout-s while the concurrent "
            "requests keep succeeding."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            "  slowloris_test.py -H u64 --hold 3 --idle-timeout-s 15\n"
        ),
    )
    parser.add_argument("-H", "--host", default=HOST, help="Target host or IP")
    parser.add_argument("--http-port", type=int, default=HTTP_PORT, help="REST HTTP port")
    parser.add_argument("--hold", type=int, default=3,
                        help="Number of silent connections to open and hold.")
    parser.add_argument("--idle-timeout-s", type=float, default=15.0,
                        help="Expected server-side idle-reap deadline (HTTP_CONN_IDLE_TIMEOUT).")
    parser.add_argument("--max-wait-s", type=float, default=30.0,
                        help="Give up (fail) if the silent connections are not reaped within this window.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Increase stderr diagnostics.")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    base = f"http://{args.host}:{args.http_port}/v1/version"

    stop = threading.Event()
    stats = {"ok": 0, "n": 0}

    def busy() -> None:
        while not stop.is_set():
            stats["n"] += 1
            try:
                urllib.request.urlopen(base, timeout=4).read()
                stats["ok"] += 1
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.05)

    worker = threading.Thread(target=busy, daemon=True)
    worker.start()
    time.sleep(1.0)

    holds = []
    for _ in range(args.hold):
        holds.append(socket.create_connection((args.host, args.http_port), timeout=4))
    print(f"opened {len(holds)} silent connections on {args.host}:{args.http_port}, holding...", flush=True)

    reaped_at = None
    t0 = time.time()
    while time.time() - t0 < args.max_wait_s:
        closed = 0
        for s in holds:
            s.setblocking(False)
            try:
                if s.recv(1) == b"":
                    closed += 1
            except BlockingIOError:
                pass
            except OSError:
                closed += 1
            s.setblocking(True)
        if args.verbose:
            print(f"  t={time.time() - t0:4.1f}s closed={closed}/{len(holds)} busy_ok={stats['ok']}/{stats['n']}", flush=True)
        if closed == len(holds):
            reaped_at = time.time() - t0
            break
        time.sleep(0.5)

    stop.set()
    time.sleep(0.3)
    for s in holds:
        try:
            s.close()
        except OSError:
            pass

    print(f"concurrent real requests during test: {stats['ok']}/{stats['n']} ok", flush=True)
    if reaped_at is None:
        print(f"FAIL: {len(holds)} silent connections were NOT reaped within {args.max_wait_s:.0f}s "
              f"(expected ~{args.idle_timeout_s:.0f}s)", flush=True)
        return 1
    print(f"PASS: silent connections reaped at ~{reaped_at:.0f}s "
          f"(HTTP_CONN_IDLE_TIMEOUT={args.idle_timeout_s:.0f}s) despite concurrent traffic", flush=True)
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
