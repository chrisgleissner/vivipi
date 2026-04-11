# ViviPulse Safe Config Convergence Prompt

ROLE

You are the implementation engineer for ViviPi, continuing from the already-completed `vivipulse` implementation.

This is not a design pass.
This is not a repo-tour pass.
This is not a "suggest next steps" pass.
This is a strict execution task focused on one remaining outcome:

Find, validate, and document a safe direct-probe health check configuration that no longer makes the C64U / U64 devices become unresponsive.

Do not stop at analysis.
Do not stop after reproducing the fault.
Do not stop after proposing a likely fix.
Do not stop until a safe configuration is found, validated, and recorded with evidence.

CURRENT REPOSITORY STATE YOU MUST TREAT AS AUTHORITATIVE

1. `docs/spec.md` remains the product source of truth.
2. `scripts/vivipulse` already exists and is the required public host-side entrypoint.
3. The current host-side reuse boundary is already implemented and must remain honest:
   - `vivipi.runtime.checks.build_runtime_definitions()`
   - `vivipi.runtime.checks.build_executor()`
   - `vivipi.core.execution.execute_check()`
   - `vivipi.core.scheduler.due_checks()`
   - `probe_host_key()`
   - `probe_backoff_remaining_s()`
4. The current `vivipulse` implementation, tests, README updates, and plan/worklog updates are already in place.
5. The current remaining gap is operational, not architectural:
   - the repo implementation is complete enough to run
   - the actual safe host-side probe profile for the real C64U / U64 targets is not yet proven
6. `vivipulse` currently emits:
   - `trace.jsonl`
   - `run-summary.txt`
   - `failure-boundary.txt`
   - `reuse-map.txt`
   - `firmware-research.txt`
   - `search-summary.txt`
   - `soak-summary.txt`
7. Existing terminology still matters:
   - `interval_s`
   - `timeout_s`
   - `allow_concurrent_same_host`
   - `same_host_backoff_ms`
   - `service_prefix`
   - `target`

PRIMARY GOAL

Converge on a safe probe configuration for the real U64 / C64U environment such that:

1. the checks remain useful,
2. the devices do not become unresponsive,
3. the safe profile is evidence-backed rather than guessed,
4. the final configuration is recorded in a form that can be reused,
5. the result is proven with both reproduction evidence and soak evidence.

STRICT STARTING ASSUMPTION

Assume the current `vivipulse` code is your starting tool, not your final result.

You must use it against the real target environment.
If defects in `vivipulse` block safe-config convergence, fix those defects in the repo, add tests, validate, and then continue the safe-config search immediately.

NON-NEGOTIABLE EXECUTION RULE

You must not claim completion until you have a real safe config that no longer makes the C64U / U64 devices unresponsive under repeated host-side checks.

If the current search strategy is insufficient, improve it and keep going.
If the current artifact set is insufficient, extend it and keep going.
If the current recovery flow is insufficient, refine it and keep going.
If the current shared probe behavior itself is still unsafe, make the smallest justified shared-layer change, add parity tests, validate locally, then resume the live search.

DO NOT STOP UNTIL ALL OF THESE ARE TRUE

1. You have reproduced the failure with the current or near-current unsafe profile, or shown with evidence that the failure no longer reproduces because a safe profile has already displaced it.
2. You have identified the last-success / first-failure boundary from live artifacts for the real devices.
3. You have run `vivipulse search` or an improved equivalent against the real targets.
4. You have found at least one concrete safe profile that preserves useful checks and no longer causes device unresponsiveness.
5. You have validated that profile with a meaningful soak run.
6. You have recorded the final safe profile in a reusable config form, not just in prose.
7. You have updated `PLANS.md`, `WORKLOG.md`, and any necessary user-facing docs.

MANDATORY TARGET-SIDE SCOPE

This is about the actual C64U / U64 environment, not a localhost simulation.

You must work against the real targets and capture artifacts from the real runs.
If the environment contains both U64 and C64U endpoints, treat both as first-class validation targets.

ALLOWED CHANGES

You may change:

- `src/vivipi/core/`
- `src/vivipi/tooling/`
- `scripts/vivipulse`
- config files
- tests
- docs
- `PLANS.md`
- `WORKLOG.md`

You may also add new artifacts and checked-in research notes if they materially help converge on the safe config.

You must not:

- replace the shared probe layer with a separate host-only implementation
- pretend success based on synthetic tests alone
- stop after the first plausible mitigation
- disable all risky checks just to make the problem disappear
- brute-force a reckless experiment matrix that repeatedly crashes the devices

REQUIRED HIGH-LEVEL WORKFLOW

Phase 1: Re-baseline The Real Target State
- Inspect the latest existing `artifacts/vivipulse/` output, if any.
- Resolve the actual target definitions being used for C64U / U64.
- Confirm the Ultimate firmware checkout path to use for source-backed reasoning.
- Confirm the exact current direct checks, ordering, and same-host grouping with `scripts/vivipulse --mode plan`.

Phase 2: Controlled Reproduction
- Run the current direct checks in a controlled way using `scripts/vivipulse --mode reproduce`.
- Use `--interactive-recovery` and `--resume-after-recovery` if there is still a realistic risk of device lock-up.
- Produce live request traces and failure-boundary artifacts.
- Distinguish:
  - one protocol dying first,
  - whole-stack disappearance,
  - temporary refusal,
  - persistent unresponsiveness requiring physical recovery.

Phase 3: Firmware-Guided Diagnosis
- Reinspect the local `1541ultimate` source only as needed to refine hypotheses from the live failure boundary.
- Separate:
  - confirmed source facts,
  - strong inferences,
  - still-open questions.
- Use that evidence to justify the next experiment set.

Phase 4: Search For A Safe Profile
- Use `scripts/vivipulse --mode search` as the starting search mechanism.
- If it needs improvement, improve it in-repo and continue.
- Search in this order unless live evidence justifies a change:
  1. increase `same_host_backoff_ms`
  2. increase pass spacing between sweeps
  3. change same-host ordering
  4. add explicit same-host spacing between specific checks
  5. reduce the effective frequency of specific checks
  6. disable individual checks only as the final fallback
- Use the smallest disciplined experiment set that can converge.
- After each destructive run:
  - preserve artifacts immediately
  - update `WORKLOG.md`
  - refine the next candidate set instead of rerunning blindly

Phase 5: Safe Config Materialization
- Once a candidate safe profile is found, record it in a reusable concrete form.
- Acceptable outputs include one or more of:
  - a checked-in safe checks YAML
  - a checked-in build/deploy config variant
  - a checked-in runtime config template
  - a checked-in vivipulse profile artifact with exact CLI parameters
- The final result must be easy for another engineer to rerun.
- Do not leave the winning config trapped only inside `search-summary.txt`.

Phase 6: Soak Validation
- Run the chosen safe profile for a long enough soak to matter.
- Use a configurable duration, but default to a serious validation target such as two hours unless the environment forces a shorter first pass before extending.
- Capture:
  - total request count
  - any transient failures
  - whether recovery was needed
  - whether either device became unresponsive

Phase 7: Documentation And Handoff
- Update `README.md` if the safe profile or recommended command surface changed materially.
- Update `PLANS.md` with the final status.
- Update `WORKLOG.md` with:
  - commands run
  - experiment results
  - failures observed
  - recoveries required
  - winning profile
  - soak result

MANDATORY LIVE-RUN REQUIREMENTS

Every live experiment must preserve enough information to reconstruct:

- exact command used
- exact target set
- exact ordering
- exact same-host policy
- exact timing/backoff values
- last success before unresponsiveness
- first failure after that point
- whether recovery was required

If current artifacts are missing any of that, extend the tool and continue.

MANDATORY SAFE-CONFIG OUTPUT

By the end of the task, produce all of the following:

1. a concrete safe configuration for the C64U / U64 checks
2. the exact command needed to run it with `scripts/vivipulse`
3. the exact artifact path proving the successful soak
4. a concise explanation of why this profile is safer than the prior one
5. any residual limitations or caveats

The safe configuration must preserve as much useful direct coverage as possible.
Do not choose a trivial "safe" config that simply disables everything.

MINIMUM ACCEPTANCE BAR FOR "SAFE"

Do not call the config safe unless all of the following are true:

1. repeated reproduce/search traffic with the chosen profile no longer makes the devices unresponsive
2. the profile survives a meaningful soak run
3. the remaining enabled checks still provide useful health signal
4. any disabled checks are explicitly justified as last-resort exclusions

REQUIRED COMMAND STYLE

Use the real repository entrypoint:

- `scripts/vivipulse --mode plan`
- `scripts/vivipulse --mode reproduce`
- `scripts/vivipulse --mode search`
- `scripts/vivipulse --mode soak`

Do not bypass the wrapper in the final operational procedure unless debugging a wrapper defect.

ANTI-SHORTCUT RULES

Do not:

- stop after updating a prompt or note
- stop after a single successful run
- treat "no reproduction this time" as proof of safety
- skip the soak
- stop because the current `search` heuristics are imperfect
- leave the final safe config implicit
- hide unresolved risk

EXECUTION LOOP

Repeat this loop until the safe config is real and proven:

1. inspect current artifacts and config
2. update `PLANS.md`
3. run the next smallest disciplined live experiment
4. preserve artifacts immediately
5. update `WORKLOG.md`
6. assess whether the evidence supports or rejects the current profile
7. continue

FINAL HANDOFF REQUIREMENTS

At the end, provide:

1. the final safe config
2. the exact files changed
3. the exact command to reproduce the safe run
4. the exact command to run the soak
5. the artifact paths for:
   - failure reproduction
   - winning search result
   - successful soak
6. the last unsafe profile and why it failed
7. the winning profile and why it is safer
8. what, if anything, remains uncertain

START NOW

Start by:

1. inspecting the latest `artifacts/vivipulse/` output
2. running `scripts/vivipulse --mode plan` for the real U64 / C64U target set
3. reproducing the current behavior with the smallest safe live run
4. using `search` to converge toward a safe config
5. refusing to stop until the safe config is found and soak-validated
