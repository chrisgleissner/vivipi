# ViviPi Multi-Device Build And Deploy Specification

Date: 2026-05-13
Status: Proposed
Scope: host-side configuration, artifact generation, provisioning, and deployment for multiple Raspberry Pi Pico devices with different ViviPi configs and different display types.

## Summary

ViviPi already supports multiple display backends at runtime through `device.display.type`, but the current host workflow still assumes exactly one config, one staged filesystem tree, and one deploy target.

This spec extends the host build and deploy path so one invocation can:

1. resolve multiple named Pico devices from one config file,
2. render one `config.json` per device,
3. build isolated firmware artifacts per device,
4. deploy to multiple boards concurrently,
5. handle mixed device states, including already-provisioned MicroPython boards and blank BOOTSEL boards,
6. preserve the current firmware runtime model: one Pico, one display, one `config.json`, deterministic rendering, event-driven updates, and identity-based selection.

The key point is that this is primarily a host-tooling feature. The firmware should remain single-device and thin. The existing display registry and display backends are already sufficient for the two attached hardware variants.

## Research Method

This spec is based on four sources:

1. current ViviPi host tooling and config code,
2. current firmware display and runtime code,
3. current unit tests and release/install documentation,
4. direct inspection of the two attached Pico boards plus vendor documentation for the Waveshare OLED and ePaper modules.

## Current State Findings

### 1. Build and deploy are still single-device

Current host tooling normalizes exactly one `device.display` mapping, renders exactly one runtime config, stages exactly one `vivipi-device-fs` tree, and deploys to exactly one resolved port.

Current evidence:

- `src/vivipi/tooling/build_deploy.py` loads one YAML config, writes one `config.json`, stages one `artifacts/release/vivipi-device-fs`, and deploys by copying that tree to one `mpremote` target.
- `build` still documents `./build deploy` as copying to the first connected Pico and accepts only one optional `--device-port` override.
- `README.md` and `.vscode/tasks.json` still frame deployment as single-board-first.

### 2. Runtime display support is already type-driven

The runtime is already in good shape for this feature:

- `src/vivipi/core/display.py` has a registry of supported display types.
- `firmware/displays/__init__.py` selects backends by normalized display type.
- `normalize_display_config()` infers controller, SPI mode, dimensions, default pins, brightness behavior, page interval, and ePaper busy pin support from `device.display.type`.

This means multi-device support does not require a new firmware display abstraction. It requires host-side orchestration that produces different per-device configs.

### 3. Current release flow already separates base UF2 provisioning from ViviPi file deployment

The repo already distinguishes two different operations:

- install a base MicroPython UF2 on a blank board,
- copy the ViviPi filesystem onto a board that already runs MicroPython.

Current evidence:

- `README.md` documents `vivipi-device-filesystem-<version>.zip` as the ViviPi device update bundle.
- `README.md` documents `pico2w-micropython-<version>.txt` as the pinned bootstrap reference for blank boards.
- `AGENTS.md` explicitly states that `./build deploy` does not flash a UF2 image onto a blank board.

This separation is correct and should remain explicit in multi-device mode.

### 4. The attached hardware confirms the missing feature boundary

Observed from the current environment:

- one board is reachable over serial as a MicroPython device,
- one board is attached in BOOTSEL mass-storage mode and is not yet reachable via `mpremote`.

Observed identifiers:

- `mpremote connect list` reported `/dev/ttyACM0 ... MicroPython Board in FS mode`.
- `/dev/serial/by-id` exposed a stable symlink for that board.
- `mpremote connect /dev/ttyACM0 fs ls :` succeeded, which confirms explicit per-port deployment works today.
- `lsusb` also reported `2e8a:000f Raspberry Pi RP2350 Boot`.
- `/dev/disk/by-id` exposed a stable BOOTSEL disk path for that second board.

Conclusion:

Multi-device support must handle at least these four device states:

- `serial-ready`: MicroPython is running and `mpremote` can deploy.
- `bootsel`: the board is attached but only exposes the UF2 mass-storage interface.
- `missing`: no matching selector is present.
- `ambiguous`: more than one connected device matches the same selector.

### 5. The ePaper display type is already supported by the current codebase

Direct host dry-run result:

- replacing the display type in a temporary config with `waveshare-pico-epaper-2.13-b-v4` succeeded for both `render-config` and `build-firmware` once the sibling checks file was present.

Current unit tests also already cover:

- ePaper display type normalization,
- tri-color ePaper buffer generation,
- backend selection for the 2.13 inch tri-color panel.

Conclusion:

The missing capability is not ePaper support itself. The missing capability is building and deploying multiple device-specific bundles from one invocation.

## Hardware-Specific Findings That Affect The Spec

### Waveshare Pico-OLED-1.3

Vendor and repo-aligned facts:

- controller: SH1107
- transport: 4-wire SPI
- SPI pins: `DIN=GP11`, `CLK=GP10`, `CS=GP9`, `DC=GP8`, `RST=GP12`
- protocol: SPI mode 3
- current ViviPi default column offset for this panel: `32`
- buttons: `KEY0=GP15`, `KEY1=GP17`

This matches the current ViviPi defaults and the existing working board.

### Waveshare Pico-ePaper-2.13-B

Vendor and repo-aligned facts:

- canonical ViviPi display type: `waveshare-pico-epaper-2.13-b-v4`
- display size: `250 x 122`
- colors: black, white, red
- transport: SPI mode 0
- SPI pins: `DIN=GP11`, `CLK=GP10`, `CS=GP9`, `DC=GP8`, `RST=GP12`, `BUSY=GP13`
- vendor full refresh time: about `15s`
- vendor recommended refresh interval: at least `180s`
- vendor guidance: the panel should be put to sleep when not actively refreshing, and repeated rapid refreshes should be avoided

Current ViviPi alignment:

- `src/vivipi/core/display.py` already defaults ePaper page rotation to `180s` for the 2.13 inch tri-color panel.
- `firmware/displays/waveshare_epaper.py` already uses the busy pin, performs a full refresh, and then enters sleep.

Spec consequence:

The multi-device deploy flow must avoid repeated automatic resets or verification loops on ePaper devices because each reset can trigger another full-screen update after boot.

## Goals

1. Support a single config file that can define multiple named Pico devices.
2. Allow per-device overrides for any existing build/deploy setting, including display type, checks file, service URL, buttons, and deploy selector.
3. Build isolated artifacts per device without cross-talk.
4. Deploy to multiple devices concurrently with explicit, stable selectors.
5. Support mixed device states: MicroPython serial-ready and BOOTSEL-only.
6. Keep firmware runtime behavior single-device and deterministic.
7. Preserve current single-device configs and commands as a compatible subset.

## Non-Goals

1. Do not make one Pico drive multiple displays.
2. Do not add multi-device coordination logic to the on-device runtime.
3. Do not change probe scheduling semantics in `probe_schedule`; this feature is about host-side build/deploy concurrency, not runtime network concurrency.
4. Do not redesign the display registry; use the existing type-driven display definitions.
5. Do not silently flash blank boards during ordinary single-device deploy unless the user explicitly requests provisioning behavior.

## Proposed Configuration Model

### Backward compatibility rule

Existing single-device configs remain valid and unchanged.

- If `devices` is absent, the file behaves exactly as it does today.
- If `devices` is present, the existing top-level keys act as defaults for each named device.

This keeps the current config shape intact and avoids forcing all existing users to migrate. A separate `targets:` sub-node is not necessary here because this spec does not define any shared multi-device metadata that needs its own nesting layer.

### New config shape

```yaml
project:
  name: vivipi

device:
  board: pico2w
  micropython:
    version: 1.25.0
    download_page: https://micropython.org/download/RPI_PICO2_W/
  buttons:
    a: GP15
    b: GP17
  display:
    mode: standard
    columns: 1
    column_separator: " "

wifi:
  ssid: ${VIVIPI_WIFI_SSID}
  password: ${VIVIPI_WIFI_PASSWORD}

service:
  default_prefix: adb
  base_url: ${VIVIPI_SERVICE_BASE_URL}
  syslog:
    port: 514

check_state:
  failures_to_degraded: 1
  failures_to_failed: 2
  successes_to_recover: 1
  visible_degraded: false

probe_schedule:
  allow_concurrent_hosts: false
  allow_concurrent_same_host: false
  same_host_backoff_ms: 500
  interval_grace_ms: 1000

checks_config: checks.local.yaml

devices:
  oled-lab:
    selector:
      serial_by_id: /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_740c0800366c92bb-if00
    device:
      display:
        type: waveshare-pico-oled-1.3
        page_interval: 15s

  epaper-lab:
    selector:
      bootsel_disk: /dev/disk/by-id/usb-RPI_RP2350_AAAE129318B6B9B6-0:0
      serial_by_id: /dev/serial/by-id/usb-MicroPython_Board_*_epaper
    bootstrap:
      uf2_url: https://micropython.org/resources/firmware/RPI_PICO2_W-20250415-v1.25.0.uf2
      serial_timeout_s: 30
    device:
      display:
        type: waveshare-pico-epaper-2.13-b-v4
        page_interval: 180s
    checks_config: checks.epaper.yaml
```

### Merge semantics

For each named device:

1. start from the full current top-level config, excluding `devices`,
2. deep-merge the device mapping into that default mapping,
3. replace scalar values and lists instead of merging them,
4. resolve environment placeholders after the merge,
5. run the existing normalization and validation logic on the resolved device config.

This preserves current validation behavior, especially for display inference, service URL reachability, and check parsing.

### Selector rules

Each `devices.<id>.selector` must contain at least one stable selector.

Supported selectors for the first implementation:

- `serial_by_id`: preferred stable serial symlink for `mpremote`
- `port`: explicit serial path such as `/dev/ttyACM0`
- `bootsel_disk`: stable mass-storage path for UF2 flashing

Validation rules:

- `auto` is forbidden in `devices` because it is ambiguous with more than one board attached.
- duplicate selectors across devices are a configuration error.
- a device with `bootsel_disk` but no `bootstrap.uf2_url` or `bootstrap.uf2_path` cannot be provisioned automatically.

### Bootstrap rules

`bootstrap` is optional and only matters when a device may appear in BOOTSEL mode.

Supported fields for the first implementation:

- `uf2_url`: download the base MicroPython UF2 when provisioning is requested
- `uf2_path`: use a local UF2 file instead of downloading
- `serial_timeout_s`: how long to wait for the board to re-enumerate as a serial device after UF2 copy

Design choice:

Keep the existing official MicroPython download page as the default source of truth, but allow per-device override of the actual UF2 source. This is necessary because Waveshare documentation for RP2350-based boards warns that some firmware combinations may require a vendor-specific UF2.

## Artifact Model

### Required output layout

Single-device mode remains unchanged.

Multi-device mode must write per-device outputs under a dedicated root, for example:

```text
artifacts/release/devices/
  manifest.json
  oled-lab/
    config.json
    vivipi-device-fs/
    vivipi-device-filesystem-0.6.1.zip
    pico2w-micropython-0.6.1.txt
  epaper-lab/
    config.json
    vivipi-device-fs/
    vivipi-device-filesystem-0.6.1.zip
    pico2w-micropython-0.6.1.txt
```

Why this is required:

- current `artifacts/release/vivipi-device-fs` is a shared mutable path and would race immediately under concurrent builds,
- each device needs its own rendered `config.json`,
- per-device manifests make it possible to confirm which config and display type were deployed.

### Devices manifest

`artifacts/release/devices/manifest.json` should include, per device:

- device id
- resolved display type
- checks file path
- selected serial selector and BOOTSEL selector
- config hash
- artifact paths
- build timestamp
- build result status

This manifest is the host-side source of truth for deployment summaries and later debugging.

## Command Surface

### Build wrapper

Extend `./build` with these behaviors:

1. `./build list-devices`
   - show configured devices and observed state: `serial-ready`, `bootsel`, `missing`, `ambiguous`
2. `./build render-config --device <id>`
   - render only one device's `config.json`
3. `./build build-firmware --device <id>`
   - build one device bundle
4. `./build build-firmware --all-devices`
   - build all devices concurrently
5. `./build provision --device <id>` or `--all-devices`
   - copy base UF2 to BOOTSEL devices only
6. `./build deploy --device <id>`
   - deploy one device over `mpremote`
7. `./build deploy --all-devices`
   - deploy all serial-ready devices concurrently
8. `./build deploy --all-devices --provision-missing`
   - provision BOOTSEL devices first when bootstrap metadata is present, then deploy the ViviPi filesystem after serial re-enumeration

### Underlying Python CLI

Mirror the same capability in `vivipi.tooling.build_deploy` with `--device`, `--all-devices`, and `--jobs` options.

### Worker model

- build jobs may run concurrently once device outputs are isolated,
- deploy jobs may run concurrently per device,
- the default worker count should be bounded, for example `min(number_of_devices, 4)`, and overridable with `--jobs`.

### Logging

Every line emitted by a concurrent worker must be prefixed with the device id so mixed logs stay readable.

Example:

```text
[oled-lab] rendered config
[oled-lab] deployed via /dev/serial/by-id/...
[epaper-lab] detected BOOTSEL disk
[epaper-lab] copied UF2
[epaper-lab] waiting for serial re-enumeration
```

## Discovery And State Resolution

### Device resolution state machine

Each named device resolves independently to one of four states:

1. `serial-ready`
   - a matching serial selector is present and `mpremote` can address it
2. `bootsel`
   - no serial selector is present, but a matching BOOTSEL disk is present
3. `missing`
   - neither serial nor BOOTSEL selector is present
4. `ambiguous`
   - more than one matching serial or BOOTSEL device is present

### Deploy rules by state

- `serial-ready`: deploy may proceed immediately.
- `bootsel`: deploy must fail with a clear message unless `--provision-missing` or `provision` is used.
- `missing`: fail that device and continue the rest when running `--all-devices`.
- `ambiguous`: fail before any file copy starts.

### Provisioning flow

When provisioning is requested for a `bootsel` device:

1. copy the configured UF2 onto the BOOTSEL disk,
2. wait for the BOOTSEL disk to disappear,
3. wait for the configured serial selector to appear,
4. confirm `mpremote fs ls :` succeeds,
5. continue with normal ViviPi filesystem deployment.

## Firmware And Runtime Impact

### Required runtime changes

The first implementation should keep runtime changes minimal.

Required change:

- include `project.device_id` in the generated `config.json` when building from a named device entry.

Optional but useful later:

- include `project.device_label` for operator-facing diagnostics.

### Explicit non-change

Do not add multi-device branching to `firmware/main.py`, `firmware/runtime.py`, or the display backends.

Each device still boots one config and one display. The multi-device behavior belongs in host tooling and in pure CPython-testable config/orchestration code.

## EPaper-Specific Safety Rules

These rules are required for implementation, not optional polish:

1. keep the current ePaper default `page_interval` behavior; if omitted, it must still resolve to the display registry default,
2. warn when a device with `family == eink` sets `page_interval` below `180s`,
3. do not issue repeated verification resets to an ePaper device,
4. after successful deploy, perform at most one post-copy reset per ePaper device,
5. verify with a lightweight `mpremote` command instead of repeated screen activity.

This aligns the multi-device flow with both vendor guidance and the current ePaper backend behavior.

## Failure Model

### Per-device isolation

Failure on one device must not invalidate already-built or already-deployed sibling devices.

Examples:

- if `epaper-lab` is still in BOOTSEL and `--provision-missing` is not set, `oled-lab` should still build and deploy successfully,
- if `oled-lab` deploy succeeds and `epaper-lab` times out waiting for serial re-enumeration, the overall command should return non-zero, but it must report both per-device outcomes clearly.

### Exit code policy

- single-device mode: preserve current success/failure semantics,
- multi-device mode: return success only if all selected devices succeed,
- multi-device mode should still attempt all selected devices unless `--fail-fast` is explicitly requested.

## Implementation Placement

To keep logic testable and aligned with repo guidance:

1. add pure config and device-resolution code in `src/vivipi/core`,
2. keep CLI argument parsing and subprocess orchestration in `src/vivipi/tooling/build_deploy.py`,
3. keep firmware entrypoints thin and unchanged except for optional device metadata injection.

Suggested new modules:

- `src/vivipi/core/multi_device_config.py` for merge, validation, and selector normalization
- `src/vivipi/core/device_inventory.py` for Linux device discovery and state resolution

The exact filenames can change, but the separation should remain.

## Acceptance Criteria

Implementation is complete only when all of the following are true:

1. existing single-device configs still pass unchanged,
2. one config file can define at least two named devices with different display types,
3. `build-firmware --all-devices` produces isolated per-device artifacts with distinct `config.json` files,
4. `list-devices` reports the attached OLED board as `serial-ready` and the blank RP2350 board as `bootsel`,
5. `deploy --all-devices` without provisioning refuses only the BOOTSEL device and still deploys serial-ready devices,
6. `deploy --all-devices --provision-missing` can provision and then deploy BOOTSEL devices when bootstrap metadata is present,
7. device selectors are stable and do not rely on `auto`,
8. per-device results are clearly summarized at the end of the command,
9. the implementation preserves or exceeds the repo branch coverage gate,
10. hardware proof for the implementation pass shows both attached boards rendering ViviPi on their own panels after deploy.
11. rollout verification for the new ePaper device includes both boot/deploy log review and direct confirmation that the panel itself renders ViviPi content, because the display path on that board is not yet proven in this repo.

## Required Test Coverage

At minimum, add focused tests for:

1. device default merge semantics,
2. rejection of duplicate or ambiguous selectors,
3. rejection of `auto` in `devices` selectors,
4. per-device artifact directory isolation,
5. concurrent build path not reusing shared mutable output paths,
6. deploy behavior for `serial-ready`, `bootsel`, `missing`, and `ambiguous` states,
7. provisioning flow from BOOTSEL disk to serial re-enumeration,
8. summary and exit-code behavior when one device fails and another succeeds,
9. ePaper warning behavior for aggressive refresh intervals,
10. retention of current single-device tests.

## Recommended Rollout Plan

### Phase 1

- add multi-device config parsing and validation
- add `list-devices`
- add per-device `render-config` and `build-firmware`

### Phase 2

- add concurrent `deploy` for already-provisioned serial devices
- add per-device status reporting and artifact manifests

### Phase 3

- add explicit `provision` support for BOOTSEL devices
- add `deploy --provision-missing`

### Phase 4

- add hardware acceptance coverage for the OLED and 2.13 inch ePaper boards
- during rollout, inspect device logs for display-init, busy-pin, or boot errors and verify the new ePaper panel itself renders live ViviPi output rather than relying on serial-only success
- document stable selector setup in `README.md` and `config/build-deploy.local.example.yaml`

## Decisions Locked By This Spec

1. Multi-device support is a host-tooling feature first, not a firmware architecture rewrite.
2. The existing top-level config remains the default layer for entries under `devices:`.
3. Stable selectors are mandatory in multi-device mode; `auto` is not acceptable.
4. Provisioning blank boards remains explicit and separately modeled from normal filesystem deploy.
5. ePaper devices get stricter deploy safety because their hardware refresh characteristics are materially different from the OLED path.

## Open Decision To Confirm During Implementation

The attached ePaper board is currently blank and visible as an RP2350 BOOTSEL disk. The implementation should keep official MicroPython as the default bootstrap source, but it must allow per-device UF2 override because Waveshare documentation for Pico2-class boards warns that some hardware combinations may require a vendor-specific RP2350 MicroPython build.

That decision does not block implementation of the multi-device framework. It only affects the default provisioning source chosen for first-time bring-up on certain boards.