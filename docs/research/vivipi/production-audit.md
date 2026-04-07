# ViviPi Firmware Production Audit

Date: 2026-04-07
Scope: runtime boot path, firmware display path, direct probe networking, on-device diagnostics, REPL debuggability, and tiny-display production behavior.

## Summary

This pass started from the current implementation, not from a blank slate. Structured logging, REPL inspection and control surfaces, deterministic rendering, bounded retained errors, and diagnostics mode already existed. The remaining real gaps were concentrated in three places: boot recovery, display fail-safe behavior, and transient transport handling.

The system is now production-ready within the limits of this environment: it boots through missing or malformed config, degrades safely when display paths fail, classifies transient network failures with bounded retry and backoff, and rejects oversized SERVICE payloads.

## Audit Method

1. Read the repository contract in the required order: README, source tree, docs, configs, and hardware-facing runtime and display paths.
2. Verified concrete behavior from `firmware/runtime.py`, `src/vivipi/runtime/app.py`, `src/vivipi/runtime/checks.py`, `src/vivipi/services/schema.py`, and the current regression suites.
3. Classified only evidence-backed issues and fixed them immediately with focused regression coverage.
4. Revalidated with focused tests first, then repository-wide validation.

## Findings Fixed

### PA-2026-04-07-01
- Severity: CRITICAL
- Title: Raw config loading could abort boot before diagnostics existed.
- Evidence: `run_forever()` loaded `config.json` directly, and malformed or missing files raised before a runtime app or diagnostics surface existed.
- Root cause: no guarded config load or bounded fallback runtime shape.
- Fix:
- Added `load_config_with_fallback()` and `build_runtime_app_from_path()` in `firmware/runtime.py`.
- Missing or malformed config now boots with a bounded fallback config, retains boot errors, and activates diagnostics when possible.
- Tests:
- `tests/unit/firmware/test_runtime.py::test_build_runtime_app_from_path_uses_fallback_config_when_config_file_is_missing`
- `tests/unit/firmware/test_runtime.py::test_build_runtime_app_recovers_from_invalid_definitions_and_records_boot_error`
- Before: missing or malformed config could brick startup.
- After: startup remains available for REPL inspection and diagnostics.

### PA-2026-04-07-02
- Severity: CRITICAL
- Title: Display initialization and draw failures were not fail-safe.
- Evidence:
- `build_runtime_app()` created the configured display backend without recovery.
- `RuntimeApp.tick()` drew frames without catching display or rendering exceptions.
- Root cause: no fallback display path and no draw retry containment.
- Fix:
- Added display bootstrap fallback to the default SH1107 OLED path when a configured backend fails.
- Added headless display fallback when no backend can be started.
- Added retained draw-failure errors, diagnostics activation, and bounded display retry backoff in `RuntimeApp`.
- Fixed fallback geometry to recompute page size and row width from the final display config.
- Tests:
- `tests/unit/firmware/test_runtime.py::test_build_runtime_app_falls_back_to_default_display_when_primary_display_init_fails`
- `tests/unit/runtime/test_app.py::test_runtime_app_backs_off_after_display_failure_and_recovers_on_retry`
- Before: display failures could abort boot or unwind the main loop.
- After: display failures are retained, diagnosable, and retried safely.

### PA-2026-04-07-03
- Severity: HIGH
- Title: Direct transport failures lacked bounded retry, backoff, and stable failure classification.
- Evidence:
- HTTP, FTP, and TELNET transport paths previously returned a single failure without bounded retry.
- Failure detail strings were inconsistent and weak for on-device diagnosis.
- Root cause: no shared retry or classification layer in `src/vivipi/runtime/checks.py`.
- Fix:
- Added bounded retry helpers with deterministic exponential backoff.
- Added failure classification for `timeout`, `dns`, `refused`, `network`, `reset`, and `io`.
- Applied the retry logic to HTTP transport failures, socket establishment, TELNET session startup, and transient ping failures.
- Added Wi-Fi connect retry with backoff in `firmware/runtime.py`.
- Tests:
- `tests/unit/firmware/test_runtime.py::test_connect_wifi_retries_with_backoff_before_reporting_failure`
- `tests/unit/runtime/test_checks.py::test_portable_http_runner_retries_transient_transport_errors`
- `tests/unit/runtime/test_checks.py::test_portable_ping_runner_retries_transient_failures`
- `tests/unit/runtime/test_checks.py::test_portable_telnet_runner_retries_and_classifies_transient_socket_failures`
- Before: transient transport issues produced one-shot failures and inconsistent detail.
- After: the runtime retries boundedly and reports stable failure labels.

### PA-2026-04-07-04
- Severity: MEDIUM
- Title: SERVICE payload size was unbounded by check count.
- Evidence: `parse_service_payload()` accepted any check list length.
- Root cause: no hard bound on service fan-out despite device memory constraints and tiny-display UX limits.
- Fix: capped SERVICE payloads at 64 checks in `src/vivipi/services/schema.py`.
- Tests:
- `tests/contract/test_service_schema.py::test_parse_service_payload_rejects_payloads_that_exceed_the_safe_check_limit`
- Before: a bad endpoint could fan out arbitrarily.
- After: oversize payloads are rejected deterministically.

## Features Verified As Already Present

- Structured logging with `DEBUG`, `INFO`, `WARN`, and `ERROR` in a bounded ring buffer.
- REPL state inspection for registered checks, current failures, metrics, network state, retained logs, and retained errors.
- REPL control for manual check execution, reset, reconnect, log level changes, debug mode, and GC/memory snapshots.
- Deterministic event-driven rendering and identity-based selection.
- On-device diagnostics mode sized for the tiny display contract.

## Residual Risk

- Physical hardware validation remains bounded by the current environment. This pass did not exercise a live Pico over `mpremote`.
- HTTPS certificate verification remains dependent on the underlying Python or MicroPython HTTP stack and is unchanged in this pass.

## Validation

- Focused regressions: `pytest --no-cov -q tests/unit/firmware/test_runtime.py tests/unit/runtime/test_app.py tests/unit/runtime/test_checks.py tests/contract/test_service_schema.py`
- Full validation for this pass is recorded in `WORKLOG.md`.