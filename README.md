# ViviPi

[![Build](https://github.com/chrisgleissner/vivipi/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/chrisgleissner/vivipi/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/chrisgleissner/vivipi/graph/badge.svg)](https://codecov.io/gh/chrisgleissner/vivipi)
[![Hardware](https://img.shields.io/badge/hardware-Raspberry%20Pi%20Pico-blue)](https://github.com/chrisgleissner/vivipi/releases)
[![Runtime](https://img.shields.io/badge/runtime-MicroPython%20%7C%20Python-blue)](https://github.com/chrisgleissner/vivipi)

ViviPi is a calm, glanceable monitoring system for a [Raspberry Pi Pico 2W](https://pip-assets.raspberrypi.com/categories/1088-raspberry-pi-pico-2-w/documents/RP-008304-DS-2-pico-2-w-datasheet.pdf?disposition=inline) paired with a [128x64 monochrome OLED](https://www.waveshare.com/wiki/Pico-OLED-1.3).

> [!WARNING]
> ViviPi is under heavy development. Some of the features mentioned below are not yet implemented.

Current architecture:

- Pure core in `src/vivipi/core` for rendering, selection, input semantics, pagination, and burn-in shift
- Host-side services in `src/vivipi/services`
- Firmware scaffold in `firmware/`
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
- Config loading and runtime-config rendering from YAML
- Default Vivi Service exposing ADB-connected devices as service checks over HTTP
- CI with tests, branch-coverage enforcement, Codecov upload, and tagged release publishing

## Not Yet Implemented

- SH1107 display driver and actual pixel output on the Pico 2W
- GPIO button polling and hardware debounce wiring
- Wi-Fi connection/bootstrap on-device
- Periodic PING, REST, and SERVICE check execution runtime
- End-to-end state-machine orchestration on device
- Diagnostics production pipeline
- Board flashing from the `./build deploy` command

The current firmware entrypoint in `firmware/main.py` only loads `config.json` and prints the configured board name.

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

## Quick Start

Use this flow for local development and packaging. It validates the pure core, runs the default ADB-backed service, and produces the current firmware bundle.

Requirements:

- Python 3.12+
- `python3 -m venv`
- `adb` if you want to use the default service against connected Android devices

1. Set Wi-Fi credentials for runtime-config generation.

```bash
export VIVIPI_WIFI_SSID="your-wifi-name"
export VIVIPI_WIFI_PASSWORD="your-wifi-password"
```

2. Install dependencies and run the local validation path.

```bash
./build ci
```

3. Start the default Vivi Service if you want the sample SERVICE check to return live ADB device data.

```bash
./build service --host 0.0.0.0 --port 8080
```

4. Build the current firmware bundle.

```bash
./build build-firmware
```

5. If you want a rendered runtime config without building the full bundle, generate it directly.

```bash
./build render-config
```

At the moment, `./build deploy` only extracts the generated bundle into a target directory. It does not flash files onto a Pico 2W.

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
./build deploy --deploy-dir /tmp/vivipi-device
./build service --host 0.0.0.0 --port 8080
```

Generated artifacts are written under `artifacts/`.

Important behavior notes:

- `./build render-config` writes `artifacts/device/config.json`
- `./build build-firmware` writes the release bundle under `artifacts/release`
- `./build deploy` currently extracts the bundle into a target directory; it does not flash the Pico

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

- The checked-in `service.base_url` default is `http://127.0.0.1:8080/checks`
- That loopback address is suitable for local host testing only
- For real Pico 2W deployment, replace it with a host address reachable over Wi-Fi

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

Tagging with `v*` triggers a release containing:

- Python wheel and source distribution
- Zipped MicroPython source bundle containing `boot.py`, `main.py`, `config.json`, and the `vivipi` package
- Sample configuration files

The current release workflow does not produce a UF2 image.

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
