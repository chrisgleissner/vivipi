# ViviPi Work Log

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
