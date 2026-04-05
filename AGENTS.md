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
./build service --host 0.0.0.0 --port 8080
```

## Current repo realities

- `firmware/main.py` is a scaffold only. It loads `config.json` and prints the configured board name; it does not yet drive the SH1107 display or poll checks.
- `config/build-deploy.yaml` and `config/checks.yaml` ship with a sample service endpoint on `127.0.0.1`. That must be replaced with a host address reachable from the Pico 2W for real device deployment.
- `./build deploy` currently extracts the firmware bundle into a directory. It does not flash the Pico or talk to a serial or USB transport.

## Implementation boundaries

- Do not add UI animation, scrolling, icons, or variable-width layout.
- Preserve the strict 16x8 character grid assumptions from the spec.
- Keep selection identity-based, never index-based.
- Keep host-reachable network settings explicit; do not hide device-facing service addresses behind localhost defaults.
- Keep release artifacts reproducible from config plus source.

## Testing expectations

- Every requirement ID from `docs/spec.md` must remain mapped in `docs/spec-traceability.md`.
- Branch coverage must stay at or above `91%`.
- Prefer pure-function tests before hardware integration tests.
