# AGENTS

## Priority order

1. Treat `docs/spec.md` as the product source of truth.
2. Keep business logic in `src/vivipi/core` so it stays testable on CPython.
3. Keep MicroPython-facing code thin and deterministic.
4. Maintain `docs/spec-traceability.md` whenever requirements or tests move.

## Default commands

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

## Current repo realities

- `firmware/main.py` delegates to `firmware/runtime.py`, which wires Wi-Fi bootstrap, button polling, SH1107 output, and the runtime loop together.
- `config/build-deploy.yaml` and `config/checks.yaml` resolve the service endpoint from `VIVIPI_SERVICE_BASE_URL`; the value must be reachable from the Pico over Wi-Fi.
- `./build deploy` uses `mpremote` to copy the prepared device filesystem to the Pico. It does not flash a UF2 image onto a blank board.

## Implementation boundaries

- Do not add UI animation, scrolling, icons, or variable-width layout.
- Preserve the strict 16x8 character grid assumptions from the spec.
- Keep selection identity-based, never index-based.
- Keep host-reachable network settings explicit; do not hide device-facing service addresses behind localhost defaults.
- Keep release artifacts reproducible from config plus source.

## Testing expectations

- Every requirement ID from `docs/spec.md` must remain mapped in `docs/spec-traceability.md`.
- Branch coverage must stay at or above `96%`.
- Prefer pure-function tests before hardware integration tests.
