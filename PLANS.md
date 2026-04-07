# ViviPi Strict Convergence Plan

Updated: 2026-04-07T00:35:00+01:00
Overall Status: DONE
Authoritative Inputs Read: README.md, source tree runtime and firmware paths, docs/spec.md, docs/spec-traceability.md, config/*.yaml, build and service entrypoints.

## Mission

Drive the current ViviPi firmware runtime to an evidence-backed production-ready state for constrained Pico deployments by fixing all real in-scope reliability, fail-safe, observability, configuration, and tiny-display UX gaps discovered in this pass.

## Constraints

- `docs/spec.md` remains the product source of truth.
- Business logic stays in `src/vivipi/core` when practical; firmware remains thin.
- Rendering stays deterministic, event-driven, and selection remains identity-based.
- The default OLED `16 x 8` contract remains intact.
- The `>= 96%` branch coverage gate must remain enforced.

## Severity Model

- `CRITICAL`: crashes or silent loss of trustworthy device behavior on supported paths.
- `HIGH`: major operational risk, missing defensive behavior, or missing production visibility.
- `MEDIUM`: bounded but real operator, validation, or resilience gap.
- `LOW`: non-blocking polish or maintenance improvement.

## Tasks

### P01
- Status: DONE
- Description: Refresh the authoritative plan and timestamped worklog for this strict convergence pass.
- Acceptance Criteria:
  - `PLANS.md` uses explicit task IDs, statuses, descriptions, and acceptance criteria.
  - `WORKLOG.md` contains a new timestamped section for this pass.
  - Subsequent fixes reference this plan and log.

### A01
- Status: DONE
- Description: Complete the runtime and firmware audit for concrete production gaps across boot, config loading, display behavior, networking, observability, REPL surfaces, and tiny-display failure visibility.
- Acceptance Criteria:
  - Every real issue is classified by severity with direct file-level evidence.
  - Mandatory requested capabilities are marked present, incomplete, or missing.
  - No speculative issues are carried forward.

### F01
- Status: DONE
- Description: Eliminate boot-time hard failures by adding safe config loading, bounded fallback configuration, and guarded runtime construction.
- Acceptance Criteria:
  - Missing or malformed `config.json` no longer bricks startup.
  - Invalid check/runtime config degrades to a diagnosable boot state instead of aborting.
  - Retained errors are available through the REPL surfaces after boot recovery.

### F02
- Status: DONE
- Description: Add display fail-safe behavior for initialization, boot logo, and frame rendering failures.
- Acceptance Criteria:
  - Display initialization failures are contained and trigger a fallback path.
  - Runtime render failures no longer unwind the main loop.
  - Repeated display failures back off deterministically and preserve diagnostics or REPL access.

### F03
- Status: DONE
- Description: Harden the network execution layer with bounded retries, backoff, and stable failure classification for transient transport failures.
- Acceptance Criteria:
  - HTTP, FTP, and TELNET transport failures use bounded retry with deterministic backoff.
  - Failure details are classified clearly enough for on-device and REPL diagnosis.
  - The SERVICE payload parser enforces a safe upper bound for returned checks.

### T01
- Status: DONE
- Description: Add focused regression tests for every material fix in this pass.
- Acceptance Criteria:
  - New tests cover boot/config failures, display failure containment, network retry behavior, invalid service payload bounds, and tiny-display diagnostics behavior where applicable.
  - New or modified code remains at or above 90% coverage for the touched areas.

### D01
- Status: DONE
- Description: Publish the new production audit and align spec and traceability with the final runtime behavior.
- Acceptance Criteria:
  - `docs/research/vivipi/production-audit.md` exists and reflects this pass.
  - `docs/spec.md` and `docs/spec-traceability.md` match the shipped behavior when requirement coverage changes.
  - `README.md` is updated if operator-facing behavior materially changes.

### V01
- Status: DONE
- Description: Run final validation and close the plan only after proof is recorded.
- Acceptance Criteria:
  - Focused tests for the touched modules pass.
  - Repository validation passes via `./build coverage` and any required build/runtime commands for touched paths.
  - All tasks above are set to `DONE` with evidence captured in `WORKLOG.md`.

## Current Findings Queue

- `PA-2026-04-07-01` CRITICAL: fixed.
- `PA-2026-04-07-02` CRITICAL: fixed.
- `PA-2026-04-07-03` HIGH: fixed.
- `PA-2026-04-07-04` MEDIUM: fixed.

## Termination Criteria

- All tasks above are `DONE`.
- All CRITICAL and HIGH findings from this pass are resolved.
- Tests and coverage validation pass after the fixes.
- The runtime exposes clear retained diagnostics, REPL debuggability, and fail-safe behavior under the exercised failure paths.
- `docs/research/vivipi/production-audit.md`, `PLANS.md`, and `WORKLOG.md` reflect the final repository truth.

## Closeout

- Final focused regression proof: `56 passed` via the strict convergence suites.
- Final repository proof: `291 passed`, `96.31%` total coverage via `./build coverage`.
- Final CI-style proof: local `./build ci --config config/build-deploy.yaml` passed with explicit runtime env vars.
- Final firmware bundle proof: `./build build-firmware --config config/build-deploy.yaml` completed and `artifacts/release/vivipi-device-fs/` remains present for deployment packaging.
- External limit retained: no live Pico serial device was available for hardware deploy execution in this environment.
