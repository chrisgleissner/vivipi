#!/usr/bin/env python3
"""Network-stack stability A/B harness for the Ultimate 64 REST/FTP/Telnet stack.

Purpose
-------
Force externally reachable HTTP-server connection-churn wedges while continuously
measuring the health of the device's network services (REST, FTP, Telnet) and the
config-mutation path that the reported "multiple config changes hang the device"
symptom lives on (SID volume via both PUT and POST /v1/configs, with read-back).

It reuses the reviewed ViviPi probe primitives (u64_http.run_probe_incomplete for
the connection-churn forcing function, u64_http.request_bytes / audio_mixer helpers
for REST) so the A and B runs exercise identical, already-trusted client behaviour.

Outputs (in --out-dir):
  <label>.jsonl   one JSON object per health probe (raw log, retained on disk)
  <label>.json    machine-readable final summary
  <label>.md      human-readable Markdown summary

A run is an A/B leg: run once against baseline (unpatched) firmware and once
against patched firmware with identical flags, then compare the two summaries.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# The shared u64_* probe modules live one level up, in scripts/u64/.
U64_DIR = SCRIPT_DIR.parent
if str(U64_DIR) not in sys.path:
    sys.path.insert(0, str(U64_DIR))

import u64_http  # noqa: E402
import u64_ident  # noqa: E402
import u64_raw64  # noqa: E402
from u64_connection_runtime import (  # noqa: E402
    ProbeCorrectness,
    ProbeExecutionContext,
    ProbeSurface,
    RuntimeSettings,
)


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def classify_error(err: BaseException) -> str:
    if isinstance(err, socket.timeout) or isinstance(err, TimeoutError):
        return "timeout"
    if isinstance(err, ConnectionRefusedError):
        return "refused"
    if isinstance(err, ConnectionResetError):
        return "reset"
    if isinstance(err, (BrokenPipeError, ConnectionAbortedError)):
        return "reset"
    if isinstance(err, OSError):
        return "oserror"
    return "other"


@dataclass
class ProbeStat:
    name: str
    attempts: int = 0
    ok: int = 0
    fail: int = 0
    timeout: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    fail_kinds: dict[str, int] = field(default_factory=dict)
    last_detail: str = ""

    def record(self, ok: bool, latency_ms: float, kind: str, detail: str) -> None:
        self.attempts += 1
        self.latencies_ms.append(latency_ms)
        self.last_detail = detail
        if ok:
            self.ok += 1
        else:
            self.fail += 1
            if kind == "timeout":
                self.timeout += 1
            self.fail_kinds[kind] = self.fail_kinds.get(kind, 0) + 1

    def pct(self, p: int) -> float:
        if not self.latencies_ms:
            return 0.0
        s = sorted(self.latencies_ms)
        import math
        rank = max(1, math.ceil(p / 100.0 * len(s)))
        return round(s[rank - 1], 1)

    def summary(self) -> dict:
        return {
            "name": self.name, "attempts": self.attempts, "ok": self.ok,
            "fail": self.fail, "timeout": self.timeout,
            "success_pct": round(100.0 * self.ok / self.attempts, 1) if self.attempts else 0.0,
            "p50_ms": self.pct(50), "p90_ms": self.pct(90), "p99_ms": self.pct(99),
            "max_ms": round(max(self.latencies_ms), 1) if self.latencies_ms else 0.0,
            "fail_kinds": self.fail_kinds, "last_detail": self.last_detail,
        }


# ---- individual health probes: return (ok, latency_ms, kind, detail) ----

def probe_rest_get(settings: RuntimeSettings, path: str, timeout: float):
    t0 = time.perf_counter_ns()
    try:
        status, body, _ = _http_get(settings, path, timeout)
        el = (time.perf_counter_ns() - t0) / 1e6
        if not (200 <= status < 300):
            return False, el, "http_error", f"{path} status={status}"
        if not body:
            return False, el, "http_error", f"{path} empty_body"
        return True, el, "", f"{path} status={status} bytes={len(body)}"
    except Exception as e:  # noqa: BLE001
        el = (time.perf_counter_ns() - t0) / 1e6
        return False, el, classify_error(e), f"{path} {type(e).__name__}:{e}"


def _http_get(settings: RuntimeSettings, path: str, timeout: float):
    conn = http.client.HTTPConnection(settings.host, settings.http_port, timeout=timeout)
    try:
        headers = {"Connection": "close"}
        if settings.network_password:
            headers["X-Password"] = settings.network_password
        conn.request("GET", path, headers=headers)
        r = conn.getresponse()
        b = r.read()
        return r.status, b, {k.lower(): v for k, v in r.getheaders()}
    finally:
        conn.close()


_VALUES_CACHE: list[str] = []
_VALUES_LOCK = threading.Lock()


def _resolve_target(settings: RuntimeSettings, target: str) -> str:
    """Map a friendly target (e.g. '0 dB') to the device's exact enum string
    (e.g. ' 0 dB'), caching the values list since it is static."""
    with _VALUES_LOCK:
        values = list(_VALUES_CACHE)
    if not values:
        _current, values, _b = u64_http.audio_mixer_item_state(settings)
        with _VALUES_LOCK:
            _VALUES_CACHE[:] = values
    return u64_http.resolve_audio_mixer_value(tuple(values), target)


def probe_cfg_put_readback(settings: RuntimeSettings, target: str, timeout: float):
    """PUT SID volume via query param (the 'working' method), then GET read-back."""
    import urllib.parse
    t0 = time.perf_counter_ns()
    try:
        resolved = _resolve_target(settings, target)
        enc = urllib.parse.quote(resolved, safe="")
        conn = http.client.HTTPConnection(settings.host, settings.http_port, timeout=timeout)
        try:
            headers = {"Connection": "close"}
            if settings.network_password:
                headers["X-Password"] = settings.network_password
            conn.request("PUT", f"{u64_http.HTTP_VOLUME_ULTISID_1_PATH}?value={enc}", headers=headers)
            r = conn.getresponse()
            r.read()
            status = r.status
        finally:
            conn.close()
        if not (200 <= status < 300):
            el = (time.perf_counter_ns() - t0) / 1e6
            return False, el, "http_error", f"put status={status}"
        current, _values, _b = u64_http.audio_mixer_item_state(settings)
        el = (time.perf_counter_ns() - t0) / 1e6
        if u64_http.normalize_audio_mixer_value(current) != u64_http.normalize_audio_mixer_value(target):
            return False, el, "verify", f"put target={target} readback={current}"
        return True, el, "", f"put target={target} readback_ok={current}"
    except Exception as e:  # noqa: BLE001
        el = (time.perf_counter_ns() - t0) / 1e6
        return False, el, classify_error(e), f"put {type(e).__name__}:{e}"


def probe_cfg_post_batch(settings: RuntimeSettings, target: str, timeout: float):
    """POST /v1/configs JSON batch (the reported 'hangs' method), then read-back.

    This is the exact heavyweight POST pipeline (multipart/tempfile/malloc/jsmn/
    JSON) that the reported LED/SID POST symptom exercises."""
    t0 = time.perf_counter_ns()
    try:
        resolved = _resolve_target(settings, target)
        body = json.dumps({"Audio Mixer": {"Vol UltiSid 1": resolved}}).encode("utf-8")
        conn = http.client.HTTPConnection(settings.host, settings.http_port, timeout=timeout)
        try:
            headers = {"Connection": "close", "Content-Type": "application/json"}
            if settings.network_password:
                headers["X-Password"] = settings.network_password
            conn.request("POST", "/v1/configs", body=body, headers=headers)
            r = conn.getresponse()
            r.read()
            status = r.status
        finally:
            conn.close()
        el = (time.perf_counter_ns() - t0) / 1e6
        if not (200 <= status < 300):
            return False, el, "http_error", f"post status={status}"
        current, _values, _b = u64_http.audio_mixer_item_state(settings)
        el = (time.perf_counter_ns() - t0) / 1e6
        if u64_http.normalize_audio_mixer_value(current) != u64_http.normalize_audio_mixer_value(target):
            return False, el, "verify", f"post target={target} readback={current}"
        return True, el, "", f"post target={target} readback_ok={current}"
    except Exception as e:  # noqa: BLE001
        el = (time.perf_counter_ns() - t0) / 1e6
        return False, el, classify_error(e), f"post {type(e).__name__}:{e}"


def probe_ftp(settings: RuntimeSettings, timeout: float):
    t0 = time.perf_counter_ns()
    s = None
    try:
        s = socket.create_connection((settings.host, settings.ftp_port), timeout=timeout)
        s.settimeout(timeout)
        banner = s.recv(256)
        el = (time.perf_counter_ns() - t0) / 1e6
        if not banner.startswith(b"220"):
            return False, el, "protocol", f"ftp banner={banner[:40]!r}"
        try:
            s.sendall(b"QUIT\r\n")
            s.recv(128)
        except OSError:
            pass
        return True, el, "", f"ftp banner_ok bytes={len(banner)}"
    except Exception as e:  # noqa: BLE001
        el = (time.perf_counter_ns() - t0) / 1e6
        return False, el, classify_error(e), f"ftp {type(e).__name__}:{e}"
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


def probe_telnet(settings: RuntimeSettings, timeout: float):
    t0 = time.perf_counter_ns()
    s = None
    try:
        s = socket.create_connection((settings.host, settings.telnet_port), timeout=timeout)
        s.settimeout(timeout)
        data = s.recv(256)
        el = (time.perf_counter_ns() - t0) / 1e6
        if not data:
            return False, el, "protocol", "telnet empty_banner"
        return True, el, "", f"telnet connect_ok bytes={len(data)}"
    except Exception as e:  # noqa: BLE001
        el = (time.perf_counter_ns() - t0) / 1e6
        return False, el, classify_error(e), f"telnet {type(e).__name__}:{e}"
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


def _smoke_ctx(protocol: str):
    return ProbeExecutionContext(protocol=protocol, runner_id=1, iteration=1,
                                 surface=ProbeSurface.SMOKE, state=None)


def probe_ident(settings: RuntimeSettings, timeout: float):
    """UDP identify listener on port 64."""
    t0 = time.perf_counter_ns()
    try:
        outcome = u64_ident.run_probe(settings, ProbeCorrectness.COMPLETE, context=_smoke_ctx("ident"))
        el = (time.perf_counter_ns() - t0) / 1e6
        ok = outcome.result == "OK"
        return ok, el, ("" if ok else "protocol"), f"ident {outcome.detail[:60]}"
    except Exception as e:  # noqa: BLE001
        el = (time.perf_counter_ns() - t0) / 1e6
        return False, el, classify_error(e), f"ident {type(e).__name__}:{e}"


def probe_dma(settings: RuntimeSettings, timeout: float):
    """DMA-capable TCP command listener on port 64."""
    t0 = time.perf_counter_ns()
    try:
        outcome = u64_raw64.run_probe(settings, ProbeCorrectness.COMPLETE, context=_smoke_ctx("dma"))
        el = (time.perf_counter_ns() - t0) / 1e6
        ok = outcome.result == "OK"
        return ok, el, ("" if ok else "protocol"), f"dma {outcome.detail[:60]}"
    except Exception as e:  # noqa: BLE001
        el = (time.perf_counter_ns() - t0) / 1e6
        return False, el, classify_error(e), f"dma {type(e).__name__}:{e}"


REST_PROBES = ("rest_version", "rest_info", "cfg_put_sid", "cfg_post_batch")

HOST = os.getenv("HOST", "u64")
HTTP_PORT = int(os.getenv("HTTP_PORT", "80"))
FTP_PORT = int(os.getenv("FTP_PORT", "21"))
TELNET_PORT = int(os.getenv("TELNET_PORT", "23"))
NETWORK_PASSWORD = os.getenv("NETWORK_PASSWORD", "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Network-stack stability A/B harness for the Ultimate 64. Drives an HTTP "
            "hostile-input flood (connection churn plus malformed requests) while "
            "health-probing every network listener (REST, FTP, Telnet, DMA/TCP-64, "
            "identify/UDP-64) and mutating SID volume via PUT and POST /v1/configs "
            "with read-back. Run once against baseline (unpatched) firmware and once "
            "against patched firmware with identical flags, then compare the two "
            "<label>.json summaries."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  # baseline (unpatched firmware)\n"
            "  network_stack_ab.py -H u64 --churn-workers 4 --duration-s 120 \\\n"
            "      --probe-timeout-s 4 --out-dir out --label baseline\n"
            "  # patched (10-minute all-listener soak)\n"
            "  network_stack_ab.py -H u64 --churn-workers 2 --malformed-workers 2 \\\n"
            "      --duration-s 600 --probe-timeout-s 8 --telnet-timeout-s 10 \\\n"
            "      --out-dir out --label patched\n"
        ),
    )
    parser.add_argument("-H", "--host", default=HOST, help="Target host or IP")
    parser.add_argument("--http-port", type=int, default=HTTP_PORT, help="REST HTTP port")
    parser.add_argument("--ftp-port", type=int, default=FTP_PORT, help="FTP control port")
    parser.add_argument("--telnet-port", type=int, default=TELNET_PORT, help="Telnet port")
    parser.add_argument("--network-password", default=NETWORK_PASSWORD,
                        help="Shared device network password used for HTTP, Telnet, FTP, and DMA.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Increase stderr diagnostics.")
    parser.add_argument("--duration-s", type=float, default=600.0, help="Total soak duration in seconds.")
    parser.add_argument("--churn-workers", type=int, default=4,
                        help="HTTP incomplete-abort (partial request + RST) churn threads; 0 disables.")
    parser.add_argument("--h1-workers", type=int, default=0,
                        help="Threads flooding oversized (40-header) requests to exercise the header-field bound; 0 disables.")
    parser.add_argument("--malformed-workers", type=int, default=0,
                        help="Threads flooding a rotating mix of malformed requests (many-header, negative Content-Length, truncated %% escape); 0 disables.")
    parser.add_argument("--tcp-churn-ports", default="",
                        help="Comma-separated ports to hammer with connect+RST churn (e.g. 21,23,64) so every TCP listener is stressed.")
    parser.add_argument("--health-interval-s", type=float, default=1.0,
                        help="Seconds between all-listener health sweeps.")
    parser.add_argument("--post-every", type=int, default=5,
                        help="Run the POST /v1/configs batch probe every Nth health cycle.")
    parser.add_argument("--probe-timeout-s", type=float, default=4.0,
                        help="Per-probe availability timeout in seconds (REST/FTP/DMA/identify).")
    parser.add_argument("--telnet-timeout-s", type=float, default=10.0,
                        help="Telnet probe timeout in seconds; Telnet runs at the lowest task priority, so allow a realistic client wait under heavy flood.")
    parser.add_argument("--out-dir", default="./out/netstack-ab",
                        help="Output directory for the <label>.jsonl/.json/.md run artifacts.")
    parser.add_argument("--label", required=True,
                        help="Run label and output basename, e.g. baseline-e7a642a5 / patched-b3bc8a5.")
    parser.add_argument("--fw-commit", default=os.getenv("FW_COMMIT", ""),
                        help="Firmware commit label recorded in the run summary metadata.")
    parser.add_argument("--httpd-commit", default=os.getenv("HTTPD_COMMIT", ""),
                        help="MicroHttpServer submodule commit label recorded in the run summary metadata.")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    settings = RuntimeSettings(
        host=args.host, http_path="v1/version", http_port=args.http_port,
        telnet_port=args.telnet_port, ftp_port=args.ftp_port, ftp_user="anonymous",
        ftp_pass=args.network_password, delay_ms=0, log_every=1, verbose=args.verbose,
        network_password=args.network_password, modem_port=3000)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"{args.label}.jsonl"
    json_path = out_dir / f"{args.label}.json"
    md_path = out_dir / f"{args.label}.md"

    stop = threading.Event()
    churn_count = [0]
    h1_count = [0]
    churn_lock = threading.Lock()

    def churn_worker():
        while not stop.is_set():
            u64_http.run_probe_incomplete(settings)
            with churn_lock:
                churn_count[0] += 1

    def h1_worker():
        req = b"GET /v1/version HTTP/1.1\r\nHost: x\r\n" + b"".join(
            b"X-H%d: v\r\n" % i for i in range(40)) + b"\r\n"
        while not stop.is_set():
            try:
                s = socket.create_connection((settings.host, settings.http_port), timeout=4)
                try:
                    s.sendall(req)
                    s.recv(64)
                finally:
                    s.close()
            except OSError:
                pass
            with churn_lock:
                h1_count[0] += 1

    # Rotating malformed requests: 40-header overflow (H1), negative
    # Content-Length (H7), truncated %-escape in the URL (H8).
    _mal_variants = [
        b"GET /v1/version HTTP/1.1\r\nHost: x\r\n" + b"".join(b"X-H%d: v\r\n" % i for i in range(40)) + b"\r\n",
        b"POST /v1/configs HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: -1\r\n\r\n{}",
        b"GET /v1/version?x=%\r\n\r\n",
        b"GET /v1/configs/Audio%20Mixer/%A HTTP/1.1\r\nHost: x\r\n\r\n",
    ]

    def tcp_rst_churn(port):
        # Connect and abort with a TCP RST (SO_LINGER 0) in a tight loop, to
        # churn the FTP/Telnet/DMA listeners' accept + per-connection teardown.
        while not stop.is_set():
            try:
                s = socket.create_connection((settings.host, port), timeout=3)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                try:
                    s.sendall(b"\r\n")
                except OSError:
                    pass
                s.close()
            except OSError:
                pass
            with churn_lock:
                h1_count[0] += 1

    def malformed_worker(wid):
        n = 0
        while not stop.is_set():
            req = _mal_variants[(wid + n) % len(_mal_variants)]
            try:
                s = socket.create_connection((settings.host, settings.http_port), timeout=4)
                try:
                    s.sendall(req)
                    s.recv(64)
                finally:
                    s.close()
            except OSError:
                pass
            n += 1
            with churn_lock:
                h1_count[0] += 1

    stats: dict[str, ProbeStat] = {n: ProbeStat(n) for n in
                                   ("rest_version", "rest_info", "cfg_put_sid", "cfg_post_batch", "ftp", "telnet", "ident", "dma")}
    first_rest_fail_t = [None]
    first_rest_fail_churn = [None]
    started_wall = ts()
    t0 = time.time()

    jsonl = open(jsonl_path, "w", buffering=1)

    def emit(rec: dict):
        jsonl.write(json.dumps(rec) + "\n")

    meta = {"label": args.label, "host": args.host, "started": started_wall,
            "duration_s": args.duration_s, "churn_workers": args.churn_workers,
            "h1_workers": args.h1_workers, "malformed_workers": args.malformed_workers,
            "tcp_churn_ports": args.tcp_churn_ports,
            "health_interval_s": args.health_interval_s, "post_every": args.post_every,
            "probe_timeout_s": args.probe_timeout_s, "telnet_timeout_s": args.telnet_timeout_s,
            "fw_commit": args.fw_commit,
            "httpd_commit": args.httpd_commit, "test_script": "network_stack_ab.py"}
    emit({"type": "meta", **meta})
    print(f'{ts()} START {json.dumps(meta)}', flush=True)

    workers = [threading.Thread(target=churn_worker, daemon=True) for _ in range(args.churn_workers)]
    workers += [threading.Thread(target=h1_worker, daemon=True) for _ in range(args.h1_workers)]
    workers += [threading.Thread(target=malformed_worker, args=(w,), daemon=True) for w in range(args.malformed_workers)]
    churn_ports = [int(p) for p in args.tcp_churn_ports.split(",") if p.strip()]
    workers += [threading.Thread(target=tcp_rst_churn, args=(p,), daemon=True) for p in churn_ports]
    for w in workers:
        w.start()

    def run_probe(name, fn):
        ok, lat, kind, detail = fn()
        with churn_lock:
            cc = churn_count[0]
        stats[name].record(ok, lat, kind, detail)
        emit({"type": "probe", "t": round(time.time() - t0, 3), "name": name, "ok": ok,
              "latency_ms": round(lat, 1), "kind": kind, "detail": detail, "churn": cc})
        if not ok and name in REST_PROBES and first_rest_fail_t[0] is None:
            first_rest_fail_t[0] = round(time.time() - t0, 3)
            first_rest_fail_churn[0] = cc
            print(f'{ts()} FIRST-REST-FAILURE name={name} t={first_rest_fail_t[0]}s '
                  f'churn={cc} kind={kind} detail="{detail}"', flush=True)
        return ok

    put_targets = ("0 dB", "+1 dB")
    cycle = 0
    try:
        while time.time() - t0 < args.duration_s:
            cycle += 1
            run_probe("rest_version", lambda: probe_rest_get(settings, "/v1/version", args.probe_timeout_s))
            run_probe("rest_info", lambda: probe_rest_get(settings, "/v1/info", args.probe_timeout_s))
            run_probe("cfg_put_sid", lambda: probe_cfg_put_readback(settings, put_targets[cycle % 2], args.probe_timeout_s))
            if args.post_every > 0 and cycle % args.post_every == 0:
                run_probe("cfg_post_batch", lambda: probe_cfg_post_batch(settings, put_targets[(cycle // 2) % 2], args.probe_timeout_s))
            run_probe("ftp", lambda: probe_ftp(settings, args.probe_timeout_s))
            run_probe("telnet", lambda: probe_telnet(settings, args.telnet_timeout_s))
            run_probe("ident", lambda: probe_ident(settings, args.probe_timeout_s))
            run_probe("dma", lambda: probe_dma(settings, args.probe_timeout_s))

            if cycle % 10 == 0:
                with churn_lock:
                    cc = churn_count[0]
                rv = stats["rest_version"]
                print(f'{ts()} cycle={cycle} t={int(time.time()-t0)}s churn={cc} '
                      f'rest_version ok={rv.ok}/{rv.attempts} p50={rv.pct(50)}ms p99={rv.pct(99)}ms', flush=True)
            time.sleep(args.health_interval_s)
    except KeyboardInterrupt:
        print(f'{ts()} interrupted', flush=True)

    stop.set()
    time.sleep(0.5)
    with churn_lock:
        total_churn = churn_count[0]
        total_h1 = h1_count[0]

    # final health suite (fresh)
    final = {}
    for name, fn in (("rest_version", lambda: probe_rest_get(settings, "/v1/version", args.probe_timeout_s)),
                     ("rest_info", lambda: probe_rest_get(settings, "/v1/info", args.probe_timeout_s)),
                     ("cfg_put_sid", lambda: probe_cfg_put_readback(settings, "0 dB", args.probe_timeout_s)),
                     ("cfg_post_batch", lambda: probe_cfg_post_batch(settings, "+1 dB", args.probe_timeout_s)),
                     ("ftp", lambda: probe_ftp(settings, args.probe_timeout_s)),
                     ("telnet", lambda: probe_telnet(settings, args.telnet_timeout_s)),
                     ("ident", lambda: probe_ident(settings, args.probe_timeout_s)),
                     ("dma", lambda: probe_dma(settings, args.probe_timeout_s))):
        ok, lat, kind, detail = fn()
        final[name] = {"ok": ok, "latency_ms": round(lat, 1), "kind": kind, "detail": detail}
        emit({"type": "final_probe", "name": name, **final[name]})

    rest_final_ok = all(final[n]["ok"] for n in REST_PROBES)
    all_final_ok = all(v["ok"] for v in final.values())
    any_probe_failed = any(s.fail > 0 for s in stats.values())
    # HEALTHY only when every listener probe passed for the whole run and the
    # post-run health suite is fully green. WEDGED if REST is down at the end.
    verdict = "HEALTHY" if (all_final_ok and not any_probe_failed) else (
        "WEDGED" if not rest_final_ok else "DEGRADED")

    summary = {
        "meta": meta, "ended": ts(), "elapsed_s": round(time.time() - t0, 1),
        "total_churn_requests": total_churn,
        "total_h1_requests": total_h1,
        "time_to_first_rest_failure_s": first_rest_fail_t[0],
        "churn_to_first_rest_failure": first_rest_fail_churn[0],
        "verdict": verdict,
        "probes": {n: s.summary() for n, s in stats.items()},
        "final_health": final,
    }
    emit({"type": "summary", **summary})
    jsonl.close()
    json_path.write_text(json.dumps(summary, indent=2))

    # Markdown
    lines = [f"# Network-stack A/B leg: `{args.label}`", "",
             f"- Host: `{args.host}`  ", f"- Firmware commit: `{args.fw_commit or 'n/a'}`  ",
             f"- httpd submodule: `{args.httpd_commit or 'n/a'}`  ",
             f"- Started: {started_wall}  Ended: {summary['ended']}  Elapsed: {summary['elapsed_s']}s  ",
             f"- Churn workers: {args.churn_workers}  Total churn (partial+RST) requests: **{total_churn}**  ",
             f"- H1 workers (40-header floods): {args.h1_workers}  Total H1 requests: **{total_h1}**  ",
             f"- Time to first REST failure: **{first_rest_fail_t[0]}**s  Churn count at first failure: **{first_rest_fail_churn[0]}**  ",
             f"- **Verdict: {verdict}**", "",
             "## Per-probe results", "",
             "| probe | attempts | ok | fail | timeout | success% | p50 ms | p90 ms | p99 ms | max ms | fail kinds |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, s in stats.items():
        d = s.summary()
        lines.append(f"| {n} | {d['attempts']} | {d['ok']} | {d['fail']} | {d['timeout']} | "
                     f"{d['success_pct']} | {d['p50_ms']} | {d['p90_ms']} | {d['p99_ms']} | {d['max_ms']} | "
                     f"{d['fail_kinds']} |")
    lines += ["", "## Final health suite (post-run)", "",
              "| probe | ok | latency ms | kind | detail |", "|---|---|---|---|---|"]
    for n, d in final.items():
        lines.append(f"| {n} | {d['ok']} | {d['latency_ms']} | {d['kind']} | {d['detail']} |")
    md_path.write_text("\n".join(lines) + "\n")

    print(f'{ts()} DONE verdict={verdict} total_churn={total_churn} '
          f'first_fail_t={first_rest_fail_t[0]} first_fail_churn={first_rest_fail_churn[0]}', flush=True)
    print(f'{ts()} wrote {json_path} , {md_path} , {jsonl_path}', flush=True)
    return 0 if verdict == "HEALTHY" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
