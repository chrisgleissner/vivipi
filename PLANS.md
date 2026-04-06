# Productionization Convergence Plan

Updated: 2026-04-06T21:13:07+01:00
Current Phase: Phase 6 - Closeout
Overall Status: DONE

## Mission

Drive ViviPi to an evidence-backed, productionized state by reconciling documented behavior with executable behavior, fixing every materially justified in-scope productionization issue, adding regression protection, and proving the resulting workflows end to end as far as the repository allows.

## Assumptions

- `docs/spec.md` is the product source of truth unless executable evidence proves a documented claim is incorrect or misleading.
- The smallest credible fix is preferred over broad refactoring.
- Hardware-specific validation may be bounded by the current environment, but build, render, package, deploy, and service paths must still be validated or documented truthfully.
- Existing tests are useful evidence but are not sufficient if README, packaging, or workflows contradict them.

## Constraints

- Keep business logic in `src/vivipi/core` where practical.
- Preserve deterministic, event-driven rendering and identity-based selection.
- Keep the strict 16x8 default-grid assumptions intact for the default OLED path.
- Maintain requirement-to-test traceability in `docs/spec-traceability.md`.
- Do not weaken the enforced branch coverage gate below `96%`.

## Severity Model

- `H1 critical`: silent incorrectness, broken release or install path, corrupted artifacts, impossible documented startup path, or materially false production claim.
- `H2 high`: major operational risk, broken important path, hazardous missing validation, or significant documentation-to-code mismatch.
- `H3 medium`: non-trivial operator friction, weak observability, partial mismatch, missing regression coverage, or lower-frequency correctness risk on a supported path.
- `H4 low`: minor docs, diagnostics, or maintainability improvement with limited production impact.
- `NOT-A-BUG`: investigated and rejected with evidence.
- `DEFERRED-OUT-OF-SCOPE`: explicitly out of scope with rationale.

## Deterministic Prioritization Rule

1. Correctness, silent failure, and broken documented workflows.
2. Broken build, test, packaging, release, and deploy paths.
3. Configuration, validation, and operator hazards.
4. Observability, diagnostics, and maintenance risks.
5. Documentation and traceability mismatches.
6. Adjacent low-cost polish only when no H1-H3 item remains.

## Phases

1. Baseline and plan.
2. Deep productionization audit.
3. Fix H1 and H2 issues.
4. Fix justified H3 issues required for convergence.
5. Final hardening and validation.
6. Closeout and residual-risk check.

## Tasks

- [x] P01 Create authoritative plan and worklog
  Rationale: replace the stale task-specific plan with a convergence plan and establish an execution log for this pass.
  Files: `PLANS.md`, `WORKLOG.md`.
  Evidence required: updated files committed in the worktree with this pass recorded.
  Dependencies: none.

- [x] A01 Audit README claims against implementation
  Rationale: documented install, build, display, service, and release claims must match real behavior.
  Files: `README.md`, `build`, `.github/workflows/*`, `src/vivipi/tooling/build_deploy.py`, `src/vivipi/services/*`, `config/*`.
  Evidence required: finding list with file-level proof and either aligned implementation or corrected docs.
  Dependencies: P01.

- [x] A02 Audit spec and traceability against implementation and tests
  Rationale: requirement coverage and documented behavior must align with code and enforced validation.
  Files: `docs/spec.md`, `docs/spec-traceability.md`, `tests/spec/*`, `tests/unit/**/*`, `tests/contract/*`.
  Evidence required: reconciled requirement mapping plus any needed spec or traceability updates.
  Dependencies: P01.

- [x] A03 Audit build, coverage, packaging, release, and deploy workflows
  Rationale: release confidence depends on the executable path actually producing the advertised artifacts and failing clearly when prerequisites are missing.
  Files: `build`, `pyproject.toml`, `.github/workflows/*`, `src/vivipi/tooling/build_deploy.py`, release artifacts under `artifacts/`.
  Evidence required: validated local workflow results and regression tests for any workflow bug fixed.
  Dependencies: P01.

- [x] A04 Audit runtime, config validation, and service correctness
  Rationale: deterministic runtime behavior, config handling, and the default service path are core production responsibilities.
  Files: `src/vivipi/core/*`, `src/vivipi/runtime/*`, `src/vivipi/services/*`, `firmware/*`, `config/*`, related tests.
  Evidence required: classified findings with root cause, fix plan, and focused validation.
  Dependencies: P01.

- [x] F01 Fix H1-H2 documentation-to-code and config-surface mismatches
  Rationale: materially false defaults or unsupported instructions create broken operator workflows.
  Files: to be determined by audit, expected to include `README.md`, `config/build-deploy.yaml`, `src/vivipi/tooling/build_deploy.py`, and tests.
  Evidence required: regression tests plus validated commands proving the documented path works or docs are corrected.
  Dependencies: A01, A03, A04.

- [x] F02 Fix H1-H2 build, packaging, release, and deploy defects
  Rationale: asset generation and deploy behavior must be repeatable and truthful.
  Files: to be determined by audit, expected to include `build`, `src/vivipi/tooling/build_deploy.py`, workflow files, and tests.
  Evidence required: targeted packaging tests and successful build or staging commands.
  Dependencies: A03.

- [x] F03 Fix H1-H2 runtime, service, and validation defects
  Rationale: runtime determinism, service payload correctness, and failure behavior must be safe and diagnosable.
  Files: to be determined by audit, expected to include `src/vivipi/core/*`, `src/vivipi/runtime/*`, `src/vivipi/services/*`, `firmware/*`, and tests.
  Evidence required: focused regression tests and successful narrow validation after each fix.
  Dependencies: A02, A04.

- [x] F04 Fix justified H3 gaps required for convergence
  Rationale: remaining medium-severity risks must be closed if they materially affect sustained engineering use.
  Files: determined by audit.
  Evidence required: issue classification, regression coverage, and validation evidence.
  Dependencies: F01, F02, F03.

- [x] T01 Run focused validation after each material fix
  Rationale: no task closes on reasoning alone.
  Files: changed modules and their tests.
  Evidence required: targeted `pytest` or scripted validation recorded in `WORKLOG.md`.
  Dependencies: F01, F02, F03, F04.

- [x] T02 Run final validation matrix
  Rationale: repository-level convergence requires broader proof, not only targeted tests.
  Files: repository-wide validation surfaces.
  Evidence required: successful `./build lint`, `./build test`, `./build coverage`, `./build build-firmware`, `./build release-assets`, and any required focused service or traceability tests; deploy validation or truthful documented hardware limitation.
  Dependencies: T01.

- [x] D01 Publish final audit and align docs/traceability
  Rationale: the final repository state must be self-describing and easy to maintain.
  Files: `docs/research/productionization/audit.md`, `README.md`, `docs/spec-traceability.md`, any other affected docs.
  Evidence required: final audit distinguishes fixed findings, not-a-bug items, and residual risk; docs match final behavior.
  Dependencies: A01, A02, A03, A04, F01, F02, F03, F04.

- [x] D02 Finalize plan and closeout checklist
  Rationale: termination criteria require an up-to-date plan with evidence-backed completion.
  Files: `PLANS.md`, `WORKLOG.md`, `docs/research/productionization/audit.md`.
  Evidence required: every in-scope task checked complete only after proof is recorded.
  Dependencies: T02, D01.

## Live Status

- Completed: P01, A01, A02, A03, A04, F01, F02, F03, F04, T01, T02, D01, D02.
- Residual external limit: live Pico deployment remains hardware-gated in this environment.
- Next decision point: none.

## Evidence Requirements

- Every fixed issue must capture: ID, title, severity, symptom, root cause, fix, tests, validation, and final status.
- Every non-fix disposition must capture: ID, title, rationale, evidence, and final status.
- `WORKLOG.md` must record commands run, modules inspected, files changed, and validation results.
- `docs/research/productionization/audit.md` must reflect the final repository truth, not interim assumptions.

## Termination Checklist

- [x] `PLANS.md` is current and every in-scope task is checked complete.
- [x] `WORKLOG.md` contains the execution and evidence trail for this pass.
- [x] `docs/research/productionization/audit.md` exists and is finalized.
- [x] All discovered H1 and H2 issues are fixed or explicitly marked `DEFERRED-OUT-OF-SCOPE` with strong rationale.
- [x] All justified H3 issues are fixed or explicitly marked `DEFERRED-OUT-OF-SCOPE` with strong rationale.
- [x] Every material fix has regression coverage.
- [x] `README.md`, `docs/spec.md`, and `docs/spec-traceability.md` align with the final implementation.
- [x] Relevant validation passes after the final changes.
- [x] Build, render, package, release, and deploy paths are validated or truthfully documented where hardware limits apply.
- [x] No materially false production claim remains in scope.
- [x] Residual risk is empty or only contains genuine external non-blocking items.
