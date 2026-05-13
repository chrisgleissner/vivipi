# Multi-Device Build And Deploy — Implementation Plan

Primary spec: [spec.md](./spec.md)

## Objective

Implement the multi-device host workflow described in [spec.md](./spec.md) so ViviPi can build, provision, and deploy multiple named Pico devices with different configs and display types, while keeping the firmware runtime single-device and deterministic.

The first implementation target is the currently attached pair:

1. a known-working Waveshare Pico-OLED-1.3 device,
2. a newly attached Waveshare Pico-ePaper-2.13-B device whose display path is not yet proven in this repo.

## Current verified facts

- Current host tooling is single-device: one rendered `config.json`, one staged `vivipi-device-fs`, one deploy port.
- The OLED board is currently reachable over serial as a MicroPython device.
- The new RP2350-based board is currently visible in BOOTSEL mass-storage mode and not yet reachable through `mpremote`.
- The 2.13 inch tri-color ePaper display type already exists in the display registry and current host build dry-runs accepted it.
- The ePaper hardware path itself has not yet been proven on a real deployed board in this repo.

## Non-negotiable constraints

1. `docs/spec.md` remains the product source of truth.
2. Keep business logic in `src/vivipi/core` where possible.
3. Keep MicroPython-facing runtime glue thin; do not move fleet-style orchestration into firmware.
4. Preserve deterministic rendering, event-driven updates, and identity-based selection.
5. Do not regress the current single-device config workflow.
6. Keep stable selectors mandatory in multi-device mode; do not rely on `auto`.
7. Completion requires hardware-facing verification for both attached boards, including direct confirmation that the new ePaper display itself works.

## Implementation principle

Start from the current single-device host path and generalize the smallest possible abstraction layers:

1. config expansion into per-device resolved configs,
2. isolated per-device artifact staging,
3. per-device discovery and state resolution,
4. per-device deploy/provision orchestration,
5. rollout verification with log evidence and direct display proof.

Do not redesign unrelated runtime or display code.

## Phase 0 — Preflight and discovery

Goal: lock the current hardware and toolchain baseline before editing code.

Tasks:

1. Reconfirm current device inventory with explicit selectors:
   - reachable OLED serial path
   - ePaper BOOTSEL disk path
2. Reconfirm the existing single-device build/deploy path still works for the OLED board.
3. Record the current ePaper unknowns:
   - whether the board can be provisioned cleanly from UF2
   - whether it re-enumerates as a MicroPython serial device
   - whether the display initializes without busy-pin or refresh failures
   - whether the panel visibly renders ViviPi output

Exit criteria:

- known selectors captured for both attached devices,
- current OLED baseline reverified,
- ePaper uncertainty explicitly logged as an implementation risk, not an ignored assumption.

## Phase 1 — Multi-device config expansion

Goal: parse and validate a direct `devices:` mapping while preserving current single-device configs.

Tasks:

1. Add pure config expansion logic that:
   - treats top-level config as the default layer,
   - expands each `devices.<id>` entry into a resolved per-device config,
   - rejects `auto` in per-device selectors,
   - rejects duplicate selectors across devices.
2. Preserve current placeholder resolution, display normalization, service URL validation, and check parsing.
3. Add tests for:
   - single-device compatibility,
   - per-device override merge behavior,
   - selector validation failures,
   - ambiguous or duplicate selector rejection.

Exit criteria:

- one config file can resolve at least two named devices into independent validated configs.

## Phase 2 — Per-device artifact isolation

Goal: stop sharing mutable build outputs across devices.

Tasks:

1. Extend the host build path so each named device gets its own:
   - rendered `config.json`
   - staged `vivipi-device-fs`
   - zipped device filesystem artifact
   - install manifest
2. Add a devices manifest under `artifacts/release/devices/manifest.json`.
3. Keep single-device output behavior unchanged when `devices:` is absent.
4. Add tests for:
   - per-device output directories,
   - artifact manifest content,
   - no shared staging directory reuse in multi-device mode.

Exit criteria:

- `build-firmware --all-devices` produces isolated artifacts for at least the OLED and ePaper device definitions.

## Phase 3 — Device inventory and state resolution

Goal: resolve each named device into `serial-ready`, `bootsel`, `missing`, or `ambiguous`.

Tasks:

1. Implement Linux-side inventory resolution for:
   - serial-by-id selectors,
   - explicit serial ports,
   - BOOTSEL disk selectors.
2. Add `list-devices` output with per-device state.
3. Add tests for:
   - serial-ready detection,
   - BOOTSEL-only detection,
   - missing device detection,
   - ambiguous match failure.

Exit criteria:

- `list-devices` can correctly classify the current OLED board and the current BOOTSEL ePaper board.

## Phase 4 — Per-device deploy and provisioning

Goal: support concurrent per-device deploy and explicit provisioning from BOOTSEL.

Tasks:

1. Add per-device deploy commands for already provisioned serial devices.
2. Add explicit provisioning for BOOTSEL devices using configured UF2 source.
3. Add `deploy --all-devices` and `deploy --all-devices --provision-missing`.
4. Keep failure isolated per device and summarize all outcomes at the end.
5. Add tests for:
   - deploy success on serial-ready device,
   - refusal on BOOTSEL without provisioning,
   - successful provision-then-deploy path,
   - partial success reporting when one device fails.

Exit criteria:

- host tooling can deploy the OLED device directly,
- host tooling can provision then deploy the ePaper device when bootstrap metadata is present.

## Phase 5 — Rollout verification and hardware bring-up

Goal: verify the implementation on the actual attached boards, not just in tests.

This phase is mandatory because the new ePaper device is still partially unknown.

Tasks:

1. Deploy the OLED board and confirm the known-working display still renders ViviPi.
2. Provision the ePaper board if needed, then deploy ViviPi to it.
3. Inspect serial and deploy logs for both devices, explicitly looking for:
   - boot failures
   - display initialization failures
   - ePaper busy-pin timeouts or refresh stalls
   - boot loops after reset
   - config mismatch or selector mismatch warnings
4. Verify the new ePaper panel itself, directly, not just via serial:
   - the panel initializes,
   - the panel refreshes,
   - the panel renders live ViviPi output,
   - the rendered output is legible and stable.
5. Capture hardware-facing proof for both boards.

Decision rule:

- serial-only success is insufficient for the ePaper device,
- absence of log errors is insufficient if the panel does not visibly render,
- visible rendering with repeated busy-pin or init errors is also not acceptable.

Exit criteria:

- both boards render ViviPi on real hardware,
- rollout logs are free of unresolved display-path errors,
- the new ePaper display path is proven working rather than assumed.

## Phase 6 — Documentation and closeout

Goal: leave the repo ready for continued implementation and future reuse.

Tasks:

1. Update user-facing docs if command surface or config examples change.
2. Record the exact selectors and rollout evidence in `WORKLOG.md`.
3. Keep `docs/spec-traceability.md` aligned if requirement coverage changes.
4. Confirm repository validation passes, including the branch coverage gate.

Exit criteria:

- code, tests, docs, and rollout evidence are internally consistent.

## Acceptance criteria

Implementation should not be considered complete until all of the following are true:

1. Existing single-device configs still work unchanged.
2. A direct `devices:` mapping can define at least two named devices with different display types.
3. Multi-device build output is isolated per device.
4. Multi-device deploy supports both serial-ready and BOOTSEL flows.
5. Device state resolution is explicit and stable.
6. Repository validation passes with coverage above the repo gate.
7. The OLED board still renders ViviPi correctly after the refactor.
8. The new ePaper board shows no unresolved display-path log errors during rollout.
9. The new ePaper panel is directly verified to render ViviPi output on hardware.

## Risks to watch

1. Shared artifact paths causing race conditions under concurrent builds.
2. Accidental dependence on `auto` in multi-device mode.
3. Provisioning that succeeds at UF2 copy but fails to re-enumerate as serial.
4. ePaper initialization succeeding in code but hanging on busy-pin waits on real hardware.
5. False positives from serial-only success when the panel itself is blank or garbled.

## Recommended execution order

1. Phase 0
2. Phase 1
3. Phase 2
4. Phase 3
5. Phase 4
6. Phase 5
7. Phase 6