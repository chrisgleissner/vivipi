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
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import u64_http  # noqa: E402
from u64_connection_runtime import RuntimeSettings  # noqa: E402


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


REST_PROBES = ("rest_version", "rest_info", "cfg_put_sid", "cfg_post_batch")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="U64 network-stack stability A/B harness")
    ap.add_argument("-H", "--host", default=os.getenv("HOST", "u64"))
    ap.add_argument("--http-port", type=int, default=int(os.getenv("HTTP_PORT", "80")))
    ap.add_argument("--ftp-port", type=int, default=int(os.getenv("FTP_PORT", "21")))
    ap.add_argument("--telnet-port", type=int, default=int(os.getenv("TELNET_PORT", "23")))
    ap.add_argument("--network-password", default=os.getenv("NETWORK_PASSWORD", ""))
    ap.add_argument("--duration-s", type=float, default=600.0)
    ap.add_argument("--churn-workers", type=int, default=4,
                    help="HTTP incomplete-abort (partial request + RST) churn threads; 0 disables")
    ap.add_argument("--health-interval-s", type=float, default=1.0)
    ap.add_argument("--post-every", type=int, default=5,
                    help="run the POST /v1/configs batch probe every Nth health cycle")
    ap.add_argument("--probe-timeout-s", type=float, default=4.0)
    ap.add_argument("--out-dir", default="./out/netstack-ab")
    ap.add_argument("--label", required=True, help="e.g. baseline-e7a642a5 / patched-78069d3f")
    ap.add_argument("--fw-commit", default=os.getenv("FW_COMMIT", ""))
    ap.add_argument("--httpd-commit", default=os.getenv("HTTPD_COMMIT", ""))
    args = ap.parse_args(argv)

    settings = RuntimeSettings(
        host=args.host, http_path="v1/version", http_port=args.http_port,
        telnet_port=args.telnet_port, ftp_port=args.ftp_port, ftp_user="anonymous",
        ftp_pass=args.network_password, delay_ms=0, log_every=1, verbose=False,
        network_password=args.network_password, modem_port=3000)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"{args.label}.jsonl"
    json_path = out_dir / f"{args.label}.json"
    md_path = out_dir / f"{args.label}.md"

    stop = threading.Event()
    churn_count = [0]
    churn_lock = threading.Lock()

    def churn_worker():
        while not stop.is_set():
            u64_http.run_probe_incomplete(settings)
            with churn_lock:
                churn_count[0] += 1

    stats: dict[str, ProbeStat] = {n: ProbeStat(n) for n in
                                   ("rest_version", "rest_info", "cfg_put_sid", "cfg_post_batch", "ftp", "telnet")}
    first_rest_fail_t = [None]
    first_rest_fail_churn = [None]
    started_wall = ts()
    t0 = time.time()

    jsonl = open(jsonl_path, "w", buffering=1)

    def emit(rec: dict):
        jsonl.write(json.dumps(rec) + "\n")

    meta = {"label": args.label, "host": args.host, "started": started_wall,
            "duration_s": args.duration_s, "churn_workers": args.churn_workers,
            "health_interval_s": args.health_interval_s, "post_every": args.post_every,
            "probe_timeout_s": args.probe_timeout_s, "fw_commit": args.fw_commit,
            "httpd_commit": args.httpd_commit, "test_script": "network_stack_ab.py"}
    emit({"type": "meta", **meta})
    print(f'{ts()} START {json.dumps(meta)}', flush=True)

    workers = [threading.Thread(target=churn_worker, daemon=True) for _ in range(args.churn_workers)]
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
            run_probe("telnet", lambda: probe_telnet(settings, args.probe_timeout_s))

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

    # final health suite (fresh)
    final = {}
    for name, fn in (("rest_version", lambda: probe_rest_get(settings, "/v1/version", args.probe_timeout_s)),
                     ("rest_info", lambda: probe_rest_get(settings, "/v1/info", args.probe_timeout_s)),
                     ("cfg_put_sid", lambda: probe_cfg_put_readback(settings, "0 dB", args.probe_timeout_s)),
                     ("cfg_post_batch", lambda: probe_cfg_post_batch(settings, "+1 dB", args.probe_timeout_s)),
                     ("ftp", lambda: probe_ftp(settings, args.probe_timeout_s)),
                     ("telnet", lambda: probe_telnet(settings, args.probe_timeout_s))):
        ok, lat, kind, detail = fn()
        final[name] = {"ok": ok, "latency_ms": round(lat, 1), "kind": kind, "detail": detail}
        emit({"type": "final_probe", "name": name, **final[name]})

    rest_final_ok = all(final[n]["ok"] for n in REST_PROBES)
    verdict = "HEALTHY" if rest_final_ok and first_rest_fail_t[0] is None else (
        "WEDGED" if not rest_final_ok else "DEGRADED")

    summary = {
        "meta": meta, "ended": ts(), "elapsed_s": round(time.time() - t0, 1),
        "total_churn_requests": total_churn,
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
