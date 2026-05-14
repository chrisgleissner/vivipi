---
description: Implement multi-device ViviPi build, provision, and deploy support with real rollout verification on the attached OLED and unproven ePaper devices
---

# Multi-Device Build And Deploy — Execution Prompt

Deliver a complete, implementation-ready first pass of ViviPi multi-device support using the direct `devices:` configuration model described in [spec.md](./spec.md). The authoritative implementation plan lives in [PLAN.md](./PLAN.md). Execute that plan end-to-end.

This is an execution prompt, not a research-only pass. Do not stop after config parsing. Do not stop after tests. Do not stop after a host-only prototype. Carry the work through implementation, validation, and hardware rollout unless you are genuinely blocked by inaccessible hardware.

## Core objective

By the time you stop, all of the following must be true:

1. ViviPi accepts a direct `devices:` mapping while preserving existing single-device configs.
2. `build-firmware --all-devices` produces isolated per-device artifacts.
3. `list-devices` resolves the current OLED board as `serial-ready` and the current new board as either `bootsel` or its later post-provision serial state.
4. `deploy --all-devices` and `deploy --all-devices --provision-missing` behave per-device with isolated failure reporting.
5. Unit tests cover config expansion, artifact isolation, state resolution, and deploy/provision behavior.
6. Full repository validation passes and coverage stays at or above the repo gate.
7. Real rollout verification proves that both attached boards render ViviPi on their actual displays.
8. The new ePaper board is not treated as working until both logs and the panel output itself are verified.

## Non-negotiable rules

1. `docs/spec.md` is still the product source of truth.
2. Keep core logic in `src/vivipi/core` when possible.
3. Keep firmware and MicroPython-facing code thin.
4. Do not move multi-device orchestration into the runtime loop on the Pico.
5. Do not regress the current single-device workflow.
6. Do not use `auto` as a per-device selector in multi-device mode.
7. Do not declare success for the new ePaper device based on serial output alone.

## Current repository facts you must treat as authoritative

1. The current deploy path is single-device and copies one staged filesystem to one resolved port.
2. The current display registry already supports the OLED and the 2.13 inch tri-color ePaper display type.
3. The currently attached OLED board is reachable as a serial MicroPython device.
4. The currently attached new board is presently visible in BOOTSEL mass-storage mode and its display path is still unproven.
5. `./build deploy` does not flash a base UF2 onto a blank board today.
6. Branch coverage must remain above the repo gate.

## Mandatory execution sequence

### Phase A — Baseline and inventory

Before editing code:

1. confirm the selectors for both attached boards,
2. confirm the OLED baseline still works,
3. record that the ePaper board is currently unproven and must be verified during rollout.

Do not skip the hardware-state baseline.

### Phase B — Config and artifact implementation

Implement the smallest correct host-side abstractions that generalize the current single-device pipeline:

1. config expansion from `devices:` into resolved per-device configs,
2. per-device artifact staging and manifest generation,
3. per-device inventory/state resolution,
4. per-device deploy/provision orchestration.

Keep the firmware runtime single-device.

### Phase C — Focused tests first

Add or update tests before broad validation. At minimum, cover:

1. single-device compatibility,
2. per-device config merge behavior,
3. rejection of `auto` in `devices` selectors,
4. duplicate selector rejection,
5. per-device artifact isolation,
6. serial-ready, BOOTSEL, missing, and ambiguous state resolution,
7. provision-then-deploy behavior,
8. partial success and summary reporting.

### Phase D — Repository validation

Run the repository’s standard validation workflow before any deployment claim. Fix failures immediately.

### Phase E — Real rollout and display proof

This phase is mandatory.

For both attached devices:

1. deploy or provision+deploy as appropriate,
2. inspect logs for boot and display-path problems,
3. verify the physical display actually renders ViviPi.

For the new ePaper device specifically, check for:

1. display init failures,
2. busy-pin waits or timeouts,
3. refresh completion,
4. sleep/reinit issues after reset,
5. visible panel output after deployment.

Do not treat any of the following as sufficient on their own:

1. successful UF2 copy,
2. successful `mpremote` file copy,
3. successful serial boot log,
4. absence of exceptions if the panel remains blank.

The new ePaper device is only verified if:

1. the logs show no unresolved display-path failures,
2. the panel visibly renders ViviPi output.

## Required deliverables

1. code implementing the first-pass multi-device workflow,
2. tests covering the new host-side behavior,
3. any required doc updates,
4. a rollout record in `WORKLOG.md`,
5. hardware-facing proof that both panels render.

## Stop conditions

You may stop only when one of these is true:

1. all implementation, validation, and rollout acceptance criteria are satisfied,
2. you are blocked by a real hardware limitation that prevents the final rollout check.

If blocked by hardware, you must still finish the code and tests, then leave an explicit operator runbook for the remaining verification steps. That runbook must include log checks plus direct visual confirmation of the new ePaper panel.