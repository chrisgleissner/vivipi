# Probe I/O Safety and Observability

This document records the root-cause analysis, applied constraints, and before/after comparison for the probe subsystem hardening performed in the `src/vivipi/runtime/checks.py` module. Scope is limited to the HTTP, FTP, and Telnet probes; the Ping probe relies on a bounded external process and is out of scope.

## Root-cause analysis

### Observability gap

Before this change every successful socket send emitted a `socket-send` trace event carrying only `{"stage": ..., "bytes_sent": ...}`. The stage field distinguishes the probe family (for example `http-send`, `ftp-send`, `telnet-send`) but does not identify the specific operation. When a probe failed intermittently, the retained syslog showed that bytes left the device but did not show *which request* they represented. Diagnosis therefore required reading the probe source instead of the log.

The `socket-recv` event is symmetric, but the target-side intent is carried by the preceding send. Extending recv would add noise without adding diagnostic value.

### Unbounded / rapid-fire I/O

Three code paths could, under pathological peer behavior, extend socket activity beyond the nominal `timeout_s`:

1. `_read_telnet_until_idle` reset the socket timeout on every iteration (120 ms initial, 20 ms post-data) and terminated only when the peer went idle for one window or when the local `max_empty_reads` was reached. A peer that continuously trickled bytes (chatty banner, login-echo storm, corrupted stream) could hold the probe in recv/send cycles for much longer than the probe-wide deadline. Each IAC negotiation option also triggered a reply send (`_telnet_send_best_effort`), so an IAC storm doubled the socket-op rate.
2. `_recv_until_closed` in the HTTP socket path ran until the peer closed or the deadline fired. A slow-drip peer could keep recv active at the chunk-size boundary for the whole `timeout_s` window, producing a large number of 1-byte recvs instead of a bounded number of reasonable-size reads.
3. `_ftp_read_response` read until the `\n` terminator. A peer that never sent `\n` would be bounded only by the deadline.

None of these was a *retry* in the traditional sense. They were progress loops that depended on peer cooperation to terminate early. Under burst scheduling (multiple probes, short intervals) this created a worst-case I/O pattern that resembled rapid-fire / DoS-like behavior against the target.

### No I/O pacing

`_socket_wait` uses `select.poll(...)` and returns as soon as the socket is readable/writable. There was no small delay between consecutive socket operations, so transient hot paths (e.g. repeated `EAGAIN` or tiny partial sends) could busy-spin at whatever rate `poll` was willing to return.

## Applied constraints

### Operation descriptors on existing `socket-send` events

- A new optional `operation` payload field is attached to the existing `socket-send` event. No new events were introduced.
- HTTP: `"{METHOD} {path}"` (for example `GET /v1/checks`).
- FTP: the command line as sent, with `PASS` specifically redacted to `PASS ***` so the operation descriptor cannot leak credentials.
- Telnet: `telnet-iac` on IAC negotiation replies, reflecting the fact that the Telnet probe never actually sends user-level content.
- Every descriptor is routed through `_bounded_operation(...)` which applies `PROBE_OPERATION_LIMIT = 48` characters and collapses internal whitespace, so the field is guaranteed to be single-line, bounded, and safe to embed in structured log lines.
- `_emit_socket_send(...)` emits the `operation` field only when provided. Probe callers that do not pass an operation (including every existing test fixture) see no change in payload shape.

### I/O budget per probe execution

- `_ProbeBudget(max_ops=PROBE_MAX_SOCKET_OPS, pacing_ms=PROBE_IO_PACING_MS)` is created once per probe invocation and threaded through the existing `trace`/`deadline` kwargs chain.
- Every successful `_socket_sendall`, `_socket_recv`, `_telnet_send_best_effort`, and top-level `_read_telnet_until_idle` recv charges the budget. Telnet IAC replies charge the same budget as the enclosing recv loop, so IAC storms count against the same cap as the data they respond to.
- When the budget is exhausted the helper raises `TimeoutError("probe io budget exhausted")`. The probe runners' existing exception handlers translate this into a normal probe-fail result; no new log event is introduced.
- `PROBE_MAX_SOCKET_OPS = 48` was chosen so healthy live targets (HTTP one-shot, FTP four-command login, Telnet banner read) finish well under the cap while pathological peers are terminated before they can burst.

### Strict time bound

- `_read_telnet_until_idle` now checks `_deadline_remaining_ms(deadline)` at the top of every iteration. Previously it only relied on per-iteration socket timeouts, which a chatty peer could defeat by continuously returning data.
- HTTP and FTP helpers already took the probe-wide `deadline` through `_socket_wait`. No change to their time-bound semantics.

### I/O pacing

- `_ProbeBudget.charge(...)` calls `_sleep_ms(pacing_ms)` after every charged op. `PROBE_IO_PACING_MS = 2` yields at most a small, deterministic pause between consecutive socket operations, which prevents tight bursts without meaningfully extending healthy probe latency.
- `_sleep_ms` gracefully no-ops when the time module exposes neither `sleep_ms` nor `sleep` (a test-harness edge case), which keeps the existing monkeypatched unit tests working.

### No retry loops

The probe attempt model was already single-attempt at the probe level. The `while` loops inside `_socket_sendall` and `_socket_recv` are progress loops keyed off `_socket_wait` readiness and `would_block` transitions; they do not retry failed probes. This change does not introduce new retry loops. The existing progress loops remain because they are the correct way to handle partial sends and nonblocking readiness under MicroPython's socket semantics.

## Telnet minimal-interaction model

The Telnet probe is a liveness check, not a session. The probe:

1. Opens a socket within the probe-wide deadline.
2. Reads at most `TELNET_MAX_RECV_CHUNKS = 8` chunks while observing the probe-wide deadline, the per-probe `_ProbeBudget`, and the pre-existing idle-based termination.
3. Responds to IAC negotiation options with WONT/DONT replies (`telnet-iac`) — this is required to keep the peer from hanging, but it is bounded by the same budget as the recv loop.
4. Never attempts to log in. The `username` and `password` arguments remain in the public signature for API stability but are explicitly discarded (`del username, password`).
5. Returns `ok=True` when any visible banner bytes are received, or when a post-connect timeout / reset occurs after the socket was established.

The effect is that the Telnet probe cannot degrade into a sustained recv/send loop even against a chatty or adversarial peer: the chunk cap, time deadline, and budget are enforced in triplicate and each is independently sufficient to terminate the probe.

## Before vs after

| Concern | Before | After |
| --- | --- | --- |
| HTTP `socket-send` payload | `{"stage": "http-send", "bytes_sent": N}` | `{"stage": "http-send", "operation": "GET /v1/checks", "bytes_sent": N}` |
| FTP `socket-send` payload | `{"stage": "ftp-send", "bytes_sent": N}` | `{"stage": "ftp-send", "operation": "USER anonymous" / "PASS ***" / "PWD" / "QUIT", "bytes_sent": N}` |
| Telnet `socket-send` payload (IAC) | `{"stage": "telnet-send", "bytes_sent": 3}` | `{"stage": "telnet-send", "operation": "telnet-iac", "bytes_sent": 3}` |
| Telnet recv termination | idle-timeout or peer close only | idle-timeout, peer close, `TELNET_MAX_RECV_CHUNKS`, probe-wide deadline, or `_ProbeBudget` exhaustion — whichever fires first |
| Per-probe socket-op cap | none | `PROBE_MAX_SOCKET_OPS = 48` combined send+recv |
| Pacing between socket ops | none | `PROBE_IO_PACING_MS = 2` after every charged op |
| Probe time bound enforcement | enforced in `_socket_wait`, not re-checked in telnet recv loop | enforced in `_socket_wait` *and* re-checked at the top of the telnet recv loop |
| Password leakage via trace | `PASS hunter2` would have appeared verbatim had `operation` existed | `PASS ***` is emitted unconditionally |
| New log events introduced | n/a | none |
| Retry loops introduced | n/a | none |

## Regression safety

- OK / FAIL semantics are preserved. Budget exhaustion surfaces as a normal probe failure through the existing `_execution_error` path without adding a new code identifier.
- Existing unit tests continue to pass after a small update to two compat-layer test stubs (they now accept `budget=None` alongside `deadline` and `trace`, reflecting the addition of the new kwarg).
- Fourteen new unit tests in `tests/unit/runtime/test_checks.py` cover: budget exhaustion, operation descriptor truncation, FTP password redaction, HTTP method/path descriptor, FTP full-command descriptor set, Telnet IAC descriptor, Telnet chunk cap, Telnet deadline enforcement, and Telnet budget enforcement.

## Files changed

- `src/vivipi/runtime/checks.py` — added `_ProbeBudget`, `_bounded_operation`, `_emit_socket_send`, `_ftp_operation_descriptor`, `_charge_budget`, new constants, telnet chunk cap, and threaded the budget/operation kwargs through the helper chain.
- `tests/unit/runtime/test_checks.py` — updated two compat-test stubs to accept `budget=None`; added fourteen new tests.
- `PLANS.md`, `WORKLOG.md` — execution tracking.
- `doc/research/probes/io-safety-and-observability.md` — this document.
