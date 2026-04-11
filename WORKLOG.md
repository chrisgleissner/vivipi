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
    - still did not reproduce any U64 or C64U transport failure in that 60-second window
    - `u64-rest`, `u64-ftp`, `u64-telnet`, `c64u-rest`, `c64u-ftp`, and `c64u-telnet` all remained successful throughout the aligned host run
- Current conclusion:
  - host-side global serialization was one real difference and is now removed from `vivipulse`
  - after removing that difference, the host still does not reproduce the Ultimate failures seen from the Pico in the same time window
  - the remaining gap is therefore more likely in the Pico-side network stack or timing environment than in the shared direct probe definitions alone
