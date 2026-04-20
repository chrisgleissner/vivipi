# Plans

## TELNET False-Positive Correction Plan

Authoritative execution plan for eliminating TELNET false positives where a bare TCP accept or an immediate remote close is currently reported as healthy even though the session is not usable.

### Problem Statement

- The current TELNET probe treats post-connect timeout/reset as healthy connectivity and can therefore report `OK` for sessions that accept the TCP connection and then terminate before meaningful TELNET interaction.
- Real-world evidence now shows probe/device disagreement: ViviPi reports `OK`, while a manual telnet session to the same target immediately disconnects.
- The current direct-probe result type only expresses `OK` or `FAIL`, so the runner cannot distinguish between a verified TELNET session and a weak or ambiguous connect-only result.

### Constraints

- `docs/spec.md` remains the product source of truth.
- Keep the change scoped to the TELNET probe path and the smallest shared execution/logging seams needed to support explicit TELNET `DEG` results.
- Preserve deterministic, event-driven behavior and avoid introducing a full TELNET client or heavy dependencies.
- Keep existing logging compatible while extending it with `close_reason`, `session_duration_ms`, and `handshake_detected`.
- Completion requires tests, `./build`, and validation against the live `c64u` (`192.168.1.167`) and `u64` (`192.168.1.13`) targets.

### Deterministic Success Model To Implement

- `OK`: the TCP session is established and at least one of these deterministic criteria is met before close or timeout:
	- TELNET negotiation is observed via `IAC DO/DONT/WILL/WONT`, or
	- non-whitespace visible TELNET payload is read, or
	- the socket remains open for at least `500 ms` after connect without an early close.
- `DEG`: the TCP session is established but the probe only reaches the stable-open threshold without negotiation or visible TELNET payload.
- `FAIL`: connection refusal, timeout before session establishment, explicit failure text, or early remote close before `100 ms` and before any negotiation or visible payload.

### Phases

Phase 1: Root-cause confirmation  
Status: COMPLETED

- Capture the current TELNET runner behavior and manual CLI telnet behavior against `c64u` and `u64`.
- Confirm the current code path that promotes post-connect reset/timeout to `OK`.

Phase 2: TELNET runner correction  
Status: COMPLETED

- Add explicit TELNET session outcome metadata and deterministic close classification.
- Replace the old post-connect-success shortcut with session validation based on negotiation, visible payload, and stable-open thresholds.

Phase 3: Shared result/logging seam  
Status: COMPLETED

- Extend direct-probe result handling so TELNET can emit `Status.DEG` without changing other probe semantics.
- Extend probe-end and check-summary logging with `close_reason`, `session_duration_ms`, and `handshake_detected` when present.

Phase 4: Regression coverage  
Status: COMPLETED

- Add deterministic tests for immediate close, sub-`100 ms` early close, stable idle open, and valid TELNET negotiation.
- Update any execution/service tests affected by the explicit TELNET status model.

Phase 5: Docs and traceability  
Status: COMPLETED

- Update `docs/spec.md`, `README.md`, and `docs/spec-traceability.md` to reflect the corrected TELNET semantics.

Phase 6: Validation  
Status: COMPLETED

- Run focused TELNET-related tests, then `./build`.
- Re-run the TELNET probe against `c64u` and `u64`, compare before/after classifications, and confirm that at least one previously incorrect `OK` now becomes non-`OK`.

### Completion Notes

- The TELNET runner no longer treats connect-only or early-close sessions as healthy. It now emits explicit `Status.OK`, `Status.DEG`, or `Status.FAIL` together with `close_reason`, `session_duration_ms`, and `handshake_detected` metadata.
- Deterministic TELNET thresholds are now explicit in code and docs:
	- close/reset before `100 ms` => `FAIL`
	- no meaningful interaction but open for at least `500 ms` => `DEG`
	- meaningful TELNET interaction that survives the non-immediate-close window => `OK`
- Direct-probe execution and the default ADB service now preserve explicit TELNET `DEG` results instead of collapsing everything to boolean success/failure.
- Runtime `CHECK` and `PROBE` logs now include TELNET session-close metadata so failures and degraded sessions are diagnosable without changing the existing log format.
- Validation completed with:
	- focused TELNET/execution/runtime/service slice passing
	- `tests/spec/test_traceability.py` passing
	- final `./build` passing at `661 passed` with `97.26%` total coverage
	- deterministic socket-fixture proof: immediate close => `FAIL`, stable idle open => `DEG`, negotiated stable session => `OK`
	- live target probe re-checks on `c64u` and `u64` still classify `OK`, while raw socket inspection shows both targets deliver TELNET payload and remain open for about `1s`, so they currently validate the non-regression side rather than the false-positive side

### Backlog

- Probe-operation logging follow-up: extend regular logs and mirrored syslog so socket-level probe traces record the concrete protocol operation being attempted, not just generic `socket-send` / `socket-recv` stage names. Example targets include FTP command names such as `USER`, `PASS`, `PWD`, `QUIT`, HTTP request/response phase labels, and TELNET negotiation or read classifications.

## Liveness Visibility Follow-Up Plan

Superseded by the bottom-heartbeat-only redesign and the follow-on runtime observability work below. Historical notes are preserved for auditability.

## Runtime Syslog And Input Verification Plan

Authoritative execution plan for mirroring all retained runtime logs to UDP syslog, making probe/button/navigation logs explicit enough for remote diagnosis, and proving the behavior on the attached Pico.

### Problem Statement

- The Pico runtime retained bounded logs in-memory and printed to serial, but had no remote syslog transport.
- The current logs were too terse for probe-by-probe remote diagnosis and did not clearly separate button presses from navigation state changes.
- The user reported that the bottom heartbeat appeared stuck and that button/menu behavior looked broken on the device, so the runtime needed remote proof rather than screen-only inference.

### Constraints

- `docs/spec.md` remains the source of truth.
- Syslog delivery must not delay normal device behavior.
- Syslog failures must emit only one warning while still retrying later.
- The attached Pico and the local `scripts/u64_syslog_server.py` listener are the required proof path.

### Phases

Phase 1: Root-cause confirmation  
Status: COMPLETED

- Confirmed the heartbeat state advanced internally but was only visibly rendered after a due-check batch.
- Confirmed the physical button GPIO path and debouncing were healthy on GP15 and GP17.
- Confirmed no UDP syslog transport existed in firmware.

Phase 2: Runtime logging and syslog transport  
Status: COMPLETED

- Added `[vivipi]`-prefixed structured logs with wider bounded fields.
- Added non-blocking UDP syslog mirroring with one-time unavailability warnings and continued retries.
- Added explicit per-probe summaries plus separate button-press and navigation logs.

Phase 3: Validation and hardware proof  
Status: IN PROGRESS

- Run focused tests and full `./build`.
- Redeploy to the attached Pico.
- Prove live syslog receipt for boot, probes, heartbeat progress, and button/navigation events.

Authoritative execution plan for fixing the current OLED liveness visibility regression, making healthy-state probe/device liveness materially easier to see on the SH1107 OLED, and documenting the exact probe-freshness verification path in the README.

### Problem Statement

- The current per-row micro variation is only a single cleared pixel inside a full-width freshness bar, which is too small to function as a useful healthy-state probe liveness indicator.
- The current bottom heartbeat defaults to a single pixel on the far-right bottom scanline, which is easy to miss on the physical panel.
- The current contrast breathing amplitude is conservative enough that it may be effectively invisible in normal viewing.
- `README.md` does not yet explain in concise operational terms how probe freshness should be verified on the device.

### Constraints

- `docs/spec.md` remains the source of truth; behavior changes must be reflected in `docs/spec-traceability.md`.
- Preserve the strict `16 x 8` text grid and do not add new layout rows or columns.
- Keep rendering deterministic and low-frequency.
- Keep the firmware/runtime split architecture intact and prefer pure, testable helper logic where practical.
- Finish with tests, `./build`, deploy, and real Pico verification.

### Design Decisions

- Keep the right-edge freshness cell as the probe freshness primitive, but replace the current one-pixel micro hole with a small deterministic notch that is visibly meaningful on the physical OLED.
- Make the bottom heartbeat clearly visible by increasing its footprint and defaulting it to a more visible lane rather than the far-right edge.
- Keep a calm global contrast pulse, but tune the checked-in defaults to be observable rather than barely measurable.
- Document verification in README as an operator-facing procedure, not an implementation note.

### Phases

Phase 1: Root-cause confirmation  
Status: COMPLETED

- Confirm the current micro variation is literally one cleared pixel.
- Confirm the current heartbeat is a one-pixel far-right cluster.
- Confirm the current config defaults keep contrast breathing very subtle.

Phase 2: Visibility redesign  
Status: COMPLETED

- Redesign the healthy-state probe marker to be visible without altering bar width semantics.
- Redesign the bottom heartbeat to be clearly visible on the physical display.
- Retune conservative but visible default liveness settings.

Phase 3: Docs and spec alignment  
Status: COMPLETED

- Update `README.md` with concise probe-freshness verification guidance.
- Update `docs/spec.md` and `docs/spec-traceability.md` if requirement language changes.

Phase 4: Validation and deploy  
Status: COMPLETED

- Update focused tests.
- Run `./build`.
- Deploy to the attached Pico and verify the improved markers on hardware.

### Acceptance Criteria

- Healthy-state probe liveness is visibly discernible on each full-width freshness bar without changing the fixed 16x8 layout.
- Device liveness on the bottom scanline is clearly visible on the physical OLED.
- Contrast breathing remains calm but is actually noticeable in normal viewing.
- `README.md` concisely explains how to verify probe freshness.
- Tests, `./build`, deploy, and Pico verification all complete successfully.

### Completion Notes

- Replaced the previous one-pixel per-row micro hole with a larger two-pixel vertical notch so healthy full-width freshness bars now show a visible probe-liveness cue without changing bar width semantics.
- Changed the bottom heartbeat from a single far-right pixel to a centered three-pixel cluster that moves slowly along the bottom scanline, making device liveness visible on the physical OLED.
- Retuned the checked-in OLED defaults to a more visible but still calm healthy-state cadence: contrast breathing `period_s: 30`, `amplitude: 16`; bottom heartbeat `period_s: 10`, `pixel_count: 3`, `position: center`.
- Updated `README.md` with a concise operator-facing section explaining how to verify probe freshness on the device and what healthy/degraded behavior should look like.
- Updated `docs/spec.md` to reflect the visible notch wording and moving bottom-heartbeat cluster semantics.
- Final validation completed:
	- focused liveness pytest slice passed
	- full `./build` passed at `636 passed` with `97.48%` total coverage
	- deploy completed successfully to the attached Pico
	- live Pico verification confirmed deployed liveness config `contrast_breathing(amplitude=16, period_s=30)`, `bottom_heartbeat(pixel_count=3, position=center, period_s=10)`, and `per_row_micro(enabled=true, stagger=true)`
	- live Pico frame samples confirmed bottom heartbeat positions `63,64,65 -> 64,65,66 -> 65,66,67`, contrast `128 -> 142 -> 114`, and active per-row micro rows `0/7 -> 2/7 -> 7/7`
	- pixel-level framebuffer inspection on the deployed Pico confirmed the full-width freshness bars now contain a larger two-pixel notch rather than a single almost-imperceptible dot

## OLED Liveness Indicators Plan

Authoritative execution plan for implementing three ultra-low-distraction liveness indicators on the 128x64 SH1107 OLED while preserving the existing 16x8 overview layout, deterministic rendering, and calm healthy-state behavior.

### Problem Statement

- The existing freshness bitmap currently fills the entire 8-pixel cell height, so adjacent rows visually run together instead of preserving the intended baseline gap.
- Healthy steady-state renders can become completely static, which removes all subtle confirmation that the Pico runtime and OLED are still alive.
- The OLED path already uses brightness-capable hardware and a deterministic bitmap renderer, but it lacks a structured low-frequency liveness layer that can operate without changing text, layout, or introducing visible animation.

### Constraints

- `docs/spec.md` remains the product source of truth; requirement updates must be reflected in `docs/spec-traceability.md`.
- Preserve the strict `16 x 8` character grid on the default `waveshare-pico-oled-1.3` profile.
- Keep rendering deterministic and avoid per-frame animation loops.
- Keep business logic in `src/vivipi/core` or other CPython-testable paths when practical; keep firmware glue thin.
- Keep SH1107 transport calibration and `column_offset = 32` unchanged.
- Preserve calm visuals: low-frequency, subtle, independently toggleable, and conservative by default.
- Finish with tests, `./build`, deploy, and hardware verification on the attached Pico.

### Design Decisions

- Extend the existing bitmap freshness primitive instead of introducing new layout elements or text glyph mutations.
- Normalize liveness configuration under `device.display.liveness` so the same schema flows from YAML to runtime config to firmware.
- Represent liveness output as deterministic frame decorations: per-frame contrast target, per-row micro-pixel metadata, and bottom-row heartbeat pixels.
- Quantize time-based liveness updates so they only advance on explicit low-frequency boundaries.
- Keep all three indicators independently optional; missing config means disabled.

### Phases

Phase 1: Analysis  
Status: COMPLETED

- Confirm the current freshness bitmap rendering path, display config normalization path, runtime frame decoration hook, and SH1107 contrast support.
- Identify the smallest set of files for plan/log tracking, config parsing, runtime scheduling, renderer updates, docs, tests, and deployment.

Phase 2: Rendering Fixes  
Status: COMPLETED

- Fix the freshness bitmap so only the top 7 logical rows render and the bottom separator row remains off.
- Preserve the current width semantics and sentinel behavior at width `0`.

Phase 3: Feature 1, Contrast  
Status: COMPLETED

- Add deterministic low-frequency contrast breathing around the configured base brightness.
- Apply it only on displays that support contrast control and keep the amplitude/period conservative.

Phase 4: Feature 2, Per-Row Micro  
Status: COMPLETED

- Add one deterministic per-row micro-pixel variation only when freshness is full width (`8`).
- Support optional row staggering without changing perceived indicator width.

Phase 5: Feature 3, Heartbeat  
Status: COMPLETED

- Add a minimal bottom-row heartbeat using 1 to 3 pixels on the unused bottom scanline.
- Keep placement configurable and visually quiet.

Phase 6: Config Integration  
Status: COMPLETED

- Extend display config normalization and runtime config rendering with `device.display.liveness`.
- Update checked-in deploy config defaults for safe, enabled-on-purpose behavior on the OLED path.

Phase 7: Validation  
Status: COMPLETED

- Add focused tests for config validation, frame decoration timing, firmware rendering, and spec traceability.
- Run focused tests, then `./build`.

Phase 8: Deployment  
Status: COMPLETED

- Build the firmware bundle.
- Deploy to the attached Pico.
- Verify the freshness separator, subtle healthy-state liveness, unchanged degraded rendering, and absence of distracting motion on hardware.

### Acceptance Criteria

- The freshness indicator uses only the top 7 logical pixels and preserves an always-off separator row.
- Contrast breathing is subtle, quantized to low-frequency updates, and disabled automatically when contrast control is unavailable.
- Per-row micro variation appears only at full freshness width and does not change perceived bar width.
- The bottom heartbeat uses at most 3 pixels on the bottom row and remains minimally visible.
- All liveness features are independently configurable via `device.display.liveness` and disabled when that section is absent.
- Rendering remains deterministic, event-driven or low-frequency scheduled, and visually calm when healthy.
- Tests, `./build`, deployment, and hardware verification complete successfully.

### Completion Notes

- Fixed the freshness bitmap renderer so every indicator cell now uses only the top 7 logical pixels, leaving the bottom separator row dark across all rows.
- Added conservative `device.display.liveness` normalization for `contrast_breathing`, `per_row_micro`, and `bottom_heartbeat`, and threaded that config through build-deploy, runtime config JSON, and firmware app construction.
- Added pure liveness timing helpers in `src/vivipi/core/liveness.py` and used them from `RuntimeApp._decorate_frame(...)` so all three indicators advance on deterministic low-frequency boundaries rather than a per-frame animation loop.
- Extended the frame model with contrast and bottom-row pixel metadata and updated the SH1107 and SSD1305 backends to honor per-frame contrast targets.
- Final validation completed:
	- focused pytest slice passed at `195 passed`
	- full `./build` passed at `635 passed` with `97.49%` total coverage
	- firmware bundle rebuilt and deploy completed to the attached Pico
- Device verification completed on the deployed Pico:
	- deployed `config.json` read back from the board includes the expected enabled liveness settings
	- direct on-device probe execution stayed healthy with all configured checks at `freshness_width_px: 8`
	- on-device OLED captures written under `artifacts/display-capture/current/liveness/` confirmed the healthy overview layout and liveness states
	- pixel-level verification on the deployed OLED buffer confirmed separator rows `y = 7, 15, 23, 31, 39, 47, 55` were all dark, the bottom heartbeat toggled exactly at `x = 120`, and the per-row micro variation toggled one non-left-edge pixel within full-width freshness bars
	- captured contrast values on the deployed SH1107 changed conservatively across healthy-state samples: `128` at `t=0`, `135` at `t=14`, and `130` at `t=21`

## ViviPi Probe Productionization Plan

Authoritative execution plan for fixing ViviPi direct-probe correctness on the Pico, improving display responsiveness, preserving the internal `OK -> DEG -> FAIL` model while making the visible degraded phase configurable, and validating the result on the attached Pico against the live U64, C64U, and Pixel 4 environment.

### Problem Statement

- The Pico-side `U64 TELNET` probe currently reports `FAIL` on the live device even though the repository's host-side `scripts/u64_connection_test.py` probe succeeds and is the behavioral source of truth.
- Displayed health transitions are slower than desired when targets power off or recover.
- The codebase must preserve its existing internal degraded-state model but allow users to choose whether the display visibly steps through `DEG` or transitions directly between `OK` and `FAIL`.
- The Waveshare Pico OLED 1.3 buttons on `GP15` and `GP17` need live verification and production-suitable observable behavior.

### Assumptions

- `docs/spec.md` is the product source of truth.
- Core behavior should stay in `src/vivipi/core` or testable runtime code unless firmware glue must change.
- The attached Pico is deployable through `./build deploy` and the repo-local config in `config/build-deploy.local.yaml` is the active deployment profile.
- `scripts/u64_connection_test.py` and its protocol runners define the intended U64 probe semantics.

### Constraints

- Keep changes minimal and architecture-consistent.
- Preserve the strict fixed-grid rendering model and selection identity semantics.
- Preserve the internal `DEG` state model even if visible transitions become direct by default.
- Reuse existing config structures such as `check_state`, `probe_schedule`, and `device.buttons` where possible.
- Update `docs/spec-traceability.md` if requirement coverage changes.
- Finish with build, deploy, and real-hardware validation rather than code-only reasoning.

### Research Findings So Far

- Active local deployment config already uses `GP15` and `GP17`, enables `startup_self_test_s: 30`, and sets `check_state` thresholds to `1/1/1`, so slow visible fail/recover transitions are not currently caused by multi-step degradation thresholds alone.
- Active local checks in `config/checks.local.yaml` still run at `interval_s: 10` / `timeout_s: 8`, which is slower than the requested preferred default and leaves little headroom for fast failure/recovery.
- The Pico FTP probe in `src/vivipi/runtime/checks.py` already aligns with the remembered source-of-truth semantics: `USER`, optional `PASS`, `PWD`, `QUIT`, no PASV/NLST smoke path.
- The Pico telnet probe already treats post-connect timeout/reset as healthy connectivity, which also matches the remembered source-of-truth semantics.
- The button path is not absent: `firmware/input.py` polls `GP15`/`GP17`, `src/vivipi/core/input.py` maps button `A` to navigation and button `B` to detail/overview toggling, and `firmware/runtime.py` already has a startup self-test frame plus short press-feedback overlay support.
- Remaining unknowns to prove on hardware: the exact on-device cause of `U64 TELNET` failing, whether the current rendered feedback is sufficiently observable in normal operation, and the measured before/after fail/recover times.

### Phased Task List

Phase 1: Ground-truth research and baseline capture  
Status: COMPLETED

- Inspect runtime, scheduler, display, config-rendering, deploy, and button code paths.
- Compare Pico `FTP` and `TELNET` smoke behavior against `scripts/u64_connection_test.py` and its protocol runners.
- Capture current local config, active probe intervals/timeouts, transition thresholds, and button configuration.
- Reproduce baseline probe behavior locally and on hardware where possible.

Phase 2: Fix U64 telnet probe correctness  
Status: COMPLETED

- Identify the exact behavioral difference between Pico telnet probing and the source-of-truth host runner.
- Implement the smallest robust Pico-side telnet change needed to make the real U64 report healthy.
- Add or adjust targeted tests around the discovered edge case.

Phase 3: Improve responsiveness while preserving the model  
Status: COMPLETED

- Reduce default direct-probe cadence toward a preferred `5s` interval with appropriately smaller timeouts.
- Preserve internal `DEG` modeling while making the visible degraded phase explicitly configurable.
- Reuse existing `check_state` and runtime/rendering paths rather than inventing parallel state machinery.
- Add code coverage proving both visible behaviors.

Phase 4: Review adjacent probes for consistency  
Status: COMPLETED

- Re-check REST/HTTP and FTP direct probes for consistency with the source-of-truth U64 probe structure.
- Make only targeted correctness or consistency fixes that improve probe behavior.

Phase 5: Button completion and observability  
Status: COMPLETED

- Verify live `GP15` / `GP17` behavior on the Pico OLED 1.3.
- Ensure button activity is visibly testable in a production-suitable way during normal operation and startup.
- Update docs/config comments if the intended behavior was previously unclear.

Phase 6: Validation, deployment, and documentation  
Status: COMPLETED

- Run targeted tests, then `./build`.
- Deploy with the supported USB flow.
- Validate on the attached Pico against live U64, C64U, and Pixel 4 targets.
- Measure and record old/new fail and recovery timings, button evidence, and final healthy status.
- Update docs and traceability coverage as required.

### Acceptance Criteria

- Real Pico `U64 TELNET` becomes healthy against the live U64.
- Default direct-probe responsiveness moves to a preferred `5s` cadence unless hard evidence supports a narrower equivalent.
- Failures surface materially faster than the current `10s/8s` configuration and recoveries also surface faster.
- Internal `OK / DEG / FAIL` state logic remains intact.
- Visible degraded behavior is explicitly configurable and integrated through the existing config model.
- FTP and REST/HTTP direct probes are reviewed and aligned where needed.
- `GP15` and `GP17` button behavior is verified live and made observable without debug-only churn.
- Required tests pass, `./build` passes, deployment succeeds, and live probes end in healthy `OK` state for the powered-on environment.

### Validation Checklist

- Read and compare relevant code and docs.
- Run targeted unit tests for runtime checks, state transitions, build/deploy config, and button/runtime behavior.
- Run `./build`.
- Render config and inspect the produced runtime config if config fields change.
- Deploy to the attached Pico via `./build deploy`.
- Observe serial/runtime logs and displayed probe state on-device.
- Verify `U64`, `C64U`, and `Pixel 4` checks reach healthy state.
- Measure time-to-fail and time-to-recover before/after on at least one powered target.
- Verify `GP15` and `GP17` press observability on hardware.

### Current Status Per Task

- Research baseline: COMPLETED
- U64 telnet root cause: COMPLETED
- Responsiveness change set: COMPLETED
- Visible degraded configuration: COMPLETED
- FTP/REST consistency review: COMPLETED
- Button live validation: COMPLETED
- Tests/docs/config updates: COMPLETED
- Build/deploy/hardware validation: COMPLETED

### Completion Notes

- Root cause confirmed on real hardware: the Pico-side telnet runner classified MicroPython `OSError(110, "ETIMEDOUT")` as generic `io` instead of `timeout`, so post-connect idle timeouts were not treated as successful telnet reachability even though the source-of-truth host semantics allow them.
- The fix preserves the existing telnet design and only corrects timeout classification for the MicroPython path.
- Visible degradation is now configurable through `check_state.visible_degraded`; the active config preserves the internal `OK -> DEG -> FAIL` model with `1/2/1` thresholds while rendering direct visible `OK <-> FAIL` transitions by setting `visible_degraded: false`.
- Checked-in direct-probe defaults now run at `interval_s: 5` and `timeout_s: 4`.
- Normal-runtime button feedback is now visible long enough to notice, and live GPIO monitoring on the attached Pico confirmed press/release events for both `GP15` and `GP17`.
- Validation completed: targeted tests passed, `./build` passed at `617 passed` and `98.46%` coverage, deploy completed, and the redeployed Pico reported `OK` for `C64U REST`, `C64U FTP`, `C64U TELNET`, `PIXEL4 ADB`, `U64 REST`, `U64 FTP`, and `U64 TELNET`.
- Follow-up production hardening: deploy now finishes with a full `mpremote ... reset` instead of only `soft-reset`, so the Pico is forced back into autonomous `boot.py` / `main.py` execution after USB tooling interactions rather than being left in a stale interactive state.
- Follow-up live-tuning: direct-probe timing was relaxed from `5s / 4s` to `7s / 5s` to reduce transient false negatives while keeping single-probe worst-case detection under the 15-second budget, and the overview selection highlight is now suppressed on the device runtime path so the first row no longer appears inverted while buttons remain non-functional.

## U64 FTP Benchmark Reproducibility Plan

Authoritative execution plan for hardening `scripts/u64_ftp_test.py` with time-normalized stage sizing, deterministic scoring, and minimal output extensions.

### Metrics Hardening Phases

Phase 1: Pin current behavior and update the active plan  
Status: COMPLETED

- Read the current CLI, stage sizing logic, summary output, and unit tests.
- Preserve any unrelated in-flight edits in `tests/unit/tooling/test_u64_ftp_test.py`.
- The implementation patch is in place and validation is complete.

Phase 2: Implement calibrated stage sizing  
Status: COMPLETED

- Replace the byte-target model with `--target-stage-duration-s`.
- Add deterministic per-size calibration that measures upload and download throughput in a short bounded probe.
- Compute worker-aware file counts with min/max clamping and explicit override bypass.
- Extend stage start logging with the planned sizing fields and sampling mode.

Phase 3: Add deterministic scoring and output extensions  
Status: COMPLETED

- Compute an auditable overall score from stage throughput, stage duration, ops latency, and failure penalties.
- Append `protocol=score` and `protocol=score_breakdown` after the existing summary line.
- Keep the line-oriented text output grep-friendly and stable.

Phase 4: Extend unit coverage and validate  
Status: COMPLETED

- Update `tests/unit/tooling/test_u64_ftp_test.py` for calibration, auto-sizing, override bypass, scoring, and output format coverage.
- Run `./.venv/bin/python -m pytest -o addopts='' tests/unit/tooling/test_u64_ftp_test.py`.
- Run `./build` after the targeted tests pass.

### Metrics Hardening Criteria

Done only when:

- Stage sizing is duration-driven by default and calibration is skipped when `--files-per-stage` is set.
- Stage start logs expose the new sizing fields and sampling mode without removing existing log lines.
- Score and score breakdown lines are emitted after the summary and remain deterministic.
- The targeted unit test file passes, then `./build` passes.

## FTP Implementation Prompt Plan

Current authoritative plan for writing a self-contained implementation prompt that can be used inside a fresh `1541ultimate` checkout with no access to this repository's research folder.

### Prompt Phases

Phase 1: Distill the implementation scope  
Status: COMPLETED

- Re-read the completed FTP findings and isolate only the high-priority, low-regression changes.
- Confirm the exact file paths, functions, reply codes, and RAM constraints that the prompt must state explicitly.
- Exclude broad lwIP, scheduler, and transport changes from the implementation scope.

Phase 2: Update execution tracking  
Status: COMPLETED

- Record this follow-on documentation task in `WORKLOG.md`.
- Keep this plan section authoritative until the prompt is written and checked against the existing report.

Phase 3: Write the standalone implementation prompt  
Status: COMPLETED

- Create `docs/research/1541ultimate/ftp-performance/prompt.md`.
- Make it self-contained so a new session in the standalone `1541ultimate` repo can execute it without referring back to `findings.md`.
- Detail the minimal-invasive implementation steps for the high-priority FTP fixes, including explicit non-goals and verification expectations.

Phase 4: Final consistency pass  
Status: COMPLETED

- Verify that `prompt.md` matches the recommendations already captured in `findings.md`.
- Close out `WORKLOG.md` and mark this plan complete.

### Prompt Completion Criteria

Done only when:

- `prompt.md` exists under `docs/research/1541ultimate/ftp-performance/`.
- The prompt is self-contained and does not depend on the local research folder existing in the target repo.
- The prompt covers all high-priority FTP-local findings and excludes broader high-risk work.
- `PLANS.md` and `WORKLOG.md` both reflect the completed task.

---

## FTP Performance RAM Viability Extension Plan

Current authoritative plan for extending the existing `1541ultimate` FTP performance investigation with RAM-cost and target-viability analysis for `U64`, `U64E-II`, and `U2+`.

### RAM Extension Phases

Phase 1: Confirm memory model and target mapping  
Status: COMPLETED

- Verify which heap implementation the relevant targets actually build with.
- Trace target linker limits and reserved memory windows for the Nios and RISC-V application builds.
- Confirm how the repository names map to the user-relevant hardware families (`U64`, `U64E-II`, `U2+`).

Phase 2: Measure nearby memory consumers  
Status: COMPLETED

- Inspect always-on or adjacent network-path allocations that provide realistic comparison points for FTP-local buffer growth.
- Inspect larger subsystem allocations and reserved windows to distinguish FTP-local changes from broad system-memory changes.
- Extend the survey across the wider firmware so the FTP recommendation is judged against resident, transient, and fixed reserved memory outside the immediate network path.

Phase 3: Rewrite the FTP findings with RAM impact  
Status: COMPLETED

- Integrate heap-model constraints and FPGA-target context into the existing report sections rather than adding a detached appendix.
- Add RAM-impact and target-viability discussion to each relevant finding.
- Rewrite the candidate improvements matrix to include RAM-specific columns.
- Add firmware-wide RAM context so the `8 KiB` FTP recommendation is explicitly compared against larger drive, tape, API, copy, and reserved-memory consumers elsewhere in the tree.

Phase 4: Final consistency pass  
Status: COMPLETED

- Align `findings.md`, `PLANS.md`, and `WORKLOG.md`.
- Confirm the report clearly separates FTP-local viable changes from global high-risk memory tuning.

### RAM Extension Completion Criteria

Done only when:

- The report explains the actual heap/allocation model used by the relevant firmware targets.
- The report compares FTP-local memory changes against nearby real allocations in the same firmware tree.
- The candidate matrix includes RAM-delta and target-viability guidance.
- The final recommendations distinguish justifiable FTP-local buffer growth from risky shared-memory retuning.

---

## FTP Performance Investigation Plan

Current authoritative plan for the requested code-grounded investigation of FTP throughput in the `1541ultimate` firmware tree. Historical plan content is retained below for traceability, but this section is the active execution plan for the current task.

### Investigation Phases

Phase 1: Map the FTP implementation  
Status: COMPLETED

- Identify the concrete FTP daemon sources, entry points, build integration, and adjacent abstractions used by control and data connections.
- Trace the VFS, file manager, filesystem, and lwIP/socket layers that the FTP code exercises.

Phase 2: Trace transfer paths end to end  
Status: COMPLETED

- Follow download flow from `RETR` command parsing through file reads, buffering, socket sends, and connection teardown.
- Follow upload flow from `STOR` command parsing through socket receives, file writes, and close/sync behavior.
- Record every chunk-size decision, queue, timeout, copy boundary, and blocking call in the path.

Phase 3: Classify bottlenecks and correctness issues  
Status: COMPLETED

- Separate confirmed code-level findings from stronger and weaker hypotheses.
- Check nearby shared infrastructure only where FTP clearly depends on it: lwIP config, socket semantics, task priorities, and file I/O behavior.
- Explicitly rule out tempting but unsupported explanations.

Phase 4: Produce decision-quality documentation  
Status: COMPLETED

- Write `docs/research/1541ultimate/ftp-performance/findings.md` with the required structure, exact file/function references, and remedy classifications.
- Rank remedy options by impact, effort, regression risk, and ownership viability.

Phase 5: Final consistency pass  
Status: COMPLETED

- Verify that `PLANS.md`, `WORKLOG.md`, and the report are aligned.
- Confirm the report covers architecture, both transfer directions, ruled-out explanations, candidate matrix, and recommended order of attack.

### Investigation Completion Criteria

Done only when:

- The FTP implementation architecture is mapped with exact source references.
- Upload and download paths have both been traced through file and socket layers.
- All plausible high-impact FTP-local bottlenecks visible in code are documented and classified.
- Shared-infrastructure explanations are only included where the code supports them.
- `docs/research/1541ultimate/ftp-performance/findings.md` exists and satisfies the requested structure.

---

## U64 Connection Test Stabilization Plan

Authoritative plan for eliminating the observed FTP and telnet failures in the shared U64 connection suite while preserving probe semantics, coverage, and correctness.

### Failure Classes

1. FTP lifecycle failures

- `550 Requested action not taken` on upload and download.
- `450 Requested file action not taken` on rename.
- Silent `skip=no_self_file` classifications that avoid exercising the intended readwrite operation.

1. Telnet state desynchronization

- Missing expected menu text such as `Audio Mixer` after prior UI interactions.
- Post-write telnet sessions trusting stale local view state instead of re-entering from verified UI state.

1. Shared state model gaps

- HTTP already verifies writes, but state tracking is confirmed-value only and does not expose tentative intent.
- FTP file lifecycle is process-local instead of shared across runners.
- Telnet session state is local and not invalidated after UI writes.

### Execution Plan

Phase 1: Reproduce and pin root causes  
Status: DONE

- Capture short concurrent runs against the live U64.
- Confirm current FTP/telnet behavior and identify silent misclassifications.
- Confirm out-of-scope stream failures are independent from the FTP/telnet state-model work.

Phase 2: Shared state model  
Status: IN_PROGRESS

- Extend the shared execution state to carry thread-safe object-valued model entries.
- Add confirmed and tentative Audio Mixer state tracking.
- Add shared FTP filesystem tracking for self-files created by the probes.

Phase 3: FTP lifecycle correction  
Status: IN_PROGRESS

- Enforce `create -> verify -> use -> rename/delete` for readwrite FTP operations.
- Remove `skip=no_self_file` fallbacks by provisioning and verifying a deterministic file when the model has no confirmed file.
- Revalidate filesystem state with bounded retries and fail on unresolved mismatches.

Phase 4: Telnet UI synchronization  
Status: IN_PROGRESS

- Replace direct right-arrow volume writes with the verified picker flow.
- Verify final Audio Mixer value via HTTP under the shared lock.
- Invalidate cached telnet view state after writes so the next operation re-enters from a verified UI state.

Phase 5: Validation  
Status: TODO

- Run focused tooling tests for FTP, telnet, and shared-state behavior.
- Run short live loops with the stress profile and explicit `correct` probe modes to validate the fixed code paths without stream noise.
- Run soak-style validation after targeted failures are eliminated.

### Termination Criteria

Done only when:

- FTP lifecycle failures are eliminated without weakening validation.
- Telnet operations no longer rely on stale UI state and verified writes converge through HTTP read-back.

---

## U64 FTP Benchmark Metrics Hardening Plan

Authoritative execution plan for removing subjective scoring from `scripts/u64_ftp_test.py` and replacing it with deterministic, engineering-grade summary metrics.

### Execution Phases

Phase 1: Re-read the current implementation and scoring surface  
Status: COMPLETED

- Confirm every scoring-related constant, helper, output line, and JSON field in `scripts/u64_ftp_test.py`.
- Confirm the existing unit coverage in `tests/unit/tooling/test_u64_ftp_test.py` that must be removed or rewritten.
- Preserve the current time-normalized stage sizing and transfer behavior.

Phase 2: Replace scoring with deterministic throughput and failure metrics  
Status: COMPLETED

- Remove all score and score-breakdown outputs, helpers, constants, and JSON fields.
- Rename visible throughput fields from `KB` to `KiB` and redefine stage/run throughput as total bytes divided by measured time.
- Add deterministic failure counts, failed-stage accounting, compact error aggregation, and latency percentiles where available.
- Rewrite the summary and stage END lines to keep them grep-friendly and compact.

Phase 3: Update tests and validate  
Status: COMPLETED

- Remove scoring-focused assertions and add coverage for KiB units, throughput aggregation, summary ordering, latency percentiles, and deterministic error grouping.
- Run `./.venv/bin/python -m pytest -o addopts='' tests/unit/tooling/test_u64_ftp_test.py`.
- Run `./build` after the targeted tests pass.

### Completion Criteria

Done only when:

- No scoring code, output, or test coverage remains.
- Stage and summary throughput fields use `KiB` naming and exact bytes-over-time semantics.
- Failures and top error classes are surfaced directly in both text and JSON summaries.
- The targeted test file passes, then `./build` passes.
- The shared model carries confirmed and tentative state for the mutated HTTP/FTP resources.
- Probe semantics remain `smoke`, `read`, `readwrite`, and `incomplete`.
- Validation evidence shows zero unexpected FAIL results for the targeted probe set.
