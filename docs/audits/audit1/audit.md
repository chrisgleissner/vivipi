# Audit 1

Date: 2026-04-05

Scope reviewed:

- `README.md`
- `AGENTS.md`
- `docs/spec.md`
- `config/build-deploy.yaml`
- `config/checks.yaml`
- `build`
- `firmware/main.py`
- `src/vivipi/core/*`
- `src/vivipi/services/adb_service.py`
- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`

Docs corrected in this audit:

- `README.md` now distinguishes implemented scaffolded pieces from missing device/runtime work.
- `README.md` now documents the real build outputs, release assets, and the fact that `./build deploy` does not flash hardware.
- `README.md` now warns that the checked-in service URL uses loopback and is not suitable for a real Pico deployment.
- `AGENTS.md` now reflects the actual `./build` commands and the current state of the firmware/runtime.

## Remaining issues

### 1. Device runtime is still a stub

Severity: high

Evidence:

- `firmware/main.py` only loads `config.json` and prints the configured board name.
- There is no SH1107 driver, SPI framebuffer upload, button GPIO polling, or Wi-Fi bootstrap in the repository.

Spec impact:

- Blocks `VIVIPI-ARCH-001`, `VIVIPI-RENDER-001`, `VIVIPI-INPUT-001`, and the hardware-facing part of the display requirements from working on a Pico 2W.

Recommended fix:

- Add a MicroPython runtime module that initializes SPI, drives the SH1107 OLED, reads buttons A and B, joins Wi-Fi, and applies rendered frames to the screen only on state or shift changes.

### 2. Periodic check execution is not implemented end-to-end

Severity: high

Evidence:

- The repo contains check definitions, state transitions, rendering, and service parsing.
- The repo does not contain an execution loop that periodically runs PING, REST, or SERVICE checks and feeds results into application state.

Spec impact:

- Blocks `VIVIPI-CHECK-001`, `VIVIPI-CHECK-002`, `VIVIPI-CHECK-TIME-001`, and the runtime portions of `VIVIPI-ARCH-001`.

Recommended fix:

- Add a scheduler/check runner layer that validates interval and timeout settings, performs periodic polling, timestamps observations, and updates selected and visible state deterministically.

### 3. Sample service URL is not reachable from a real Pico 2W

Severity: high

Evidence:

- `config/build-deploy.yaml` sets `service.base_url` to `http://127.0.0.1:8080/checks`.
- `config/checks.yaml` sets the sample SERVICE check target to `http://127.0.0.1:8080/checks`.
- On the Pico, `127.0.0.1` refers to the device itself, not the host running the ADB service.

Spec impact:

- Prevents the default SERVICE integration from working in a real Wi-Fi deployment.

Recommended fix:

- Replace loopback defaults with an explicit host or IP placeholder, or derive the service endpoint from a deployment variable that points to a host reachable from the device.

### 4. `./build deploy` is packaging-only, not deployment

Severity: medium

Evidence:

- The `deploy` command in `build` only unzips `artifacts/release/vivipi-firmware-bundle.zip` into a target directory.
- It does not flash MicroPython, copy files to the Pico, or interact with USB or serial tooling.

Spec impact:

- Does not meet user expectations for build-and-deploy automation on actual hardware.

Recommended fix:

- Either rename the command to something like `extract-bundle`, or implement real deployment through a supported tool such as `mpremote` and document the supported flash path.

### 5. Release assets are not yet sufficient for turnkey hardware setup

Severity: medium

Evidence:

- The release workflow publishes Python distributions, the MicroPython source bundle, and config templates.
- It does not publish a flash-ready UF2, a pinned MicroPython firmware image, or a fully device-ready deployment package.

Spec impact:

- Leaves manual work for end users before they can run ViviPi on hardware.

Recommended fix:

- Decide on the supported installation flow and publish the necessary release artifacts for it. For example: pinned Pico 2W MicroPython firmware reference, deployment instructions, and a directly copyable device filesystem bundle.

### 6. Diagnostics view has rendering support but no producer path

Severity: medium

Evidence:

- `src/vivipi/core/render.py` renders diagnostics rows.
- `src/vivipi/core/state.py` can switch into diagnostics mode.
- No runtime code populates diagnostics messages from network, service, or hardware failures.

Spec impact:

- Leaves `VIVIPI-UX-DIAG-001` incomplete in the real product flow.

Recommended fix:

- Define a compact diagnostics event schema and wire runtime failures into it so diagnostics mode can be entered with meaningful, short, structured messages.

### 7. Build help text formatting is inconsistent

Severity: low

Evidence:

- In `build`, the `render-config` and `build-firmware` lines are indented differently from the rest of the usage block.

Spec impact:

- No product impact, but it makes the CLI help look less intentional.

Recommended fix:

- Normalize the command indentation in the usage output.