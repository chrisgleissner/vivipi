# Audit 1 Implementation Prompt

Use this prompt to drive the repository from the current scaffold to an implementation that closes every item in [audit.md](/home/chris/dev/vivipi/docs/audits/audit1/audit.md) while converging on the product defined in [docs/spec.md](/home/chris/dev/vivipi/docs/spec.md).

## Prompt

You are working in the `vivipi` repository. Fully implement [docs/audits/audit1/audit.md](/home/chris/dev/vivipi/docs/audits/audit1/audit.md) against [docs/spec.md](/home/chris/dev/vivipi/docs/spec.md), which is the product source of truth.

Read first:

- [docs/spec.md](/home/chris/dev/vivipi/docs/spec.md)
- [docs/spec-traceability.md](/home/chris/dev/vivipi/docs/spec-traceability.md)
- [docs/audits/audit1/audit.md](/home/chris/dev/vivipi/docs/audits/audit1/audit.md)
- [AGENTS.md](/home/chris/dev/vivipi/AGENTS.md)
- [README.md](/home/chris/dev/vivipi/README.md)

Non-negotiable repo rules:

- Treat `docs/spec.md` as the source of truth.
- Keep business logic in `src/vivipi/core`.
- Keep MicroPython-facing code thin, deterministic, and free of UI/business-policy logic.
- Preserve the strict 16x8 character grid.
- Keep selection identity-based, never index-based.
- Do not add animation, blinking, icons, scrolling, variable-width layout, or UI clutter.
- Maintain `docs/spec-traceability.md` whenever tests or requirement coverage move.
- The repository enforces `>= 91%` branch coverage. Do not stop at 90%; meet or exceed 91%.

Current reality to replace:

- `firmware/main.py` is only a scaffold.
- There is no SH1107 driver, button polling, Wi-Fi bootstrap, or runtime loop on-device.
- Periodic execution for `PING`, `REST`, and `SERVICE` checks is not implemented end-to-end.
- Default service URLs still point at loopback and are not device-reachable.
- `./build deploy` is packaging-only, not real deployment.
- Diagnostics rendering exists, but runtime diagnostics production does not.
- Build help formatting is inconsistent.

Required outcomes:

1. Implement a real runtime architecture that satisfies `VIVIPI-ARCH-001` end-to-end.
2. Add a deterministic check-execution and scheduling layer for `PING`, `REST`, and `SERVICE` checks, including interval/timeout handling and observation timestamps.
3. Wire observations into application state using the existing identity-based selection and rendering model.
4. Add a MicroPython runtime path that performs Wi-Fi bootstrap, button polling, SH1107 framebuffer output, and event-driven redraws only when state changes or burn-in shift changes.
5. Keep hardware-specific code thin by pushing policy, scheduling decisions, state transitions, and render decisions into pure core modules under `src/vivipi/core`.
6. Implement a compact diagnostics event pipeline so runtime failures can surface via diagnostics mode without raw logs or wrapping.
7. Replace loopback defaults in shipped config with explicit device-reachable host configuration. Do not hide this behind localhost.
8. Make `./build deploy` accurately perform deployment for the supported workflow, or rename/restructure the command set so the shipped command behavior is truthful and aligned with README/release behavior. Prefer implementing a real supported deploy path if feasible.
9. Make release assets sufficient for the supported install flow and document that flow precisely.
10. Normalize the `build` help text formatting.

Implementation guidance:

- Start by mapping each audit issue to concrete code and test changes before editing.
- Reuse the existing pure-core surface where possible:
  - `src/vivipi/core/config.py`
  - `src/vivipi/core/state.py`
  - `src/vivipi/core/render.py`
  - `src/vivipi/core/input.py`
  - `src/vivipi/core/scheduler.py`
  - `src/vivipi/core/shift.py`
- Add new pure-core modules when needed for:
  - check scheduling and due-time calculation
  - check execution orchestration
  - diagnostics events/state
  - runtime state reduction
- Keep hardware adapters and transport wrappers separate from the pure core. If new MicroPython modules are needed, isolate them under `firmware/` or a clearly separated runtime package.
- Ensure redraw behavior remains event-driven only. No continuous render loop.
- Ensure burn-in shift stays global and uniform.
- Preserve deterministic ordering, page visibility, and selection identity semantics.
- For `SERVICE` checks, preserve stable child identities using the existing prefix rules from the spec.
- For timing, enforce the spec constraint that timeout is at least 20% smaller than interval.

Expected deliverables:

- Runtime and scheduler implementation code.
- Hardware-facing display/input/network integration code.
- Deployment and release-tooling updates.
- Config and template updates for reachable host configuration.
- README and any needed docs updates reflecting the now-supported workflow.
- Updated `docs/spec-traceability.md`.
- Tests that cover all newly introduced logic and keep every requirement mapped.

Testing requirements:

- Add unit tests first for pure logic.
- Add contract tests where schema or serialized runtime/config behavior matters.
- Mock hardware and network edges so most behavior remains testable on CPython.
- Add only the thinnest integration tests around hardware adapters that can run in CI.
- Maintain or improve requirement mappings for every spec ID.
- Finish with passing results for:
  - `./build lint`
  - `./build test`
  - `./build coverage`
  - `./build ci`

Convergence loop:

1. Read the spec, traceability matrix, and audit.
2. Create a concrete checklist for all seven audit findings and keep it updated as work progresses.
3. Implement the missing pure-core pieces before the MicroPython adapters whenever possible.
4. After each substantial change, run the smallest relevant tests immediately.
5. Expand tests until branch coverage is at least 91% and the new behavior is well specified.
6. Update docs and traceability as soon as the implementation shape settles.
7. Run the full validation commands and fix any regressions.
8. Do not stop at partial progress, analysis, or “next steps”. Stop only when the repository is in a coherent state that closes the audit findings and aligns with the spec, or when an external blocker makes completion impossible.

Definition of done:

- Every issue in [docs/audits/audit1/audit.md](/home/chris/dev/vivipi/docs/audits/audit1/audit.md) is resolved in code, tooling, docs, or explicitly justified as no longer applicable.
- The implementation materially satisfies the spec rather than merely documenting gaps.
- `docs/spec-traceability.md` still covers every requirement in `docs/spec.md`.
- Branch coverage is `>= 91%`.
- The final summary explains which files changed, how each audit item was closed, what was validated, and any residual hardware-only risks.
