# ViviPulse Parity Audit

Date: 2026-04-11
Status: In progress

## Scope

This audit compares the host-side `scripts/vivipulse --parity-mode` runner against the Pico runtime using the shared probe execution seam.

Shared execution seam:
- `vivipi.runtime.checks.build_executor`
- `vivipi.core.execution.execute_check`
- `vivipi.core.scheduler.due_checks`
- `vivipi.core.scheduler.probe_host_key`
- `vivipi.core.scheduler.probe_backoff_remaining_s`

## Transport Trace Schema

Both environments can now emit machine-parseable JSONL transport events with these normalized fields:

- wall-clock timestamp with millisecond precision
- monotonic timestamp
- check id / type / target / host key
- request index and request id
- event name
- stage
- timeout
- DNS host/port/result addresses
- socket target
- bytes sent / bytes received
- timeout remaining
- final probe status / detail / latency

Shared event names now emitted by the shared probe runners:

- `probe-start`
- `probe-end`
- `dns-start`
- `dns-result`
- `dns-error`
- `socket-open`
- `socket-ready`
- `socket-send`
- `socket-recv`
- `socket-timeout`
- `socket-error`
- `socket-close`

## Current Host Evidence

Artifacts:
- `artifacts/vivipulse/20260411T191725Z-local`
- `artifacts/vivipulse/20260411T191747Z-reproduce`
- `artifacts/vivipulse/20260411T191843Z-reproduce`
- `artifacts/vivipulse/20260411T191914Z-reproduce`

Observed parity-mode behavior on 2026-04-11:

1. One full `local` pass completed with `7/7` successes.
2. Two consecutive `30 s` parity-mode reproduce runs completed with `0` transport failures.
3. A third `30 s` parity-mode reproduce run produced one transport failure on `u64-ftp` after prior U64 success on both REST and TELNET.

## Equivalent vs Divergent

Equivalent, now instrumented:
- shared probe runners
- per-check request ordering on the host
- DNS resolution and socket lifecycle capture
- same-host backoff capture from the active runtime profile

Divergent or still unproven:
- no fresh Pico JSONL transport trace has been captured from the current hardware, so ordering/lifecycle/timing parity has not yet been proven against the device
- no side-by-side parity summary exists yet because `--firmware-trace PATH` has not been supplied with a Pico-generated JSONL trace

## Current Assessment

The host-side parity runner is producing the correct artifact types and enough low-level detail to support a real parity comparison. The hard gap is no longer instrumentation; it is missing Pico JSONL evidence from the current runtime.

## Next Step

Deploy a Pico config with `service.probe_trace_jsonl: true`, capture the mixed serial stream to a file, extract the JSONL probe events, and run:

```bash
scripts/vivipulse --build-config config/build-deploy.local.yaml --mode reproduce --duration 30s --parity-mode --firmware-trace /path/to/pico-trace.jsonl --json
```
