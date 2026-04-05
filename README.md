# ViviPi

ViviPi is a calm, glanceable monitoring system for a Raspberry Pi Pico 2W driving a 128x64 monochrome OLED over SPI. This repository is scaffolded around MicroPython on-device, Python-based Vivi Services on the host, and a heavily tested pure-core layer so the product logic can be validated on normal CI runners before hardware is involved.

## What is scaffolded

- Pure rendering, state, selection, pagination, input, and pixel-shift logic under `src/vivipi/core`
- A default Vivi Service that exposes connected ADB devices as service checks under `src/vivipi/services`
- Build and deploy tooling that renders the runtime device config and packages a firmware bundle under `src/vivipi/tooling`
- CI and release workflows for linting, tests, coverage upload, Python package builds, and downloadable release assets
- Spec traceability so each requirement in `docs/spec.md` maps to tests

## Hardware target

- Board: Raspberry Pi Pico 2W
- Display controller: SH1107
- Display resolution: 128x64, monochrome, fixed 8x8 character grid
- Interface: 4-wire SPI, mode 3
- Pin map:
  - DIN: GP11
  - CLK: GP10
  - CS: GP9
  - DC: GP8
  - RST: GP12

## Quick start

```bash
export VIVIPI_WIFI_SSID="your-wifi-name"
export VIVIPI_WIFI_PASSWORD="your-wifi-password"
./build all
```

The top-level `./build` script is the canonical Linux entrypoint. Useful examples:

```bash
./build install
./build test
./build coverage
./build render-config
./build build-firmware
./build deploy --deploy-dir /tmp/vivipi-device
./build service --host 0.0.0.0 --port 8080
```

Generated local artifacts are written under `artifacts/`.

## Running the default Vivi Service

The default host-side service inspects all connected ADB devices and exposes them via the required service JSON schema.

```bash
./build service --host 0.0.0.0 --port 8080
```

The scaffolded build-time checks configuration already includes that service in `config/checks.yaml`.

## Configuration model

`config/build-deploy.yaml` is the build-and-deploy source of truth for the Pico 2W bundle. It includes Wi-Fi fields directly, but the actual values are resolved from environment variables so credentials do not need to live in git history.

```yaml
wifi:
  ssid: ${VIVIPI_WIFI_SSID}
  password: ${VIVIPI_WIFI_PASSWORD}
```

`config/checks.yaml` defines the monitored checks at build time, including direct `PING` and `REST` checks and `SERVICE` endpoints.

## Test and quality gates

- `./build` provides the standard local entrypoint for install, test, coverage, packaging, deploy, and service tasks
- `pytest` runs unit, contract, tooling, and traceability tests
- Coverage is enforced at `>= 91%` branch coverage
- `ruff check .` is run in CI
- The CI workflow also builds the firmware bundle to catch packaging regressions

## Release artifacts

Pushing a tag that matches `v*` triggers the release workflow. The GitHub release page will receive:

- The Python wheel and source distribution
- The zipped firmware bundle containing `boot.py`, `main.py`, `config.json`, and the `vivipi` package
- The checked-in sample config files used to bootstrap local builds

## Repository layout

```text
config/                  Build-time check and deploy configuration
docs/                    Product specification and traceability
firmware/                Thin MicroPython entrypoints
src/vivipi/core/         Pure application logic and rendering
src/vivipi/services/     Host-side service implementations
src/vivipi/tooling/      Build, bundle, and deploy helpers
tests/                   Unit, contract, tooling, and spec tests
```

## Agent docs

- Copilot: `.github/copilot-instructions.md`
- Shared repo guidance: `AGENTS.md`
- Claude notes: `CLAUDE.md`
- Codex notes: `CODEX.md`
- Delivery plan: `PLANS.md`
- Running log: `WORKLOG.md`

## Current scope boundary

This scaffold intentionally focuses on the testable core, service shape, build pipeline, and release automation. The final hardware integration loop for the SH1107 driver, button GPIO polling, Wi-Fi runtime, and on-device check execution still needs to be implemented on top of this structure.
