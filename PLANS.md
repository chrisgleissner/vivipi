## Display Mode + Multi-Column Convergence

Status: BLOCKED ON HARDWARE ACCESS

1. Add display mode, column count, and separator settings to the validated display configuration path and runtime state. DONE
2. Refactor overview rendering so `standard` mode with one column preserves existing output while new overview layouts use deterministic 1-4 column math on the 16x8 grid. DONE
3. Extend the frame/display contract to support glyph-only failed-state inversion without affecting separators, padding, or background pixels. DONE
4. Add unit coverage for configuration validation, layout calculation, truncation, rendering determinism, and failed inversion behavior. DONE
5. Run targeted tests after each change set, then full test and coverage. DONE
6. Attempt Pico deployment and capture hardware validation evidence in `WORKLOG.md`. BLOCKED: `mpremote` is installed and firmware artifacts are built, but `/dev/ttyACM0` is not accessible from this environment.