# ViviPi Health Check Display Validation Plan

## Phase 1: Local validation of checks
- Status: complete, with `U64` currently offline.
- Run the mandated local validation command against `config/checks.local.yaml`.
- Confirm all 7 checks return `OK` before advancing.
- If any check fails, identify and fix the cause before proceeding.

## Phase 2: Reproduce stale-state bug
- Status: complete. The OLED continued to show all three `U64` rows as `OK` for more than 130 seconds while independent probes from both the workstation and `mickey:8081` returned failures for `U64`.
- Put `U64` offline.
- Wait at least 2 full check intervals (30 seconds minimum).
- Observe whether the OLED display updates the `U64` checks away from the previous `OK` state.

## Phase 3: Root cause analysis
- Status: complete. Two concrete causes were confirmed:
  1. `_run_check()` updated `registered_results` but could leave the rendered `state.checks` stale when an executor exception occurred.
  2. Tool-driven `soft-reset` left the previous OLED frame visible unless the board re-entered the normal boot path.
- Trace result flow through `src/vivipi/runtime/app.py` and `src/vivipi/core/state.py`.
- Verify overwrite semantics for `OK -> FAIL` and `FAIL -> OK`.
- Confirm whether timestamps, persistence, or merge logic allow stale state retention.

## Phase 4: Fix implementation
- Status: complete. Source fixes and regression tests are in place for stale executor failures and periodic network recovery.
- Apply the smallest deterministic fix needed so the latest check result always replaces prior state.
- Add unit tests covering `OK -> FAIL`, `FAIL -> OK`, and no stale retention.
- Update traceability documentation only if requirements or tests move.

## Phase 5: Firmware deploy + validation
- Status: in progress. The patched firmware is deployed and verified on-device:
  - with `U64` offline, `C64U` and `PIXEL4 ADB` recover to `OK`
  - `U64` no longer remains stuck at `OK`
  - the corrected non-`OK` state remains visible across multiple later captures
  - the current deployment now also enforces a 6-second boot logo hold and same-host probe pacing defaults (`allow_concurrent_same_host: false`, `same_host_backoff_ms: 250`)
- Deploy firmware with `./build deploy`.
- Soft-reset once with `sg dialout -c "mpremote connect /dev/ttyACM0 soft-reset"`.
- Wait for Wi-Fi bootstrap and at least one full check cycle without using `mpremote exec`.

## Phase 6: Proof capture
- Status: blocked on physical device state. `U64` must be turned on before the final all-`OK` proof can be captured.
- Capture `/tmp/proof.png` from the Pixel 4 via ADB.
- Verify the image shows all 7 rows with correct labels and `OK` statuses.
- Confirm stable behavior across at least 2 check cycles before concluding.
