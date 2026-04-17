# Plans

## FTP Implementation Prompt Plan

Current authoritative plan for writing a self-contained implementation prompt that can be used inside a fresh `1541ultimate` checkout with no access to this repository's research folder.

### Phases

Phase 1: Distill the implementation scope  
Status: COMPLETED

- Re-read the completed FTP findings and isolate only the high-priority, low-regression changes.
- Confirm the exact file paths, functions, reply codes, and RAM constraints that the prompt must state explicitly.
- Exclude broad lwIP, scheduler, and transport changes from the implementation scope.

Phase 2: Update execution tracking  
Status: COMPLETED

- Record this follow-on documentation task in `WORKLOG.md`.
- Keep this plan section authoritative until the prompt is written and checked against the existing report.

Phase 3: Write the standalone implementation prompt  
Status: COMPLETED

- Create `docs/research/1541ultimate/ftp-performance/prompt.md`.
- Make it self-contained so a new session in the standalone `1541ultimate` repo can execute it without referring back to `findings.md`.
- Detail the minimal-invasive implementation steps for the high-priority FTP fixes, including explicit non-goals and verification expectations.

Phase 4: Final consistency pass  
Status: COMPLETED

- Verify that `prompt.md` matches the recommendations already captured in `findings.md`.
- Close out `WORKLOG.md` and mark this plan complete.

### Completion Criteria

Done only when:

- `prompt.md` exists under `docs/research/1541ultimate/ftp-performance/`.
- The prompt is self-contained and does not depend on the local research folder existing in the target repo.
- The prompt covers all high-priority FTP-local findings and excludes broader high-risk work.
- `PLANS.md` and `WORKLOG.md` both reflect the completed task.

---

## FTP Performance RAM Viability Extension Plan

Current authoritative plan for extending the existing `1541ultimate` FTP performance investigation with RAM-cost and target-viability analysis for `U64`, `U64E-II`, and `U2+`.

### Phases

Phase 1: Confirm memory model and target mapping  
Status: COMPLETED

- Verify which heap implementation the relevant targets actually build with.
- Trace target linker limits and reserved memory windows for the Nios and RISC-V application builds.
- Confirm how the repository names map to the user-relevant hardware families (`U64`, `U64E-II`, `U2+`).

Phase 2: Measure nearby memory consumers  
Status: COMPLETED

- Inspect always-on or adjacent network-path allocations that provide realistic comparison points for FTP-local buffer growth.
- Inspect larger subsystem allocations and reserved windows to distinguish FTP-local changes from broad system-memory changes.
- Extend the survey across the wider firmware so the FTP recommendation is judged against resident, transient, and fixed reserved memory outside the immediate network path.

Phase 3: Rewrite the FTP findings with RAM impact  
Status: COMPLETED

- Integrate heap-model constraints and FPGA-target context into the existing report sections rather than adding a detached appendix.
- Add RAM-impact and target-viability discussion to each relevant finding.
- Rewrite the candidate improvements matrix to include RAM-specific columns.
- Add firmware-wide RAM context so the `8 KiB` FTP recommendation is explicitly compared against larger drive, tape, API, copy, and reserved-memory consumers elsewhere in the tree.

Phase 4: Final consistency pass  
Status: COMPLETED

- Align `findings.md`, `PLANS.md`, and `WORKLOG.md`.
- Confirm the report clearly separates FTP-local viable changes from global high-risk memory tuning.

### Completion Criteria

Done only when:

- The report explains the actual heap/allocation model used by the relevant firmware targets.
- The report compares FTP-local memory changes against nearby real allocations in the same firmware tree.
- The candidate matrix includes RAM-delta and target-viability guidance.
- The final recommendations distinguish justifiable FTP-local buffer growth from risky shared-memory retuning.

---

## FTP Performance Investigation Plan

Current authoritative plan for the requested code-grounded investigation of FTP throughput in the `1541ultimate` firmware tree. Historical plan content is retained below for traceability, but this section is the active execution plan for the current task.

### Phases

Phase 1: Map the FTP implementation  
Status: COMPLETED

- Identify the concrete FTP daemon sources, entry points, build integration, and adjacent abstractions used by control and data connections.
- Trace the VFS, file manager, filesystem, and lwIP/socket layers that the FTP code exercises.

Phase 2: Trace transfer paths end to end  
Status: COMPLETED

- Follow download flow from `RETR` command parsing through file reads, buffering, socket sends, and connection teardown.
- Follow upload flow from `STOR` command parsing through socket receives, file writes, and close/sync behavior.
- Record every chunk-size decision, queue, timeout, copy boundary, and blocking call in the path.

Phase 3: Classify bottlenecks and correctness issues  
Status: COMPLETED

- Separate confirmed code-level findings from stronger and weaker hypotheses.
- Check nearby shared infrastructure only where FTP clearly depends on it: lwIP config, socket semantics, task priorities, and file I/O behavior.
- Explicitly rule out tempting but unsupported explanations.

Phase 4: Produce decision-quality documentation  
Status: COMPLETED

- Write `docs/research/1541ultimate/ftp-performance/findings.md` with the required structure, exact file/function references, and remedy classifications.
- Rank remedy options by impact, effort, regression risk, and ownership viability.

Phase 5: Final consistency pass  
Status: COMPLETED

- Verify that `PLANS.md`, `WORKLOG.md`, and the report are aligned.
- Confirm the report covers architecture, both transfer directions, ruled-out explanations, candidate matrix, and recommended order of attack.

### Completion Criteria

Done only when:

- The FTP implementation architecture is mapped with exact source references.
- Upload and download paths have both been traced through file and socket layers.
- All plausible high-impact FTP-local bottlenecks visible in code are documented and classified.
- Shared-infrastructure explanations are only included where the code supports them.
- `docs/research/1541ultimate/ftp-performance/findings.md` exists and satisfies the requested structure.

---

## U64 Connection Test Stabilization Plan

Authoritative plan for eliminating the observed FTP and telnet failures in the shared U64 connection suite while preserving probe semantics, coverage, and correctness.

### Failure Classes

1. FTP lifecycle failures

- `550 Requested action not taken` on upload and download.
- `450 Requested file action not taken` on rename.
- Silent `skip=no_self_file` classifications that avoid exercising the intended readwrite operation.

2. Telnet state desynchronization

- Missing expected menu text such as `Audio Mixer` after prior UI interactions.
- Post-write telnet sessions trusting stale local view state instead of re-entering from verified UI state.

3. Shared state model gaps

- HTTP already verifies writes, but state tracking is confirmed-value only and does not expose tentative intent.
- FTP file lifecycle is process-local instead of shared across runners.
- Telnet session state is local and not invalidated after UI writes.

### Execution Plan

Phase 1: Reproduce and pin root causes  
Status: DONE

- Capture short concurrent runs against the live U64.
- Confirm current FTP/telnet behavior and identify silent misclassifications.
- Confirm out-of-scope stream failures are independent from the FTP/telnet state-model work.

Phase 2: Shared state model  
Status: IN_PROGRESS

- Extend the shared execution state to carry thread-safe object-valued model entries.
- Add confirmed and tentative Audio Mixer state tracking.
- Add shared FTP filesystem tracking for self-files created by the probes.

Phase 3: FTP lifecycle correction  
Status: IN_PROGRESS

- Enforce `create -> verify -> use -> rename/delete` for readwrite FTP operations.
- Remove `skip=no_self_file` fallbacks by provisioning and verifying a deterministic file when the model has no confirmed file.
- Revalidate filesystem state with bounded retries and fail on unresolved mismatches.

Phase 4: Telnet UI synchronization  
Status: IN_PROGRESS

- Replace direct right-arrow volume writes with the verified picker flow.
- Verify final Audio Mixer value via HTTP under the shared lock.
- Invalidate cached telnet view state after writes so the next operation re-enters from a verified UI state.

Phase 5: Validation  
Status: TODO

- Run focused tooling tests for FTP, telnet, and shared-state behavior.
- Run short live loops with the stress profile and explicit `correct` probe modes to validate the fixed code paths without stream noise.
- Run soak-style validation after targeted failures are eliminated.

### Termination Criteria

Done only when:

- FTP lifecycle failures are eliminated without weakening validation.
- Telnet operations no longer rely on stale UI state and verified writes converge through HTTP read-back.
- The shared model carries confirmed and tentative state for the mutated HTTP/FTP resources.
- Probe semantics remain `smoke`, `read`, `readwrite`, and `incomplete`.
- Validation evidence shows zero unexpected FAIL results for the targeted probe set.
