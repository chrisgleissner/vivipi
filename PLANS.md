## Observability / REPL Convergence

Status: DONE
Started: 2026-04-06T17:28:25+01:00
Completed: 2026-04-06T17:49:09+01:00

- [x] T1 Structured Logging System
	Dependencies: none
	Success criteria: add a deterministic logging module with `DEBUG`/`INFO`/`WARN`/`ERROR`, component tags, fixed-format bounded records, ring-buffer integration hooks, and unit coverage for formatting, truncation, and level gating.
- [x] T2 Hot Path Logging Guardrails
	Dependencies: T1
	Success criteria: annotate runtime hot paths, keep debug logging off by default, prevent per-iteration logging in tight loops, and prove bounded log emission with targeted tests or measurements.
- [x] T3 Runtime State Introspection
	Dependencies: T1
	Success criteria: expose registered checks, latest results, last errors, timings, and network state through a read-only state registry with REPL-safe accessors and tests.
- [x] T4 REPL Control Surface
	Dependencies: T3
	Success criteria: expose safe idempotent controls to run checks, reset runtime state, reconnect Wi-Fi, dump logs, and change log levels at runtime with tests covering state safety.
- [x] T5 Ring Buffer Log Storage
	Dependencies: T1
	Success criteria: add a fixed-size constant-time in-memory ring buffer, integrate it with the logger, expose retrieval APIs, and document explicit memory bounds.
- [x] T6 Error Capture And Retention
	Dependencies: T3, T5
	Success criteria: capture runtime exceptions and per-check failures, retain them for REPL retrieval, and cover the behavior with tests.
- [x] T7 Memory Observability
	Dependencies: T3
	Success criteria: expose free/allocated memory and GC counters through a debug surface, collect snapshots outside hot paths, and cover the fallback behavior in tests.
- [x] T8 Timing / Latency Instrumentation
	Dependencies: T3
	Success criteria: record check duration, network latency, and cycle timing using lightweight timers, surface the metrics through REPL APIs, and validate with tests.
- [x] T9 Debug Mode Switch
	Dependencies: T1, T3, T4, T8
	Success criteria: add a runtime-toggleable debug mode that enables debug logs and deeper instrumentation while remaining off by default.
- [x] T10 Failure Reproducibility
	Dependencies: T1, T3, T5, T6, T8, T9
	Success criteria: provide deterministic snapshots, retained logs/errors, and documented reproduction steps sufficient to diagnose failures without reflashing.
- [x] T11 Testing Requirements
	Dependencies: T1, T3, T4, T5, T6, T7, T8, T9, T10
	Success criteria: add focused tests for the new modules and behaviors, keep requirement traceability current, and maintain repository branch coverage at or above the enforced gate.
- [x] T12 Performance Validation
	Dependencies: T2, T5, T7, T8, T9, T11
	Success criteria: collect before/after measurements for cycle latency, logging overhead, and memory stability, and record the evidence in `WORKLOG.md`.

## Display Mode + Multi-Column Convergence

Status: BLOCKED ON HARDWARE ACCESS

1. Add display mode, column count, and separator settings to the validated display configuration path and runtime state. DONE
2. Refactor overview rendering so `standard` mode with one column preserves existing output while new overview layouts use deterministic 1-4 column math on the 16x8 grid. DONE
3. Extend the frame/display contract to support glyph-only failed-state inversion without affecting separators, padding, or background pixels. DONE
4. Add unit coverage for configuration validation, layout calculation, truncation, rendering determinism, and failed inversion behavior. DONE
5. Run targeted tests after each change set, then full test and coverage. DONE
6. Attempt Pico deployment and capture hardware validation evidence in `WORKLOG.md`. BLOCKED: `mpremote` is installed and firmware artifacts are built, but `/dev/ttyACM0` is not accessible from this environment.

## Plan Extension — 2026-04-05T21:44:11+01:00

Status: DONE

1. Add `FTP` and `TELNET` check types with optional `username` and `password` fields in the shared check model and config/runtime serialization path. DONE
2. Implement deterministic FTP and Telnet probe runners in `src/vivipi/runtime/checks.py` with CPython-testable socket-level behavior, keeping `src/vivipi/core/execution.py` protocol-agnostic. DONE
3. Extend execution logic and tests so FTP validates login plus top-directory listing, and Telnet validates login plus post-login prompt/banner output. DONE
4. Update product docs and traceability for the new check types and authentication behavior. DONE
5. Raise the enforced branch coverage gate to strictly above `95%`, document that expectation in `AGENTS.md`, and keep CI hard-failing below the new threshold. DONE: gate raised to `96%`.
6. Run targeted suites during implementation, then rerun the full `./build ci` path to verify the new gate and protocol support. DONE: `178` tests green with `96.97%` total coverage.