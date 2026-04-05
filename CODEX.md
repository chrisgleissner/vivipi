# CODEX

This repository is optimized for a spec-first, test-heavy workflow.

- Product rules live in `docs/spec.md`.
- Requirement-to-test mapping lives in `docs/spec-traceability.md`.
- Pure logic belongs in `src/vivipi/core`.
- The default host-side service for local development is `src/vivipi/services/adb_service.py`.
- Build and release packaging logic lives in `src/vivipi/tooling/build_deploy.py`.

Do not weaken the coverage gate or bypass the traceability tests.
