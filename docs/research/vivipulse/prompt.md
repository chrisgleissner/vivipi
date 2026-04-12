# ViviPulse Refined Implementation Prompt

ROLE

You are the implementation engineer for ViviPi. You are responsible for building a host-side stability and soak-testing tool named `vivipulse`.

This is a strict execution task.
This is not a brainstorming pass.
This is not a research-only pass.
Do not stop at analysis.
You must implement, validate, iterate, and converge.

CURRENT REPOSITORY FACTS YOU MUST TREAT AS AUTHORITATIVE

1. `docs/spec.md` is the product source of truth.
2. The Pico production entrypoint is `firmware/main.py`, which delegates to `firmware/runtime.py:run_forever()`.
3. The correct reuse boundary for host-side probe execution is not the full firmware boot/runtime loop. The reusable execution seam already used by the Pico is:
   - runtime definition loading via `vivipi.runtime.checks.build_runtime_definitions()`
   - executor construction via `vivipi.runtime.checks.build_executor()`
   - per-check execution via `vivipi.core.execution.execute_check()`
   - scheduling semantics via `vivipi.core.scheduler.due_checks()`, `probe_host_key()`, and `probe_backoff_remaining_s()`
4. The current direct probe implementations already live in `src/vivipi/runtime/checks.py` and are intentionally portable across MicroPython and CPython through fallback imports and adapters.
5. Business logic belongs in `src/vivipi/core`. Host-side CLI and filesystem/process adapters belong in `src/vivipi/tooling`. Do not bury orchestration logic in shell scripts.
6. Existing project terminology matters:
   - checks use `interval_s` and `timeout_s`
   - same-host pacing uses `probe_schedule.allow_concurrent_same_host` and `probe_schedule.same_host_backoff_ms`
   - service check YAML uses `prefix`, while the runtime model uses `service_prefix`
   - `target` means the actual URL/host/socket target, not the display name
7. Existing top-level plan/log files already exist. You must update `PLANS.md` and `WORKLOG.md` in place for this task instead of replacing unrelated content.
8. Generated artifacts in this repository conventionally live under `artifacts/`.
9. The current spec requires single-attempt transport classification with stable failure detail for direct probes. Do not casually introduce retry behavior into the shared probe runners.

PRIMARY GOAL

Build `vivipulse`, a generic Linux host-side CLI tool that reuses ViviPi's existing shared probe execution layer to:

1. reproduce instability outside the Pico runtime loop,
2. identify the exact failure boundary against a target device,
3. inspect the local Ultimate firmware source to form evidence-based hypotheses,
4. autonomously search for a safer probe-execution profile,
5. minimize destructive traffic and human recovery steps,
6. soak-test a chosen profile for long durations.

MANDATORY ARCHITECTURE CONSTRAINT

Do not frame this as "run the whole Pico firmware on Linux."

The host tool must reuse the shared check execution path already used by the Pico, not the Pico-specific runtime shell around it. In practice:

- Reuse `src/vivipi/runtime/checks.py` for portable direct probe runners.
- Reuse `src/vivipi/core/execution.py` for the mapping from raw probe results to `CheckExecutionResult`.
- Reuse `src/vivipi/core/config.py` and/or `src/vivipi/tooling/build_deploy.py` for existing config parsing and normalization.
- Reuse `src/vivipi/core/scheduler.py` for same-host ordering and backoff semantics where applicable.

Do not build `vivipulse` by driving:

- `firmware.runtime.run_forever()`
- `RuntimeApp`
- display rendering
- button handling
- Wi-Fi bootstrap
- firmware display backends

unless a narrowly scoped parity test makes a specific adapter unavoidable.

NON-NEGOTIABLE REUSE REQUIREMENT

Do not reimplement the probe logic in a separate host-only stack unless absolutely unavoidable.

You must first identify and document the exact shared production functions already used by the Pico for:

- definition loading,
- executor construction,
- per-check execution,
- same-host scheduling identity,
- same-host backoff enforcement.

Then make `vivipulse` execute those exact functions wherever practical.

Any unavoidable host-only logic must be:

- minimal,
- isolated,
- placed in the correct layer,
- documented precisely,
- covered by parity-focused tests.

If you must introduce compatibility shims, keep them thin and explicit. Acceptable shim categories include:

- host-side orchestration around the shared executor
- artifact writers
- timing/trace adapters
- interactive recovery prompts
- firmware-research adapters for the external `1541ultimate` checkout

GENERICITY REQUIREMENT

`vivipulse` must be generic.

It must not hardcode or otherwise depend on:

- U64
- C64U
- Pixel 4
- specific hostnames
- specific device counts
- specific ports
- the current developer machine layout

The current U64/C64U setup is only the first validation scenario.

CURRENT-CODE CONSISTENCY REQUIREMENT

The implementation must reflect how ViviPi is currently architected today, not how an older design note described it.

Specifically:

- Use existing config and model terminology.
- Keep any new orchestration policy testable on CPython in `src/vivipi/core`.
- Do not duplicate config parsing already available in `core.config` or `tooling.build_deploy`.
- Do not invent a second same-host scheduling vocabulary when the project already uses `allow_concurrent_same_host` and `same_host_backoff_ms`.
- Do not invent a second failure-class taxonomy when the current direct-runner layer already classifies transport failures as `timeout`, `dns`, `refused`, `network`, `reset`, and `io`.
- Do not claim "exact Pico entrypoint reuse" if you are actually reusing the shared lower-level execution seam. Be precise and honest about the reuse boundary.

PUBLIC TOOL SHAPE

The public repo entrypoint must be:

`scripts/vivipulse`

It must be a lightweight Bash wrapper around a CPython module in the repository, analogous in spirit to the existing host-side `./build` wrapper.

Expected structure:

- `scripts/vivipulse`
- thin CLI entrypoint under `src/vivipi/tooling/`
- reusable orchestration/state/search logic under `src/vivipi/core/`

If a console script is added to `pyproject.toml`, it is secondary. `scripts/vivipulse` remains the required public repo entrypoint.

CONFIG INPUT REQUIREMENT

Do not introduce an ambiguous bare `--config` unless its meaning is explicit and consistent with the rest of the repo.

The tool must support at least one of these existing repository input shapes cleanly, and document the canonical choice:

1. raw checks YAML, reusing the current checks schema from `config/checks.yaml`
2. rendered runtime config JSON, reusing the shape consumed by `build_runtime_definitions()`
3. build/deploy YAML plus existing resolution of `checks_config`

If you support more than one input shape, do it by reusing existing parsers instead of writing new ones.

Preserve current terminology:

- YAML service prefix field: `prefix`
- runtime field: `service_prefix`
- per-check timing: `interval_s`, `timeout_s`
- same-host pacing: `allow_concurrent_same_host`, `same_host_backoff_ms`

ADDITIONAL REQUIRED INPUT

A full Ultimate firmware repository is expected to be available locally for source inspection.

Do not hardcode one machine path into the implementation.

The tool may default to a sibling checkout such as `../1541ultimate`, but it must also support an explicit CLI override such as:

`--ultimate-repo PATH`

If the repository is required for the chosen mode and is missing, fail fast with a clear message.

REQUIRED OUTCOMES

You must converge to all of the following outcomes:

1. Exact reuse map
   - Identify the Pico production entrypoint.
   - Identify the exact shared probe-execution functions already used by that entrypoint.
   - Prove which of those shared functions are executed unchanged by `vivipulse`.
   - State clearly which Pico-specific layers are intentionally not reused.

2. Reproduction harness
   - Run the existing shared checks from Linux with deterministic ordering and timing.
   - Reproduce the failure outside the Pico UI/runtime shell if it is reproducible from the host.

3. Failure-boundary evidence
   - Determine the last successful request to the failing device.
   - Determine the first failed request.
   - Record the immediate preceding context.
   - Determine whether one protocol fails first or whether the whole stack disappears.

4. Firmware-informed diagnosis
   - Inspect the Ultimate firmware source.
   - Identify likely fragile network-stack or protocol codepaths relevant to the observed behavior.
   - Distinguish confirmed facts, strong inferences, and open questions.

5. Stabilization search
   - Autonomously search for a safer host execution profile.
   - Prefer minimally invasive changes first.
   - Minimize destructive runs and human recoveries.
   - If a safer profile requires changes to shared ViviPi probe behavior, make those changes in the shared layer with parity tests and with full awareness that the Pico also uses that code.

6. Interactive recovery flow
   - When a device becomes unresponsive, stop destructive traffic to that target.
   - Flush artifacts immediately.
   - Show a concise diagnosis.
   - Ask the user for only the minimum required physical recovery action.
   - Resume only when explicitly confirmed and only when resume is enabled.

7. Soak validation
   - Once a candidate stable profile is found, run it autonomously for a configurable duration such as two hours.
   - Produce a final stability summary.

PRIORITY ORDER FOR STABILIZATION

Search for mitigations in this order unless evidence from source inspection justifies a different order:

1. increase same-host backoff (`same_host_backoff_ms`)
2. increase host-side spacing between consecutive passes over the same target set
3. change check ordering for checks that share a host
4. insert explicit spacing between specific checks that share a `probe_host_key`
5. reduce the effective frequency of specific checks by increasing their host-side interval between passes
6. disable individual checks only as a last resort

Do not disable checks early.
Do not brute-force a huge search space.
Use the smallest disciplined experiment set that can converge.

REQUIRED MODES

`vivipulse` must support these modes:

1. `plan`
   - print the intended execution plan, resolved checks, same-host grouping, and effective ordering without sending traffic

2. `reproduce`
   - run the configured checks with full tracing to reproduce instability

3. `search`
   - autonomously explore safer profiles with minimal destructive runs

4. `soak`
   - run the selected stable profile for a configurable duration

REQUIRED CLI SURFACE

The CLI must be consistent with existing repository terminology.

At minimum, support a coherent subset equivalent to the following:

- input selection via explicit config-path options such as `--checks-config PATH`, `--runtime-config PATH`, and/or `--build-config PATH`
- `--mode plan|reproduce|search|soak`
- `--duration 2h`
- `--passes N` or another clearly named host-run count option
- `--same-host-backoff-ms N`
- `--allow-concurrent-same-host`
- `--target TARGET`
- `--check-id ID`
- `--artifacts-dir PATH`
- `--stop-on-failure`
- `--interactive-recovery`
- `--resume-after-recovery`
- `--max-experiments N`
- `--ultimate-repo PATH`
- `--debug`
- `--json`
- `--help`

Add options only when clearly justified.

Do not use misleading option names such as `--target NAME` when the value is actually a URL/host/socket target.

REQUIRED LOGGING

Every request-level event must record enough detail to reconstruct exact order and timing.

Use machine-readable logs first, preferably JSONL.

At minimum capture:

- wall-clock timestamp
- monotonic timestamp
- global sequence number
- per-target sequence number
- mode
- pass number or equivalent host-run index
- check identifier
- check name
- check type
- raw target
- derived same-host key
- effective timeout
- effective same-host backoff
- called function path
- latency
- resulting observation status
- transport/failure class
- concise response summary
- exception details if any

Result classification must preserve current ViviPi semantics where possible.

At minimum distinguish:

- success
- timeout
- dns
- refused
- network
- reset
- io
- protocol/schema/application failure above the transport layer
- unexpected exception

Do not throw away the current detail strings emitted by the shared runner layer. Preserve both normalized class and raw detail.

REQUIRED ARTIFACTS

Persist all of the following under `artifacts/`, preferably under a dedicated subdirectory such as `artifacts/vivipulse/`:

- raw JSONL trace
- concise human-readable run summary
- failure-boundary summary
- exact reuse map
- firmware research summary
- stabilization-search summary
- soak summary

Use deterministic, timestamped filenames and stable directory layout.

INTERACTIVE RECOVERY REQUIREMENT

When a device becomes unresponsive, `vivipulse` must:

- stop traffic to the affected target
- flush and preserve all logs immediately
- print a concise diagnosis with last-success and first-failure context
- state the minimum required physical recovery action
- wait for explicit confirmation before resuming when resume is enabled

User interventions must be minimized.
Everything else should be autonomous.

DOCUMENTATION REQUIREMENT

This tool must be documented as part of the implementation.

At minimum:

- update `README.md` with a focused `vivipulse` section
- document purpose, prerequisites, supported config input shapes, mode examples, artifact layout, and recovery flow
- document how `vivipulse` relates to the existing shared runtime check layer
- document any intentionally unsupported scenarios

Do not hide the tool in research notes only.

MANDATORY TEST COVERAGE

Add real behavioral tests that prove:

- the claimed shared-function reuse path is actually exercised
- any config input adapters preserve existing config semantics
- same-host serialization is enforced by default
- same-host backoff enforcement matches the shared scheduler semantics
- ordering is deterministic
- trace records are correct and reconstructable
- failure-boundary detection is correct
- interactive recovery flow works
- stabilization search behavior works
- soak orchestration works

Prefer pure-function tests for orchestration and classification logic before shell/process integration tests.

Reuse existing test structure where practical:

- core logic tests under `tests/unit/core/`
- tooling/CLI tests under `tests/unit/tooling/`
- extend existing runtime tests only when verifying actual shared-runner reuse

Do not write shallow tests that only assert trivial implementation details.

Branch coverage must remain at or above `96%`.

MANDATORY FIRMWARE RESEARCH

Inspect the Ultimate firmware checkout and identify, with evidence where possible:

- the underlying network stack implementation
- shared transport/resource-management code
- telnet handling
- FTP handling
- REST/HTTP handling
- connection lifecycle management
- relevant limits, buffers, worker counts, socket pools, or timeout paths
- plausible shared fragility that could explain whole-stack unresponsiveness

Use these findings to guide stabilization search.
Do not treat stabilization as blind trial and error.

CONVERGENCE RULES

You must maintain the existing top-level files:

- `PLANS.md`
- `WORKLOG.md`

Do not replace unrelated prior content.
Add or update clearly scoped `vivipulse` work within those files.

`PLANS.md` must contain or be updated with:

- phases
- checkpoints
- risks
- validation steps
- intervention points
- termination criteria

`WORKLOG.md` must contain timestamped entries for:

- inspection performed
- changes made
- validations run
- blockers and resolutions
- commands run
- experiment results
- soak results

EXECUTION LOOP

Follow this loop until termination criteria are met:

1. inspect current state
2. update `PLANS.md`
3. implement the next smallest meaningful step
4. validate immediately
5. update `WORKLOG.md`
6. assess whether evidence supports progress
7. continue without pausing unless blocked by a real external dependency or required physical recovery

Do not drift into open-ended exploration.
Do not reset direction unnecessarily.
Do not leave partially implemented scaffolding without validation.

PHASED EXECUTION ORDER

Phase A - Exact reuse mapping
- identify the Pico production entrypoint
- identify the actual shared probe-execution seam already reused by the Pico
- produce a concrete reuse/parity map

Phase B - External firmware research
- inspect the Ultimate firmware checkout
- identify relevant network/protocol codepaths
- derive evidence-based stabilization hypotheses

Phase C - Host execution path
- implement a host orchestration path around the shared executor and scheduler semantics
- avoid pulling in Pico-only UI or hardware layers

Phase D - Tool implementation
- implement the core orchestration logic
- implement the CLI module
- implement `scripts/vivipulse`
- implement tracing, summaries, and interactive recovery

Phase E - Baseline reproduction
- run plan mode
- run reproduce mode
- capture failure-boundary evidence

Phase F - Stabilization search
- run the smallest disciplined experiment set
- prefer `same_host_backoff_ms`, ordering, and spacing changes first
- converge on at least one credible safer profile

Phase G - Soak validation
- run the chosen profile for a configurable long duration such as two hours
- assess long-term stability

Phase H - Documentation and final handoff
- update `README.md`
- summarize files changed, exact reuse mapping, firmware findings, commands, limitations, and remaining uncertainty

ANTI-SHORTCUT RULES

Do not:

- stop at analysis
- produce only design notes
- reimplement the shared probe logic in a second host-only stack
- drive the whole firmware runtime loop just to claim reuse
- duplicate config parsing already present in the repo
- invent inconsistent CLI vocabulary
- hardcode the current device setup into the tool
- brute-force a huge experiment matrix
- disable checks too early
- skip interactive recovery support
- skip soak mode
- ignore the Ultimate firmware checkout
- overwrite unrelated `PLANS.md` or `WORKLOG.md` content
- hide uncertainty
- claim success while any required outcome remains partial or unvalidated

TERMINATION CRITERIA

Do not claim completion until all are true:

1. The Pico production entrypoint and the actual shared probe-execution seam are identified accurately.
2. The host-side harness executes the claimed shared functions where stated.
3. Any compatibility shim is minimal, documented, and tested.
4. The Ultimate firmware checkout has been inspected and summarized with evidence.
5. `scripts/vivipulse` exists and works as the public repo entrypoint.
6. `plan`, `reproduce`, `search`, and `soak` modes exist.
7. Ordered machine-readable traces are emitted.
8. Same-host serialization and backoff behavior are demonstrated and tested.
9. Interactive recovery mode works.
10. Stabilization search works and evaluates at least one mitigation candidate.
11. Soak mode works for configurable long durations.
12. Failure-boundary detection is clear and evidence-based.
13. `README.md` documents the new tool.
14. `PLANS.md` and `WORKLOG.md` are current.
15. Remaining uncertainty is stated explicitly and honestly.

EXPECTED FINAL HANDOFF

At the end provide:

1. concise implementation summary
2. exact files changed
3. exact-function reuse mapping
4. intentional non-reuse boundaries and why they are correct
5. compatibility shims and why they were needed
6. key findings from the Ultimate firmware source
7. commands for each mode
8. test commands
9. baseline reproduction procedure
10. interactive recovery procedure
11. stabilization-search procedure
12. soak procedure
13. known limitations
14. assessment of readiness for real host-side stability verification

Start now by locating the exact shared probe-execution seam used by the Pico runtime, updating `PLANS.md`, inspecting the Ultimate firmware checkout, and then implementing `scripts/vivipulse`, the host orchestration layer, tracing, recovery flow, stabilization search, tests, and documentation end to end.
