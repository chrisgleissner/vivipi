# ViviPi

[![Build](https://github.com/chrisgleissner/vivipi/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/chrisgleissner/vivipi/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/chrisgleissner/vivipi/graph/badge.svg)](https://codecov.io/gh/chrisgleissner/vivipi)
[![Hardware](https://img.shields.io/badge/hardware-Raspberry%20Pi%20Pico-blue)](https://github.com/chrisgleissner/vivipi/releases)
[![Runtime](https://img.shields.io/badge/runtime-MicroPython%20%7C%20Python-blue)](https://github.com/chrisgleissner/vivipi)

ViviPi is a calm, glanceable monitoring system for a [Raspberry Pi Pico 2W](https://pip-assets.raspberrypi.com/categories/1088-raspberry-pi-pico-2-w/documents/RP-008304-DS-2-pico-2-w-datasheet.pdf?disposition=inline) paired with a [128x64 monochrome OLED](https://www.waveshare.com/wiki/Pico-OLED-1.3) with a SH1107 display driver.

> [!WARNING]
> ViviPi is under heavy development. Some of the features mentioned below are not yet implemented.

Current architecture:

- Pure core in `src/vivipi/core` for rendering, scheduling, state reduction, diagnostics, and selection semantics
- CPython-testable runtime orchestration in `src/vivipi/runtime`
- Host-side services in `src/vivipi/services`
- Thin device adapters in `firmware/` for SH1107 output, button polling, and Wi-Fi bootstrap
- Build and release tooling in `src/vivipi/tooling`

## Features

- Pure, deterministic 16x8 character-grid rendering aligned to the product spec
- Identity-based selection, pagination, detail-view layout, and burn-in shift logic
- YAML-defined check configuration for `PING`, `REST`, and `SERVICE` checks
- Default host-side Vivi Service that exposes connected ADB devices as service checks
- Single-entrypoint build, test, coverage, packaging, and release commands via `./build`
- CI and release automation with branch coverage enforcement and Codecov upload

## Implemented Today

- 16x8 character-grid rendering model, including idle, overview, detail, and diagnostics frames
- Identity-based selection and page visibility logic
- Input debounce and auto-repeat behavior in the pure core
- Deterministic due-check scheduling and execution orchestration for `PING`, `REST`, and `SERVICE`
- Runtime diagnostics pipeline that produces compact diagnostics rows and activates diagnostics mode on runtime faults
- Config loading and runtime-config rendering from YAML and environment placeholders
- Thin firmware adapters for SH1107 rendering, button polling, and Wi-Fi bootstrap
- `./build deploy` support via `mpremote` for copying the built device filesystem to a Pico 2W
- Default Vivi Service exposing ADB-connected devices as service checks over HTTP
- CI with tests, branch-coverage enforcement, Codecov upload, and tagged release publishing
- Release assets for the device filesystem bundle plus a pinned MicroPython download reference

## Remaining Risks

- Physical Pico 2W validation is still required for the on-device `uping`, `urequests`, and SH1107 stack
- `./build deploy` copies files with `mpremote`; it does not flash a UF2 image onto a blank board
- The supported install flow still depends on a reachable host service address and working Wi-Fi credentials

The firmware entrypoint in `firmware/main.py` now delegates to the runtime loop in `firmware/runtime.py`.

## Hardware Target

- Board: Raspberry Pi Pico 2W
- Display controller: SH1107
- Resolution: 128x64 monochrome
- Character grid: 16 columns x 8 rows using an 8x8 fixed bitmap cell model
- Interface: 4-wire SPI, mode 3

### Pin Mapping

| Signal | GPIO |
|--------|------|
| DIN    | GP11 |
| CLK    | GP10 |
| CS     | GP9  |
| DC     | GP8  |
| RST    | GP12 |
| BTN A  | GP14 |
| BTN B  | GP15 |

## Quick Start

Use this flow for local development and packaging. It validates the pure core, runs the default ADB-backed service, and produces the current firmware bundle.

Requirements:

- Python 3.12+
- `python3 -m venv`
- `adb` if you want to use the default service against connected Android devices
- `mpremote` if you want `./build deploy` to copy files onto a Pico 2W

1. Set Wi-Fi credentials and an explicit host-side service URL that the Pico can reach.

```bash
export VIVIPI_WIFI_SSID="your-wifi-name"
export VIVIPI_WIFI_PASSWORD="your-wifi-password"
export VIVIPI_SERVICE_BASE_URL="http://192.168.1.10:8080/checks"
```

2. Install dependencies and run the local validation path.

```bash
./build ci
```

3. Start the default Vivi Service if you want the sample SERVICE check to return live ADB device data.

```bash
./build service --host 0.0.0.0 --port 8080
```

4. Build the current firmware bundle and filesystem assets.

```bash
./build build-firmware
```

5. Flash MicroPython onto the Pico 2W if the board is blank.

Use the pinned reference written to `artifacts/release/pico2w-micropython.txt`, or the matching asset from a GitHub release, to pick the correct Pico 2W download page.

6. Deploy the built device filesystem onto the Pico.

```bash
./build deploy --device-port /dev/ttyACM0
```

7. If you want a rendered runtime config without building the full bundle, generate it directly.

```bash
./build render-config
```

`./build deploy` copies the prepared filesystem onto the board with `mpremote`. It does not flash the MicroPython UF2 itself.

## Build Tooling

The `./build` script is the canonical entrypoint.

### Common commands

```bash
./build install
./build lint
./build test
./build coverage
./build ci
./build render-config
./build build-firmware
./build release-assets
./build deploy --device-port /dev/ttyACM0
./build service --host 0.0.0.0 --port 8080
```

Generated artifacts are written under `artifacts/`.

Important behavior notes:

- `./build render-config` writes `artifacts/device/config.json`
- `./build build-firmware` writes `vivipi-firmware-bundle.zip`, `vivipi-device-filesystem.zip`, `pico2w-micropython.txt`, and the unpacked `vivipi-device-fs/` tree under `artifacts/release`
- `./build deploy` copies the unpacked `vivipi-device-fs/` tree onto the Pico with `mpremote`

## Running the Default Vivi Service

The default service discovers connected ADB devices and exposes them as monitoring checks.

```bash
./build service --host 0.0.0.0 --port 8080
```

The HTTP endpoint implementation lives in `src/vivipi/services/adb_service.py`.

The sample SERVICE check is preconfigured in `config/checks.yaml`.

## Configuration Model

### Build and Deployment

`config/build-deploy.yaml` is the build-time source of truth for:

- board and display metadata
- Wi-Fi credentials
- service endpoint defaults
- the path to the checks config

Wi-Fi credentials are injected via environment variables:

```yaml
wifi:
  ssid: ${VIVIPI_WIFI_SSID}
  password: ${VIVIPI_WIFI_PASSWORD}
```

Important:

- `service.base_url` is resolved from `VIVIPI_SERVICE_BASE_URL`
- The sample `SERVICE` check target in `config/checks.yaml` uses the same value
- The value must point to a host address reachable from the Pico over Wi-Fi; do not use `127.0.0.1`

### Checks

`config/checks.yaml` defines build-time checks.

Supports:

- PING
- REST
- SERVICE endpoints

## Testing and Quality Gates

- Unified entrypoint: `./build`
- Test framework: `pytest`
- Coverage requirement: `>= 91%` branch coverage
- Linting: `ruff`
- CI runs on Python 3.12 and 3.13
- CI verifies runtime-config rendering and firmware packaging through `./build ci`

## Release Artifacts

Tagging with a x.y.z version triggers a release containing:

- Python wheel and source distribution
- Zipped MicroPython source bundle containing the firmware files, `config.json`, and the `vivipi` package
- Zipped device filesystem bundle ready for `mpremote fs cp`
- `pico2w-micropython.txt` with the supported Pico 2W MicroPython download reference
- Sample configuration files

The release workflow does not produce a UF2 image; it publishes the filesystem assets plus the pinned download reference used by the supported install flow.

## Repository Layout

```text
config/                  Build-time configuration
docs/                    Specification, traceability, and audits
firmware/                MicroPython entrypoints
src/vivipi/core/         Pure application logic and rendering model
src/vivipi/services/     Host-side services
src/vivipi/tooling/      Build and deploy logic
tests/                   All test suites
```
