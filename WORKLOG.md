# ViviPi Work Log

## 2026-04-23T12:42:51Z

- Started Stage 1 real-device validation for the expanded Ultimate 64 host-side connection test tooling with the U64 still configured for no network password.
- Required planning/logging files already existed, so I updated `PLANS.md` with a dedicated `U64 Connection Test Stage 1 Validation Plan` and will treat that section as the authoritative execution plan for this task.
- Repository discovery completed before any code changes:
  - inspected `scripts/u64_connection_test.py`, `scripts/u64_connection_runtime.py`, `scripts/u64_http.py`, `scripts/u64_telnet.py`, `scripts/u64_ftp.py`, `scripts/u64_ident.py`, `scripts/u64_raw64.py`, `scripts/u64_modem.py`, `scripts/u64_stream.py`
  - inspected focused tooling tests in `tests/unit/tooling/test_u64_connection_test.py`, `tests/unit/tooling/test_u64_connection_protocols.py`, `tests/unit/tooling/test_u64_telnet.py`, and `tests/unit/tooling/test_u64_stream.py`
  - inspected repository-integrated config and invocation paths in `config/build-deploy.yaml`, `config/build-deploy.local.yaml`, `config/checks.yaml`, `config/checks.local.yaml`, `src/vivipi/core/config.py`, `src/vivipi/tooling/vivipulse.py`, `src/vivipi/services/adb_service.py`, and `README.md`
  - reviewed the task source documents in `docs/research/connection-test-expansion/u64-connection-test-expansion.md` and `docs/research/connection-test-expansion/plan.md`
- Verified discovery findings:
  - `scripts/u64_connection_test.py` is a standalone host-side probe driver; no production repo path currently invokes it directly
  - the repository-integrated host path is `scripts/vivipulse`, which resolves `config/build-deploy*.yaml` plus `config/checks*.yaml` into the shared runtime probe model
  - the active checked-in local build profile is `config/build-deploy.local.yaml`, which selects `config/checks.local.yaml`
  - `config/checks.local.yaml` currently covers only `HTTP`, `FTP`, and `TELNET` for `c64u` and `u64`; the newly added `ident`, `raw64`, and optional `modem` surfaces are not yet exercised through the ViviPi runtime config path
  - the checked-in config path already models an optional shared network password via `${VIVIPI_NETWORK_PASSWORD}`, and `vivipi.core.config.load_checks_config(...)` resolves missing auth placeholders to empty strings for `username` / `password`
- Working hypothesis for Stage 1:
  - the highest-risk no-password failures are in the newly added direct-tool `ident` / `raw64` live-device behavior or in the clarity of no-password diagnostics, not in the parser defaults alone
- Next actions:
  - run the focused tooling test slice unchanged
  - run direct no-password U64 connection-test invocations, starting with `ident` and `raw64`
  - run the repository-integrated local ViviPi path via `scripts/vivipulse --mode local`

## 2026-04-23T12:50:11Z

- Completed Stage 1 real-device validation for the expanded U64 host-side connection tooling against the live no-password U64 at `192.168.1.13`.
- Focused tooling validation before fixes:
  - `python -m pytest -o addopts='' tests/unit/tooling/test_u64_connection_test.py tests/unit/tooling/test_u64_connection_protocols.py tests/unit/tooling/test_u64_telnet.py tests/unit/tooling/test_u64_stream.py` -> `78 passed`
- Live no-password direct-tool validation:
  - `./.venv/bin/python scripts/u64_connection_test.py --host 192.168.1.13 --probes ident,raw64 --duration-s 5 --log-every 1 -v`
  - observed `ident` and `raw64` both succeeding on the device, but the command exited non-zero because explicit `--probes ident,raw64` still inherited soak-profile stream monitoring and hit unrelated stream timeout failures
  - discriminating rerun `./.venv/bin/python scripts/u64_connection_test.py --host 192.168.1.13 --profile stress --probes ident,raw64 --duration-s 5 --log-every 1 -v` confirmed the new direct surfaces were healthy and isolated the bug to config resolution, not the device protocols
- Minimal Stage 1 fix applied:
  - updated `scripts/u64_connection_test.py` so explicit `--probes` disables profile-default streams unless `--stream` is also supplied
  - updated `tests/unit/tooling/test_u64_connection_test.py` with a regression test for that behavior and extended CLI help coverage
  - documented the new behavior in the `--probes` help text
- Post-fix validation of the repaired live scenario:
  - `python -m pytest -o addopts='' tests/unit/tooling/test_u64_connection_test.py` -> `28 passed`
  - re-ran `./.venv/bin/python scripts/u64_connection_test.py --host 192.168.1.13 --probes ident,raw64 --duration-s 5 --log-every 1 -v`
  - observed `streams=off`, repeated `ident` success with strict JSON echo validation, and repeated `raw64` success across identify/debug-register/flash metadata reads with no unrelated stream failures
- Wider direct-tool no-password validation:
  - `./.venv/bin/python scripts/u64_connection_test.py --host 192.168.1.13 --profile stress --probes ping,http,ftp,telnet,ident,raw64 --schedule sequential --runners 1 --duration-s 3 --log-every 1 -v`
  - observed repeated `OK` results for all six probes with `network_password_set=0`
- Repository-integrated ViviPi path validation:
  - first attempted `./.venv/bin/python scripts/vivipulse ...` and confirmed the failure was only an invocation mistake because `scripts/vivipulse` is a shell entrypoint, not a Python module
  - `env -u VIVIPI_NETWORK_PASSWORD -u VIVIPI_NETWORK_USERNAME scripts/vivipulse --mode local --build-config config/build-deploy.local.yaml --check-id u64-rest --check-id u64-ftp --check-id u64-telnet` -> `Selected checks: u64-rest, u64-ftp, u64-telnet`, `Successes: 3`
  - `VIVIPI_NETWORK_PASSWORD='' VIVIPI_NETWORK_USERNAME='' scripts/vivipulse --mode local --build-config config/build-deploy.local.yaml --check-id u64-rest --check-id u64-ftp --check-id u64-telnet` -> `Selected checks: u64-rest, u64-ftp, u64-telnet`, `Successes: 3`
  - this verified both omitted-password and explicit-empty-password behavior through the checked-in local build-config and checks-config path
- Optional modem listener classification:
  - `./.venv/bin/python scripts/u64_connection_test.py --host 192.168.1.13 --profile stress --probes modem --duration-s 2 --log-every 1 -v`
  - observed clean smoke classifications on port `3000`, alternating between `status=offline` and `status=busy`; no destructive action required and the probe remained non-blocking
- Final focused validation after the fix:
  - initial rerun of the required focused slice found only one wrapped-help assertion failure in `tests/unit/tooling/test_u64_connection_test.py`; the implementation itself was fine
  - tightened that assertion to match the stable clause instead of the exact wrapped sentence
  - reran `python -m pytest -o addopts='' tests/unit/tooling/test_u64_connection_test.py tests/unit/tooling/test_u64_connection_protocols.py tests/unit/tooling/test_u64_telnet.py tests/unit/tooling/test_u64_stream.py` -> `79 passed`
- Final repository validation:
  - `./build` -> `731 passed`, total coverage `97.90%`
- Stage 1 outcome:
  - no-password direct-tool path: PASS
  - no-password ViviPi-integrated path: PASS
  - optional modem smoke classification: PASS as non-blocking `offline` / `busy`
  - Stage 2 remains deferred until the shared U64 network password is enabled by the user

## 2026-04-23T13:12:14Z

- Completed Stage 2 password-enabled validation after the user enabled the shared U64 network password on the live device.
- Direct authenticated host-tool validation:
  - `./.venv/bin/python scripts/u64_connection_test.py --host 192.168.1.13 --network-password pwd --profile stress --probes ping,http,ftp,telnet,ident,raw64 --schedule sequential --runners 1 --duration-s 3 --log-every 1 -v`
  - observed clean authenticated success across `ping`, `http`, `ftp`, `telnet`, `ident`, and `raw64`; startup line confirmed `network_password_set=1`
  - authenticated HTTP readwrite operations returned `200`, FTP authenticated and completed readwrite operations, Telnet authenticated and completed menu-abort probes, `ident` remained healthy without device-state mutation, and `raw64` completed authenticated read operations successfully
- Targeted authenticated direct validation:
  - `./.venv/bin/python scripts/u64_connection_test.py --host 192.168.1.13 --network-password pwd --probes ident,raw64 --duration-s 5 --log-every 1 -v`
  - observed `streams=off` with repeated `ident` success and repeated authenticated `raw64` identify/debug-register/flash-metadata operations
  - noted occasional `ident` iterations around `~1116ms` latency but still `result=OK`; no auth failure or functional regression was observed
- Repo-integrated password-enabled validation initially failed:
  - `VIVIPI_NETWORK_PASSWORD='pwd' VIVIPI_NETWORK_USERNAME='' scripts/vivipulse --mode local --build-config config/build-deploy.local.yaml --check-id u64-rest --check-id u64-ftp --check-id u64-telnet` -> `Successes: 2`
  - artifact trace in `artifacts/vivipulse/20260423T130553.128035Z-local/trace.jsonl` showed the single failing check was `u64-rest` with `HTTP 403`; `u64-ftp` and `u64-telnet` were already `OK`
- Root cause and fix:
  - inspected `src/vivipi/runtime/checks.py` and confirmed `portable_http_runner(...)` accepted `password` but discarded it, always sending only `Connection: close`
  - inspected `config/checks.local.yaml` and confirmed the local `C64U REST` and `U64 REST` checks did not pass `${VIVIPI_NETWORK_PASSWORD}` through at all
  - updated `src/vivipi/runtime/checks.py` so both the normal `http.client` branch and `_portable_http_runner_socket(...)` send `X-Password` when `password` is configured
  - updated `config/checks.local.yaml` so `C64U REST` and `U64 REST` include `password: ${VIVIPI_NETWORK_PASSWORD}`
  - added focused runtime regressions in `tests/unit/runtime/test_checks.py` covering `X-Password` forwarding in both HTTP execution branches
- Post-fix validation:
  - `python -m pytest -o addopts='' tests/unit/runtime/test_checks.py` -> `119 passed`
  - re-ran `VIVIPI_NETWORK_PASSWORD='pwd' VIVIPI_NETWORK_USERNAME='' scripts/vivipulse --mode local --build-config config/build-deploy.local.yaml --check-id u64-rest --check-id u64-ftp --check-id u64-telnet` -> `Successes: 3`
- Final repository validation:
  - `./build` -> `732 passed`, total coverage `97.90%`
- Stage 2 outcome:
  - direct password-enabled host-tool path: PASS
  - repo-integrated password-enabled local path: PASS
  - the Stage 1 and Stage 2 validation/hardening work for the extended U64 network-surface tooling is complete

## 2026-04-20T16:35:00Z

- Investigated the report that the attached Pico appeared stuck on the boot screen with a non-moving liveness pixel.
- Root cause analysis:
  - the earlier runtime hardening had already switched the scheduler and render path to monotonic time, but the newly added Pico watchdog still was not active on-device because the timeout clamp was invalid
  - `firmware/runtime.py` still used `WATCHDOG_MIN_TIMEOUT_MS = 15000` while the RP2040 MicroPython watchdog rejects values above `8388ms`
  - because `_watchdog_timeout_ms(...)` applied `max(MIN, min(MAX, value))`, the effective timeout always became `15000`, `_build_runtime_watchdog(...)` always hit `ValueError`, and the watchdog silently degraded to `_NoopRuntimeWatchdog`
  - direct on-device probing confirmed the actual hardware limit: `machine.WDT(timeout=8388)` succeeds, `machine.WDT(timeout=8400)` fails with `ValueError`
- Final corrective implementation:
  - lowered `WATCHDOG_MIN_TIMEOUT_MS` to `4000` so the clamp now produces a valid watchdog timeout on Pico hardware
  - kept `WATCHDOG_MAX_TIMEOUT_MS = 8388` and the consecutive-loop / consecutive-display reset fail-safes
  - added `set_probe_activity_callback(...)` to `src/vivipi/runtime/checks.py` and sliced `_socket_wait(...)` into `1000ms` poll windows so healthy long socket waits feed the hardware watchdog while true stalls still reset the board
  - emitted probe activity before long-running ping attempts and from the firmware runtime registered the watchdog feed callback into the probe transport layer
  - preserved timeout behavior under mocked or coarse clocks by falling back to local wait accounting when deadline values do not decrease between poll slices
- Regression coverage added:
  - watchdog timeout clamp test
  - watchdog construction test when the hardware API accepts the chosen timeout
  - existing focused runtime / firmware coverage still passes with the transport-slice changes
- Validation:
  - `python -m pytest -o addopts='' tests/unit/firmware/test_runtime.py tests/unit/runtime/test_checks.py tests/unit/runtime/test_app.py` -> `171 passed`
  - `./build` -> `688 passed`, total coverage `97.53%`
- Pico redeploy and boot proof:
  - redeployed the corrected bundle to the attached Pico with `./build deploy --device-port /dev/ttyACM0`
  - captured boot logs over raw serial across a forced reboot instead of using `mpremote resume`, which had been misleading because REPL attachment interrupts the running loop
  - serial boot logs proved the device is no longer stuck at boot:
    - watchdog armed successfully: `[BOOT][WDT] armed timeout_ms=8388`
    - startup advanced past the boot phase: `[BOOT][BOOT] startup tick now_s=14.624`
    - the first probe sweep completed and advanced the display heartbeat from `1` through `7`
    - the runtime entered the steady loop: `[BOOT][BOOT] enter loop poll_interval_ms=50`
    - subsequent loop probes started normally after boot

## 2026-04-20T14:19:29Z

- Closed the remaining active PR review issue on `fix/prevent-telnet-probe-storm` after pulling the unresolved review thread from PR #17.
- Root cause confirmed from the reviewer report and current code:
  - `_recv_telnet_chunk(...)` still converted every `TimeoutError` into `b""`
  - because probe budget depletion is intentionally surfaced as `TimeoutError("probe io budget exhausted")`, the telnet helper layer could silently swallow the hard cap on the compat path
- Final corrective code change:
  - updated `_recv_telnet_chunk(...)` so it still treats ordinary socket timeouts as empty reads, but re-raises `TimeoutError("probe io budget exhausted")`
- Final regression coverage:
  - added an explicit unit assertion proving `_recv_telnet_chunk(...)` re-raises budget exhaustion instead of returning `b""`
- Validation after the final code change:
  - `python -m pytest -o addopts='' tests/unit/runtime/test_checks.py` -> `92 passed`
  - `./build` -> `680 passed`, total coverage `97.60%`
- Pico deploy and live proof:
  - redeployed the updated bundle to the attached Pico with `./build deploy`
  - verified the configured device-facing ADB probe endpoint directly: `http://mickey:8081/vivipi/probe/adb/9B081FFAZ001WX` -> `status: OK`
  - read back the live Pico `config.json` and confirmed build metadata `0.4.1-cba2a670`, active checks, SH1107 display profile, and startup-self-test disabled
  - ran `mpremote connect auto run artifacts/device/pico_probe_runner.py` on the deployed Pico and confirmed all direct hardware-side checks reached `status: OK`:
    - `c64u-rest` -> `HTTP 200`
    - `c64u-ftp` -> `pwd=/`
    - `c64u-telnet` -> `visible_bytes=2295`
    - `pixel4-adb` -> `HTTP 200`
    - `u64-rest` -> `HTTP 200`
    - `u64-ftp` -> `pwd=/`
    - `u64-telnet` -> `visible_bytes=2009`
  - captured a live runtime-rendered display buffer from the deployed board after four seconds of real runtime ticks under `artifacts/display-capture/current/20260420-runtime-proof/`
  - inspected `artifacts/display-capture/current/20260420-runtime-proof/logical.png` and confirmed the deployed Pico rendered the expected health overview with all seven checks visible and `OK`
- Hardware-facing camera proof attempt:
  - brought the preferred Pixel camera app to the foreground candidate state and attempted both a pulled screencap and a triggered photo capture
  - the Android device remained stuck with `mCurrentFocus=NotificationShade`, so the pulled screen artifact was not usable as optical proof even though the Pico-side runtime proof succeeded
- PR cleanup:
  - resolved the addressed review thread `PRRT_kwDOR6RG5858N82F`

## 2026-04-20T14:01:01Z

- Completed the corrective follow-up audit for probe I/O safety and observability after verifying that the earlier completion claims in `PLANS.md` overstated the actual enforcement.
- Audit findings confirmed against `src/vivipi/runtime/checks.py`, `tests/unit/runtime/test_checks.py`, and `docs/research/network/io-safety-and-observability.md`:
  - `_ProbeBudget` was previously charged only after successful send/recv progress, which allowed repeated `would block` loops to continue uncharged and allowed one extra socket syscall beyond the nominal cap.
  - `_telnet_send_best_effort(...)` likewise charged only after a successful send, so timed-out IAC reply attempts were not consuming budget.
  - `_recv_telnet_chunk(...)` still converted every `TimeoutError` into an empty read, which would have swallowed budget exhaustion on the telnet compat path.
  - Legacy helper `_telnet_strip_negotiation(...)` still used direct `handle.sendall(...)`, which bypassed the hardened send path and would have been a future bypass if reused.
  - The prior tests did not prove mid-loop exhaustion under partial sends, `would block` storms, slow-drip recvs, telnet deadline expiry after progress, or IAC-storm exhaustion inside the telnet inner loop.
- Minimal corrective implementation:
  - moved budget charging to happen before each attempted low-level socket syscall in `_socket_sendall(...)`, `_socket_recv(...)`, `_recv_telnet_chunk(...)`, `_read_telnet_until_idle(...)`, and `_telnet_send_best_effort(...)`
  - changed `_recv_telnet_chunk(...)` to re-raise `TimeoutError("probe io budget exhausted")` while still treating ordinary socket timeouts as empty reads
  - routed `_telnet_strip_negotiation(...)` through `_telnet_send_best_effort(...)` with the existing `telnet-iac` operation descriptor instead of direct `sendall(...)`
  - threaded `budget` and `trace` through `_read_until_markers(...)` / `_telnet_strip_negotiation(...)` so the legacy telnet helper path no longer bypasses the hardened send path
- Test hardening added deterministic coverage for:
  - internal whitespace sanitization of bounded operation descriptors
  - budget exhaustion mid-loop on partial sends
  - budget exhaustion under repeated `would block` send storms
  - budget exhaustion under repeated `would block` recv storms
  - slow-drip recv exhaustion in `_recv_until_closed(...)`
  - telnet deadline expiry after one successful recv iteration
  - telnet IAC-storm exhaustion when negotiation replies time out
  - legacy telnet negotiation helper routing through the budgeted/logged `telnet-iac` send path
- Validation:
  - `python -m pytest -o addopts='' tests/unit/runtime/test_checks.py` -> `92 passed`
  - `./build` -> `680 passed`, total coverage `97.60%`, branch-coverage gate satisfied
- Documentation reconciliation:
  - updated `PLANS.md` with `Audit Findings` and corrected the stated enforcement model from successful-op charging to attempt-based charging
  - updated `docs/research/network/io-safety-and-observability.md` with the corrected assumptions, attempt-based budget model, legacy telnet-helper note, and the new edge-case coverage summary

## 2026-04-20T00:00:00Z

- Started the probe I/O safety and observability hardening task (see `PLANS.md` -> "Probe I/O Safety and Observability Plan").
- Discovery:
  - Inspected `src/vivipi/runtime/checks.py`, `src/vivipi/core/execution.py`, and `src/vivipi/core/probe_trace.py`.
  - Confirmed `socket-send` is emitted in exactly two code paths: `_socket_sendall(...)` (used by HTTP and FTP) and `_telnet_send_best_effort(...)` (used only for IAC negotiation replies).
  - Confirmed `socket-recv` is emitted from `_socket_recv(...)` and from `_read_telnet_until_idle(...)`.
  - Confirmed `_read_telnet_until_idle(...)` sets per-iteration socket timeouts (`TELNET_IDLE_TIMEOUT_S` = 120 ms, `TELNET_POST_DATA_IDLE_TIMEOUT_S` = 20 ms) and does *not* check any probe-wide deadline, so a chatty peer can prolong the loop beyond `timeout_s`.
  - Confirmed there are no retry loops in the probe-level attempt model; existing `while` loops inside `_socket_sendall` and `_socket_recv` are progress loops keyed off `_socket_wait` readiness and the passed-in deadline.
- Decision:
  - Add a `_ProbeBudget` helper threaded through the existing `trace`/`deadline` kwargs chain as an optional parameter. Charge every successful send/recv, enforce a hard per-probe ops cap, and apply 2 ms of pacing between ops.
  - Add an optional `operation` field to the `socket-send` event payload. Emit conditionally so existing tests that assert the current payload shape remain untouched.
  - For Telnet: cap `_read_telnet_until_idle` at `TELNET_MAX_RECV_CHUNKS` and check the probe-wide deadline at the top of every iteration. Do not change OK/FAIL semantics.
  - Redact FTP `PASS` so the operation descriptor cannot leak credentials.
- Implementation:
  - Added `PROBE_MAX_SOCKET_OPS`, `PROBE_IO_PACING_MS`, `PROBE_OPERATION_LIMIT`, and `TELNET_MAX_RECV_CHUNKS` constants and a `_ProbeBudget` class to `src/vivipi/runtime/checks.py`.
  - Added `_bounded_operation`, `_emit_socket_send`, `_ftp_operation_descriptor`, and `_charge_budget` helpers. Operation field is emitted only when provided, preserving existing `socket-send` payload shape for callers that do not opt in.
  - Threaded `budget` through `_socket_sendall`, `_socket_recv`, `_ftp_read_response`, `_ftp_command_with_deadline`, `_recv_until_closed`, `_recv_telnet_chunk`, `_recv_telnet_chunk_compat`, `_telnet_send_best_effort`, `_telnet_collect_visible`, and `_read_telnet_until_idle`.
  - `_read_telnet_until_idle` now also accepts `deadline` and `max_chunks` kwargs and checks both at the top of every iteration.
  - Reordered `_charge_budget` calls to run *outside* the low-level try/except in `_socket_sendall` / `_socket_recv`, so `TimeoutError("probe io budget exhausted")` propagates cleanly instead of being re-wrapped as a generic timeout.
  - Hardened `_sleep_ms` to no-op gracefully when `time.sleep` is unavailable (supports the MicroPython test harness where the time module is a narrow stub).
  - Each probe runner now creates a fresh budget at entry: `_portable_http_runner_socket`, `portable_ftp_runner`, and both paths of `portable_telnet_runner` (stdlib socket path + portable/MicroPython path).
- Tests:
  - Updated two compat-layer test stubs in `tests/unit/runtime/test_checks.py` to accept `budget=None` alongside `deadline`/`trace`.
  - Added 14 focused tests: budget exhaustion, operation bounding, FTP password redaction, per-command descriptor set, HTTP method+path descriptor, Telnet IAC descriptor, Telnet chunk cap, Telnet deadline enforcement, Telnet budget enforcement, and send/recv budget accounting.
- Validation:
  - `.venv/bin/python -m pytest -o addopts='' tests/unit/runtime/test_checks.py` -> `84 passed`
  - `.venv/bin/python -m pytest -o addopts='' tests/unit/runtime/ tests/unit/core/ tests/unit/services/` -> `362 passed`
  - `.venv/bin/python -m pytest -o addopts='' tests/unit/` -> `660 passed` (previous baseline `646`, delta +14 for the new coverage; no regressions)
- Documentation:
  - Wrote `docs/research/network/io-safety-and-observability.md` with root-cause analysis, applied constraints, Telnet minimal-interaction justification, and a before/after comparison table.
  - Updated `PLANS.md` section with per-phase COMPLETED status and the concrete acceptance-criteria checklist.

## 2026-04-20T11:16:18Z

- Investigated the reported sporadic `C64U TELNET` failures after the recent TELNET classifier changes.
- Root-cause evidence from real Pico and host checks:
  - isolated direct Pico TELNET probes against `192.168.1.167:23` were initially stable, which ruled out the basic TELNET classifier as the first-order fault
  - synthetic back-to-back same-host probe bursts on the Pico could still drive the C64U into transient `FTP` resets followed by `TELNET` `DEG` / immediate-reset failures and then `HTTP` resets, confirming a broader service-pressure pattern rather than a TELNET-only classifier bug
  - the real production Pico `tick()` loop and a focused per-host cadence run showed the runtime uses the normal `c64u-rest -> c64u-telnet -> c64u-ftp` order and did not reproduce the same immediate burst-collapse in the short safe-profile window
  - repeated real-device evidence showed C64U TELNET session output arrives in bursts and can continue well past the old `20 ms` post-data quiet window, so the Pico probe could close the socket while unread TELNET output was still pending
- Implemented the smallest justified TELNET teardown fix in `src/vivipi/runtime/checks.py`:
  - increased `TELNET_POST_DATA_IDLE_TIMEOUT_S` from `0.02` to `0.1` so the probe drains bursty C64U TELNET output before concluding the session is quiet
  - changed `_close_socket(...)` to perform a best-effort `shutdown(SHUT_RDWR)` before `close()` so the Pico stops ending TELNET sessions with the most abrupt possible teardown when the platform supports shutdown
- Added regression coverage in `tests/unit/runtime/test_checks.py` for best-effort shutdown-before-close and updated the TELNET quiet-drain expectations for the longer post-data idle window.
- Validation:
  - focused runtime check slice passed: `pytest --no-cov tests/unit/runtime/test_checks.py -q` -> `74 passed`
  - full repository validation passed: `./build` -> `667 passed`, total coverage `97.56%`
  - redeployed the Pico and confirmed the live device now reports `TELNET_POST_DATA_IDLE_TIMEOUT_S = 0.1`
  - real Pico post-fix cadence proof saved in `artifacts/hardware-proof/pico-c64u-hostgroup-70s-postfix.log` showed `7` consecutive `c64u-rest -> c64u-telnet -> c64u-ftp` cycles at the real `10 s` cadence with all `C64U` probes `OK`; `c64u-telnet` stayed on the visible-payload path with `visible_bytes=3770`, `handshake_detected=true`, and `session_duration_ms` about `329-416 ms`
  - baseline real-runtime capture in `artifacts/hardware-proof/pico-normal-loop-c64u.log` confirmed the production Pico loop order and showed no short-window `C64U` failures under the normal runtime path

## 2026-04-19T21:03:39Z

- Started the TELNET false-positive correction task.
- Repository and architecture review completed before edits:
  - confirmed `docs/spec.md`, `README.md`, and `docs/spec-traceability.md` still encode the older TELNET rule that post-connect idle timeout or reset counts as healthy reachability
  - confirmed `src/vivipi/runtime/checks.py` currently returns `PingProbeResult(ok=True, details="connected")` for post-connect timeout/reset and for blank/quiet sessions
  - confirmed `src/vivipi/core/execution.py` currently maps direct-probe results to only `OK` or `FAIL`, so TELNET cannot currently emit an explicit `DEG`
  - confirmed `src/vivipi/runtime/app.py` already supports `DEG` observations and only needs probe-result/logging metadata wired through
  - confirmed existing unit coverage in `tests/unit/runtime/test_checks.py` currently locks in the false-positive TELNET behavior and will need to be replaced with deterministic session-validation tests
- Current implementation target:
  - treat early close and immediate disconnect as `FAIL`
  - treat stable open with no negotiation or visible TELNET payload as `DEG`
  - reserve `OK` for verified TELNET interaction via negotiation, visible payload, or a clearly stable open session that exceeds the configured threshold
- Baseline live-target capture against `c64u` and `u64` is in progress so the final worklog can include a before/after comparison.

## 2026-04-19T21:10:26Z

- Added a new backlog item to `PLANS.md` for probe-operation logging.
- Scope of that follow-up item:
  - preserve the current regular log and syslog transport paths
  - extend socket-level probe traces with operation-specific labels so logs clearly show which FTP command, HTTP phase, or TELNET interaction is happening at each send/receive step
  - keep that work separate from the current TELNET correctness patch so the false-positive fix can complete and validate independently

## 2026-04-19T21:32:00Z

- Completed the TELNET false-positive correction task.
- Final implementation changes:
  - extended `PingProbeResult` / `CheckExecutionResult` so direct probes can carry explicit status plus probe metadata without changing unrelated probe architecture
  - rewrote `portable_telnet_runner()` and the TELNET session reader in `src/vivipi/runtime/checks.py` so connect-only, blank, reset, and early-close sessions are no longer treated as healthy
  - added deterministic TELNET session metadata: `close_reason`, `session_duration_ms`, `handshake_detected`
  - preserved TELNET `DEG` through `src/vivipi/core/execution.py`, `src/vivipi/runtime/app.py`, and `src/vivipi/services/adb_service.py`
  - updated `docs/spec.md`, `README.md`, and `docs/spec-traceability.md` to match the new TELNET contract
- Deterministic classification now enforced:
  - remote close/reset before `100 ms` => `FAIL`
  - stable open for at least `500 ms` with no meaningful TELNET interaction => `DEG`
  - meaningful TELNET interaction that survives the non-immediate-close window => `OK`
- Regression and validation evidence:
  - focused TELNET/execution/runtime/service slice passed: `135 passed`
  - traceability slice passed: `3 passed`
  - final full repository validation passed again: `./build` -> `661 passed`, coverage `97.26%`
  - deterministic local socket fixtures now prove the corrected classifier end to end:
    - `immediate-close -> FAIL closed immediately`
    - `idle-open -> DEG connected-no-telnet-data`
    - `negotiated-open -> OK visible_bytes=5`
- Live target results:
  - updated TELNET probe against `192.168.1.167:23` now reports `OK visible_bytes=2295`, `handshake_detected=true`, `session_duration_ms=103.2`
  - updated TELNET probe against `192.168.1.13:23` now reports `OK visible_bytes=2009`, `handshake_detected=true`, `session_duration_ms=149.4`
  - raw socket inspection against both targets showed first TELNET data arriving at about `37-48 ms`, about `2.0-2.3 KiB` total payload, and no remote close within a `1s` observation window, so these live targets are currently behaving like valid TELNET services rather than reproducing the false-positive scenario
  - the shell `telnet` client still printed `Connected ... Connection closed by foreign host.`, but the raw socket capture showed that this current operator-side symptom is not sufficient on its own to prove an immediate remote close on the wire for these targets
  - direct Pico-side probe execution via `mpremote connect auto run artifacts/device/pico_probe_runner.py` showed `c64u-telnet` still in the visible-payload path, while `u64-rest`, `u64-ftp`, and `u64-telnet` all timed out together from the Pico on that pass, which points to a broader `u64` reachability issue on the device path rather than a TELNET-only false-positive classification

## 2026-04-18T21:30:00Z

- Completed the runtime syslog and observability expansion and fixed the stuck-looking bottom heartbeat behavior.
- Implementation changes:
  - updated `src/vivipi/core/logging.py` so every retained/runtime log line now starts with `[vivipi]` and supports wider bounded fields for concise probe summaries
  - added `src/vivipi/runtime/syslog.py` with non-blocking UDP syslog mirroring, config derivation from the normal YAML/runtime config flow, and one-time unavailability warnings with continued retry behavior
  - updated `firmware/runtime.py` so boot logs and retained runtime logs continue to print locally while also mirroring to syslog when configured
  - updated `src/vivipi/runtime/app.py` so each completed probe logs its identity, type, target, status, duration, latency, detail, and manual flag; button presses and resulting navigation changes are now logged separately
  - updated checked-in deploy configs so the default build profile derives syslog host from `service.base_url` and the active local profile explicitly points syslog at `mickey:514`
  - updated `docs/spec.md`, `docs/spec-traceability.md`, and `PLANS.md` to reflect the current observability behavior and the in-progress hardware proof phase
- Validation:
  - focused logging/runtime/syslog/firmware/build-deploy test slice passed: `148 passed`
  - full repository validation passed again: `./build` -> `641 passed`, coverage `96.50%`

## 2026-04-18T19:05:00Z

- Completed the liveness visibility follow-up.
- Implementation changes:
  - updated `src/vivipi/core/liveness.py` so the bottom heartbeat can move a 3-pixel cluster through a visible lane over time instead of blinking a single fixed edge pixel
  - updated `src/vivipi/runtime/app.py` to feed time into heartbeat placement while keeping the same low-frequency deterministic decoration model
  - updated `firmware/displays/rendering.py` so full-width freshness bars use a larger two-pixel vertical notch instead of a single cleared pixel
  - raised the checked-in OLED liveness defaults in `config/build-deploy.yaml`, `config/build-deploy.local.yaml`, and `config/build-deploy.local.example.yaml` to more visible values
  - updated `README.md` with concise probe-freshness verification guidance and updated `docs/spec.md` wording to match the more visible indicators
- Validation:
  - focused liveness slice passed
  - full repository validation passed again: `./build` -> `636 passed`, coverage `97.48%`
- Deployment and live Pico verification:
  - redeployed with `./build deploy`
  - live Pico runtime now reports boot config `display liveness contrast=True heartbeat=True micro=True`
  - live Pico liveness samples now show:
    - `t=0.0`: heartbeat pixels `[63, 64, 65]`, contrast `128`, micro rows `0/7`
    - `t=10.0`: heartbeat pixels `[64, 65, 66]`, contrast `142`, micro rows `2/7`
    - `t=20.0`: heartbeat pixels `[65, 66, 67]`, contrast `114`, micro rows `7/7`
  - live Pico liveness logs now show visible-state transitions such as `heartbeat=64,65,66` and `heartbeat=65,66,67`
  - framebuffer inspection on the deployed board confirmed the healthy full-width bars now contain a larger two-pixel notch in active rows, replacing the old single-pixel dot

## 2026-04-18T18:40:00Z

- Started the liveness visibility follow-up after user feedback that the current healthy-state markers are too hard to see on the Pico OLED.
- Root-cause confirmation before edits:
  - `per_row_micro` currently renders as a single cleared pixel inside each full-width freshness bar
  - `bottom_heartbeat` currently defaults to one pixel at the far-right bottom scanline
  - checked-in contrast breathing defaults still use `period_s: 45` and `amplitude: 8`, which is likely too subtle for normal viewing
  - `README.md` does not yet describe a concise operator-facing procedure for verifying probe freshness
- Next step is to redesign the visible markers, retune the checked-in defaults, update tests/spec/docs, and redeploy to the Pico for live verification.

## 2026-04-18T18:25:50Z

- Follow-up verification after the report that the Pico display still showed no obvious liveness indicators.
- Investigation outcome:
  - confirmed the built firmware bundle and deployed Pico filesystem both include the new liveness modules and updated runtime/display code
  - confirmed on-device normalized config still enables all three features: `contrast_breathing`, `per_row_micro`, and `bottom_heartbeat`
  - confirmed the deployed Pico runtime was changing live frame metadata as expected at representative healthy-state timestamps: contrast `128 -> 135 -> 130`, heartbeat `on -> on -> off`, and per-row micro rows `0/7 -> 6/7 -> 7/7`
  - confirmed the missing observability gap was real: the feature worked, but there were no dedicated serial log lines for liveness state changes
- Repo changes in this follow-up:
  - added a boot-time serial line in `firmware/runtime.py` summarizing whether contrast, micro variation, and heartbeat are enabled in the deployed display config
  - added deduplicated `[INFO][DISP] liveness ...` runtime logs in `src/vivipi/runtime/app.py` so OLED liveness state changes can be verified from serial output without relying only on image capture
  - added focused runtime coverage in `tests/unit/runtime/test_app.py` for the new liveness log summaries
- Validation and deploy:
  - focused runtime liveness tests passed
  - full repo validation passed again: `./build` -> `636 passed`, coverage `97.50%`
  - redeployed to the attached Pico with `./build deploy`
  - post-deploy Pico serial verification now shows:
    - boot config log: `[BOOT][BOOT] display liveness contrast=True heartbeat=True micro=True`
    - runtime liveness logs: `[20][DISP] liveness contrast=135 heartbeat=120 micro_on=6/7 full_rows=7` and `[20][DISP] liveness contrast=130 heartbeat=off micro_on=7/7 full_rows=7`
    - direct frame samples from the deployed board still report the baseline healthy-state values at `t=0`: contrast `128`, heartbeat pixel `[120]`, micro rows `0/7`

## 2026-04-18T17:25:00Z

- Completed the OLED liveness indicators task end-to-end.
- Core/runtime/render changes:
  - added `src/vivipi/core/liveness.py` with deterministic helper functions for quantized contrast breathing, per-row micro phases, and heartbeat pixel placement
  - extended `src/vivipi/core/render.py` frame metadata so runtime decoration can carry per-frame contrast, bottom-row pixels, and optional freshness micro-pixel inversion without altering layout text
  - updated `src/vivipi/runtime/app.py` to normalize `display_liveness`, apply low-frequency liveness decorations during frame build, and allow liveness-only redraws even when the app state is otherwise unchanged
  - fixed `firmware/displays/rendering.py` freshness drawing so each indicator leaves the bottom logical pixel row dark and can optionally clear one deterministic micro pixel at full freshness
  - updated `firmware/displays/sh1107.py` and `firmware/displays/ssd1305.py` to honor per-frame contrast targets
- Config and docs:
  - extended display config normalization and runtime JSON generation to include `device.display.liveness.*`
  - enabled conservative defaults in `config/build-deploy.yaml`, `config/build-deploy.local.yaml`, and `config/build-deploy.local.example.yaml`
  - updated `docs/spec.md` and `docs/spec-traceability.md` to cover liveness config, the separator row, the per-row micro variation, and the bottom heartbeat constraints
- Validation:
  - focused pytest slice for liveness/config/runtime/display/build-deploy/traceability: `195 passed`
  - full repository validation: `./build` -> `635 passed`, coverage `97.49%`
  - firmware bundle rebuild task completed successfully
- Deployment and hardware verification:
  - `./build deploy` completed to the attached Pico
  - read back the deployed `config.json` and confirmed the SH1107 OLED profile now carries enabled `contrast_breathing`, `per_row_micro`, and `bottom_heartbeat` settings
  - on-device probe runner remained healthy for all configured targets and reported `freshness_width_px: 8` across the board
  - captured deployed OLED frame artifacts under `artifacts/display-capture/current/liveness/`
  - pixel-level analysis of the deployed OLED captures confirmed:
    - separator rows at `y = 7, 15, 23, 31, 39, 47, 55` are all dark
    - healthy-state heartbeat toggles a single pixel at `x = 120` on the bottom scanline and is absent in the heartbeat-off sample
    - per-row micro variation toggles one deterministic non-left-edge pixel within the full-width freshness bars
    - contrast targets on the device changed conservatively from `128` to `135` to `130` across the sampled healthy-state timestamps

## 2026-04-18T16:35:00Z

- Started the OLED liveness indicators task.
- Replaced the active top-level section in `PLANS.md` with a task-specific plan covering analysis, rendering fixes, the three liveness features, config integration, validation, and deployment.
- Repository inspection completed before edits:
  - confirmed the existing freshness indicator already flows through `CheckRuntime -> Frame.freshness_indicators -> firmware.displays.rendering._draw_freshness_indicator`
  - confirmed `RuntimeApp._decorate_frame(...)` is the right hook for low-frequency, deterministic liveness decoration without changing layout or text
  - confirmed SH1107 and SSD1305 backends already expose `set_contrast(...)`, while the active deployment target remains the SH1107 `waveshare-pico-oled-1.3`
  - confirmed display config normalization currently owns brightness, paging, and boot-logo timing, so `device.display.liveness` should be normalized there and passed through `build_deploy` unchanged
- No implementation changes yet beyond execution tracking; next step is the core/runtime/firmware patch set plus focused tests.

## 2026-04-18T16:05:00Z

- Follow-up on the reported cold-boot regression where the Pico appeared stuck on the boot logo after replug.
- Root cause confirmed at the active deploy-profile level:
  - the tracked `config/build-deploy.local.yaml` was still enabling `device.buttons.startup_self_test_s: 30`
  - that meant real cold boots could spend 30 seconds in startup-only UI before health probes appeared, which is incompatible with treating the board as "done" for probe-display work
- Device-level isolation and proof:
  - direct on-device probe execution via `artifacts/device/pico_probe_runner.py` stayed healthy, proving the runtime/check stack itself was not dead
  - a direct OLED test pattern written through the live `SH1107Display` backend rendered successfully on the physical panel, proving the panel + transport path were healthy
  - a controlled on-device replay of the startup sequence showed Wi-Fi connect, live probe execution, state transitions, and an overview frame with all checks rendered after the boot-logo window expired
  - after disabling startup self-test in the active deploy profile, redeploying, and replugging, the user directly observed the correct health-probe overview on the Pico a few seconds after boot
- Preventive repo changes already in place:
  - `config/build-deploy.local.yaml` now sets `startup_self_test_s: 0`
  - `AGENTS.md` now explicitly requires that real-device Pico work is only complete when the attached board itself is observed showing the health-probe overview, not merely a boot screen or serial-only evidence

## 2026-04-18T15:10:00Z

- Completed the probe freshness indicator and timing stabilization task end-to-end.
- Core/runtime/render/config changes:
  - added `src/vivipi/core/freshness.py` with bounded freshness math and missed-window accounting helpers
  - extended the standard overview render path to reserve the rightmost cell for a bitmap freshness indicator and updated `firmware/displays/rendering.py` to draw widths `{8, 6, 4, 2, 0}` with a visible zero-width sentinel pixel
  - updated `src/vivipi/runtime/app.py` to track `last_started_at`, `last_completed_at`, per-check freshness widths, and missed overdue windows with `probe_schedule.interval_grace_ms`
  - updated checked-in configs and runtime-config plumbing to use direct-probe defaults `interval_s: 10`, `timeout_s: 8`, `allow_concurrent_hosts: false`, and `interval_grace_ms: 1000`
  - slowed pixel shifting to the requested low-distraction range by changing the default cadence to `180s` and validating the `120..300s` config range
- Docs and test coverage:
  - updated `docs/spec.md` and `docs/spec-traceability.md` for the new freshness requirement, 10s/8s timing contract, calm healthy-display behavior, and slower burn-in shift cadence
  - added/updated focused coverage in `tests/unit/core/test_freshness.py`, `tests/unit/core/test_text.py`, `tests/unit/core/test_render.py`, `tests/unit/core/test_shift.py`, `tests/unit/core/test_config.py`, `tests/unit/runtime/test_app.py`, `tests/unit/firmware/test_display.py`, `tests/unit/tooling/test_build_deploy.py`, `tests/unit/core/test_vivipulse.py`, and `tests/spec/test_traceability.py`
  - focused validation after docs alignment: `217 passed`
  - full repo validation: `./build` -> `627 passed`, coverage `98.30%`
- Hardware verification on the attached Pico:
  - redeployed with the supported `./build deploy` flow and read the live `config.json` back from the board to confirm `interval_s: 10`, `timeout_s: 8`, `allow_concurrent_hosts: false`, and `interval_grace_ms: 1000`
  - live baseline after final deploy via `artifacts/device/pico_probe_runner.py` was healthy for all configured targets: `c64u-rest`, `c64u-ftp`, `c64u-telnet`, `pixel4-adb`, `u64-rest`, `u64-ftp`, and `u64-telnet` all reported `OK` with full `freshness_width_px: 8`
  - device-side runtime probes confirmed the calm path and shift cadence: steady healthy ticks were suppressed after the initial state settled, and the first automatic shift occurred at `180.1s` with no earlier shift at `60.0s` or `119.9s`
  - device-side in-flight miss simulation confirmed freshness behavior on the Pico interpreter itself: `8 -> 6 -> 4 -> 8` across success, first overdue window, second overdue window, and recovery
- Late hardware-only bug found and fixed:
  - while validating calm rendering on the Pico, discovered that identical `AppState` and `Frame` objects compared unequal on-device even though their contents matched
  - root cause was the MicroPython dataclass shim in `firmware/dataclasses.py`, which implemented `__init__` and `__repr__` but not value-based `__eq__`
  - added structural equality to the shim and a regression assertion in `tests/unit/firmware/test_dataclasses.py`; after redeploy, the Pico render-dedup path behaved as intended

## 2026-04-18T00:20:00Z

- Started the probe freshness indicator and timing stabilization task.
- Replaced the active top section in `PLANS.md` with a task-specific execution plan covering analysis, implementation, integration, validation, and deployment.
- Reviewed the current runtime, scheduler, renderer, display transport, config parsing, and deployment paths before code changes.
- Confirmed the current checked-in direct-probe defaults are still `7s / 5s` in `config/checks.yaml` and `config/checks.local.yaml`.
- Confirmed the device runtime already forces serial probe execution, so the new timing work needs deterministic missed-window accounting rather than new concurrency controls.
- Confirmed the current standard overview row consumes the full 16-column width, so the freshness indicator requires an explicit fixed-width row-layout change.
- Confirmed the SH1107 OLED path still relies on the rotated `128x64 -> 64x128` transport with `column_offset = 32`; that mapping must remain unchanged.
- No code changes yet beyond execution tracking files.

## 2026-04-17T17:45:00Z

- Follow-up tuning after live validation showed intermittent single-probe false negatives.
- Relaxed the checked-in direct-probe timing from `5s / 4s` to `7s / 5s` in `config/checks.yaml` and `config/checks.local.yaml`.
  - rationale: preserve sub-15-second single-probe detection while stopping transient healthy probes from briefly flipping to `FAIL` on short network stalls
  - `5s / 5s` was rejected by the config validator because `timeout_s` must remain at least 20% smaller than `interval_s`, so `7s / 5s` is the smallest legal timing pair that adds the needed slack
- Investigated the inverted first display row and confirmed it was the normal overview selection highlight, not a display transport/render bug.
- Temporarily disabled overview selection highlighting on the device runtime path:
  - added a `highlight_selection` render/runtime switch with a safe default of `True`
  - firmware now instantiates `RuntimeApp(..., highlight_selection=False)` so the real Pico display stays neutral until button input is fixed
  - internal selection state is preserved; only the visible inversion is suppressed
- Added focused coverage for the new behavior and revalidated:
  - `tests/unit/core/test_render.py`
  - `tests/unit/firmware/test_runtime.py`
  - focused result: `39 passed`

## 2026-04-17T17:20:00Z

- Investigated the new report that powered-off `C64U` and `U64` were still shown as `OK` after 30s and that button presses had no effect.
- Confirmed the direct probe/runtime logic itself was not the failing layer:
  - with both devices powered off, `sg dialout -c 'mpremote connect auto run artifacts/device/pico_probe_runner.py'` reported `FAIL` for `c64u-rest`, `c64u-ftp`, `c64u-telnet`, `u64-rest`, `u64-ftp`, and `u64-telnet` within the configured `4s` timeout budget
  - this means the runtime probe engine can detect the off-state correctly; the symptom was a stale or non-running unattended firmware session on the device
- Root cause hypothesis strengthened by live behavior:
  - the supported deploy path ended with `mpremote ... soft-reset`
  - after prior USB/REPL interactions, that was not strong enough to guarantee the Pico returned to autonomous `boot.py` / `main.py` execution
  - a stale screen plus dead buttons is consistent with the board being left in a non-running interactive state even though the copied firmware is correct
- Implemented a deploy-path fix in `src/vivipi/tooling/build_deploy.py`:
  - after copying the device filesystem, deploy now finishes with `mpremote connect <port> reset` instead of only `soft-reset`
  - recovery behavior inside `_run_mpremote_command(...)` still uses `soft-reset` for retry purposes; only the final post-deploy restart is now a full device reset
- Updated and revalidated deploy coverage in `tests/unit/tooling/test_build_deploy.py`:
  - focused test file: `65 passed`
  - full repo build: `./build` -> `617 passed`, coverage `98.46%`
- Redeployed the Pico with the new final-reset behavior so the device should now come back in the unattended runtime path rather than a stale USB-touched state.

## 2026-04-17T16:55:00Z

- Completed the ViviPi probe productionization task end-to-end.
- Root cause and code fix:
  - reproduced the failure on the real Pico with `artifacts/device/pico_probe_runner.py`; both `c64u-telnet` and `u64-telnet` failed on-device with `io: [Errno 110] ETIMEDOUT` even though the same runtime helper succeeded under CPython and the host-side `scripts/u64_connection_test.py` suite also passed
  - fixed `src/vivipi/runtime/checks.py` so MicroPython `ETIMEDOUT` (`errno 110`) is classified as `timeout`, which restores the intended post-connect telnet success semantics on-device
- Responsiveness and state-model changes:
  - added render-time `visible_degraded` handling so the display can hide the visible `DEG` phase without changing the internal hysteresis model
  - updated runtime/config plumbing so `check_state.visible_degraded` is parsed, validated, rendered into `config.json`, and consumed by the Pico runtime
  - lengthened normal button press feedback visibility from `0.15s` to `0.75s` so button activity is observable during normal use
  - updated checked-in configs to use internal thresholds `1/2/1`, `visible_degraded: false`, and direct probe cadence `interval_s: 5`, `timeout_s: 4`
- Docs and tests:
  - updated `docs/spec.md`, `docs/spec-traceability.md`, `README.md`, and checked-in config examples to reflect telnet success semantics, visible degraded configuration, faster default cadence, and observable button feedback
  - added/updated unit coverage in `tests/unit/runtime/test_checks.py`, `tests/unit/runtime/test_app.py`, `tests/unit/firmware/test_runtime.py`, and `tests/unit/tooling/test_build_deploy.py`
- Validation evidence:
  - focused tests: `pytest --no-cov tests/unit/runtime/test_checks.py tests/unit/runtime/test_app.py tests/unit/firmware/test_runtime.py tests/unit/tooling/test_build_deploy.py -q` -> `188 passed`
  - full repo build: `./build` -> `617 passed`, coverage `98.46%`
  - redeployed the Pico with the supported deploy task and verified the live on-device baseline with `sg dialout -c 'mpremote connect auto run artifacts/device/pico_probe_runner.py'`
  - post-fix on-device results were all healthy with the redeployed config:
    - `c64u-rest OK HTTP 200`
    - `c64u-ftp OK pwd=/`
    - `c64u-telnet OK visible_bytes=3770`
    - `pixel4-adb OK HTTP 200`
    - `u64-rest OK HTTP 200`
    - `u64-ftp OK pwd=/`
    - `u64-telnet OK visible_bytes=2009`
  - read deployed `config.json` from the Pico and confirmed `failures_to_failed: 2`, `visible_degraded: false`, and `interval_s: 5` / `timeout_s: 4`
  - live GPIO/button validation on the deployed Pico:
    - idle monitor confirmed `GP15` and `GP17` both idle high
    - interactive button monitor captured `PRESS` and `RELEASE` events for both button A on `GP15` and button B on `GP17`
- No remaining code-side blockers were found after deploy and hardware validation.

## 2026-04-17T10:05:00Z

- Started the ViviPi probe productionization task and replaced the active top-of-file plan in `PLANS.md` with a task-specific execution plan for probe correctness, responsiveness, configurable visible degradation, button verification, deployment, and live validation.
- Repository and spec review completed for the active task:
  - read `docs/spec.md`, `README.md`, `AGENTS.md`
  - inspected repo memories for prior validated probe and OLED transport semantics
  - inspected `config/build-deploy.yaml`, `config/build-deploy.local.example.yaml`, `config/build-deploy.local.yaml`, `config/checks.yaml`, and `config/checks.local.yaml`
  - inspected Pico/runtime code in `src/vivipi/runtime/checks.py`, `src/vivipi/runtime/app.py`, `src/vivipi/core/models.py`, `src/vivipi/core/state.py`, `src/vivipi/core/config.py`, `src/vivipi/core/scheduler.py`, `src/vivipi/core/input.py`, `firmware/runtime.py`, and `firmware/input.py`
  - inspected source-of-truth host probes in `scripts/u64_connection_test.py`, `scripts/u64_connection_runtime.py`, `scripts/u64_telnet.py`, `scripts/u64_ftp.py`, and `scripts/u64_http.py`
  - inspected relevant tests in `tests/unit/runtime/test_checks.py`, `tests/unit/runtime/test_app.py`, `tests/unit/firmware/test_runtime.py`, `tests/unit/firmware/test_firmware_input.py`, and `tests/unit/tooling/test_u64_connection_protocols.py`
- Findings documented before code changes:
  - local deployment config already targets `GP15` / `GP17` and enables `startup_self_test_s: 30`
  - local deployment config already uses immediate state thresholds: `failures_to_degraded=1`, `failures_to_failed=1`, `successes_to_recover=1`
  - active local checks still use `interval_s: 10` and `timeout_s: 8`, which is the strongest current explanation for slow visible fail/recover behavior
  - Pico FTP probe is already structurally aligned with the remembered source-of-truth smoke semantics: connect, login, `PWD`, `QUIT`
  - Pico telnet probe is already structurally aligned with the remembered source-of-truth smoke semantics in one key respect: post-connect timeout/reset is treated as healthy connectivity
  - button handling is present, not missing: the runtime polls buttons, applies navigation actions, shows short press feedback, and supports a startup self-test frame
- Current leading hypotheses:
  - `U64 TELNET` may still fail on the Pico because of a smaller remaining socket-handling mismatch rather than a missing authentication flow
  - slow displayed transitions are likely dominated by the `10s/8s` cadence and timeout budget, not by the threshold model, because the active config already forces direct fail/recover thresholds
  - button complaints are likely due to discoverability or insufficiently observable normal-runtime feedback rather than total absence of button software support
- No code changes yet beyond updating execution tracking files.
- No runtime commands executed yet; next step is live baseline reproduction of the current probe behavior and then a targeted code fix.

## 2026-04-17T06:35:00Z

- Tightened the telnet `Vol UltiSid 1` write path in `scripts/u64_telnet.py` to match the live-proven exact sequence for this setting:
  - `F2`, `DOWN`, `ENTER` to open the Audio Mixer page from home.
  - `ENTER` on `Vol UltiSid 1`, then cursor `UP`/`DOWN` without wraparound to the target value, then `ENTER` to commit the submenu selection.
  - exactly two `LEFT` presses to back out and raise `Save changes to Flash? Yes No`, then `ENTER` to accept `Yes`.
- Removed wraparound picker math for telnet volume writes so the probe now follows the linear menu semantics the real device actually uses.
- Updated `tests/unit/tooling/test_u64_telnet.py` to assert the exact `ENTER`, `UP`/`DOWN`, `ENTER`, `LEFT`, `LEFT`, `ENTER` write/save sequence and the non-wrapping picker behavior.
- Live validation on the real U64 through the normal probe entrypoint now succeeds for both telnet write operations:
  - `set_vol_ultisid_1_0_db`: `OK from=-2 dB to=0 dB picker=down steps=2`
  - `set_vol_ultisid_1_plus_1_db`: `OK from=0 dB to=+1 dB picker=down steps=1`
- Live 30-second concurrent validation also completed with no ping/http/ftp/telnet FAIL results for `scripts/u64_connection_test.py --profile stress --probes ping,http,ftp,telnet --mode correct --surface readwrite --runners 2 --duration-s 30 --log-every 1`.

## 2026-04-17T05:35:00Z

- Replaced the stale FTP CLI plan with a task-specific stabilization plan for the shared U64 connection suite.
- Inspected `scripts/u64_connection_test.py`, `scripts/u64_http.py`, `scripts/u64_ftp.py`, and `scripts/u64_telnet.py` against the current probe semantics and repo notes.
- Confirmed the pre-fix state-model gaps:
  - HTTP tracked only confirmed Audio Mixer values.
  - FTP self-file state was process-local and could devolve to `skip=no_self_file` instead of a real readwrite operation.
  - Telnet write flow still used direct right-arrow edits and trusted cached UI state after writes.

## 2026-04-17T05:55:00Z

- Ran a live concurrent baseline with `./.venv/bin/python scripts/u64_connection_test.py --duration-s 20 --probes ftp,telnet,http --schedule concurrent --runners 2 --log-every 1`.
- Observed that the immediate FAILs on this host were from the default stream monitor (`stream_audio=FAIL`, `stream_video=FAIL`), not from ping/http/ftp/telnet.
- Captured concrete silent-misclassification evidence in the same run: `protocol=ftp result=OK ... op=ftp_rename_self_file skip=no_self_file`, which violates the intended readwrite semantics even when it does not raise FAIL.
- Confirmed that telnet writes were still taking the legacy right-step path instead of the verified picker path documented for the real U64 Audio Mixer UI.

## 2026-04-17T06:20:00Z

- Implemented the shared-state fix set in the U64 protocol runners:
  - `ExecutionState` now allows thread-safe object-valued shared state entries.
  - HTTP now carries tentative Audio Mixer state during writes and confirms it only after bounded read-back verification.
  - FTP now maintains shared confirmed/tentative self-file state, provisions verified files instead of returning `skip=no_self_file`, and revalidates rename/delete/download state with bounded retries.
  - Telnet now uses the Audio Mixer picker flow (`ENTER`, `UP`/`DOWN`, `ENTER`), verifies the final value via HTTP, and invalidates cached telnet view state after writes.
- Added/updated tooling tests to cover the new HTTP tentative state behavior, FTP rename/delete provisioning without silent skips, and telnet picker-flow verification behavior.

## 2026-04-17T06:30:00Z

- Started work on deterministic U64 FTP performance CLI (`scripts/u64_ftp_test.py`).
- Rewrote `PLANS.md` as authoritative execution plan for this task.
- Confirmed existing runner logging style (`timestamp protocol=X result=Y detail="k=v ..."`) and adopted it for the new tool.
- Next: implement argparse, payload generation, stage runner, and metrics aggregation in a single stdlib-only file.

## 2026-04-17T07:45:00Z

- Added a single-line, TTY-aware progress bar to `scripts/u64_ftp_test.py`:
  - `ProgressBar` class writes via `\r` plus ANSI `\x1b[2K` erase so the bar
    occupies exactly one line and is overwritten in place.
  - Bar renders only when stdout is an interactive TTY and `--format=text`.
    When output is piped to a file, CI, or `--format=json`, the bar is
    suppressed entirely so batch consumers see the existing clean
    line-oriented format untouched.
  - `Emitter.emit_text()` coordinates with the bar by calling
    `progress.pause()` before each permanent log line and
    `progress.resume()` afterward, so log lines never mid-line the bar.
  - Bar content: `[##########--------] 42% 19/52 size=20K mode=single workers=1 fail=0 eta=3s`.
- Reworked throughput reporting for unambiguous units and aggregation semantics:
  - Per-transfer: `up_KBps` / `down_KBps` (kilobytes/sec, 1024-based).
  - Per-stage END line: `stage_s=<wall-clock>` plus
    `agg_up_KBps`/`agg_down_KBps` computed as total bytes transferred
    across all workers divided by the stage's wall-clock duration. This
    is explicitly "aggregate across all workers", not per-worker mean.
  - Summary line: `total_up_KB` / `total_down_KB` and aggregate
    `agg_up_KBps` / `agg_down_KBps` over the sum of all stage durations.
  - JSON output mirrors the same keys and adds
    `throughput_basis=aggregate_across_all_workers` and
    `throughput_unit=KBps_1024_based` per stage for self-describing output.
- Tests (`tests/unit/tooling/test_u64_ftp_test.py`):
  - Added `test_progress_bar_disabled_when_stdout_is_not_tty`,
    `test_progress_bar_renders_single_line_when_tty_enabled` (uses a
    `FakeTTY(StringIO)` that reports `isatty() is True`),
    `test_progress_bar_tick_counts_failures_separately`,
    `test_progress_bar_noop_when_disabled`, and
    `test_aggregate_throughput_is_bytes_over_stage_wall_time` which
    verifies 30 files * 20 KB over 5 s == 120 KBps aggregate.
  - Updated existing assertions to match the renamed keys
    (`agg_up_KBps`, `stage_s`, `total_up_KB`).
- Bugs caught by tests and fixed:
  - `ProgressBar.__init__` previously bound `stream=sys.stdout` at
    function-definition time, defeating `contextlib.redirect_stdout`.
    Switched to lazy `stream if stream is not None else sys.stdout`.
  - Throttled redraws suppressed the visible counter on very fast
    transfers; ticks now force a redraw.
- Verified: `python -m pytest -o addopts='' tests/unit/tooling/test_u64_ftp_test.py` -> 23 passed.
- Verified progress bar under a PTY:
  `script -qfec "python scripts/u64_ftp_test.py --sizes 20K --target-bytes 100K" /tmp/u64ftp_tty.log` shows
  `\r[####-----]` redraws and `\x1b[2K` erase sequences interleaved
  with permanent `protocol=*` log lines.

## 2026-04-17T09:15:00Z

- Root-caused the remaining telnet latency/correctness failures against the live U64 rather than by further timeout tuning.
- Confirmed on the live device that the action-menu path is deferred-repaint, not immediate-response:
  - `F2` is accepted on this firmware build but often produces no immediate visible frame.
  - The next bounded input triggers the repaint.
- Reworked `scripts/u64_telnet.py` menu navigation accordingly:
  - `session_open_menu()` and `open_menu()` now tolerate bounded deferred repaint instead of failing on the first empty read.
  - `session_open_audio_mixer()` now uses the live-verified bounded path from home to Audio Mixer, which consistently reaches `Vol UltiSid 1` on the real device.
- Validated the live read path after the fix:
  - direct `session_open_audio_mixer()` on the real U64 succeeded repeatedly.
  - orchestrated `run_probe(... op=telnet_open_audio_mixer ...)` completed successfully in about `494 ms`, which is within the <1 s requirement.
- Corrected the telnet Audio Mixer interaction model from live-device evidence:
  - `Vol UltiSid 1` opens a vertical value submenu with `ENTER` on the row.
  - Inside that submenu, volume selection uses cursor `UP` / `DOWN`, followed by `ENTER` to commit the row value.
  - The change is not persisted until the probe navigates `LEFT` repeatedly to the `Save changes to Flash? Yes No` dialog and confirms the default `Yes` choice with `ENTER`.
- Updated telnet write handling to use the vertical picker flow plus bounded save-to-flash confirmation before HTTP verification.
- Focused regressions after the telnet fixes passed:
  - `pytest --no-cov tests/unit/tooling/test_u64_telnet.py tests/unit/tooling/test_u64_connection_protocols.py tests/unit/tooling/test_u64_concurrent_runner_isolation.py` -> `35 passed`.
- Ran `./build` after the change as required. Result:
  - the repository build/test pipeline reached `580 passed` and `1 failed`.
  - the remaining failure is unrelated to the telnet work: `tests/unit/tooling/test_u64_ftp_test.py::test_zero_arg_defaults_match_spec` expects the default size tuple to include `1M`, but the current CLI default resolves only `20K,200K`.

## 2026-04-17T07:20:00Z

- Found that the first real-device invocation failed with `cwd_failed 550 Requested action not taken` because the default
  `--remote-dir=/USB2/test/FTP` did not exist on the device.
- Added idempotent `ensure_remote_dir()` (walks each path segment with `cwd` then `mkd`) and a
  `--ensure-remote-dir/--no-ensure-remote-dir` flag (default on) so zero-arg invocation provisions the dir itself.
- Aligned `max-runtime-s` with the spec's "finish current stage" rule by removing the mid-stage deadline check from
  `worker_loop`; the deadline is now only consulted between stages in `run`.
- Authored `tests/unit/tooling/test_u64_ftp_test.py` with 18 unit tests covering: size/byte parsing, deterministic
  payload, filename format, `files_per_worker` math (including the spec's 20K -> 52 example), mode and worker
  resolution, help-text flag coverage, zero-arg defaults, config-line format, full end-to-end happy path (STOR+RETR
  counts verified), remote-dir auto-create, connect failure classification, verify-mismatch detection, fail-fast,
  JSON-mode single-document emission, runtime-limit behavior, and `--no-verify` skip marker. Suite uses the existing
  `_script_loader.load_script_module` helper and a thread-safe in-memory `FakeFTP`/`FakeServerState` pair.
- Verified: `python -m pytest -o addopts='' tests/unit/tooling/test_u64_ftp_test.py` -> 18 passed.
- Verified: zero-arg real-device run `python scripts/u64_ftp_test.py --max-runtime-s 150` against `host=u64` executed
  all 6 stages (20K/200K/1M x single/multi), auto-created `/USB2/test/FTP`, uploaded and verified 12856 KB each way,
  runtime 148 s. One transfer in the 200K multi stage returned `550 Requested action not taken` from the device;
  the tool correctly classified it as `phase=download_failed` and emitted the final `protocol=summary`.
- Decision: this intermittent 550 is a known U64 firmware quirk (see `fix/ftp-partial-stor-returns-550-for-incomplete-probe`
  branch in git history) and is outside the scope of this CLI, which is only required to detect and report failures
  cleanly. Success is defined as: tests pass, real-device run completes end-to-end with structured output.

## 2026-04-17T06:45:00Z

- Implemented `scripts/u64_ftp_test.py` in a single stdlib-only file covering:
  - All specified CLI flags and defaults, including `--passive/--no-passive`, `--verify/--no-verify`, `--fail-fast/--no-fail-fast`.
  - `parse_sizes` for `20K,200K,1M` style and `parse_byte_size` for `--target-bytes`.
  - Deterministic 32-byte-seed payload generator reproducible across runs.
  - Filename pattern `u64ftp_<sizeLabel>_<worker>_<iter>.bin`.
  - `FtpOpenError` phase tagging (`connect_failed`, `login_failed`, `cwd_failed`).
  - Per-transfer `upload_failed`, `download_failed`, `verify_mismatch` classification.
  - Thread-per-worker stage runner using `ThreadPoolExecutor`, with `single` and `multi` mode ordering per spec.
  - Per-stage START/END lines, transfer lines only on verbose or failure, global summary line.
  - JSON mode: single object with config/stages/summary emitted after text line suppressed.
  - `--max-runtime-s` deadline checked between stages; emits `summary result=PARTIAL` when tripped.
  - `--fail-fast` aborts after first failure.
- Verified:
  - `python scripts/u64_ftp_test.py --help` renders all flags.
  - `build_payload` is deterministic and exact-size.
  - `parse_sizes("20K,200K,1M") == (("20K",20480),("200K",204800),("1M",1048576))`.
  - `compute_files_per_worker(20480, 1048576, None) == 52` (matches spec example).
  - Zero-arg invocation produces the exact config line from spec (`host=u64 dir=/USB2/test/FTP sizes=20K,200K,1M target=1M concurrency=3 mode=both verify=1`).
  - End-to-end mocked FTP run completes all 4 stages (both modes × two sizes) with matching verify.
  - Forced `OSError` on connect yields `phase=connect_failed` in transfer line and `summary result=FAIL`.
  - Corrupt RETR yields `phase=verify_mismatch`.
  - `--fail-fast` with failures stops after the first stage.
  - JSON mode produces a single well-formed document with `config`, `stages[]`, and `summary`.
- PLANS.md phases all marked DONE.

## 2026-04-10T21:16:30Z

- Created `PLANS.md` as the authoritative execution plan.
- Began repository inspection for Phase A.
- Confirmed clean worktree before edits.
- Identified two immediate Phase A issues:
  - Boot logo hold defaults to 6 seconds, which violates the 3 second boot-screen target.
  - Firmware button defaults still target `GP14` and `GP15` instead of the required `GP22` and `GP21`.
- Next step: instrument the boot sequence and tighten startup timing without introducing blocking behavior.

## 2026-04-10T21:29:21Z

- Implemented Phase A boot instrumentation in `firmware/runtime.py`:
  - Added serial boot-stage logs for config load, display setup, boot logo, button setup, check loading, startup tick, and loop entry.
  - Removed blocking startup Wi-Fi connect from `run_forever`; network reconnect is now deferred into the runtime tick path.
  - Changed the default boot logo duration from `6s` to `2s` and updated deploy config defaults accordingly.
  - Changed runtime button defaults from `GP14/GP15` to `GP22/GP21`.
- Implemented Phase C, D, and E runtime behavior in `src/vivipi/runtime/app.py`:
  - Added non-blocking network reconnect worker handling in the runtime.
  - Added `last_success_s` tracking for checks.
  - Added manual force-refresh queueing for all checks.
  - Added display propagation logging for state transitions.
  - Added debug overlay rows and transient on-screen feedback messages.
  - Remapped buttons at the runtime boundary:
    - Button A toggles debug overlay.
    - Button B requests an immediate refresh of all checks.
- Implemented Phase E button instrumentation in `firmware/input.py`:
  - Added per-button bias detection with `auto`, `up`, and `down` handling.
  - Added debounced edge detection with raw value, stable value, and event logging.
  - Added logger binding so button activity reaches the serial log stream.
- Updated SH1107 and config validation coverage:
  - Added an explicit SPI mode 3 assertion to firmware display tests.
  - Preserved the SH1107 `column_offset = 32` path.
- Validation results:
  - `python -m pytest -o addopts='' tests/unit/runtime/test_observability.py tests/unit/runtime/test_app.py tests/unit/firmware/test_runtime.py tests/unit/firmware/test_firmware_input.py tests/unit/firmware/test_display.py tests/unit/core/test_display_config.py tests/unit/tooling/test_build_deploy.py` passed with `145 passed`.
  - `./build lint` passed.
  - `./build test` completed with `330 passed` but still failed the repository coverage gate at `90.38%` versus the required `96%`.
- Remaining blockers:
  - Hardware-only acceptance proof is still pending in this environment:
    - actual button electrical polarity on `GP22` and `GP21`
    - OLED framebuffer photos/screenshots
    - serial logs from a live Pico boot
    - Pixel 4 / ADB / scrcpy screenshots
  - The repository-wide coverage threshold remains below the required floor and is not isolated to the firmware changes in this turn.

## 2026-04-10T21:30:50Z

- Ran `./build render-config` and verified the generated device config carries:
  - `buttons.a = GP22`
  - `buttons.b = GP21`
  - `device.display.boot_logo_duration_s = 2`
  - `device.display.spi_mode = 3`
  - `device.display.column_offset = 32`
- Rebuilt the firmware bundle with `./build build-firmware`.
- Verified the release filesystem artifact now matches the same runtime config values as the device staging artifact.

## 2026-04-10T21:37:32Z

- Replaced `PLANS.md` with a hardware-first recovery plan.
- Marked the previous `GP22` / `GP21` assumption as untrusted pending real-board proof.
- Started Phase 1 ground-truth recovery:
  - identify connected Pico USB/serial paths
  - identify actual deployment path
  - identify Pixel 4 / `adb` / `scrcpy` observation path
  - prove whether the connected device is running stale firmware

## 2026-04-10T21:56:28Z

## 2026-04-17T06:02:20Z

- Started a follow-on RAM and target-viability pass for the existing `1541ultimate` FTP findings.
- Verified that the relevant `U64`, `U64E-II`, and `U2+` firmware builds all compile with `heap_3.c`, so FreeRTOS dynamic allocations and ordinary `new`/`malloc` share the same general heap instead of a standalone `configTOTAL_HEAP_SIZE` arena.
- Confirmed target memory bounds from linker scripts:
  - Nios application builds use `software/nios_appl_bsp/linker.x` with `__alt_heap_limit = 0xea0000` and large reserved windows starting at `0x0EA8000`.
  - The U64E-II RISC-V build uses `target/u64/riscv/ultimate/linker.x` with a heap region ending at `0xEA0000`.
- Collected comparison allocations to judge whether FTP-local buffer growth is reasonable:
  - RMII RX pool: `49,408` bytes.
  - USB AX88772 packet pool: `98,304` bytes.
  - Socket DMA load buffer: `200,000` bytes.
  - `GcrImage` backing store: `635,932` bytes per built-in drive object.
- Next: rewrite `docs/research/1541ultimate/ftp-performance/findings.md` so RAM impact and target viability are integrated into the existing findings and matrix.

## 2026-04-17T06:07:59Z

- Rewrote `docs/research/1541ultimate/ftp-performance/findings.md` to integrate RAM and target-viability analysis throughout the existing structure instead of appending a detached chapter.
- Updated the report to:
  - correct the heap model with code evidence from `heap_3.c`, `memory.cc`, and target linker scripts
  - add RAM-relevant platform context for `U64`, `U64E-II`, and `U2+`
  - attach per-finding RAM impact, allocation domain, and FPGA-target viability
  - rewrite the candidate improvements matrix with RAM-specific columns
- Final conclusions recorded in the report:
  - FTP-local `8 KiB` buffer growth is justified and should be tried before `16 KiB`.
  - Reusing one enlarged FTP session buffer is materially safer than adding separate staging buffers.
  - Shared `lwIP` pool and window growth remains viable only with explicit heap-headroom measurements and should stay a second-stage investigation.

## 2026-04-17T06:16:58Z

- Extended the same FTP report with a broader whole-firmware RAM survey to answer whether the buffer recommendation still holds when judged against the rest of the firmware tree.
- Added code-grounded comparison points outside the immediate FTP/network path:
  - fixed reserved windows for `REU`, `RAM disk`, and updater space
  - per-drive dynamic memory for `C1541` objects, including `GcrImage`, `BinImage`, and dummy-track storage
  - capability-local caches such as the `TapeRecorder` cache
  - wider-firmware transient helpers such as `FileManager` copy staging, API memory buffers, and zero-fill staging
- Rewrote the report matrix again to add a `Firmware Context` column so the recommendations now distinguish:
  - FTP-local `8 KiB` growth, which is smaller than several existing staged buffers
  - shared `lwIP` pool growth, which is closer to adding another resident subsystem-sized buffer
- Final report position after the wider survey is unchanged in direction but stronger in justification:
  - `8 KiB` FTP-local buffering is well within the range of already-accepted local allocations in this firmware.
  - The real caution line remains global, always-resident network-pool growth rather than the FTP-local change itself.

- Established the actual hardware access paths:
  - Pico serial device: `/dev/ttyACM0`
  - Pico deploy/access command: `sg dialout -c 'mpremote connect /dev/ttyACM0 ...'`
  - Pixel 4 observation path: `adb shell screencap -p ...` plus `adb pull ...`
  - Pixel camera app confirmed active via `dumpsys window` with `org.lineageos.aperture/.CameraLauncher`
- Proved the connected Pico was running the freshly deployed bundle by reading live `/config.json` from device storage and matching the deployed build metadata over serial.
- Captured the first real Pico boot traceback:
  - startup crashed in `vivipi/runtime/state.py` while formatting an exception via `sys.print_exception`
  - on-device exception: `OSError: stream operation not supported`
  - effect: runtime handoff aborted, leaving the OLED on the boot logo
- Fixed the stuck-logo root cause:
  - made exception-trace capture non-fatal on MicroPython
  - added serial breadcrumbs for captured startup and loop exceptions
- Captured the next real hardware faults after the boot crash was removed:
  - startup button exception: `AttributeError("'str' object has no attribute 'value'")`
  - loop scheduler exception: `AttributeError("'str' object has no attribute 'casefold'")`
- Fixed the next on-device compatibility faults:
  - button logging now tolerates plain-string button identifiers on MicroPython
  - scheduler host normalization now falls back to `.lower()` when `.casefold()` is unavailable
  - background thread startup now falls back cleanly instead of surfacing `OSError: core1 in use`
- Verified the repaired runtime path on real hardware:
  - boot logs now proceed through config load, display init, button bind, network state, startup tick, and live check execution
  - first rendered check/progression logs show live runtime activity beyond the boot logo
- Captured real health-transition proof on the running Pico:
  - forced `pixel4-adb` into `FAIL` by stopping the host ADB-backed service
  - serial log showed `transition id=pixel4-adb from=OK to=FAIL`
  - serial log showed `DISP propagation id=pixel4-adb status=FAIL delay_ms=0.0`
  - after restoring the service on `0.0.0.0:8081`, serial log showed `transition id=pixel4-adb from=FAIL to=OK`
  - serial log showed `DISP propagation id=pixel4-adb status=OK delay_ms=0.0`
- Updated the active deployed button mapping from the known-bad `GP22/GP21` to the board-image lead `GP15/GP17`.
- Current remaining blockers are physical, not software:
  - no autonomous actuation path exists for the on-board buttons
  - no autonomous cold-power-cycle path exists from this shell; `usbreset`/raw USB reset is blocked by device permissions
- Proof artifacts captured in `artifacts/hardware-proof/`:
  - `health-transition-serial.log`
  - `health-recovery-serial-2.log`
  - `pico-runtime-screen-2.png`

## 2026-04-10T21:58:46Z

- Cleaned remaining stale repo references from `GP22/GP21` to `GP15/GP17` in:
  - `README.md`
  - `tests/unit/firmware/test_firmware_input.py`
  - `tests/unit/firmware/test_runtime.py`
  - `tests/unit/tooling/test_build_deploy.py`
- Re-ran the affected unit slices and confirmed they pass with the corrected mapping.
- Re-confirmed the live Pico filesystem is deployed with:
  - `device.buttons.a = GP15`
  - `device.buttons.b = GP17`

## 2026-04-10T22:10:29Z

- Investigated the local `1541ultimate` source tree to match ViviPi probe behavior against the actual device implementations.
- Ruled out the earlier `X-Password` null-pointer path as the active root cause for this setup after confirming the user does not run a network password on the C64U/U64.
- Identified the active crash-risk probe behaviors from source:
  - FTP health checks were using `PASV` + `LIST`, which forces an extra passive data socket, an extra task, and a full directory transfer on the 1541ultimate FTP daemon.
  - Telnet health checks were sending fallback probe bytes (`IAC NOP`, then newline) that are not required for the 1541ultimate telnet server and are risky against its minimal parser and login flow.
  - Probe-local retry backoff was still `100 ms` / `200 ms`, which violated the intended `>= 500 ms` same-device spacing rule even after the scheduler-level fix.
- Implemented the corresponding ViviPi fixes:
  - HTTP runner now supports explicit request headers cleanly and always asks the server to close the connection.
  - FTP runner was reduced to a control-channel-only probe: login, `PWD`, `QUIT`.
  - Telnet runner no longer sends fallback `NOP`/newline probe bytes; after login it only waits briefly for additional server output and otherwise treats the connected session as sufficient.
  - Probe retry backoff base was raised to `500 ms`, producing retry spacing of `500 ms`, then `800 ms`.
- Validation:
  - `./.venv/bin/python -m pytest -o addopts='' tests/unit/runtime/test_checks.py tests/unit/core/test_execution.py` passed with `42 passed`.
  - `./.venv/bin/python -m pytest -o addopts='' tests/unit/core/test_scheduler.py tests/unit/runtime/test_app.py tests/unit/tooling/test_build_deploy.py` passed with `77 passed`.

## 2026-04-10T22:12:43Z

- Deployed the updated bundle to the real Pico with `./build deploy --device-port /dev/ttyACM0`.
- Forced a Pico reset and captured the next on-device serial window in:
  - `artifacts/hardware-proof/u64-safe-probes-serial-after-reset.log`
- Verified from real serial output that the deployed runtime is active and running the updated health-check set:
  - boot completed through display init and button bind
  - `checks loaded count=7`
  - live executions started for `u64-rest`, `u64-ftp`, `u64-telnet`, `c64u-rest`, `c64u-ftp`, and `c64u-telnet`
- The captured window shows the Pico executing the new probe bundle on-device; the individual device checks were failing at transport level in that capture window rather than crashing the Pico runtime.

## 2026-04-11T09:53:48Z

- Began the `vivipulse` implementation pass.
- Inspected and confirmed the current shared probe-execution seam used by the Pico runtime:
  - `firmware/main.py -> firmware.runtime.run_forever()`
  - `firmware.runtime.build_runtime_app()` uses `vivipi.runtime.checks.build_runtime_definitions()`
  - `firmware.runtime.build_runtime_app()` uses `vivipi.runtime.checks.build_executor()`
  - the built executor delegates to `vivipi.core.execution.execute_check()`
  - `vivipi.runtime.RuntimeApp` uses `vivipi.core.scheduler.due_checks()`, `probe_host_key()`, and `probe_backoff_remaining_s()`
- Confirmed the intended non-reuse boundary for `vivipulse`:
  - no reuse of `RuntimeApp`
  - no display rendering
  - no button handling
  - no Wi-Fi bootstrap
  - no firmware display backends
- Located the local Ultimate firmware checkout at `/home/chris/dev/c64/1541ultimate` after the default sibling path `../1541ultimate` was absent.
- Inspected the Ultimate firmware sources relevant to stabilization hypotheses:
  - build files point to lwIP libraries under `target/libs/*/lwip` and `target/u2/microblaze/mb_lwip`
  - telnet server in `software/network/socket_gui.cc` binds port `23`, listens with backlog `2`, sets a `200 ms` receive timeout, and spawns a task per accepted connection
  - FTP daemon in `software/network/ftpd.cc` binds port `21`, listens with backlog `2`, sets a `100 ms` receive timeout, spawns a task per accepted control connection, and opens passive data ports from a rotating `51000-60999` range
  - HTTP daemon in `software/network/httpd.cc` runs the MicroHTTPServer loop, while `software/httpd/c-version/lib/server.c` tracks a bounded `MAX_HTTP_CLIENT` pool and closes sockets after request completion
  - shared listener/task patterns also appear in `software/io/acia/listener_socket.cc`
- Updated `PLANS.md` with a dedicated `vivipulse` execution plan before starting code changes.
- Commands run during inspection:
  - `rg --files ...`
  - `rg -n "build_runtime_definitions|build_executor|due_checks|probe_backoff_remaining_s|probe_host_key|run_forever\\(" firmware src tests`
  - `find /home/chris -maxdepth 4 -type d \\( -iname '*1541ultimate*' -o -iname '1541u*' -o -iname 'ultimate*' \\)`
  - targeted `sed -n` reads across `firmware/runtime.py`, `src/vivipi/runtime/checks.py`, `src/vivipi/core/execution.py`, `src/vivipi/core/scheduler.py`, `src/vivipi/tooling/build_deploy.py`, and the Ultimate firmware sources above
- Next step:
  - add the host-side orchestration, CLI, wrapper script, artifacts, tests, and README updates end to end

## 2026-04-11T10:12:59Z

- Implemented the host-side `vivipulse` stack:
  - added `src/vivipi/core/vivipulse.py` for reusable host orchestration, deterministic pass/duration runners, failure-boundary tracking, search candidate generation, and soak handling
  - added `src/vivipi/tooling/vivipulse.py` for CLI parsing, config-shape adapters, artifact writing, firmware-research summaries, and interactive recovery prompts
  - added the public wrapper `scripts/vivipulse`
  - added the secondary console entrypoint `vivipi-vivipulse` and shipped `scripts/` in `pyproject.toml`
- Tightened shared parsing reuse instead of duplicating probe-schedule normalization:
  - added `vivipi.core.config.parse_probe_schedule_config()`
  - switched `firmware/runtime.py` to use that helper
- Updated `README.md` with a focused `vivipulse` section covering:
  - purpose
  - canonical and alternate input shapes
  - `plan`, `reproduce`, `search`, and `soak` mode examples
  - artifact layout
  - recovery flow
  - exact shared-layer reuse boundary and intentional non-reuse
- Added behavioral coverage for the new tooling:
  - `tests/unit/core/test_vivipulse.py`
  - `tests/unit/tooling/test_vivipulse_cli.py`
  - `tests/unit/tooling/test_vivipulse_entrypoint.py`
  - extended `tests/unit/core/test_config.py` for the shared probe-schedule parser
- Validations run:
  - `PYTHONPATH=src python3 -m pytest -o addopts='' tests/unit/core/test_vivipulse.py tests/unit/tooling/test_vivipulse_cli.py tests/unit/tooling/test_vivipulse_entrypoint.py tests/unit/core/test_config.py`
  - `scripts/vivipulse --mode plan --runtime-config /tmp/vivipulse-runtime.json --artifacts-dir /tmp/vivipulse-artifacts --json`
  - `./build lint`
  - `./build test`
- Validation results:
  - focused vivipulse/config test slice passed
  - wrapper smoke test passed and wrote `/tmp/vivipulse-artifacts/20260411T100858Z-plan`
  - `./build lint` passed
  - `./build test` passed with `408 passed`
  - coverage gate passed at `96.89%`
- Blockers encountered and resolved:
  - initial pytest collection failed because both new test modules used the same basename; renamed the tooling-side module
  - initial repo lint failed on an unused `ProbeSchedulingPolicy` import in `firmware/runtime.py`; removed it
  - initial coverage gate failed because the new vivipulse modules were under-covered; added direct helper/edge-path tests until the repository-wide `>=96%` branch requirement was satisfied

## 2026-04-11T11:18:34Z

- Reverted the earlier Pico-side probe serialization change after confirming the desired target behavior is still:
  - parallel checks across distinct devices
  - sequential checks against the same device
  - `same_host_backoff_ms = 250`
- Changed `vivipulse` to match that Pico scheduling shape more closely:
  - `HostProbeRunner.run_passes()` now groups checks by host and runs those groups in parallel threads
  - checks within each host group remain sequential and still use `probe_backoff_remaining_s()` for the configured `250 ms` same-host gap
  - `HostProbeRunner.run_duration()` now uses the same host-group parallelism for due checks
- Added coverage proving the host runner now overlaps distinct hosts:
  - `tests/unit/core/test_vivipulse.py` uses a `threading.Barrier` so the test fails under the old globally serial host runner and passes only when two hosts execute in parallel
- Validation:
  - `PYTHONPATH=src python3 -m pytest -o addopts='' tests/unit/core/test_vivipulse.py tests/unit/runtime/test_app.py tests/unit/firmware/test_runtime.py`
  - `./build lint`
- Validation results:
  - focused unit slice passed with `57 passed`
  - repository lint passed
- Live repro, aligned Ultimate-only host run:
  - command:
    - `scripts/vivipulse --mode reproduce --duration 30s --stop-on-failure --check-id c64u-rest --check-id c64u-ftp --check-id c64u-telnet --check-id u64-rest --check-id u64-ftp --check-id u64-telnet --json`
  - artifact:
    - `artifacts/vivipulse/20260411T111550Z-reproduce`
  - result:
    - the trace now shows real host overlap across Ultimate devices, e.g. `c64u-ftp` and `u64-ftp` start within the same millisecond-scale burst while same-host follow-up probes still wait `250 ms`
    - no U64 or C64U transport failures reproduced over the 30-second run
- Live repro, aligned full active-check-set host run:
  - command:
    - `scripts/vivipulse --mode reproduce --duration 60s --stop-on-failure --json`
  - artifact:
    - `artifacts/vivipulse/20260411T111649Z-reproduce`
  - result:
    - reproduced transport failures on `pixel4-adb` only: `6` repeated `refused` failures at `192.168.1.185:8081`

## 2026-04-12T10:54:00Z

- Replaced `PLANS.md` with a task-specific execution plan for the U64/C64U protocol health-check audit and fix pass.
- Began Phase 1 and Phase 2 source inspection for the shared health-check runners used by both firmware and vivipulse.
- Files inspected for current client behavior:
  - `src/vivipi/runtime/checks.py`
  - `src/vivipi/core/execution.py`
  - `config/checks.local.yaml`
- Files inspected for target-side server behavior:
  - `1541ultimate/software/network/ftpd.cc`
  - `1541ultimate/software/network/socket_gui.cc`
  - `1541ultimate/software/httpd/c-version/lib/server.c`
- Audit findings with file and line references:
  - HTTP probe behavior is currently protocol-correct in both host and device paths. `portable_http_runner()` explicitly sets `Connection: close` at `src/vivipi/runtime/checks.py:288`, the device socket path sends `HTTP/1.0` plus `Connection: close` at `src/vivipi/runtime/checks.py:822-825`, reads the full response until EOF at `src/vivipi/runtime/checks.py:842`, and always closes the socket in `finally` via `_close_socket()` at `src/vivipi/runtime/checks.py:521-529` and `src/vivipi/runtime/checks.py:854`.
  - The 1541ultimate HTTP server uses a small fixed client pool and explicitly closes sockets after each request. Evidence: `MAX_HTTP_CLIENT` pool usage and accept gating at `1541ultimate/software/httpd/c-version/lib/server.c:26`, `:74`, `:98-100`, and explicit `shutdown()` plus `close()` at `:204-206`.
  - Telnet probe behavior is currently short-lived and explicitly closes its socket. `portable_telnet_runner()` opens one socket, reads the initial transcript, responds to IAC DO/DONT/WILL/WONT negotiation in `_telnet_strip_negotiation()` at `src/vivipi/runtime/checks.py:661-688`, and always closes the socket in `finally` at `src/vivipi/runtime/checks.py:764-765`.
  - The 1541ultimate telnet server listens with backlog `2`, uses a `200 ms` receive timeout, prompts for a password when configured, and closes plus deletes the stream on disconnect. Evidence: `listen(sockfd, 2)` at `1541ultimate/software/network/socket_gui.cc:204`, `SO_RCVTIMEO` at `:218`, password prompt plus `IAC WILL ECHO` at `:55-56`, `str->close()` at `:123` and `:163`, and task teardown via `vTaskDelete(NULL)` at `:174`.
  - FTP probe behavior is not protocol-correct enough for this task. `portable_ftp_runner()` currently accepts the `220` greeting and then sends `QUIT` immediately at `src/vivipi/runtime/checks.py:644-650`, ignoring provided `username` and `password` entirely even though the local U64/C64U checks configure them in `config/checks.local.yaml:9-24` and `:33-48`.
  - The 1541ultimate FTP server only allows `USER`, `PASS`, and `QUIT` before authentication and returns `530 Not logged in` for commands like `PWD` until login succeeds. Evidence: `ftpd_last_unauthenticated_command_index = 2` at `1541ultimate/software/network/ftpd.cc:759`, unauthenticated command table entries at `:763-765`, authenticated-only `PWD` at `:771-772`, and `530` enforcement at `:818-819`.
  - The 1541ultimate FTP server listens with backlog `2` and applies a `100 ms` receive timeout per accepted control socket. Evidence: `listen(sockfd, 2)` at `1541ultimate/software/network/ftpd.cc:187` and `SO_RCVTIMEO` at `:201`.
  - Current defect ranking:
    - High likelihood: FTP probe does not execute a valid login sequence despite configured credentials, so it is not exercising the authenticated control-channel path that the real server expects.
    - Medium likelihood: because the FTP probe can disconnect before authentication, it provides weaker coverage and may interact differently with the server's task/auth state than a real minimal client.
    - Low likelihood: HTTP and Telnet currently appear cleanup-correct from source inspection; no missing socket close was found in the shared client code.
- Next step: implement the minimal FTP fix, add tests that prove `USER`/`PASS` or anonymous login plus `PWD` plus `QUIT`, and then validate against the real targets with vivipulse.

## 2026-04-12T11:02:46Z

- Replaced the first Bash repro draft with a trimmed U64-only script named `scripts/u64_connection_test.sh`.
- Removed the extra host/port/url timeout knobs and the telnet fallback branch so the script now keeps only the requested essentials:
  - `HOST`
  - `FTP_USER`
  - `FTP_PASS`
  - `INTER_CALL_DELAY_MS`
  - `INTER_ITERATION_DELAY_MS`
- Kept the required protocol behavior intact:
  - HTTP uses `curl` with `HTTP/1.0` and `Connection: close`
  - Telnet uses `nc` for connect-and-disconnect
  - FTP uses `curl --ftp-pasv` for login plus passive root listing

## 2026-04-12T11:03:22Z

- Made `scripts/u64_connection_test.sh` executable and validated its shell syntax with `bash -n`.
- Ran a bounded live smoke test against `HOST=192.168.1.13`.
- Observed repeated successful protocol lines from the trimmed script:
  - HTTP `OK` with `HTTP 200`
  - Telnet `OK` with `connected`
  - FTP `OK` with `directory listed`

## 2026-04-12T11:05:54Z

- Updated `scripts/u64_connection_test.sh` to accept a minimal positional argument set:
  - `HOST`
  - `INTER_CALL_DELAY_MS`
  - `INTER_ITERATION_DELAY_MS`
- Added `-h` and `--help` usage output while keeping the existing environment-variable defaults intact.

## 2026-04-12T11:06:37Z

- Updated `scripts/u64_connection_test.sh` to log an iteration-start line before each loop pass.
- The new line includes:
  - iteration count
  - elapsed runtime in seconds
  - active host name

## 2026-04-11T19:22:00Z

- Extended the shared probe execution seam with machine-parseable transport tracing.
  - Added `src/vivipi/core/probe_trace.py` with:
    - `ProbeTraceCollector`
    - `ProbeTraceJsonlWriter`
    - JSONL loading helpers
    - parity comparison helpers for request ordering, lifecycle pattern, and relative timing deltas
- Updated `src/vivipi/runtime/checks.py` so shared probe runners now emit:
  - `probe-start` / `probe-end`
  - `dns-start` / `dns-result` / `dns-error`
  - `socket-open` / `socket-ready` / `socket-close` / `socket-error`
  - `socket-send` / `socket-recv` / `socket-timeout`
- Updated `src/vivipi/runtime/app.py` and `firmware/runtime.py` so the Pico runtime can forward the same low-level events to a JSONL serial sink when `service.probe_trace_jsonl: true` or `observability.probe_trace_jsonl: true` is present in the runtime config.
- Updated `src/vivipi/tooling/vivipulse.py`:
  - added `--parity-mode`
  - added `--firmware-trace PATH`
  - writes `transport-trace.jsonl`, `parity-mode.txt`, and `parity-summary.txt` artifacts per run
  - includes parity data in JSON output when a firmware trace is supplied
- Added `scripts/vivipulse_stress_test.sh` as the deterministic host-side parity soak entrypoint.
- Added focused tests for the new functionality:
  - `tests/unit/core/test_probe_trace.py`
  - extended `tests/unit/runtime/test_checks.py`
  - extended `tests/unit/tooling/test_vivipulse_cli.py`
- Validation:
  - `pytest -o addopts='' tests/unit/core/test_probe_trace.py tests/unit/runtime/test_checks.py tests/unit/tooling/test_vivipulse_cli.py tests/unit/core/test_vivipulse.py`
  - result: `79 passed`
- Live parity-mode evidence gathered from the current network:
  - `scripts/vivipulse --build-config config/build-deploy.local.yaml --mode local --parity-mode --json`
    - artifact: `artifacts/vivipulse/20260411T191725Z-local`
    - result: `7` requests, `0` transport failures
  - `scripts/vivipulse --build-config config/build-deploy.local.yaml --mode reproduce --duration 30s --parity-mode --json`
    - artifact: `artifacts/vivipulse/20260411T191747Z-reproduce`
    - result: `21` requests, `0` transport failures
  - `scripts/vivipulse --build-config config/build-deploy.local.yaml --mode reproduce --duration 30s --parity-mode --json`
    - artifact: `artifacts/vivipulse/20260411T191843Z-reproduce`
    - result: `21` requests, `0` transport failures
  - `scripts/vivipulse --build-config config/build-deploy.local.yaml --mode reproduce --duration 30s --parity-mode --json`
    - artifact: `artifacts/vivipulse/20260411T191914Z-reproduce`
    - result: `16` requests, `1` transport failure, blocked host `192.168.1.13`
- Failure-boundary evidence from `artifacts/vivipulse/20260411T191914Z-reproduce`:
  - last U64 success before failure: `u64-rest` then `u64-telnet`
  - first transport failure: `u64-ftp`
  - host transport trace shows DNS success and `socket-open` for `192.168.1.13:21`, followed by an `8 s` connect timeout rather than immediate refusal or route failure
- Current blocker state:
  - the host-side failure is intermittent rather than deterministic, so Phase 3 is still open
  - a fresh Pico JSONL transport trace has not yet been captured, so parity proof against the real firmware remains open
  - the new wrapper script was validated end to end with `DURATION=10s bash scripts/vivipulse_stress_test.sh`; it exited `FAIL` with artifact `artifacts/vivipulse/20260411T192506Z-soak` after `3` transport failures, proving the script’s machine-verifiable fail gate is live and that the current environment is still unstable under short soak conditions
    - still did not reproduce any U64 or C64U transport failure in that 60-second window
    - `u64-rest`, `u64-ftp`, `u64-telnet`, `c64u-rest`, `c64u-ftp`, and `c64u-telnet` all remained successful throughout the aligned host run
- Current conclusion:
  - host-side global serialization was one real difference and is now removed from `vivipulse`
  - after removing that difference, the host still does not reproduce the Ultimate failures seen from the Pico in the same time window
  - the remaining gap is therefore more likely in the Pico-side network stack or timing environment than in the shared direct probe definitions alone

## 2026-04-11T23:11:03Z

- Investigated the symlinked `1541ultimate` checkout directly to explain the full REST/FTP/TELNET collapse seen under aggressive same-host bursts.
- Confirmed the relevant target-side constraints from source:
  - `1541ultimate/software/network/socket_gui.cc`
    - telnet listens with backlog `2`
    - each accepted session spawns a FreeRTOS task
    - completed and failed telnet sessions end in `vTaskSuspend(NULL)` instead of deleting the task
    - the telnet listener returns on `accept()` error, so a transient accept failure can stop the service entirely until reboot
  - `1541ultimate/software/network/ftpd.cc`
    - FTP control listens with backlog `2`
    - each control connection spawns a task
    - passive-mode data sockets spawn an additional `FTP Data` task
    - that passive accept task also ends in `vTaskSuspend(NULL)` instead of deleting itself
  - `1541ultimate/software/network/config/lwipopts.h`
    - `MEMP_NUM_NETCONN = 16`
    - `MEMP_NUM_TCP_PCB = 30`
    - `TCP_LISTEN_BACKLOG = 0`
  - `1541ultimate/software/httpd/c-version/lib/server.h`
    - `MAX_HTTP_CLIENT = 4`
- Conclusion from source inspection:
  - the full three-protocol collapse during a burst is best explained by shared TCP/task resource exhaustion rather than three independent application-level parser failures
  - repeated telnet probes leak suspended tasks even when the client disconnects cleanly
  - passive FTP probes leak suspended tasks as well
  - once the shared pool is stressed, REST/FTP/TELNET can all fail together because they share the same lwIP/socket budget
- Action taken in the ViviPi repo:
  - updated `src/vivipi/tooling/vivipulse.py` research output so future `--ultimate-repo` summaries report the actual task-leak and lwIP-limit risks
  - updated `src/vivipi/core/vivipulse.py` search-candidate generation so the safety candidate now explicitly disables same-host concurrency for 1541ultimate-style targets
  - updated `docs/research/network/root-cause.md` to record the burst-collapse explanation and avoidance guidance

## 2026-04-11T15:10:00Z

- Investigated the new `Pico OLED 1.3` button failure report against `docs/spec.md` and the live firmware path.
- Confirmed the current button pin assignment remained correct for this board:
  - `GP15` = User Key 0
  - `GP17` = User Key 1
- Identified three concrete causes for “no display change” on button press:
  - `RuntimeApp._apply_button_events()` had been repurposed to map `Button.A` to debug-mode toggle and `Button.B` to manual refresh instead of the spec-defined next/detail navigation behavior.
  - `render_frame()` did not visually mark the selected check in overview mode, so selection changes were invisible on-screen even when the state changed.
  - `firmware/input.py` only emitted debounced edge presses, so `Button.A` did not provide the required `500 ms` auto-repeat behavior on hold.
- Implemented the button fix:
  - `firmware/input.py`
    - added held-button tracking and repeat-step emission for `Button.A`
    - kept `Button.B` single-fire while pressed
  - `src/vivipi/runtime/app.py`
    - restored spec-driven button routing through `InputController.apply()`
    - preserved compatibility with plain-string button IDs when present
  - `src/vivipi/core/render.py`
    - restored visible overview selection highlighting via `inverted_row` for standard overview

## 2026-04-12T11:29:13Z

- Completed the protocol-check audit/fix pass for the shared runtime FTP probe and the standalone repro tools.
- Shared runtime findings and fix status:
  - the shared FTP probe in `src/vivipi/runtime/checks.py` now performs `USER` / `PASS`, passive-mode directory listing, transfer completion handling, and explicit `QUIT`
  - focused runtime coverage for the corrected lifecycle remains in `tests/unit/runtime/test_checks.py`
- Standalone tool outcome:
  - replaced the earlier mixed implementation with a short pure Bash tool at `scripts/u64_connection_test.sh`
  - added a short pure Python equivalent at `scripts/u64_connection_test.py`
  - kept the interface aligned across both tools: positional `HOST`, `INTER_CALL_DELAY_MS`, `INTER_ITERATION_DELAY_MS`, plus environment-variable defaults and `-h` / `--help`
- Final Bash FTP correction:
  - identified a live mismatch where curl completed the listing with `226 Closing data connection` but the script still required `221 Goodbye`
  - confirmed against the live U64 that curl supports explicit post-transfer teardown via `-Q '-QUIT'`
  - updated the Bash FTP path to send `QUIT` explicitly so the Bash and Python implementations validate the same lifecycle on the real target
- Validation completed:
  - `bash -n scripts/u64_connection_test.sh` passed
  - `python3 -m py_compile scripts/u64_connection_test.py` passed
  - live U64 smoke test for `scripts/u64_connection_test.sh` at `HOST=192.168.1.13` produced repeated `OK` results for HTTP, Telnet, and FTP
  - live U64 smoke test for `scripts/u64_connection_test.py` at `HOST=192.168.1.13` produced repeated `OK` results for HTTP, Telnet, and FTP
  - post-smoke socket inspection with `ss -tan state established '( dst 192.168.1.13 )'` showed no lingering established sockets
- Additional validation context:
  - local mock good/bad services were used during development to exercise success and failure paths for the compact scripts
  - the compact Bash FTP path depends on curl's built-in FTP control flow, which includes `PWD` before `NLST`; the minimal mock FTP server used during development does not implement that full command set, so the authoritative FTP validation was the live U64 itself
- Remaining validation step in progress:
  - started an isolated 10-minute live soak of `scripts/u64_connection_test.sh` against `192.168.1.13` in a dedicated terminal to confirm repeated checks do not render the U64 unresponsive

## 2026-04-12T11:37:03Z

- Updated both standalone U64 connection testers to reduce default pacing and quiet the normal success logs:
  - `scripts/u64_connection_test.sh`
  - `scripts/u64_connection_test.py`
- Behavior change:
  - default `INTER_CALL_DELAY_MS` is now `1`
  - default `INTER_ITERATION_DELAY_MS` is now `1`
  - quiet mode now logs only every `50th` iteration by default while still logging every `FAIL` immediately
  - the success-log sampling interval is configurable through `LOG_EVERY_N_ITERATIONS`
  - both scripts now accept `-v` / `--verbose` to restore per-iteration success logging
- Validation:
  - `bash -n scripts/u64_connection_test.sh` passed
  - `python3 -m py_compile scripts/u64_connection_test.py` passed
  - default quiet-mode failure checks against local bad mocks still emitted all HTTP, Telnet, and FTP failures immediately
  - verbose mode against local good mocks produced per-iteration success output for both scripts
  - quiet mode with `LOG_EVERY_N_ITERATIONS=5` against the live U64 emitted only the `5th` iteration success bundle for both scripts

## 2026-04-12T11:44:35Z

- Extended both standalone U64 connection testers with an explicit ICMP reachability step:
  - `scripts/u64_connection_test.sh`
  - `scripts/u64_connection_test.py`
- Sequence change:
  - the loop order is now `ping`, `http`, `ftp`, `telnet`
- Implementation detail:
  - the Bash script uses `ping -n -c 1 -W 2`
  - the Python script uses `subprocess.run([... "ping", "-n", "-c", "1", "-W", "2", HOST])`
- Validation:
  - `bash -n scripts/u64_connection_test.sh` passed
  - `python3 -m py_compile scripts/u64_connection_test.py` passed
  - live verbose smoke against `192.168.1.13` showed the requested order for both scripts with successful `ping`, `http`, `ftp`, and `telnet` results

## 2026-04-12T11:46:54Z

- Reworked both standalone U64 connection testers to use flag-based Linux-style CLIs instead of positional arguments:
  - `scripts/u64_connection_test.sh`
  - `scripts/u64_connection_test.py`
- New common option shape:
  - `-H` / `--host`
  - `-d` / `--delay-ms`
  - `-n` / `--log-every`
  - `-u` / `--ftp-user`
  - `-P` / `--ftp-pass`
  - `-v` / `--verbose`
  - long options for `--http-path`, `--http-port`, `--ftp-port`, and `--telnet-port`
- Validation:
  - `bash -n scripts/u64_connection_test.sh` passed
  - `python3 -m py_compile scripts/u64_connection_test.py` passed
  - help output for both scripts now advertises only flagged options
  - live flagged runs against `192.168.1.13` succeeded for both scripts using `--host 192.168.1.13 --delay-ms 1 -v`
  - old positional forms are now rejected by both scripts

## 2026-04-12T11:49:43Z

- Added running average latency reporting to both standalone U64 connection testers:
  - `scripts/u64_connection_test.sh`
  - `scripts/u64_connection_test.py`
- Behavior change:
  - each emitted check result line now appends `avg_ms=<value>` for that protocol
  - averages are maintained independently for `ping`, `http`, `ftp`, and `telnet`
  - the average updates across iterations and appears whenever that check result line is emitted under the existing logging rules
- Validation:
  - `bash -n scripts/u64_connection_test.sh` passed
  - `python3 -m py_compile scripts/u64_connection_test.py` passed
  - live verbose runs against `192.168.1.13` showed `avg_ms=...` on the emitted `ping`, `http`, `ftp`, and `telnet` result lines for both scripts

## 2026-04-12T11:51:59Z

- Replaced the earlier running-average latency output in both standalone U64 connection testers with percentile tracking across the full test run:
  - `scripts/u64_connection_test.sh`
  - `scripts/u64_connection_test.py`
- Behavior change:
  - each check keeps a continuous latency sample history for `ping`, `http`, `ftp`, and `telnet`
  - iteration summary lines now report cumulative `median`, `p90`, and `p99` latencies for each protocol
  - verbose check lines now report only the single-check `latency_ms=<value>`
  - failure lines also include `latency_ms=<value>` so error timing stays visible outside verbose mode
  - the iteration summary line is emitted after the four checks so it reflects the current cumulative sample set
- Validation:
  - `bash -n scripts/u64_connection_test.sh` passed
  - `python3 -m py_compile scripts/u64_connection_test.py` passed
  - live verbose runs against `192.168.1.13` showed concise check lines with `latency_ms=...` and iteration lines with cumulative `*_median_ms`, `*_p90_ms`, and `*_p99_ms` fields for both scripts

## 2026-04-12T11:59:12Z

- Investigated and optimized the standalone telnet checks after observing persistent `~2.07 s` telnet latencies in the live iteration summaries.
- Root cause findings from live measurement and target-side source inspection:
  - the earlier clients kept reading until a `2 s` local socket timeout even after the U64 had already produced a usable telnet response
  - direct live probing showed the U64 sends visible telnet data quickly, with the first response chunk arriving in roughly `30-40 ms`
  - the server then emits a short burst with an internal gap of about `245 ms`, after which the earlier clients waited an unnecessary extra `2 s` for more data
  - `1541ultimate/software/network/socket_gui.cc` confirms the telnet service itself uses a `200 ms` receive timeout on accepted sockets, which matches the interactive feel more closely than the earlier client-side `2 s` wait
- Implementation changes:
  - `scripts/u64_connection_test.sh`
    - switched the telnet probe to `nc -n -t` so netcat answers telnet negotiation bytes correctly
    - replaced the old long `nc -w 2` read with a bounded `0.20 s` idle window using `timeout`, which captures the initial responsive burst and then closes promptly
  - `scripts/u64_connection_test.py`
    - added explicit handling for telnet `IAC DO/DONT/WILL/WONT` negotiation bytes
    - replaced the old `2 s` read timeout loop with a `0.20 s` idle cutoff after connection while preserving clean socket shutdown
- Validation:
  - live verbose run for the Bash script against `192.168.1.13` showed telnet success at `latency_ms=218`
  - live verbose run for the Python script against `192.168.1.13` showed telnet success at `latency_ms=280`
  - post-run socket inspection showed no lingering established client-side telnet sockets to `192.168.1.13:23`
    - added selected-cell inversion spans for compact overview
- Updated focused behavioral coverage and traceability:
  - `tests/unit/firmware/test_firmware_input.py`
  - `tests/unit/runtime/test_app.py`
  - `tests/unit/core/test_render.py`
  - `docs/spec-traceability.md`
- Validation results:
  - `./.venv/bin/python -m pytest -o addopts='' tests/unit/firmware/test_firmware_input.py tests/unit/core/test_input.py tests/unit/core/test_render.py tests/unit/runtime/test_app.py` passed with `57 passed`
  - `./.venv/bin/python -m ruff check firmware/input.py src/vivipi/runtime/app.py src/vivipi/core/render.py tests/unit/firmware/test_firmware_input.py tests/unit/core/test_render.py tests/unit/runtime/test_app.py docs/spec-traceability.md` passed
  - `./.venv/bin/python -m pytest -o addopts='' tests/spec/test_traceability.py` passed with `3 passed`
- Deployed the updated bundle to the connected Pico with `./build deploy`.
- Remaining gap:
  - I deployed the fix, but I cannot physically press the two on-board buttons from this shell, so final real-device button proof still requires a human press and visible/serial capture.

## 2026-04-11T15:40:00Z

- Implemented a simpler local host-side health workflow in `vivipulse`:
  - added `scripts/vivipulse --mode local` as a one-command single-pass run of all resolved local checks
  - covered it in `tests/unit/tooling/test_vivipulse_cli.py`
- Hardened the ADB-backed host health path for Kubuntu boot/resume:
  - `src/vivipi/services/adb.py` now attempts a bounded `adb start-server` plus `adb reconnect offline` recovery before returning the final device state when `adb` is down or the target device is offline/missing
  - `scripts/run_adb_service.sh` now supports `serve`, `start`, and `ensure-adb` modes
  - added `scripts/install_adb_service_user_units.sh` to install and enable user-level systemd units for the `:8081` ADB health endpoint and periodic `adb` recovery
- Installed the user units on this host:
  - `~/.config/systemd/user/vivipi-adb-service.service`
  - `~/.config/systemd/user/vivipi-adb-recover.service`
  - `~/.config/systemd/user/vivipi-adb-recover.timer`
- Verified the live host-side ADB endpoint after installation:
  - `curl http://127.0.0.1:8081/adb/9B081FFAZ001WX` returned `status = OK` for the connected Pixel 4
- Verified the simplified local health workflow end to end:
  - `scripts/vivipulse --mode local --json`
  - artifact written to `artifacts/vivipulse/20260411T143936Z-local`
  - result: `7` requests, `0` transport failures, active checks `c64u-rest`, `c64u-ftp`, `c64u-telnet`, `pixel4-adb`, `u64-rest`, `u64-ftp`, `u64-telnet`
- Validation results:
  - `./.venv/bin/python -m pytest -o addopts='' tests/unit/services/test_adb.py tests/unit/services/test_adb_service.py tests/unit/tooling/test_vivipulse_cli.py` passed with `37 passed`
  - `./.venv/bin/python -m ruff check src/vivipi/services/adb.py src/vivipi/tooling/vivipulse.py tests/unit/services/test_adb.py tests/unit/tooling/test_vivipulse_cli.py README.md` passed
  - `bash -n scripts/run_adb_service.sh scripts/install_adb_service_user_units.sh` passed
- Full repository test status after this turn:
  - `./build test` now passes functionally with `418 passed`
  - the repository still fails the global coverage gate at `94.66%` versus the required `96%`; that remaining gap is broader than the specific ADB/button/local-run fixes in this turn

## 2026-04-11T18:31:00Z

- Task: `fix-buttons phase-a ladder + step-1 assert`
- Action:
  - re-read [docs/research/fix-buttons/PLAN.md](docs/research/fix-buttons/PLAN.md), [docs/spec.md](docs/spec.md), [firmware/input.py](firmware/input.py), [src/vivipi/runtime/app.py](src/vivipi/runtime/app.py), and the button/runtime unit tests before editing
  - ran live ladder commands that were possible from this shell:
    - `sg dialout -c 'mpremote connect auto exec "import machine; print(machine.freq())"'`
    - `sg dialout -c 'mpremote connect auto ls /'`
    - `sg dialout -c 'mpremote connect auto exec "from machine import Pin; import time; a=Pin(15, Pin.IN, Pin.PULL_UP); b=Pin(17, Pin.IN, Pin.PULL_UP); print(\"idle\", a.value(), b.value()); [print(a.value(), b.value()) or time.sleep_ms(50) for _ in range(10)]"'`
    - `sg dialout -c 'timeout 3s mpremote connect auto run scripts/monitor_pico_buttons.py'`
- Result:
  - Phase 0 passed with live board reachability and deployed runtime files present
  - Phase 1 showed the expected idle-high pull-up baseline on `GP15` / `GP17`
  - Phase 3 showed `pull=up idle=1` for both buttons at startup
  - actual press pairs, boot self-test confirmation, and OLED observations were blocked because this shell could not physically press the HAT buttons or watch the display
- Next step: apply `5.A + 5.B + 5.C` together because the blocked hardware actuation prevented branch disambiguation and the plan explicitly allows the safe vendor-equivalent simplifications in that case

## 2026-04-11T18:18:00Z

- Task: `fix-buttons 5.a 5.b 5.c implementation`
- Action:
  - touched [firmware/input.py](firmware/input.py), [src/vivipi/runtime/app.py](src/vivipi/runtime/app.py), [tests/unit/firmware/test_firmware_input.py](tests/unit/firmware/test_firmware_input.py), [tests/unit/runtime/test_app.py](tests/unit/runtime/test_app.py), and [docs/spec-traceability.md](docs/spec-traceability.md)
  - code delta before documentation append: `5 files changed, 415 insertions(+), 158 deletions(-)` from `git diff --stat -- firmware/input.py src/vivipi/runtime/app.py tests/unit/firmware/test_firmware_input.py tests/unit/runtime/test_app.py docs/spec-traceability.md`
  - removed `_sample_with_pull`, `_detect_bias`, `_bind_irq`, `_drain_latched_presses`, and the IRQ latch state from `ButtonReader`
  - defaulted string button config to pull-up, kept explicit `pull="down"` support, and preserved the polling/debounce/repeat path with the `Button.B` one-step clamp
  - added a `150 ms` `BTN <button>` runtime overlay so accepted button presses visibly acknowledge even when selection or mode do not change
- Result:
  - firmware and runtime now match the plan’s requested root-cause fixes without changing the `snapshot()` shape
  - firmware input coverage now exercises constructor defaults, explicit pull-down, single press/release, A repeat, B clamp, snapshot state, and rejection of the removed `auto` mode
- Next step: run focused pytest, then repository lint / coverage / firmware build / deploy gates

## 2026-04-11T18:27:00Z

- Task: `fix-buttons validation and deploy`
- Action:
  - ran `./.venv/bin/python -m pytest -o addopts='' tests/unit/firmware/test_firmware_input.py tests/unit/runtime/test_app.py`
  - ran `./build test`
  - ran `./build lint`
  - ran `./build coverage`
  - ran `./build build-firmware`
  - ran `./build deploy`
  - re-ran `sg dialout -c 'timeout 3s mpremote connect auto run scripts/monitor_pico_buttons.py'` after deploy
- Result:
  - focused button slice passed with `35 passed`
  - full repository pytest passed functionally with `429 passed`
  - `./build lint` passed
  - repository coverage measured `94.86%`, which is above the prompt’s `>=91%` target but still below the repo-wide `96%` fail-under, so `./build test` / `./build coverage` exit non-zero on the broader global coverage gate
  - firmware bundle build succeeded and deploy succeeded to the connected Pico
  - post-deploy monitor startup still printed `CONFIG button=A pin=GP15 pull=up idle=1` and the same for `B`
- Next step: append the final execution log, plan extension, and operator-deferred hardware runbook

## 2026-04-11T18:06:40Z

- Task: `fix-buttons plan and spec reconciliation`
- Action:
  - appended the ladder evidence and chosen fix branches to [docs/research/fix-buttons/PLAN.md](docs/research/fix-buttons/PLAN.md)
  - added the plan extension to [PLANS.md](PLANS.md)
  - refreshed [docs/spec-traceability.md](docs/spec-traceability.md) for the new firmware input test names
  - recorded the operator runbook for the remaining real-device proof steps in this worklog and the execution log
- Result:
  - required documentation updates are in place and the remaining uncertainty is explicit: the code, tests, lint, firmware build, and deploy are complete; the only unresolved proof requires a human to press KEY0 / KEY1 and observe the OLED on the real board
- Next step: operator reruns phases 1–4 on hardware and appends the resulting serial / OLED evidence

## 2026-04-12T12:02:48Z

- Updated `scripts/u64_connection_test.py` so `Ctrl-C` is treated as normal termination.
- Implementation change:
  - wrapped the top-level `main()` invocation in a `KeyboardInterrupt` handler that exits with status `0`
- Validation:
  - `python3 -m py_compile scripts/u64_connection_test.py` passed
  - a supervised `SIGINT` test against `python3 scripts/u64_connection_test.py --host 192.168.1.13` exited with status `0` and emitted no traceback

## 2026-04-17T05:41:56Z

- Task: `1541ultimate ftp performance investigation`
- Action:
  - prepended a dedicated FTP investigation section to [PLANS.md](PLANS.md) while preserving older task history below it
  - mapped the concrete FTP sources and build integration in [1541ultimate/software/network/ftpd.cc](1541ultimate/software/network/ftpd.cc), [1541ultimate/software/network/ftpd.h](1541ultimate/software/network/ftpd.h), and [1541ultimate/target/u2plus/nios/ultimate/Makefile](1541ultimate/target/u2plus/nios/ultimate/Makefile)
  - traced the adjacent abstractions used by FTP: [1541ultimate/software/network/vfs.cc](1541ultimate/software/network/vfs.cc), [1541ultimate/software/filemanager/filemanager.cc](1541ultimate/software/filemanager/filemanager.cc), [1541ultimate/software/filesystem/filesystem_fat.cc](1541ultimate/software/filesystem/filesystem_fat.cc), [1541ultimate/software/network/config/lwipopts.h](1541ultimate/software/network/config/lwipopts.h), and in-tree lwIP socket sources
- Result:
  - confirmed the active FTP implementation is local to `software/network/ftpd.*` and uses a very small `1024` byte transfer buffer in `FTPDataConnection`
  - ruled out per-write `f_sync()` in the FAT write path; FTP writes go through `f_write()` and final close, not explicit sync on each chunk
  - confirmed the FTP tasks run below the `tcpip` thread priority, so scheduler priority inversion is not an obvious first-order explanation
- Next step:
  - finish classifying the strongest throughput findings, then write the report under `docs/research/1541ultimate/ftp-performance/`

## 2026-04-17T05:41:55Z

- Task: `1541ultimate ftp performance investigation`
- Action:
  - compared FTP transfer chunking to nearby socket services and checked lwIP socket semantics in [1541ultimate/software/lwip/src/api/sockets.c](1541ultimate/software/lwip/src/api/sockets.c) and [1541ultimate/software/lwip/src/api/api_lib.c](1541ultimate/software/lwip/src/api/api_lib.c)
  - checked the built-in Ethernet path in [1541ultimate/software/io/network/rmii_interface.cc](1541ultimate/software/io/network/rmii_interface.cc) for hardware/link-context clues
- Result:
  - identified a strong upload-specific throttling mechanism: FTP calls `recv(..., 1024, ...)`, while lwIP only advances the TCP receive window after each completed socket read; this makes throughput sensitive to per-iteration file-write latency in roughly the observed `100 KB/s` to `1 MB/s` range
  - identified a strong both-directions hypothesis: FTP uses one socket API call and one file API call per KiB, while other local services use materially larger socket chunks (`8192` byte reads, `2048` byte writes in `socket_dma.cc`)
  - noted that the active hardware path is RMII-based, which strongly suggests the device link is not gigabit-class even on a gigabit LAN; this reduces inflated expectations but does not explain sub-megabyte throughput by itself
- Next step:
  - write the findings document with confirmed findings, weaker hypotheses, ruled-out explanations, and remedy classifications

## 2026-04-17T05:47:18Z

- Task: `1541ultimate ftp performance investigation`
- Action:
  - wrote the final report to [docs/research/1541ultimate/ftp-performance/findings.md](docs/research/1541ultimate/ftp-performance/findings.md)
  - normalized the candidate-improvements matrix to explicit single-value impact/risk categories
  - rechecked required deliverables and updated the FTP investigation section of [PLANS.md](PLANS.md) to completed status
- Result:
  - the report now contains the required sections for objective, scope, architecture, evidence-backed findings, ruled-out explanations, improvement matrix, order of attack, measurement gaps, and conclusion
  - the strongest confirmed code-level issue is the `1024` byte upload receive quantum combined with lwIP's per-`recv()` receive-window advancement
  - the highest-value low-risk next step is an FTP-local batching change rather than shared lwIP retuning
- Next step:
  - deliver the investigation summary and report path to the user

## 2026-04-17T06:28:17Z

- Task: `1541ultimate ftp implementation prompt`
- Action:
  - re-read [docs/research/1541ultimate/ftp-performance/findings.md](docs/research/1541ultimate/ftp-performance/findings.md) to isolate the `Do now` items and the RAM-viability constraints that justify them
  - rechecked the concrete FTP signatures and data-path helpers in [1541ultimate/software/network/ftpd.h](1541ultimate/software/network/ftpd.h) and [1541ultimate/software/network/ftpd.cc](1541ultimate/software/network/ftpd.cc) so the new prompt can name exact files and functions without depending on the report
  - prepended a dedicated prompt-writing section to [PLANS.md](PLANS.md)
- Result:
  - the implementation scope is narrowed to a defensible FTP-local patch set: enlarge and reuse the single FTP session buffer, batch `STOR` / `RETR` / `LIST`, and fix false-success reply handling
  - broad lwIP memory tuning, driver work, and other shared infrastructure changes remain explicitly out of scope for this prompt
- Next step:
  - write the standalone `prompt.md` and then do a final consistency pass against the existing report

## 2026-04-17T06:31:48Z

- Task: `1541ultimate ftp implementation prompt`
- Action:
  - wrote the standalone handoff prompt to [docs/research/1541ultimate/ftp-performance/prompt.md](docs/research/1541ultimate/ftp-performance/prompt.md)
  - checked the prompt against the existing findings to confirm it includes the high-priority FTP-local fixes, RAM justification, explicit non-goals, and validation requirements
  - marked the prompt-writing section of [PLANS.md](PLANS.md) complete
- Result:
  - the new prompt is self-contained for use inside a fresh standalone `1541ultimate` checkout where this research folder does not exist
  - it keeps the implementation scope intentionally narrow: `ftpd.h`, `ftpd.cc`, one `8 KiB` FTP-local buffer, transfer batching, and truthful reply propagation
- Next step:
  - deliver the prompt path to the user
