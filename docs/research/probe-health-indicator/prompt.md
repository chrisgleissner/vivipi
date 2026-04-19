# Probe Freshness Indicator And Timing Stabilization Prompt

ROLE

You are the implementation engineer for ViviPi.

You are working on the real ViviPi codebase, not a generic Pico demo.
You must execute against the repository as it exists today.

This is a strict execution task.
This is not a brainstorming pass.
This is not a research-only pass.
Do not stop at analysis.
Do not stop after adding tests only.
Do not stop after adding a renderer stub.
Do not stop after changing config defaults.
Implement, validate, deploy, and verify.

CURRENT REPOSITORY FACTS YOU MUST TREAT AS AUTHORITATIVE

1. `docs/spec.md` is the product source of truth.
2. If behavior, requirements, or defaults change, you must update `docs/spec.md` and `docs/spec-traceability.md` in the same task.
3. Business logic belongs in `src/vivipi/core`.
4. Pico runtime glue belongs in `src/vivipi/runtime` and `firmware/`.
5. `firmware/main.py` delegates to `firmware/runtime.py`, which builds the runtime and enters the device loop.
6. The current runtime already has:
   - due-check scheduling in `src/vivipi/core/scheduler.py`
   - event-driven rendering in `src/vivipi/core/render.py` and `src/vivipi/runtime/app.py`
   - pixel shift control in `src/vivipi/core/shift.py`
   - portable direct probe runners in `src/vivipi/runtime/checks.py`
   - check-result mapping in `src/vivipi/core/execution.py`
7. On the Pico path, `firmware/runtime.py` already forces serial probe execution by disabling background workers through `_force_serial_probe_execution(app)`. Do not accidentally re-enable probe overlap on-device.
8. The display is not free-form. The default 1.3 inch SH1107 OLED is still a strict `16 x 8` character grid at the default `medium` font size.
9. The current standard overview row format is the spec-driven `NAME ... STATUS` layout, not a generic `TARGET PROBE STATUS` split. Use the repository's real `name` field and real row-width math.
10. The Waveshare 1.3 inch OLED path uses a logical `128x64` framebuffer with a portrait-native SH1107 transport mapping and calibrated `column_offset = 32`. Preserve that mapping.
11. The current renderer already supports shift offsets and deterministic fixed-width text rendering. Do not add scrolling, animations, icons, or variable-width layout.
12. Current timing defaults are still `interval_s = 7` and `timeout_s = 5` in both the spec and sample config. If you move the product to `10s / 8s`, update the spec, traceability, config defaults, and tests together.
13. `timeout_s` is validated in `src/vivipi/core/config.py` and must remain at least 20% smaller than `interval_s`. `10s / 8s` is valid, but only exactly.
14. Burn-in mitigation already exists conceptually and in code. The current product spec requires the four-step one-pixel shift cycle `(0,0) -> (1,0) -> (1,1) -> (0,1)` with a `30-60s` interval. Do not silently invent a different burn-in strategy or a `2-5 minute` cadence without updating the spec.
15. `./build deploy` uses `mpremote connect auto` to copy the prepared filesystem to the first attached Pico. It does not flash a blank board.
16. `./build build-firmware`, `./build render-config`, and `./build deploy` automatically prefer `config/build-deploy.local.yaml` when it exists.
17. Device-facing service endpoints must remain explicit and host-reachable from the Pico. Do not hide them behind localhost defaults. Preserve `VIVIPI_SERVICE_BASE_URL` semantics.
18. Branch coverage must remain at or above `96%`.

PROBLEM TO SOLVE

Implement a deterministic per-probe freshness indicator and stabilize probe timing on the Pico without breaking the calm, static ViviPi UX.

The current issue is not just probe success vs. failure.
The missing behavior is a distinct freshness / liveness signal that answers:

- did this probe complete on schedule,
- is it falling behind,
- and has it recovered.

That freshness signal must remain visually quiet and must not create a noisy display.

PRIMARY GOALS

You must converge to all of the following:

1. A per-probe freshness indicator rendered in exactly one `8x8` character cell.
2. Deterministic interval-driven probe execution at `10s` interval and `8s` timeout.
3. Stabilized scheduling semantics with no on-device probe overlap and no timing drift accumulation.
4. A calm, static display when checks are healthy.
5. Burn-in mitigation that remains compliant with the actual ViviPi spec.
6. Full test coverage for the new behavior plus successful deployment to the attached Pico if hardware is available.

NON-NEGOTIABLE UX RULES

1. No continuous animation.
2. No pulsing, blinking, sweeping, cycling, or spinner-like behavior.
3. No scrolling.
4. No icons.
5. No variable-width layout tricks.
6. No layout jitter when the indicator changes state.
7. The healthy steady state must look static.

FRESHNESS INDICATOR REQUIREMENTS

Treat freshness as schedule freshness, not health.

That means:

- a quick `FAIL` result that still completes on time is still fresh
- a quick `DEG` result that still completes on time is still fresh
- freshness decays only when the scheduled execution cadence is missed
- freshness resets instantly only when a successful probe completion occurs

Each direct check must maintain a freshness width `w` in `{0, 2, 4, 6, 8}`.

Indicator cell rules:

1. The indicator occupies exactly one character cell: `8x8` pixels.
2. It is rendered manually as pixels, not as a font glyph.
3. It is left-aligned within the cell and shrinks from the right.
4. Visual states are:
   - `8`: full-width bar
   - `6`: six-pixel bar
   - `4`: four-pixel bar
   - `2`: two-pixel bar
   - `0`: single-pixel sentinel, not a blank cell
5. The `0` state must still draw one stable pixel so the cell remains visibly occupied.
6. The indicator must not animate between widths.

State-transition rules:

1. On successful probe completion, reset freshness to `8` immediately.
2. On each missed interval window, decrement by `2`, clamped at `0`.
3. Do not decay repeatedly on every tick while a probe remains overdue.
4. Do not tie decay to render frequency.
5. Use the scheduler cadence itself as the only driver for decay.
6. Add a small grace window of at most `1.0s` so normal runtime jitter does not cause false decay.

TIMING STABILIZATION REQUIREMENTS

Implement a stable interval model with these semantics:

1. Default probe interval becomes `10s`.
2. Default timeout becomes `8s`.
3. Probes must not overlap on-device.
4. Each scheduled attempt runs at most once.
5. A probe attempt that completes after its expected interval window plus grace is considered missed for freshness purposes.
6. Scheduling must not accumulate decay or execution drift just because the `tick()` loop runs every `50ms`.
7. The implementation must remain deterministic under both CPython tests and MicroPython runtime execution.

You must distinguish clearly between:

- `last_started_at`
- actual completion time
- successful completion time
- missed freshness window count

Do not collapse those into a single ambiguous timestamp.

LAYOUT REQUIREMENTS

You must preserve the fixed-width overview model and the `16 x 8` grid assumptions on the default OLED.

Before implementing, do the exact row-budget math.

The current standard row already fills the available width with `NAME ... STATUS`.
Adding a one-cell freshness indicator means you must deliberately choose how the row fits in `16` columns without introducing overflow, wrapping, or jitter.

Required layout behavior:

1. Keep the overview calm and aligned.
2. Ensure the right edge remains deterministic.
3. Ensure worst-case names do not overflow the row width.
4. Do not rely on runtime-measured text widths.
5. Keep selection identity-based.
6. Do not change the overall display mode architecture.

If the row budget requires adjusting the standard overview text layout, do it in a spec-consistent, fixed-width way and update the spec plus traceability.

RENDERING REQUIREMENTS

1. Prefer keeping the frame model pure and explicit.
2. Do not smuggle the freshness indicator through fake text glyphs.
3. Extend the frame/render pipeline in a way that makes the indicator an intentional render primitive.
4. Keep SH1107-specific transport calibration in display definitions and backends, not ad hoc runtime tweaks.
5. Preserve deterministic frame equality so event-driven rendering still skips identical frames.

IMPORTANT CURRENT-CODE REALITY

The current SH1107 backend renders a full framebuffer and writes the rotated buffer to the device.
Do not invent a complicated partial-update transport unless it is clearly justified and tested.

The priority is:

1. correct state transitions
2. stable event-driven rendering
3. deterministic pixel output

If row-only transport optimization is not cleanly supportable with the current SH1107 path, document that and keep the existing full-frame device write behavior while still avoiding unnecessary re-renders when state is unchanged.

LIKELY CHANGE AREAS

Inspect and update the smallest justified set of files. You will likely need changes in some combination of:

- `src/vivipi/core/models.py`
- `src/vivipi/core/render.py`
- `src/vivipi/core/state.py`
- `src/vivipi/core/scheduler.py`
- `src/vivipi/core/config.py`
- `src/vivipi/runtime/app.py`
- `firmware/displays/rendering.py`
- `firmware/displays/sh1107.py`
- `config/checks.yaml`
- `docs/spec.md`
- `docs/spec-traceability.md`
- relevant unit and spec tests

Keep business logic in `src/vivipi/core` whenever possible.
Keep MicroPython-facing code thin.

MANDATORY EXECUTION TRACKING

You must maintain:

1. `PLANS.md`
2. `WORKLOG.md`

Rules:

1. If they already exist, update them in place.
2. If they do not exist, create them.
3. `PLANS.md` must contain explicit phases:
   - Analysis
   - Implementation
   - Integration
   - Validation
   - Deployment
4. `WORKLOG.md` must contain timestamped entries and no skipped steps.

MANDATORY IMPLEMENTATION APPROACH

Phase 1: Inspect And Model The Existing Path
- Trace how a check becomes due, starts, completes, and renders today.
- Identify where freshness state belongs.
- Identify where the frame pipeline should carry a bitmap indicator.
- Document the exact row-width budget for the default `16 x 8` SH1107 layout.

Phase 2: Add Pure Freshness Logic
- Implement the freshness state machine in a pure, testable form.
- Ensure it handles:
  - steady healthy state
  - single miss
  - multiple misses
  - late completion
  - successful recovery
  - repeated ticks inside the same overdue window without double decay

Phase 3: Integrate Runtime Scheduling
- Wire the freshness transitions into `RuntimeApp`.
- Keep on-device execution serial and deterministic.
- Do not introduce race conditions.
- Do not add continuous timers just for the indicator.

Phase 4: Integrate Rendering
- Extend the frame model so the indicator is rendered intentionally as pixels.
- Render the indicator in the reserved `8x8` cell with exact pixel output.
- Keep frame equality stable for unchanged states.

Phase 5: Update Defaults And Spec
- Move default checks and validation expectations to `10s / 8s` if that is the chosen final product behavior.
- Update spec sections covering timing, layout, rendering, and any new freshness requirement.
- Update `docs/spec-traceability.md`.

Phase 6: Validate
- Add pure-function tests first.
- Add runtime integration tests second.
- Add firmware/display pixel tests where needed.
- Run repository validation commands.

Phase 7: Deploy And Verify
- Render config with existing tooling.
- Deploy to the attached Pico.
- Restart and verify the real display behavior.

MANDATORY TEST COVERAGE

At minimum, add or update tests that prove:

1. Freshness state math:
   - `8 -> 6 -> 4 -> 2 -> 0`
   - clamp at `0`
   - instant reset to `8` on success
   - no duplicate decay during repeated ticks in the same missed window
2. Timing defaults:
   - config parsing accepts and defaults to `10s / 8s`
   - the `20% smaller` validation still passes
3. Runtime behavior:
   - a probe that completes on time stays fresh even if status is `FAIL`
   - a probe that misses an interval decays exactly once per missed window
   - recovery after a success is immediate
   - render reasons stay event-driven
4. Rendering:
   - the indicator cell is pixel-accurate for all five widths
   - the sentinel pixel at `0` is present
   - layout remains deterministic
   - SH1107 output remains stable with `column_offset = 32`
5. Spec discipline:
   - traceability still covers every requirement
   - coverage gate still passes

VALIDATION COMMANDS

Use the repository commands, not ad hoc substitutes, unless you need a narrower test target while iterating:

```bash
./build lint
./build test
./build coverage
```

If config or deploy artifacts changed, also use the existing build flow:

```bash
./build render-config
./build build-firmware
./build deploy
```

DEPLOYMENT REQUIREMENTS

After implementation and automated validation:

1. Build the deployable runtime using existing tooling.
2. Deploy to the attached Pico using the existing repo flow.
3. Restart or allow the board to restart into the updated runtime.
4. Verify on-device:
   - overview renders correctly
   - no text overflow or jitter
   - probe interval is about `10s`
   - timeout behavior is consistent with `8s`
   - quick `FAIL -> OK` flicker is reduced or eliminated
   - healthy rows remain visually static
   - missed checks decay freshness one step at a time
   - successful checks reset freshness immediately
   - pixel shift remains active and unobtrusive

If the device is not physically available, or `mpremote connect auto` cannot find it, treat that as a hard blocker for deployment verification:

- finish code, tests, docs, and build validation
- record the exact blocker in `WORKLOG.md`
- do not claim full completion

WHAT YOU MUST NOT DO

Do not:

- refactor unrelated systems
- move business logic into `firmware/` without necessity
- re-enable background worker overlap on the Pico path
- implement the indicator as a text character hack
- add animations or periodic redraw loops
- add scrolling or change the fixed-width grid model
- break SH1107 rotation or column-offset calibration
- silently change spec-defined behavior without updating `docs/spec.md` and `docs/spec-traceability.md`
- stop after tests if deployment was requested and hardware is available

DONE ONLY WHEN ALL OF THESE ARE TRUE

1. Probe defaults are intentionally and consistently set to `10s / 8s`, with spec, config, and tests aligned.
2. Freshness is tracked separately from probe health.
3. Freshness decays only on missed schedule windows, with grace and no double-counting.
4. Successful completion resets freshness immediately to full.
5. The indicator is rendered as an exact `8x8` bitmap cell and remains stable.
6. The overview remains calm, fixed-width, and deterministic on the default SH1107 OLED.
7. Event-driven rendering still skips unchanged frames.
8. Pixel shift remains active and spec-compliant.
9. `PLANS.md` and `WORKLOG.md` are complete and current.
10. `./build lint`, `./build test`, and `./build coverage` pass.
11. Deployment to the Pico is completed and verified, or a concrete hardware blocker is documented and full completion is withheld.
