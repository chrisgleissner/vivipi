# ViviPi

[![Build](https://github.com/chrisgleissner/vivipi/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/chrisgleissner/vivipi/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/chrisgleissner/vivipi/graph/badge.svg)](https://codecov.io/gh/chrisgleissner/vivipi)
[![License: GPL v3](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0.en.html)
[![Hardware](https://img.shields.io/badge/hardware-Raspberry%20Pi%20Pico-blue)](https://github.com/chrisgleissner/vivipi/releases)
[![Runtime](https://img.shields.io/badge/runtime-MicroPython%20%7C%20Python-blue)](https://github.com/chrisgleissner/vivipi)

ViviPi (pronounced “VEE-vee-pie”, from the Latin *viv-* in *vivere*, “to live”) is a minimal, glanceable monitoring system for Raspberry Pi Pico display modules. The default target is a Pico 2W with a 128x64 SH1107 OLED, but the runtime and build pipeline also support Waveshare Pico OLED, LCD, and e-paper modules.

## What You Get

- Deterministic fixed-width rendering across supported Pico OLED, LCD, and e-paper modules
- Legacy `16 x 8` grid preserved on the default 1.3 inch OLED at the `medium` font preset
- Deterministic scheduling and execution for `PING`, `HTTP`, `FTP`, `TELNET`, and `SERVICE`
- One `./build` entrypoint for install, lint, test, coverage, packaging, deploy, and service hosting

## Default Hardware Target

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

This is the shortest useful path.

Requirements:

- Python 3.12+
- `python3 -m venv`
- `adb` only if you want the default service against connected Android devices
- `mpremote` only if you want `./build deploy` to copy files onto a Pico 2W

Step 1: Set Wi-Fi credentials. Add `VIVIPI_SERVICE_BASE_URL` only if you want `SERVICE` checks.

```bash
export VIVIPI_WIFI_SSID="your-wifi-name"
export VIVIPI_WIFI_PASSWORD="your-wifi-password"
export VIVIPI_SERVICE_BASE_URL="http://192.168.1.10:8080/checks"
```

Step 2: Run the default local workflow.

```bash
./build
```

Without `VIVIPI_SERVICE_BASE_URL`, ViviPi builds only the direct `PING`, `HTTP`, `FTP`, and `TELNET` checks from `config/checks.yaml`.

Step 3: Start the default Vivi Service only if you want the sample `SERVICE` check.

```bash
export VIVIPI_SERVICE_BASE_URL="http://192.168.1.10:8080/checks"
./build service --host 0.0.0.0 --port 8080
```

Step 4: Build and deploy to the Pico when hardware is connected.

```bash
./build build-firmware
./build deploy --device-port /dev/ttyACM0
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
./build build-firmware
```

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

[`./config/build-deploy.yaml`](./config/build-deploy.yaml) is the build-time source of truth for:

- device metadata and default board wiring
- display selection and layout behavior
- Wi-Fi credentials
- service endpoint defaults
- the path to the checks config

Environment variables are injected into `build-deploy.yaml` placeholders:

```yaml
wifi:
  ssid: ${VIVIPI_WIFI_SSID}
  password: ${VIVIPI_WIFI_PASSWORD}
```

### Configuration Reference

#### Environment Variables

| Variable | Required | Used by | Notes |
| --- | --- | --- | --- |
| `VIVIPI_WIFI_SSID` | Yes | `wifi.ssid` | Required to build device config |
| `VIVIPI_WIFI_PASSWORD` | Yes | `wifi.password` | Required to build device config |
| `VIVIPI_SERVICE_BASE_URL` | No | `service.base_url`, sample `SERVICE` checks | Must be reachable from the Pico over Wi-Fi, for example `http://192.168.1.10:8080/checks` |

If `VIVIPI_SERVICE_BASE_URL` is omitted, build-time filtering drops `SERVICE` checks and keeps the direct checks defined in [config/checks.yaml](config/checks.yaml).

#### `build-deploy.yaml`

| Key | Values | Default | Notes |
| --- | --- | --- | --- |
| `project.name` | string | `vivipi` | Project name stored in the rendered runtime config |
| `device.board` | string | `pico2w` | Board identifier used for packaging and install metadata |
| `device.micropython_port` | path-like string | `/dev/ttyACM0` | Default port for `./build deploy` |
| `device.micropython.version` | string | `1.25.0` | Pinned MicroPython version reference |
| `device.micropython.download_page` | absolute URL | Pico 2W download page | Included in the install manifest |
| `device.buttons.a` | GPIO pin name | `GP14` | Left button pin |
| `device.buttons.b` | GPIO pin name | `GP15` | Right button pin |
| `device.display.type` | see display matrix below | `waveshare-pico-oled-1.3` | Selects the backend and infers controller, SPI mode, geometry, default pins, and default page interval |
| `device.display.mode` | `standard`, `compact` | `standard` | Overview layout mode |
| `device.display.columns` | integer `1` to `4` | `1` | Number of overview columns |
| `device.display.column_separator` | exactly one character | space | Inserted only between overview columns |
| `device.display.font` | `extrasmall`, `small`, `medium`, `large`, `extralarge` | `medium` | Resolves the character cell size from the selected display geometry |
| `device.display.font.width_px` | integer `6` to `32` | inferred | Optional backward-compatible override |
| `device.display.font.height_px` | integer `6` to `32` | inferred | Optional backward-compatible override |
| `device.display.page_interval` | integer seconds or `Ns` | inferred by display | Use `0s` to disable automatic page cycling |
| `device.display.failure_color` | color name string | `red` | Used for failed-check accent rendering on color-capable displays |
| `device.display.brightness` | `low`, `medium`, `high`, `max`, or `0` to `255` | `medium` on OLED/LCD | Unsupported on e-paper display types |
| `wifi.ssid` | placeholder or string | none | Normally `${VIVIPI_WIFI_SSID}` |
| `wifi.password` | placeholder or string | none | Normally `${VIVIPI_WIFI_PASSWORD}` |
| `service.base_url` | absolute `http` or `https` URL | omitted | Required only when using `SERVICE` checks |
| `service.default_prefix` | string | `adb` | Default prefix for service-discovered checks |
| `checks_config` | relative path | `checks.yaml` | Path to the checks definition file |

Visible rows and columns are derived automatically from the selected display geometry and the resolved font size.

`device.display.page_interval` defaults by display family:

| Display family | Default page interval |
| --- | --- |
| OLED and LCD | `15s` |
| 2.13 inch e-paper | `180s` |
| 2.7 to 2.9 inch e-paper | `240s` |
| 3.7 to 4.2 inch e-paper | `300s` |
| 7.5 inch e-paper | `600s` |

Example:

```yaml
device:
  display:
    type: waveshare-pico-lcd-1.3
    mode: compact
    columns: 2
    font: medium
    page_interval: 15s
```

### Supported Display Types

| Family | Module | Diagonal | Resolution | `device.display.type` |
| --- | --- | --- | --- | --- |
| `oled` | Waveshare Pico OLED 1.3 | `1.3"` | `128 × 64` | `waveshare-pico-oled-1.3` |
| `oled` | Waveshare Pico OLED 2.23 | `2.23"` | `128 × 32` | `waveshare-pico-oled-2.23` |
| `lcd` | Waveshare Pico LCD 0.96 | `0.96"` | `160 × 80` | `waveshare-pico-lcd-0.96` |
| `lcd` | Waveshare Pico LCD 1.14 | `1.14"` | `240 × 135` | `waveshare-pico-lcd-1.14` |
| `lcd` | Waveshare Pico LCD 1.14 V2 | `1.14"` | `240 × 135` | `waveshare-pico-lcd-1.14-v2` |
| `lcd` | Waveshare Pico LCD 1.3 | `1.3"` | `240 × 240` | `waveshare-pico-lcd-1.3` |
| `lcd` | Waveshare Pico LCD 1.44 | `1.44"` | `128 × 128` | `waveshare-pico-lcd-1.44` |
| `lcd` | Waveshare Pico LCD 1.8 | `1.8"` | `160 × 128` | `waveshare-pico-lcd-1.8` |
| `lcd` | Waveshare Pico LCD 2.0 | `2.0"` | `320 × 240` | `waveshare-pico-lcd-2.0` |
| `eink` | Waveshare Pico e-Paper 2.13 V3 | `2.13"` | `250 × 122` | `waveshare-pico-epaper-2.13-v3` |
| `eink` | Waveshare Pico e-Paper 2.13 V4 | `2.13"` | `250 × 122` | `waveshare-pico-epaper-2.13-v4` |
| `eink` | Waveshare Pico e-Paper 2.13 V2 | `2.13"` | `250 × 122` | `waveshare-pico-epaper-2.13-v2` |
| `eink` | Waveshare Pico e-Paper 2.13 B V4 | `2.13"` | `250 × 122` | `waveshare-pico-epaper-2.13-b-v4` |
| `eink` | Waveshare Pico e-Paper 2.7 | `2.7"` | `264 × 176` | `waveshare-pico-epaper-2.7` |
| `eink` | Waveshare Pico e-Paper 2.7 V2 | `2.7"` | `264 × 176` | `waveshare-pico-epaper-2.7-v2` |
| `eink` | Waveshare Pico e-Paper 2.9 | `2.9"` | `296 × 128` | `waveshare-pico-epaper-2.9` |
| `eink` | Waveshare Pico e-Paper 3.7 | `3.7"` | `480 × 280` | `waveshare-pico-epaper-3.7` |
| `eink` | Waveshare Pico e-Paper 4.2 | `4.2"` | `400 × 300` | `waveshare-pico-epaper-4.2` |
| `eink` | Waveshare Pico e-Paper 4.2 V2 | `4.2"` | `400 × 300` | `waveshare-pico-epaper-4.2-v2` |
| `eink` | Waveshare Pico e-Paper 7.5 B V2 | `7.5"` | `800 × 480` | `waveshare-pico-epaper-7.5-b-v2` |

### Checks

[config/checks.yaml](config/checks.yaml) defines build-time checks.

| Field | Required | Notes |
| --- | --- | --- |
| `name` | Yes | Label shown on the display |
| `type` | Yes | `ping`, `http`, `telnet`, `ftp`, or `service` |
| `target` | Yes | Host, URL, or service endpoint target |
| `interval_s` | Yes | Check cadence in seconds |
| `timeout_s` | Yes | Per-check timeout in seconds |
| `method` | HTTP only | Request method, for example `GET` |
| `username` | Optional | Used by FTP and TELNET checks when needed |
| `password` | Optional | Used by FTP and TELNET checks when needed |
| `prefix` | `service` only | Prefix applied to service-discovered checks |

## Testing and Quality Gates

- Unified entrypoint: `./build`
- Test framework: `pytest`
- Coverage requirement: `>= 96` branch coverage
- Linting: `ruff`
- CI runs on Python 3.12 and 3.13
- CI verifies runtime-config rendering, packaging, and the firmware adapter path through `./build ci`

The firmware adapters and runtime loop are exercised on CPython, so the same modules used on the board stay covered in the normal development workflow.

The display backend boundary lives under `firmware/displays/`, while rendering intent stays in `src/vivipi/core/`. New panel support should be added by registering a display type and backend rather than branching through the core renderer.

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
firmware/displays/       Display backend registry and hardware drivers
src/vivipi/core/         Pure application logic and rendering model
src/vivipi/services/     Host-side services
src/vivipi/tooling/      Build and deploy logic
tests/                   All test suites
```

## Display Detection

ViviPi does not currently claim true hardware autodetection for SPI display modules, and that is intentional.

Why:

- The vendor Pico SPI examples are model-specific scripts such as `Pico-OLED-1.3(spi).py` and `Pico_ePaper-2.13-B_V4.py`, which indicates manual driver selection rather than a common probe path.
- The OLED SPI example configures the bus as write-only with `miso=None`, so there is no controller readback channel available for reliable identification.
- The e-paper examples expose a `BUSY` line, but that is a status signal for the selected driver flow, not a standardized product identity mechanism.
- The vendor C examples also rely on manually uncommenting the matching display routine in `main.c`.
- This matches the normal Raspberry Pi SPI model more broadly: explicit driver selection is standard, while generic SPI display autodetection is not.

Evidence in the bundled vendor tree:

- [pi/displays/waveshare/Pico-OLED1.3/Pico_code/Python/Pico-OLED-1.3/Pico-OLED-1.3(spi).py](/home/chris/dev/vivipi/pi/displays/waveshare/Pico-OLED1.3/Pico_code/Python/Pico-OLED-1.3/Pico-OLED-1.3(spi).py)
- [pi/displays/waveshare/Pico-OLED1.3/Pico_code/Python/Pico-OLED-1.3/ReadmeEN.txt](/home/chris/dev/vivipi/pi/displays/waveshare/Pico-OLED1.3/Pico_code/Python/Pico-OLED-1.3/ReadmeEN.txt)
- [pi/displays/waveshare/Pico-ePaper-2.13/Spec.md](/home/chris/dev/vivipi/pi/displays/waveshare/Pico-ePaper-2.13/Spec.md)
- [pi/displays/waveshare/Pico-ePaper-2.13/Pico_ePaper_Code/pythonNanoGui/drivers/ePaper2in13bV4.py](/home/chris/dev/vivipi/pi/displays/waveshare/Pico-ePaper-2.13/Pico_ePaper_Code/pythonNanoGui/drivers/ePaper2in13bV4.py)
- [pi/displays/waveshare/Pico-ePaper-2.13/Pico_ePaper_Code/pythonNanoGui/drivers/ePaper2in9.py](/home/chris/dev/vivipi/pi/displays/waveshare/Pico-ePaper-2.13/Pico_ePaper_Code/pythonNanoGui/drivers/ePaper2in9.py)

Because of that, `device.display.type` remains the explicit selector when you are not using the default display.

The current implementation directly covers every bundled Pico OLED/LCD MicroPython sample in the vendor tree and the bundled Pico e-paper MicroPython drivers for `2.13`, `2.13-B`, `2.7`, `2.7-V2`, `2.9`, `3.7`, `4.2`, `4.2-V2`, and `7.5-B`.

For normative product behavior and requirement wording, use [docs/spec.md](docs/spec.md) as the source of truth.

