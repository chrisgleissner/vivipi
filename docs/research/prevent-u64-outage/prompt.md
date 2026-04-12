# Prevent U64/C64U Outage Prompt

ROLE

You are the implementation engineer for ViviPi.

This is a strict execution task focused on one remaining outcome:

Find and fix the Pico-side behavior that causes the real U64 / C64U devices to degrade or become unresponsive under on-device health checks, while preserving the intended scheduling model:

- checks may run concurrently across different devices
- checks against the same device must remain sequential
- the same-device backoff must remain `probe_schedule.same_host_backoff_ms`

This is not a design pass.
This is not a repo-tour pass.
This is not a host-tool implementation pass.
Do not stop at analysis.
Do not stop after naming a likely cause.
Do not stop after adding logging.
Do not stop until the Pico-side behavior is corrected, validated on real hardware, and documented with evidence.

CURRENT REPOSITORY FACTS YOU MUST TREAT AS AUTHORITATIVE

1. `docs/spec.md` remains the product source of truth.
2. Business logic belongs in `src/vivipi/core`.
3. Pico runtime glue lives in `firmware/` plus `src/vivipi/runtime/`.
4. `firmware/main.py` delegates to `firmware/runtime.py`, which builds the runtime and enters the Pico loop.
5. The shared direct probe layer already lives in:
   - `vivipi.runtime.checks.build_runtime_definitions()`
   - `vivipi.runtime.checks.build_executor()`
   - `vivipi.core.execution.execute_check()`
   - `vivipi.core.scheduler.due_checks()`
   - `vivipi.core.scheduler.probe_host_key()`
   - `vivipi.core.scheduler.probe_backoff_remaining_s()`
6. The intended scheduling model is:
   - parallel across distinct hosts/devices
   - sequential for checks sharing a `probe_host_key`
   - paced by `allow_concurrent_same_host` and `same_host_backoff_ms`
7. Existing terminology matters:
   - `interval_s`
   - `timeout_s`
   - `allow_concurrent_same_host`
   - `same_host_backoff_ms`
   - `service_prefix`
   - `target`
8. `PLANS.md` and `WORKLOG.md` already exist and must be updated in place.
9. The current `vivipulse` host-side runner exists and must remain honest about its reuse boundary.

CRITICAL FINDINGS FROM THE CURRENT STATE

Treat the following as established starting evidence:

1. The host-side `vivipulse` runner was updated to overlap distinct hosts while keeping same-host probes sequential with `same_host_backoff_ms = 250`.
2. The aligned host-side repros did not reproduce U64/C64U failures in the same short window as the Pico:
   - Ultimate-only aligned host run:
     - command:
       - `scripts/vivipulse --mode reproduce --duration 30s --stop-on-failure --check-id c64u-rest --check-id c64u-ftp --check-id c64u-telnet --check-id u64-rest --check-id u64-ftp --check-id u64-telnet --json`
     - artifact:
       - `artifacts/vivipulse/20260411T111550Z-reproduce`
     - result:
       - overlapping host execution confirmed
       - no U64/C64U transport failures
   - full active-check-set aligned host run:
     - command:
       - `scripts/vivipulse --mode reproduce --duration 60s --stop-on-failure --json`
     - artifact:
       - `artifacts/vivipulse/20260411T111649Z-reproduce`
     - result:
       - transport failures reproduced on `pixel4-adb` only
       - no U64/C64U transport failures
3. Older Pico serial captures do show problematic on-device behavior not seen in the aligned host runs:
   - `artifacts/hardware-proof/health-transition-serial.log`
   - `artifacts/hardware-proof/health-recovery-serial-2.log`
   - those captures include very long U64 probe durations, such as about `24 s`, `32 s`, and `73 s`
4. Therefore the remaining gap is not simply "host was globally serial, Pico was not".
5. The remaining likely cause is Pico-side behavior, such as:
   - timing drift or scheduling semantics that differ from the host
   - retry behavior or repeated connection attempts not visible in the host traces
   - MicroPython socket behavior, DNS behavior, or timeout behavior differing from CPython
   - interaction between Pico runtime backgrounding, network state, and probe execution
   - transport cleanup or socket close behavior differing on-device

PRIMARY GOAL

Converge on a Pico-side fix such that:

1. U64 and C64U no longer degrade or become unresponsive under repeated on-device health checks.
2. Parallelism across different devices is preserved.
3. Same-device checks remain sequential and paced by the configured backoff.
4. The fix is evidence-backed from real Pico runs, not inferred from host runs alone.
5. The result is documented clearly enough for another engineer to rerun the proof.

NON-NEGOTIABLE EXECUTION RULE

You must not claim completion until you have real evidence that the Pico-side runtime no longer degrades the U64 / C64U devices under the intended scheduling model.

If instrumentation is missing, add it.
If the timing model is unclear, prove it.
If MicroPython socket behavior differs from CPython, isolate that difference.
If a shared probe-layer change is necessary, make the smallest justified shared change and validate it on both Pico and host.

DO NOT STOP UNTIL ALL OF THESE ARE TRUE

1. You have identified at least one concrete Pico-side difference that still remains after host-side scheduling alignment.
2. You have captured live Pico evidence showing actual probe timing and overlap on-device.
3. You have either reproduced the U64/C64U degradation on the Pico with improved evidence or shown with evidence that the fix prevents the previous degradation.
4. You have made the smallest justified code change to eliminate the Pico-side outage behavior.
5. You have validated the fix on real hardware.
6. You have updated `PLANS.md`, `WORKLOG.md`, and any necessary user-facing docs.

WHAT YOU MUST NOT DO

Do not:

- remove cross-device concurrency globally
- replace the shared probe layer with a Pico-only probe implementation
- declare success based on host-side `vivipulse` evidence alone
- hide the problem by disabling all Ultimate checks
- stop after adding logs without using them to converge to a fix
- assume MicroPython behaves like CPython without proving it

ALLOWED CHANGE AREAS

You may change:

- `firmware/`
- `src/vivipi/runtime/`
- `src/vivipi/core/`
- tests
- docs
- `PLANS.md`
- `WORKLOG.md`

You may add:

- hardware-focused artifact notes
- extra serial or trace instrumentation
- parity tests comparing host and Pico execution semantics

You must keep business logic testable on CPython whenever possible.

MANDATORY INVESTIGATION TARGETS

You must explicitly inspect and, where needed, instrument all of the following:

1. Pico scheduling path
   - `firmware/runtime.py`
   - `src/vivipi/runtime/app.py`
   - `src/vivipi/core/scheduler.py`
2. Pico-side direct probe path
   - `src/vivipi/runtime/checks.py`
   - especially timeout handling, socket open/close behavior, and network error classification on MicroPython
3. Runtime interaction points that may interfere with probes
   - background workers
   - network reconnect logic
   - loop timing and poll cadence
   - any shared locks or queues around check execution
4. Existing hardware evidence
   - `artifacts/hardware-proof/health-transition-serial.log`
   - `artifacts/hardware-proof/health-recovery-serial-2.log`
   - `artifacts/hardware-proof/u64-safe-probes-serial-after-reset.log`
5. Latest aligned host artifacts
   - `artifacts/vivipulse/20260411T111550Z-reproduce`
   - `artifacts/vivipulse/20260411T111649Z-reproduce`

REQUIRED HIGH-LEVEL WORKFLOW

Phase 1: Re-Baseline The Pico Path
- Inspect the current Pico runtime scheduling and execution path end to end.
- State exactly where:
  - checks become due
  - checks are queued
  - same-host backoff is enforced
  - check completion is recorded
  - background work is drained
  - network reconnect work may interfere
- Compare that path explicitly against the aligned host-side `vivipulse` runner.

Phase 2: Add Pico-Side Evidence
- Add the minimum instrumentation needed to reconstruct on-device behavior precisely.
- Capture at least:
  - check enqueue time
  - actual check start time
  - actual check end time
  - host key
  - same-host backoff delay actually slept
  - whether the check ran in a background worker
  - any retries or repeated socket/connect attempts
  - socket timeout values actually applied
- Preserve the output in artifacts or serial logs with enough detail to compare against host traces.

Phase 3: Reproduce The Pico Failure With Better Evidence
- Run the real Pico against the real U64/C64U targets.
- Preserve enough evidence to determine:
  - whether same-host serialization is actually being honored on-device
  - whether cross-host concurrency is causing unexpected blocking or starvation
  - whether probe durations exceed the configured `timeout_s`
  - whether any socket calls remain blocked after the nominal timeout
  - whether a specific protocol starts failing first

Phase 4: Isolate The Remaining Pico-Side Difference
- Use the captured evidence to identify the smallest still-unexplained gap between Pico and host behavior.
- Distinguish clearly between:
  - confirmed facts
  - strong inferences
  - open questions
- Plausible categories include:
  - MicroPython socket timeout semantics
  - DNS or `getaddrinfo` cost on-device
  - delayed or incomplete socket close
  - background queue/drain timing
  - host completion timestamps being recorded too late or too early
  - blocking interactions with reconnect or observability code

Phase 5: Implement The Smallest Correct Fix
- Make the narrowest change that removes the Pico-only failure mode while preserving intended semantics.
- If the fix belongs in shared code, keep it shared and add parity tests.
- If the fix belongs in Pico runtime glue, keep it there and explain why it is Pico-specific.
- Do not broaden the change unnecessarily.

Phase 6: Validate On Real Hardware
- Re-run the Pico against the real U64/C64U targets.
- Show that:
  - same-host probes remain sequential
  - cross-device concurrency is still present
  - the prior degradation no longer occurs in the observed window
  - probe durations stay within expected bounds

Phase 7: Documentation And Handoff
- Update `PLANS.md` and `WORKLOG.md`.
- Update `README.md` only if the operational behavior or required commands materially change.
- Record the exact commands and artifact paths used for the proof.

MANDATORY LIVE-RUN REQUIREMENTS

Every Pico-side live run must preserve enough information to reconstruct:

- exact deployed code version
- exact config used on-device
- exact check set
- exact ordering / overlap behavior
- exact same-host backoff value
- actual start and end times for each check
- any runtime/network reconnect activity overlapping with checks
- last success before degradation
- first failure after that point
- whether manual recovery was required

If current artifacts are missing any of that, extend the instrumentation and continue.

MANDATORY ACCEPTANCE BAR

Do not call the Pico fix complete unless all of the following are true:

1. The Pico still allows cross-device concurrency.
2. Same-device checks are still sequential and paced by `same_host_backoff_ms`.
3. U64 and C64U no longer degrade in the previously observed failure window.
4. The fix is backed by real Pico-side evidence, not just unit tests.
5. Any remaining uncertainty is stated explicitly.

REQUIRED HANDOFF OUTPUT

At the end, provide all of the following:

1. the confirmed Pico-side root cause or best-supported root-cause statement
2. the exact files changed
3. the exact validation commands run
4. the exact artifact paths proving the before/after behavior
5. the prior Pico-side behavior and why it was unsafe
6. the winning Pico-side fix and why it is safer
7. what, if anything, remains uncertain

START NOW

Start by:

1. inspecting the latest aligned host-side `vivipulse` artifacts
2. comparing them directly to the existing Pico serial captures
3. instrumenting the Pico runtime to expose actual on-device scheduling and socket timing
4. reproducing the U64/C64U degradation on the Pico with better evidence
5. fixing the Pico-side cause without removing cross-device concurrency
