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