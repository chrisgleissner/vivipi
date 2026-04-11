---
description: Drive the Pico-OLED-1.3 button-recovery plan to full, validated completion
---

# Fix Pico-OLED-1.3 Buttons — Execution Prompt

Deliver a complete, production-ready fix for the KEY0 / KEY1 buttons on the
Waveshare Pico-OLED-1.3 HAT (Pico 2W). The authoritative plan lives at
[docs/research/fix-buttons/PLAN.md](docs/research/fix-buttons/PLAN.md). You
must execute that plan end-to-end.

This is an execution prompt, not an analysis prompt. Do not stop after
diagnostics. Do not stop after a partial fix. Carry the work through to all
acceptance criteria in the plan unless genuinely blocked by physical
hardware you cannot reach.

---

# Core Objective

By the time you stop, all of the following must be true:

1. `scripts/monitor_pico_buttons.py` prints clean `PRESS` / `RELEASE` pairs
   for KEY0 (GP15) and KEY1 (GP17) against a freshly deployed board.
2. On boot with `device.buttons.startup_self_test_s: 30`, the USB serial log
   contains `[BOOT][BTNTEST] button-detected` after one real press.
3. In normal runtime: KEY1 enters/exits a detail screen; KEY0 cycles
   selection — with visible OLED change within one `poll_interval_ms`.
4. Unit tests in [tests/unit/firmware/test_firmware_input.py](tests/unit/firmware/test_firmware_input.py)
   cover the fixed path (press, release, held-repeat, snapshot).
5. Full test suite passes and coverage stays **≥ 91%**.
6. [PLANS.md](PLANS.md) has a new `Plan Extension` entry referencing this
   work; [WORKLOG.md](WORKLOG.md) has timestamped entries for every step.
7. No TODOs, placeholders, or `# FIXME` remain in touched files.

---

# Non-Negotiable Rules

- Act as a deterministic execution engine, not a planner.
- Walk the diagnostic ladder (Phase 0 → Phase 4) **before** editing
  firmware. Do not pre-judge which fix branch to apply.
- Modify only what the ladder proves is broken. Prefer the smallest correct
  change. Do not refactor adjacent code.
- Do not rewrite [docs/research/fix-buttons/PLAN.md](docs/research/fix-buttons/PLAN.md);
  append an `## Execution Log — <UTC timestamp>` section at the bottom with
  the ladder evidence.
- Never skip hooks, never `--no-verify`, never touch git config.
- Preserve public behaviour for anyone relying on `ButtonReader.poll()` /
  `snapshot()` return shapes — the on-device self-test reads them.

---

# Phase A — Ladder Evidence (Diagnostics)

Walk phases 0–4 of the plan in order. For **each** phase, append a row to
`docs/research/fix-buttons/PLAN.md` under a new `## Execution Log — <UTC>`
section with: phase, command, expected, observed, decision.

## If hardware is reachable (mpremote connect auto succeeds)

Run every phase live. After Phase 4, the plan's decision tree uniquely
identifies the fix branch (5.A, 5.B, or 5.C). Record the chosen branch in
the execution log with a one-line justification.

## If hardware is NOT reachable

Do not stop. Instead:

1. Mark phases 1–4 as `BLOCKED (no device)` in the execution log.
2. Proceed to Phase B with **both** fix branches 5.A and 5.B applied — the
   static analysis in the plan already justifies both as safe,
   vendor-equivalent simplifications, and 5.C is a strict additive
   improvement (visible press feedback). Note in the execution log that
   on-device acceptance criteria 1–3 will require a human to re-run phases
   1–4 before the fix can be declared landed.

---

# Phase B — Execution Loop

Execute the chosen fix branches **one at a time** using the repo's standard
loop. For each task:

## Step 1 — Assert
Re-read the target file(s). State the exact gap in one sentence in
[WORKLOG.md](WORKLOG.md).

## Step 2 — Implement
Apply the smallest correct change. Specifically:

### 5.A — `firmware/input.py` bias simplification
- Delete `_sample_with_pull`, `_detect_bias`.
- In `_normalize_button_entry`, default `pull` to `"up"`; accept only
  `"up"` / `"down"` (drop `"auto"`).
- In `__init__`, open `Pin(pin_number, Pin.IN, self._pull_constant(bias))`
  directly; set `idle_value = 1` when `bias == "up"` else `0`, and log
  `sample=<raw pin read after init>`.
- Do not change the `snapshot()` return shape.

### 5.B — `firmware/input.py` IRQ/latch removal
- Delete `_bind_irq` and all calls to it.
- Delete `_drain_latched_presses`.
- Remove `latched_presses` and `last_irq_press_ms` from the per-button
  state dict.
- In `poll()`, keep the existing polling-with-debounce logic and the
  `_step_count`-driven repeat path. Verify the `button != Button.A` clamp
  remains (`min(step_count, 1)` for B).
- Ensure the `BTN raw` / `BTN debounced` / `BTN event` log lines still fire
  — they are the acceptance signal in Phase 4.

### 5.C — Visible press feedback (only if Phase 4 pointed here, OR if
hardware was unreachable)
- In [src/vivipi/runtime/app.py](src/vivipi/runtime/app.py)
  `_apply_button_events`, after the state transition, flag the next frame
  as press-highlighted for a short window (`150 ms`) so the user sees an
  unambiguous response even when the semantic transition is a no-op.
- Implement as state (`AppState` field or transient on `RuntimeApp`), not
  as a direct display call — keep tests pure.

## Step 3 — Test
Update / add tests in
[tests/unit/firmware/test_firmware_input.py](tests/unit/firmware/test_firmware_input.py):

1. Construction with config `{"a": "GP15", "b": "GP17"}` yields `pull="up"`
   and `idle_value=1` under a Pin stub that returns `1` on read.
2. Single press (`1 → 0 → 0 → 1`) with controlled `ticks_ms` emits
   exactly one `ButtonEvent(button=Button.A, held_ms=30)` and zero after
   release.
3. Held repeat for Button.A: after `debounce_ms + N*repeat_ms` the total
   event count equals `1 + N`.
4. Held repeat for Button.B is clamped to 1 regardless of hold length.
5. `snapshot()` reflects `pressed=True` between debounce and release.
6. Construction with explicit `{"pin": "GP15", "pull": "down"}` inverts
   `idle_value` to `0` and a `0 → 1 → 1 → 0` sequence emits one event.
7. Legacy `"auto"` string is rejected with a clear `ValueError` (regression
   guard against accidental reintroduction).

If 5.C is in scope, add coverage in
[tests/unit/runtime/test_app.py](tests/unit/runtime/test_app.py) asserting
that `_apply_button_events` sets and clears the press-highlight flag
around `now_s`.

## Step 4 — Validate
Run the full pre-merge gate. Use whatever the repo's canonical command is
— check [`./build`](build) first, then fall back to `pytest` + `ruff`:

```
./build test
./build lint    # if defined
```

All green before proceeding. Fix failures immediately — do not defer.

## Step 5 — Coverage
Run coverage. Target ≥ 91% total; every new branch in `firmware/input.py`
must be exercised. If below threshold, add tests for the uncovered lines
before continuing.

## Step 6 — Verify
Re-read the edited files cold to confirm:
- No dead imports (`utime`, `machine.Pin.IRQ_RISING`, etc. should be
  removed if the IRQ path is gone).
- No references to deleted state keys.
- Log lines still match the format `_log("info", "<event>", (...))` that
  [firmware/runtime.py:249-293](firmware/runtime.py#L249-L293) relies on.

## Step 7 — Record
Append to [WORKLOG.md](WORKLOG.md):
- UTC timestamp
- Task (e.g. `fix-buttons 5.A bias simplification`)
- Action (files touched, lines delta)
- Result (test + coverage numbers)
- Next step

## Step 8 — Continue
Move to the next branch / next task immediately. Do not pause to
summarise.

---

# Phase C — Plan & Spec Reconciliation

After the last code change lands:

1. Append a `## Plan Extension — <UTC>` section to [PLANS.md](PLANS.md)
   describing: what was diagnosed, which branches were applied, the final
   state of `firmware/input.py`, and the acceptance-criteria status.
2. Append the ladder evidence and the chosen fix branch to
   [docs/research/fix-buttons/PLAN.md](docs/research/fix-buttons/PLAN.md)
   under `## Execution Log — <UTC>`.
3. If any entry in [docs/spec-traceability.md](docs/spec-traceability.md)
   references button handling, update it to point at the new code.
4. Update [WORKLOG.md](WORKLOG.md) with a final entry summarising results.

---

# Phase D — On-Device Acceptance (if hardware reachable)

Only executable on a real Pico 2W with the HAT attached. If reachable:

```
./build deploy
mpremote connect auto
```

- Watch serial for `[BOOT][BTNTEST] start` then `button-detected` on press.
- Confirm OLED reacts: KEY0 cycles selection, KEY1 toggles detail view.
- Confirm visible press feedback appears within 50 ms of each press.

Append the serial transcript (trimmed to the relevant lines) and a
`hardware-verified: yes|no` marker to the Execution Log.

If the device is not reachable, mark Phase D as `DEFERRED (operator)` and
leave a one-line operator runbook in
[docs/research/fix-buttons/PLAN.md](docs/research/fix-buttons/PLAN.md):
> Operator: connect Pico 2W with Pico-OLED-1.3 HAT, run `./build deploy`,
> then re-run phases 1–4 of this plan; append results to Execution Log.

---

# Convergence Criteria

Stop only when **all** of the following are true:

- Ladder evidence recorded in Execution Log (or BLOCKED with justification)
- Chosen fix branch(es) fully applied
- All unit tests pass
- Coverage ≥ 91%
- Build succeeds
- No TODOs / placeholders in touched files
- `PLANS.md` has a Plan Extension entry
- `WORKLOG.md` has entries for every step
- Phase D recorded (verified or explicitly deferred with runbook)

If any item above is false, continue working.

---

# Failure Handling

If a fix branch breaks tests that you cannot quickly diagnose:

1. Revert just that branch to the prior vendor-equivalent form.
2. Record the failure + reproduction in the Execution Log.
3. Continue with the remaining branches.
4. Mark the reverted branch `BLOCKED (regression)` with a concrete
   reproduction, not a vague description.

Do not silently swallow exceptions in firmware code. `ButtonReader`
construction failures must still surface as the existing `BTN init failed`
boot diagnostic.

---

# Anti-Patterns (Do Not Do)

- Do not rewrite `firmware/input.py` from scratch — make targeted deletes +
  edits so the diff is reviewable.
- Do not add backwards-compatibility shims for the removed `"auto"` pull
  mode; it is not consumed by any shipped config.
- Do not add comments explaining *what* the polling loop does. Add a
  comment only if the *why* (e.g. "match Waveshare vendor reference —
  no IRQ because…") is non-obvious.
- Do not introduce new dependencies. No new modules in `firmware/`.
- Do not end your run with "remaining work", "next steps", or
  "plan updated". If work remains, you are not done.

---

# Completion Summary

When fully complete, your final message must contain:

- Which fix branches were applied (5.A / 5.B / 5.C)
- Final ladder evidence (phase-by-phase pass/fail)
- Test result + coverage percentage
- Hardware-verification status (verified / deferred-with-runbook)
- Links to the updated PLAN.md Execution Log, PLANS.md Plan Extension, and
  WORKLOG.md entries
- A one-line operator action, if Phase D was deferred
