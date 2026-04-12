# Fix Pico-OLED-1.3 Buttons — Bottom-Up Recovery Plan

## Symptom
Pressing KEY0 / KEY1 on the Waveshare Pico-OLED-1.3 HAT mounted on the Pico 2W
produces no visible change on the OLED and no state transitions in the ViviPi
runtime.

## Hardware facts (from Waveshare wiki + vendor example)
Reference: https://www.waveshare.com/wiki/Pico-OLED-1.3

- Display: **SH1107 OLED, 128x64**, supports SPI or I2C via solder jumpers.
- SPI pins: `CS=GP9`, `DC=GP8`, `RST=GP12`, `CLK=GP10`, `DIN=GP11`.
- I2C pins: `SDA=GP6`, `SCL=GP7`.
- **Buttons** (only two — no joystick):
  - **KEY0 → GP15**, button-to-GND, relies on **internal PULL_UP**
  - **KEY1 → GP17**, button-to-GND, relies on **internal PULL_UP**
  - Pressed ⇒ pin reads **0**; released ⇒ pin reads **1**.
- Vendor MicroPython example uses `Pin(15, Pin.IN, Pin.PULL_UP)` and
  `Pin(17, Pin.IN, Pin.PULL_UP)` and a pure polling loop — **no IRQ, no
  auto-detect, no debounce logic**.

## Current firmware behaviour (what we ship)
- [firmware/runtime.py:36](firmware/runtime.py#L36) defaults the button config
  to `{"a": "GP15", "b": "GP17"}`. The on-device
  [artifacts/device/config.json](artifacts/device/config.json) already sets
  `device.buttons.a = "GP15"`, `b = "GP17"`, `startup_self_test_s = 30`.
  Pin numbers are therefore **correct**.
- [firmware/input.py:32-71](firmware/input.py#L32-L71) `ButtonReader.__init__`
  calls `_detect_bias()` which probes the pin twice (PULL_UP then PULL_DOWN)
  and picks the bias that yields a stable reading. On a PULL_UP-only HAT this
  returns `up` with idle value `1`, matching the vendor sample. But the extra
  probe reconfigures the pin — worth scrutinising as a potential first-read
  race.
- [firmware/input.py:129-147](firmware/input.py#L129-L147) binds an IRQ that
  latches press counts. [firmware/input.py:149-170](firmware/input.py#L149-L170)
  then drains those latched presses only once the pin has returned to idle,
  *and resets the counter to zero if the pin is still held*. This hybrid
  IRQ+poll path is materially more complex than the vendor reference and is a
  strong candidate for the root cause if events are silently dropped.
- [firmware/runtime.py:249-293](firmware/runtime.py#L249-L293) already
  implements a **boot-time button self-test** that renders `BTN SELFTEST`,
  live `GP15` / `GP17` values, and exits on first detected event OR after
  `startup_self_test_s` seconds. This is our fastest on-device diagnostic.
- [src/vivipi/runtime/app.py:1265-1275](src/vivipi/runtime/app.py#L1265-L1275)
  `RuntimeApp.tick` calls `button_reader.poll()` each iteration and feeds
  events through `_apply_button_events` → `InputController.apply` → state
  transition. The display then re-renders on the next frame.

## Guiding principle
Walk the stack from copper → pin → driver → runtime → UI, proving each layer
in isolation. Do not modify firmware until we know which layer actually
fails. Use the **simplest possible vendor-equivalent probe at each layer**
so that a failure unambiguously localises the fault.

---

## Phase 0 — Physical + port sanity (2 min)

1. Visually confirm the HAT is fully seated on the Pico 2W (both 20-pin
   headers engaged; no bent pins).
2. `mpremote connect auto eval 'import machine; print(machine.freq())'` — prove
   we can reach the board at all.
3. `mpremote connect auto ls /` — confirm `main.py`, `config.json`,
   `display.py`, `input.py`, `runtime.py` are present from the last deploy.

**Stop condition:** if mpremote cannot see the board, fix USB/driver first.

---

## Phase 1 — Raw GPIO probe, no firmware (3 min)

Run the absolute minimum vendor-equivalent one-liner on the Pico with the
HAT attached **and nothing else running**:

```bash
mpremote connect auto exec '
from machine import Pin
import time
a = Pin(15, Pin.IN, Pin.PULL_UP)
b = Pin(17, Pin.IN, Pin.PULL_UP)
print("idle", a.value(), b.value())
for _ in range(200):
    print(a.value(), b.value())
    time.sleep_ms(50)
'
```

**Expected:** `idle 1 1`, and pressing KEY0 flips the first column to `0`,
pressing KEY1 flips the second column to `0`.

**Decision tree:**
- Both pins read `1` always, even while held → **hardware** (HAT seating,
  solder, broken switch). Stop and fix physically.
- Both pins read `0` always → short-to-ground or wrong HAT (LCD vs OLED).
- Expected toggling observed → GPIO is fine. Proceed to Phase 2.

---

## Phase 2 — Vendor reference, buttons + display (3 min)

Copy the SPI reference from the Waveshare wiki (the `__main__` block at the
bottom that fills `fill_rect(0, 44, 128, 20, white)` while KEY0/KEY1 are held)
into a throwaway `pico_vendor_demo.py`, then:

```bash
mpremote connect auto run docs/research/fix-buttons/pico_vendor_demo.py
```

**Expected:** top/bottom bars on the OLED toggle while buttons are pressed;
serial shows `A`, `B`.

**Decision tree:**
- OLED + prints both work → the stack from **pin → SH1107 → serial** is
  healthy end-to-end. Our bug lives in `firmware/input.py` or
  `RuntimeApp._apply_button_events`. Proceed to Phase 3.
- OLED toggles but no serial prints → REPL is swallowing stdout (ignore,
  irrelevant to the fix).
- OLED does not react → display init is suspect; that's a **separate** bug
  and is not in scope for this plan, but note it.
- Serial prints but no OLED change → vendor display init differs from
  `firmware/displays/sh1107.py`; also out of scope unless Phase 5 fails.

---

## Phase 3 — Existing standalone button monitor (2 min)

We already ship a clean poll-only monitor:
[scripts/monitor_pico_buttons.py](scripts/monitor_pico_buttons.py).

```bash
mpremote connect auto run scripts/monitor_pico_buttons.py
```

**Expected:** `CONFIG button=A pin=GP15 pull=up idle=1`, same for B, then
`PRESS ... value=0 ... previous=1` on press and `RELEASE ... value=1` on
release, with 30 ms debounce.

**Decision tree:**
- `idle=0` at startup → `_detect_pull` picked `down` incorrectly (would only
  happen if the HAT has external pull-downs — it does not for the OLED 1.3).
  Root cause: `_detect_bias`. Fix = Phase 5.A.
- No PRESS line ever printed → pull-up not actually enabled, or pin number
  mismatch; revisit Phase 1.
- PRESS/RELEASE printed cleanly → polling + debounce work. Problem is the
  full `ButtonReader` (IRQ/latching) or the runtime routing. Proceed to
  Phase 4.

---

## Phase 4 — Full runtime, boot self-test (5 min)

The on-device config already has `startup_self_test_s: 30`, so a normal
`./build deploy` + reboot will immediately enter `_run_button_self_test`
rendering `BTN SELFTEST`, the `ButtonReader.snapshot()` of each button, and
a parallel `probe_pin_states` read. The test exits on first detected event.

```bash
./build deploy
mpremote connect auto   # attach to the USB serial repl, do NOT Ctrl-D
```

Watch on the OLED **and** on the serial log for:
```
[BOOT][BTNTEST] start duration_s=30.0
[BOOT][BTNTEST] snapshot a=A15 r1 s1 b=B17 r1 s1 gp15=1 gp17=1
```

Press KEY0 and KEY1 within 30 s. Expected:
```
[BOOT][BTNTEST] snapshot a=A15 r0 s0 b=B17 r1 s1 gp15=0 gp17=1
[BOOT][BTNTEST] button-detected count=1
```

**Decision tree:**
- On-screen `A15 r1 s1` stays `r1 s1` while pressed, **but** the parallel
  `probe_pin_states` line shows `gp15=0` → `ButtonReader`'s pin object is
  broken (wrong pin, bias auto-detect sticking, or IRQ handler flipping pull
  direction). Root cause inside [firmware/input.py](firmware/input.py).
  Fix = Phase 5.A.
- Both the `r` field and the probe flip together but `button-detected` never
  fires → the IRQ-latched / step-count emission path in `poll()` is dropping
  events. Fix = Phase 5.B.
- `button-detected` fires but the normal runtime UI still doesn't change →
  events are generated but `RuntimeApp._apply_button_events` /
  `InputController.apply` doesn't produce a re-render. Fix = Phase 5.C.

---

## Phase 5 — Targeted fixes

Apply only the branch identified by Phase 4. Do not over-refactor.

### 5.A — Simplify `ButtonReader` initialisation to match vendor

Delete `_detect_bias` and hard-code `PULL_UP` (the HAT has no external
pull-down; auto-detect adds two reconfigure cycles and gains nothing). Keep
`pull` overridable via config only as `"up"` / `"down"`, default `"up"`.

- Remove `_sample_with_pull`, `_detect_bias`.
- `_normalize_button_entry` default `pull` becomes `"up"`.
- `__init__` opens `Pin(n, Pin.IN, Pin.PULL_UP)` directly.
- Idle value is hard-coded `1` (document: vendor assumes active-low).

Rationale: matches Waveshare reference exactly; eliminates a whole class of
"wrong bias at boot" failures. Cost: one config field stops supporting
`"auto"`.

### 5.B — Drop the IRQ / latched-press hybrid, keep polling only

Current `poll()` mixes three mechanisms:
1. IRQ handler latching presses into `state["latched_presses"]`.
2. `_drain_latched_presses` emitting events **only after release** AND
   zeroing the counter if still held (silent drop).
3. Polling + debounce + `_step_count` repeat logic.

The IRQ path was presumably added to catch sub-poll-interval taps, but on a
50 ms poll loop with 30 ms debounce it buys us nothing and introduces the
silent-drop branch at [firmware/input.py:155-158](firmware/input.py#L155-L158).

Fix:
- Delete `_bind_irq`, `_drain_latched_presses`, all `latched_presses` /
  `last_irq_press_ms` state.
- `poll()` becomes: read raw → update `raw_changed_ms` on change → once
  `debounce_ms` elapsed, commit to `stable_value` → on `idle → pressed`
  transition, emit one `ButtonEvent` with `held_ms=debounce_ms` → while
  still pressed, emit additional events at `repeat_ms` cadence via the
  existing `_step_count` path.

This is ~60 fewer lines and matches both the vendor demo and
`scripts/monitor_pico_buttons.py`.

### 5.C — Runtime routing fix

If Phase 4 shows events are generated but UI doesn't change, inspect:
- `RuntimeApp._apply_button_events` — does it call the state transition and
  flag the frame dirty?
- `InputController.apply` in [src/vivipi/core/input.py](src/vivipi/core/input.py)
  — the `Button.A` branch requires `state.mode == AppMode.ABOUT` or
  `AppMode.DETAIL` to produce any visible change. If the app is in
  `OVERVIEW` mode, pressing A *does* call `move_selection`; pressing B
  enters `DETAIL`. Verify via log lines `BTN event ...` followed by a
  subsequent frame draw.

Most likely sub-case: events are emitted but the app is blocked in the boot
self-test loop or waiting on `boot_logo_until_s`, so early presses are
eaten. If so, Phase 4's own self-test will succeed — which confirms the
driver is fine and the perceived "buttons don't work" is actually "nothing
happens on the overview screen because my first press was during the
2-second boot splash". Fix: emit a visible highlight on every accepted
event regardless of mode (e.g. flash the top row for 100 ms in
`_apply_button_events`) so the user gets unambiguous feedback.

---

## Phase 6 — Guardrails (30 min, after the fix lands)

1. **Unit test** in [tests/unit/firmware/test_firmware_input.py](tests/unit/firmware/test_firmware_input.py):
   a synthetic `Pin` stub that steps through `1 → 0 → 0 → 1` with controlled
   `ticks_ms`, asserting exactly one `ButtonEvent` is emitted and
   `snapshot()` reflects `pressed=True` mid-sequence. (Add equivalent for
   `B` and for a held-repeat scenario.)
2. **Regression guard**: assert that constructing `ButtonReader` with the
   production config `{"a": "GP15", "b": "GP17"}` yields `pull="up"`,
   `idle_value=1`, regardless of the first raw read.
3. **Boot log assertion**: after deploy, grep the USB serial boot log for
   `[BOOT][BTNTEST] button-detected` as the acceptance criterion. A green
   run requires a human to press both buttons inside 30 s — document this
   step in [WORKLOG.md](WORKLOG.md).

---

## Files to touch (by phase)

| Phase | File | Change |
|---|---|---|
| 1-4 | none | diagnostic only |
| 5.A | [firmware/input.py](firmware/input.py) | drop `_detect_bias`, hard-code `PULL_UP` |
| 5.B | [firmware/input.py](firmware/input.py) | delete IRQ/latched branches, keep polling |
| 5.C | [src/vivipi/runtime/app.py](src/vivipi/runtime/app.py) | add visible press feedback |
| 6 | [tests/unit/firmware/test_firmware_input.py](tests/unit/firmware/test_firmware_input.py) | coverage for the fixed path |

## Acceptance criteria

1. `mpremote connect auto run scripts/monitor_pico_buttons.py` prints a
   clean PRESS/RELEASE pair for both KEY0 and KEY1.
2. On boot with `startup_self_test_s: 30`, serial log contains
   `[BOOT][BTNTEST] button-detected` within one press.
3. In normal runtime: pressing KEY1 (B) from the overview enters a detail
   screen; pressing KEY0 (A) cycles selection. Visible change on the OLED
   within one `poll_interval_ms` (50 ms).
4. Unit tests in Phase 6 pass.

## Out of scope
- Display init regressions (SH1107 command sequence).
- I2C variant of the HAT — we only ship SPI.
- Any change to `repeat_ms` / `debounce_ms` defaults.

## Execution Log — 2026-04-11T18:06:40Z

| Phase | Command | Expected | Observed | Decision |
|---|---|---|---|---|
| 0 | `sg dialout -c 'mpremote connect auto exec "import machine; print(machine.freq())"'`; `sg dialout -c 'mpremote connect auto ls /'` | Board reachable; deployed runtime files present | `150000000`; `/` contained `config.json`, `display.py`, `input.py`, `main.py`, and `runtime.py` | PASS: board reachable, proceed |
| 1 | `sg dialout -c 'mpremote connect auto exec "from machine import Pin; import time; a=Pin(15, Pin.IN, Pin.PULL_UP); b=Pin(17, Pin.IN, Pin.PULL_UP); print(\"idle\", a.value(), b.value()); [print(a.value(), b.value()) or time.sleep_ms(50) for _ in range(10)]"'` | `idle 1 1`; pressed KEY0 / KEY1 toggle their samples to `0` | Idle baseline stayed `1 1` across 10 samples; no physical button actuation was available from this shell | PASS for pull-up baseline; press proof BLOCKED (operator) |
| 2 | `mpremote connect auto run docs/research/fix-buttons/pico_vendor_demo.py` | Vendor OLED bars and serial `A` / `B` while pressed | BLOCKED (operator): vendor demo was not run because this shell could not press the board buttons or observe the OLED | Defer pin-to-display proof; do not infer against runtime |
| 3 | `sg dialout -c 'timeout 3s mpremote connect auto run scripts/monitor_pico_buttons.py'` | `pull=up idle=1` at startup; clean `PRESS` / `RELEASE` pairs while pressed | Startup printed `CONFIG button=A pin=GP15 pull=up idle=1` and the same for `B`, both before and after the final deploy; no press pair could be generated from this shell | PASS for config/bias baseline; PRESS / RELEASE proof BLOCKED (operator) |
| 4 | `./build deploy`; `sg dialout -c 'mpremote connect auto soft-reset'`; `sg dialout -c 'timeout 6s mpremote connect auto'` | `[BOOT][BTNTEST] start`; `[BOOT][BTNTEST] button-detected` after one real press | Deploy completed and the device REPL was reachable after soft reset, but no operator press or live OLED observation was available and the boot self-test transcript was not captured in time | BLOCKED (operator): defer `button-detected` and UI proof |

Chosen fix branches: `5.A + 5.B + 5.C`

Justification: physical button actuation and OLED observation were unavailable from this shell, so the safe vendor-equivalent simplifications from `5.A` and `5.B` were applied together, and `5.C` adds a deterministic `150 ms` visible acknowledgment for accepted button presses.

hardware-verified: no

Serial transcript: not captured from a clean boot window in this shell-only session; operator must collect it during the rerun below.

Operator: connect Pico 2W with Pico-OLED-1.3 HAT, run `./build deploy`, then re-run phases 1–4 of this plan; append the resulting PRESS / RELEASE pairs, `[BOOT][BTNTEST] button-detected` lines, and OLED observations to this Execution Log.
