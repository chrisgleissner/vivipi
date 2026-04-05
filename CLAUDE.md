# CLAUDE

Use `docs/spec.md` as the source of truth and `docs/spec-traceability.md` as the contract tying implementation to tests.

When changing behavior:

1. Update pure-core logic in `src/vivipi/core` first.
2. Add or adjust tests under `tests/` before touching firmware entrypoints.
3. Keep `config/build-deploy.yaml` and `config/checks.yaml` aligned with any runtime assumptions.

Prefer deterministic, host-testable code over device-only logic.
