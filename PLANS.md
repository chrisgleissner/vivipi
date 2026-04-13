# U64 Firmware Instability Reproducer Plan

Authoritative execution plan for creating a fast, repeatable reproducer for the known 1541ultimate network-firmware lockup using the existing ViviPi runner.

## Objective

Drive the target into a persistent failed state in less than 15 seconds such that ping, HTTP, FTP, and Telnet all fail consistently during the run and continue failing after the stress workload stops.

## Hard Rules

- Use code inspection of `1541ultimate` to justify every stress change.
- Keep changes minimal and localized to `scripts/u64_connection_test.py`, its tests, and plan/log updates needed for this task.
- Do not revert unrelated local changes.
- Do not claim success without explicit post-run checks for ping, HTTP, FTP, and Telnet.
- Keep soak-mode FTP behavior protocol-correct while making stress mode intentionally harsher.
- Update this file after each substantial finding or implementation step.

## Phase 1: Firmware Research And Weak-Spot Identification

Status: IN_PROGRESS

Tasks:
- Inspect the linked `1541ultimate` FTP server implementation, especially PASV, LIST/NLST, data socket teardown, `/Temp` file handling, and per-session cleanup.
- Inspect the linked `1541ultimate` Telnet implementation, especially menu/UI coupling, parser state, disconnect handling, and partial-input behavior.
- Inspect the linked `1541ultimate` HTTP/REST implementation, especially memory read/write, config writes, Audio Mixer state, and shared/global objects.
- Identify cross-protocol shared state, worker/resource pools, and cleanup asymmetries that could lead to persistent failure.
- Rank the most promising weak spots and map each one to concrete runner capabilities.

Evidence required before exit:
- Concrete source locations in `1541ultimate` for FTP, Telnet, HTTP, and shared-state weak spots.
- Failure-mode classification for each weak spot.
- A prioritized list of stress directions justified by source behavior.

## Phase 2: Strengthen The Stress Profile

Status: TODO

Tasks:
- Inspect current `scripts/u64_connection_test.py` soak and stress behavior, including incomplete FTP/Telnet handling and operation scheduling.
- Fix any soak-mode FTP correctness issue around PASV, NLST/LIST, data-connection completion, and final reply consumption.
- Strengthen the `stress` profile with minimal invasive changes informed by Phase 1.
- Add explicit post-run verification logic for ping, HTTP, FTP, and Telnet.
- Add or update focused tests for config resolution, protocol correctness, and post-run verification behavior.

Evidence required before exit:
- Code changes are directly traceable to identified weak spots.
- Unit coverage passes for the changed runner behavior.
- Stress mode now expresses concurrency, hot-path overlap, incomplete interaction pressure, and post-run verification explicitly.

## Phase 3: Execute And Iterate

Status: TODO

Tasks:
- Run focused unit tests for the changed runner.
- Execute the strengthened stress profile against the real target.
- Measure time to consistent failure across ping, HTTP, FTP, and Telnet.
- Stop the workload and run explicit post-run checks.
- If criteria are not met, tighten the most promising pressure points and repeat.
- Record concrete evidence if the exact target cannot be met with the current runner plus justified minimal extensions.

Evidence required before exit:
- Exact commands used.
- Logged time-to-failure evidence.
- Logged post-run verification evidence.
- Either full success against all criteria or a concrete blocker backed by code and execution evidence.

## Current Findings

- Existing local runner changes already add surface-aware HTTP/FTP/Telnet operations and session reuse; build on them rather than replacing them.
- Current `stress` profile is still mild: sequential scheduling, one runner, HTTP/FTP/Telnet all at `READ`, and only FTP/Telnet marked incomplete.
- The runner does not yet have task-specific post-run persistence verification.
- Firmware weak-spot research is in progress; the prioritized list will be filled in after code-level source inspection is complete.

## Iteration Log

- [2026-04-13] Replaced stale plan content with this task-specific reproducer plan.
- [2026-04-13] Confirmed existing runner profile and execution-loop structure in `scripts/u64_connection_test.py`.
- [2026-04-13] Started targeted `1541ultimate` network-surface inspection for FTP, Telnet, HTTP, and shared-state weak spots.
- [2026-04-13] Fixed the extended-runner correctness bug so FTP/Telnet `INCOMPLETE` modes now execute incomplete operations in surface-aware stress runs.
- [2026-04-13] Corrected soak-mode FTP `READ` behavior so it no longer mutates `/Temp`, then added stronger FTP/Telnet incomplete operations plus HTTP memory/config write pressure for stress mode.
- [2026-04-13] Updated the default `stress` profile to concurrent multi-runner `READWRITE` pressure with duplicated FTP/Telnet probes, and added focused tooling tests to pin the new behavior.
- [2026-04-13] Focused validation passed: `pytest -q -o addopts='' tests/unit/tooling/test_u64_connection_test.py` => `43 passed`; Python compile checks passed for the touched runner files.
- [2026-04-13] Real-device control run confirmed baseline health for ping/HTTP/FTP/Telnet before stressing `u64`.
- [2026-04-13] Mixed stress iteration 1 reproduced fast HTTP/FTP/Telnet collapse with repeated connection resets, but ping stayed healthy and all four endpoints recovered immediately after the run stopped.
- [2026-04-13] Mixed stress iteration 2 with tighter FTP/Telnet weighting reproduced the same fast HTTP/FTP/Telnet collapse, again without any ping failures.
- [2026-04-13] Focused FTP-only iteration with 24 concurrent incomplete runners forced near-immediate HTTP and FTP failure, but Telnet and ping stayed healthy throughout and all endpoints recovered immediately after the run stopped.

## Evidence-backed conclusion

- The current runner now deterministically reproduces fast application-service collapse in the U64 firmware: mixed pressure knocks out HTTP, FTP, and Telnet; focused FTP teardown pressure knocks out HTTP and FTP.
- The failure does not extend to ICMP reachability, and it does not persist after the stress workload stops.
- This matches the audited weak spots in the firmware: FTP passive/data-connection lifecycle, Telnet UI session churn, and HTTP route handling are application-level failure points. They do not, by themselves, demonstrate a lower-level network-stack wedge.
- Reaching the original objective now requires a new lower-level pressure path beyond the current FTP/Telnet/HTTP probe families.
- Stuck-logo root cause:
  - startup error formatting crashed on-device in `vivipi/runtime/state.py`, raising `OSError: stream operation not supported` before runtime handoff could complete.
  - after fixing that, two MicroPython compatibility faults remained:
    - button logging assumed enum members exposed `.value`
    - scheduler host normalization assumed `str.casefold()` existed
- Button lead for the actual board is now deployed as `GP15` / `GP17`, but physical proof is still required.
- Button runtime root cause on the current branch:
  - `RuntimeApp` was routing `Button.A` / `Button.B` to debug-toggle and manual refresh instead of the spec-defined next/detail navigation model.
  - Overview rendering did not invert the selected row or selected compact cell, so even a valid selection change had no visible display effect.
  - `firmware/input.py` emitted only debounced edge presses, so `Button.A` auto-repeat was not available on-device.
- The button fix is now deployed in the latest local bundle built and copied with `./build deploy` from version `0.3.1.dev3+g4406adb8c.d20260411`, but physical press proof is still pending.
- The host-side ADB service/local-health fix is now in place:
  - `scripts/install_adb_service_user_units.sh` installed `vivipi-adb-service.service` and `vivipi-adb-recover.timer` under `~/.config/systemd/user/` on this Kubuntu host.
  - `curl http://127.0.0.1:8081/adb/9B081FFAZ001WX` now returns `status = OK` for the connected Pixel 4.
  - `scripts/vivipulse --mode local --json` completed a clean single-pass local run with `7` requests and `0` transport failures against the current checked-in local config.
- Host/Pico probe-alignment finding:
  - `vivipulse` now overlaps distinct hosts while keeping same-host probes sequential with `same_host_backoff_ms = 250`, matching the intended Pico scheduling model more closely.
  - With that alignment in place, host-side live repro still does not trigger U64/C64U failures in the same short window that the Pico does.
  - The remaining differential is therefore likely Pico-side transport/runtime behavior rather than the shared probe definitions by themselves.

## ViviPulse Host Stability Plan

Objective:
- Build `scripts/vivipulse` as the public host-side entrypoint.
- Reuse the existing shared probe-execution seam already exercised by the Pico runtime.
- Produce deterministic host-side traces, failure-boundary evidence, firmware-backed mitigation hypotheses, stabilization search results, and soak summaries under `artifacts/vivipulse/`.

Phases:
- Phase A: prove the exact reuse seam from `firmware/main.py` through `firmware/runtime.py`, then document the intentionally excluded Pico-only layers.
- Phase B: inspect the local Ultimate firmware checkout and extract evidence about lwIP, HTTP, FTP, telnet, and connection/task limits that should shape the first mitigation candidates.
- Phase C: add a CPython-testable host orchestration layer in `src/vivipi/core` that reuses `build_runtime_definitions()`, `build_executor()`, `execute_check()`, `due_checks()`, `probe_host_key()`, and `probe_backoff_remaining_s()`.
- Phase D: add the thin CLI and artifact adapters in `src/vivipi/tooling`, plus the `scripts/vivipulse` wrapper.
- Phase E: validate `plan`, `reproduce`, `search`, and `soak` modes with request-level JSONL traces, failure-boundary reporting, and interactive recovery behavior.
- Phase F: update `README.md` and keep `WORKLOG.md` current with commands, validations, and experiment outcomes.

Checkpoints:
- The host tool executes the shared runtime definition builder unchanged for supported input shapes.
- The host tool executes the shared executor unchanged and captures its raw observation details without introducing retries.
- Same-host serialization and backoff match `ProbeSchedulingPolicy` defaults unless explicitly overridden.
- Search mode evaluates mitigation candidates in the documented order and records why a candidate was chosen or rejected.
- Soak mode can run for a wall-clock duration while preserving deterministic, reconstructable traces.

Risks:
- Runtime-config parsing for `probe_schedule` currently lives in firmware glue, so any host-side reuse gap must stay minimal and explicit.
- The Ultimate checkout is not at the default sibling path here; the implementation must support `--ultimate-repo` and fail fast when research-backed modes need it.
- Coverage is already tight, so the vivipulse code needs high-signal unit coverage from the start rather than a cleanup pass later.

Validation steps:
- Add focused unit tests for orchestration, failure classification, failure-boundary detection, interactive recovery, search candidate ordering, and soak scheduling.
- Add tooling tests for CLI input-shape resolution, wrapper forwarding, JSON output, and artifact generation.
- Run targeted pytest slices during implementation, then `./build test` and `./build lint` before handoff.

Intervention points:
- If a target becomes transport-unresponsive after a last known success, stop further same-host traffic, flush artifacts, and gate resume behind explicit confirmation when recovery mode is enabled.
- If the Ultimate checkout is unavailable, allow `plan` mode and pure input validation to proceed, but fail fast for firmware-research-dependent search flows.

Termination criteria:
- `scripts/vivipulse` exists and dispatches to the repository module.
- `plan`, `reproduce`, `search`, and `soak` all exist and are covered by tests.
- The exact reuse map, firmware research summary, search summary, and soak summary are emitted into `artifacts/vivipulse/`.
- Remaining uncertainty is documented explicitly rather than implied away.

Status:
- Completed in repository code and tests.
- Remaining real-world uncertainty is operational rather than architectural: actual long-duration target behavior still depends on running `reproduce`, `search`, and `soak` against the intended hardware and firmware checkout.

## Plan Extension — 2026-04-11T19:22:00Z

Objective:
- Close the unfinished vivipulse/Pico parity and failure-analysis loop with machine-verifiable transport traces and automated stress entrypoints.

Phases:
- Phase 1: parity instrumentation
  - [x] Add a shared transport-trace schema that records per-probe start/end, DNS resolution, socket open/ready/close, bytes sent/received, and timeouts.
  - [x] Wire that schema into the shared probe runners used by both vivipulse and the Pico runtime.
  - [ ] Capture a fresh Pico JSONL transport trace from the current hardware using `service.probe_trace_jsonl: true` or equivalent runtime config.
- Phase 2: forced parity mode
  - [x] Add `scripts/vivipulse --parity-mode` so host-side runs discard host-only search knobs and use the Pico runtime schedule as the ground truth profile.
  - [x] Emit host-side `transport-trace.jsonl` plus parity summary artifacts for every vivipulse run.
  - [ ] Compare a fresh Pico trace against a parity-mode host trace and prove ordering/lifecycle/timing deltas are within the stated tolerance.
- Phase 3: deterministic reproduction
  - [ ] Reproduce the U64/C64U failure on vivipulse parity mode for 3 consecutive runs with the same trigger sequence.
  - [ ] Reproduce the same triggering sequence on the Pico.
- Phase 4: root cause
  - [ ] Identify a single root cause with >= 90% confidence using transport traces and controlled protocol/timing isolation.
- Phase 5: fix and validation
  - [ ] Implement the smallest shared fix that preserves HTTP, FTP, TELNET, and PING coverage.
  - [ ] Run a 30-minute parity-mode soak with zero failures on host.
  - [ ] Run a 30-minute real-Pico soak with zero failures and captured JSONL transport traces.
- Phase 6: regression guard
  - [x] Add `scripts/vivipulse_stress_test.sh` as the deterministic host-side stress entrypoint.
  - [ ] Extend the script or a companion runner to capture Pico serial JSONL traces automatically.

Current evidence snapshot:
- Host parity-mode implementation is now instrumented and test-covered.
- Live host parity-mode runs against `config/build-deploy.local.yaml` on 2026-04-11 produced:
  - `20260411T191725Z-local`: 7/7 successes, 0 transport failures.
  - `20260411T191747Z-reproduce`: 21/21 successes, 0 transport failures.
  - `20260411T191843Z-reproduce`: 21/21 successes, 0 transport failures.
  - `20260411T191914Z-reproduce`: one U64 FTP timeout after prior U64 success on REST and TELNET; host blocked `192.168.1.13` after the first transport failure boundary.
- Deterministic crash reproduction remains open because the current live environment is intermittently healthy rather than consistently failing, and no fresh Pico JSONL transport trace has been captured yet.

## Plan Extension — 2026-04-11T18:06:40Z

- Re-ran the Pico-OLED-1.3 button-recovery ladder against the connected board with live serial access but without physical button actuation or OLED observation from this shell.
- Applied fix branches `5.A`, `5.B`, and `5.C`:
  - [firmware/input.py](firmware/input.py) now defaults string button config to `pull="up"`, accepts only `up` / `down`, fixes idle state from the configured bias, and removes the IRQ / latched-press path in favor of deterministic polling, debounce, and repeat.
  - [src/vivipi/runtime/app.py](src/vivipi/runtime/app.py) now sets a `150 ms` `BTN <button>` overlay so accepted presses always produce a visible frame change, even when the semantic transition is a no-op.
- Updated button-specific coverage and traceability in [tests/unit/firmware/test_firmware_input.py](tests/unit/firmware/test_firmware_input.py), [tests/unit/runtime/test_app.py](tests/unit/runtime/test_app.py), and [docs/spec-traceability.md](docs/spec-traceability.md).
- Final acceptance state:
  - Phase 0/1/3 startup baselines are proven from this shell.
  - Real PRESS / RELEASE proof, `[BOOT][BTNTEST] button-detected`, and OLED interaction proof remain deferred to an operator because the board buttons and OLED could not be physically exercised from this shell.
  - Focused button tests passed (`35 passed`), full repository pytest passed functionally (`429 passed`), lint passed, firmware build succeeded, deploy succeeded, and repository coverage measured `94.86%`.
  - The repository’s stricter global `96%` fail-under remains unmet at `94.86%`; that gap predates this button-specific fix path and is broader than the touched button work.
