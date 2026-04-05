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
./build render-config
./build build-firmware
./build service --host 0.0.0.0 --port 8080
```

## Implementation boundaries

- Do not add UI animation, scrolling, icons, or variable-width layout.
- Preserve the strict 16x8 character grid assumptions from the spec.
- Keep selection identity-based, never index-based.
- Keep release artifacts reproducible from config plus source.

## Testing expectations

- Every requirement ID from `docs/spec.md` must remain mapped in `docs/spec-traceability.md`.
- Branch coverage must stay at or above `91%`.
- Prefer pure-function tests before hardware integration tests.
