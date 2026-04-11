# ViviPi Hardware Recovery Plan

Authoritative execution plan for fixing the real Pico failure in `/home/chris/dev/vivipi`.

## Objective

Close all hardware gates on the actual Waveshare Pico-OLED-1.3 board:

1. The Pico leaves the boot logo and enters the runtime UI on real hardware.
2. The OLED updates live without freezing or stale-frame behavior.
3. Health checks execute continuously and propagate FAIL/OK changes to the OLED within 10 seconds.
4. Both physical buttons are mapped correctly for the actual board revision and produce visible on-screen actions.
5. Serial logs and visual evidence are collected from the real hardware.

## Hard Rules

- `docs/spec.md` is the product source of truth.
- The real Pico behavior is the source of truth for completion.
- Unit tests, mocks, rendered config, and local builds are supporting signals only.
- `GP22` / `GP21` are untrusted until proven on hardware.
- The board image lead is `GP15 -> KEY0` and `GP17 -> KEY1`; prove or disprove this on hardware.
- Keep business logic in `src/vivipi/core` where practical.
- Keep MicroPython-facing changes thin, deterministic, and minimally invasive.
- Keep SH1107 calibration in normalized display config, not runtime hacks.
- Keep `docs/spec-traceability.md` aligned if requirements or tests move.

## Phase 1: Re-establish Hardware Ground Truth

Tasks:
- Determine the currently connected Pico device path(s) and deployment mechanism actually available in this environment.
- Determine the live serial-log capture path for the Pico.
- Determine the Pixel 4 / `adb` / `scrcpy` path for observing the OLED.
- Determine whether the Pico is currently running stale or newly deployed firmware.
- Determine actual KEY0 / KEY1 GPIO wiring from available board evidence plus live hardware behavior.

Evidence required before exit:
- Exact serial device path and working log-capture command.
- Exact deployment command and proof of the deployed payload.
- Exact display-observation command path (`adb`, `scrcpy`, screenshots, or equivalent).
- Explicit statement of active button GPIO mapping, marked proven or still under test.

Status:
- Completed for access and deployment.

## Phase 2: Minimal On-Device Diagnostic Reproducer

Tasks:
- Add a diagnostic firmware path or mode that does only:
  - show `BOOT`
  - after 2 seconds show `STAGE 1`
  - after 2 seconds show `STAGE 2`
  - then update a heartbeat/counter every second
  - log each stage to serial
- Deploy that diagnostic path to the real Pico.
- Verify the boot logo can be replaced and the main loop is alive.

Evidence required before exit:
- Serial logs for each stage.
- Visual proof that the OLED advances through the stages.
- Determination whether the fault is deployment, crash/reset, blocked loop, or display refresh failure.

Status:
- Completed via direct on-device serial isolation of the failing startup path.

## Phase 3: Fix Root Cause of Boot-Logo Stuck Failure

Tasks:
- Implement the smallest change that fixes the actual identified cause.
- Preserve instrumentation for:
  - boot stage logs
  - exception visibility
  - render-loop visibility
- Re-deploy and validate on hardware across 3 cold boots.

Evidence required before exit:
- Concise root-cause statement.
- 3 cold-boot proofs showing the logo exits and the runtime UI appears.

Status:
- Completed for the stuck-logo root cause; cold-boot repetition proof remains outstanding.

## Phase 4: Fix Buttons on Real Hardware

Tasks:
- Prove actual GPIO mapping on the connected board.
- Prove correct polarity and pull configuration from raw live readings.
- Add or correct debounce if needed.
- Ensure both buttons cause visible on-screen action and serial edge/action logs.

Evidence required before exit:
- Raw, debounced, and action logs for both buttons.
- Visual proof of both on-screen button actions.
- Concise root-cause statement if the previous mapping was wrong.

Status:
- In progress. Active device config now targets `GP15` / `GP17`; physical press proof still required.

## Phase 5: Restore Full Runtime + Health Checks

Tasks:
- Return from diagnostic mode to the full runtime path only after Phases 2-4 are stable.
- Validate non-blocking startup and ongoing checks on hardware.
- Prove at least one FAIL -> OK or OK -> FAIL transition appears on the OLED within 10 seconds.
- Harden the C64U/U64 protocol probes so they do not destabilize the 1541ultimate network services.
  - Inspect the local `1541ultimate` source for the actual HTTP, FTP, and telnet server behavior.
  - Remove avoidable probe behaviors that create extra sockets, extra commands, or parser-hostile bytes.
  - Enforce at least 500 ms between retry attempts so probe-local retries cannot violate the same-device spacing rule.

Evidence required before exit:
- Serial timestamps for check execution and render propagation.
- Visual proof of a state transition on-screen.
- Concise protocol-level root-cause statement for any 1541ultimate-specific crash vector that was found.

Status:
- In progress. Real FAIL and recovery propagation were captured on hardware; protocol hardening for C64U/U64 is implemented locally and pending any further device-side validation.

## Phase 6: End-to-End Proof Run

Tasks:
- Perform and record:
  - 3 cold boots
  - button A proof
  - button B proof
  - at least one health transition proof
  - serial log capture
  - Pixel 4 / `adb` / `scrcpy` screenshots or equivalent
- Update `WORKLOG.md` with observations, not assumptions.
- Leave no unresolved TODO for this task.

Evidence required before exit:
- All five non-negotiable gates closed with real-hardware proof.

Status:
- In progress.

## Active TODOs

- [ ] Establish the actual Pico USB/serial path in this environment.
- [ ] Establish the actual deployment command that reaches the connected Pico.
- [ ] Establish the Pixel 4 observation path with `adb` / `scrcpy` or equivalent.
- [ ] Prove whether the current Pico firmware matches the latest local build.
- [ ] Prove actual KEY0 / KEY1 GPIO mapping on this board from physical button presses.
- [x] Build and deploy the minimal staged diagnostic reproducer.
- [x] Isolate and fix the real root cause of the stuck-logo failure.
- [ ] Prove both buttons on real hardware with logs and visible feedback.
- [x] Prove health-check transitions reach the OLED within 10 seconds.
- [ ] Capture 3 true cold boots or equivalent physical power cycles.
- [ ] Capture the final proof set and fully close the worklog.
- [ ] Investigate why the ADB-backed Pixel 4 health check can stay FAIL after host suspend/resume even when `adb devices` shows the device as connected, and harden service restart/recovery if needed.

## Confirmed Findings

- Pico serial path: `/dev/ttyACM0` exposed as `usb-MicroPython_Board_in_FS_mode_740c0800366c92bb-if00`.
- Pico access path: `sg dialout -c 'mpremote connect /dev/ttyACM0 ...'`.
- Pixel observation path: `adb shell screencap -p ...` / `adb pull ...`, with the phone already focused on `org.lineageos.aperture/.CameraLauncher`.
- Active deployed firmware proof:
  - `mpremote fs cat :config.json` showed the live device config.
  - serial boot logs showed the deployed build version `0.2.3-41c04cd7`.
- Stuck-logo root cause:
  - startup error formatting crashed on-device in `vivipi/runtime/state.py`, raising `OSError: stream operation not supported` before runtime handoff could complete.
  - after fixing that, two MicroPython compatibility faults remained:
    - button logging assumed enum members exposed `.value`
    - scheduler host normalization assumed `str.casefold()` existed
- Button lead for the actual board is now deployed as `GP15` / `GP17`, but physical proof is still required.
