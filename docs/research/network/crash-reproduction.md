# Network Crash Reproduction

Date: 2026-04-11
Status: In progress

## Goal

Deterministically reproduce the target-side failure sequence on both vivipulse parity mode and the Pico runtime.

## Current Host Results

Artifacts:
- `artifacts/vivipulse/20260411T191747Z-reproduce`
- `artifacts/vivipulse/20260411T191843Z-reproduce`
- `artifacts/vivipulse/20260411T191914Z-reproduce`

Results:
- Run 1: `21` requests, `0` transport failures
- Run 2: `21` requests, `0` transport failures
- Run 3: `16` requests, `1` transport failure, blocked host `192.168.1.13`

First recorded host-side failure boundary:
- target: `192.168.1.13`
- last success before failure: `u64-rest`, then `u64-telnet`
- first failure after that success window: `u64-ftp`

Low-level transport evidence from `transport-trace.jsonl` in `artifacts/vivipulse/20260411T191914Z-reproduce`:
- DNS for `192.168.1.13:21` succeeded
- the FTP probe opened a socket and began connect handling
- the connection then stalled for the full `8 s` timeout window
- the failure surfaced as `socket-timeout` followed by `socket-error` and a failing `probe-end`

## Current Reproduction Status

The failure is real but not deterministic yet.

What is proven:
- parity-mode vivipulse can observe an intermittent U64-side failure after prior success on the same device
- the failure is not a host-only DNS error or immediate route failure in the current run

What is not yet proven:
- the same failure sequence has not repeated for `>= 3` consecutive runs
- the same sequence has not yet been reproduced on the Pico with JSONL transport traces enabled

## Current Minimal Trigger Candidate

The strongest current trigger candidate is:

1. successful U64 FTP probe
2. successful U64 REST probe
3. successful U64 TELNET probe
4. second-pass U64 FTP connect timeout on port `21`

This is a candidate only. It is not yet stable enough to declare deterministic reproduction.
