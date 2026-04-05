# ViviPi

[![Build](https://github.com/chrisgleissner/vivipi/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/chrisgleissner/vivipi/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/chrisgleissner/vivipi/graph/badge.svg)](https://codecov.io/gh/chrisgleissner/vivipi)
[![Hardware](https://img.shields.io/badge/hardware-Raspberry%20Pi%20Pico-blue)](https://github.com/chrisgleissner/vivipi/releases)
[![Runtime](https://img.shields.io/badge/runtime-MicroPython%20%7C%20Python-blue)](https://github.com/chrisgleissner/vivipi)

ViviPi (pronounced “VEE-vee-pie”, from Latin *viv-* “to live”) is a minimal, glanceable monitoring system built on the Raspberry Pi Pico 2W, paired with a 128×64 monochrome OLED.

## What You Get

- Strict 16x8 rendering for idle, overview, detail, and diagnostics views
- Deterministic scheduling and execution for `PING`, `REST`, and `SERVICE`
- Compact runtime diagnostics and burn-in shift control
- `./build` commands for install, lint, test, coverage, packaging, deploy, and service hosting

## Hardware Target

- Board: Raspberry Pi Pico 2W
- Display: 128x64 monochrome OLED
- Display controller: SH1107
- Character grid: 16 columns x 8 rows using 8x8 bitmap cells
- Display interface: 4-wire SPI, mode 3

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

This is the shortest end-to-end development flow.

Requirements:

- Python 3.12+
- `python3 -m venv`
- `adb` if you want to use the default service against connected Android devices
- `mpremote` if you want `./build deploy` to copy files onto a Pico 2W

Commands that render or package device config need three values:

- `VIVIPI_WIFI_SSID`
- `VIVIPI_WIFI_PASSWORD`
- `VIVIPI_SERVICE_BASE_URL`

1. Set Wi-Fi credentials and a service URL that the Pico can reach over Wi-Fi.

```bash
export VIVIPI_WIFI_SSID="your-wifi-name"
export VIVIPI_WIFI_PASSWORD="your-wifi-password"
export VIVIPI_SERVICE_BASE_URL="http://192.168.1.10:8080/checks"
```

1. Install dependencies and run the full local gate.

```bash
./build ci
```

`./build`, `./build ci`, `./build render-config`, `./build build-firmware`, and `./build deploy` all use those same values.

1. Start the default Vivi Service if you want the sample `SERVICE` check to report connected ADB devices.

```bash
./build service --host 0.0.0.0 --port 8080
```

1. Build the firmware bundle and device filesystem assets.

```bash
./build build-firmware
```

1. Install the matching Pico 2W MicroPython UF2 onto the board.

Use the pinned reference written to `artifacts/release/pico2w-micropython.txt`, or the matching GitHub release asset, to choose the supported Pico 2W download page.

1. Copy the built device filesystem onto the Pico.

```bash
./build deploy --device-port /dev/ttyACM0
```

1. Render only the runtime config when you do not need the full bundle.

```bash
./build render-config
```

`./build deploy` uses `mpremote` to copy the prepared filesystem. The MicroPython UF2 is installed separately from the pinned Pico 2W download reference.

## Build Tooling

The `./build` script is the canonical entrypoint.
Running `./build` with no command is equivalent to `./build ci`.

### Common commands

```bash
./build
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

Typical examples:

```bash
VIVIPI_WIFI_SSID="your-wifi" \
VIVIPI_WIFI_PASSWORD="your-password" \
VIVIPI_SERVICE_BASE_URL="http://192.168.1.10:8080/checks" \
./build build-firmware
```

```bash
./build service --host 0.0.0.0 --port 8080
```

Generated artifacts are written under `artifacts/`.

Key outputs:

- `./build render-config` writes `artifacts/device/config.json`
- `./build build-firmware` writes `vivipi-firmware-bundle.zip`, `vivipi-device-filesystem.zip`, `pico2w-micropython.txt`, and the unpacked `vivipi-device-fs/` tree under `artifacts/release`
- `./build deploy` copies the unpacked `vivipi-device-fs/` tree onto the Pico with `mpremote`
- `./build` and `./build ci` validate the core, runtime, tooling, and firmware adapters together on CPython

## Running the Default Vivi Service

The default service discovers connected ADB devices and exposes them as monitoring checks.

```bash
./build service --host 0.0.0.0 --port 8080
```

The HTTP endpoint implementation lives in `src/vivipi/services/adb_service.py`.
The sample `SERVICE` check in `config/checks.yaml` points at `VIVIPI_SERVICE_BASE_URL`.

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

Notes:

- `service.base_url` is resolved from `VIVIPI_SERVICE_BASE_URL`
- The sample `SERVICE` check target in `config/checks.yaml` uses the same value
- The value points to a host address reachable from the Pico over Wi-Fi, such as `http://192.168.1.10:8080/checks`

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
- CI verifies runtime-config rendering, packaging, and the firmware adapter path through `./build ci`

The firmware adapters and runtime loop are exercised on CPython, so the same modules used on the board stay covered in the normal development workflow.

## Release Artifacts

Tagging with a x.y.z version triggers a release containing:

- Python wheel and source distribution
- Zipped MicroPython source bundle containing the firmware files, `config.json`, and the `vivipi` package
- Zipped device filesystem bundle ready for `mpremote fs cp`
- `pico2w-micropython.txt` with the supported Pico 2W MicroPython download reference
- Sample configuration files

The release workflow publishes the filesystem assets and the pinned download reference used by the install flow above.

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
