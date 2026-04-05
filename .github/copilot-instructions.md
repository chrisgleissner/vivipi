# Copilot Instructions

Use `docs/spec.md` as the source of truth. Prefer changing `src/vivipi/core` over adding logic to firmware entrypoints because the core is the part enforced by the CI test matrix.

When implementing features:

- Keep the 16x8 character grid invariant intact.
- Keep rendering deterministic and event-driven.
- Keep selection identity-based.
- Add tests in `tests/` and update `docs/spec-traceability.md` whenever requirement coverage changes.
- Preserve the `>= 91%` branch coverage gate.

The default host-side Vivi Service is the ADB service exposed by `src/vivipi/services/adb_service.py`.
