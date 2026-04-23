# U64 Connection Test Expansion Plan

## Goal

Expand `scripts/u64_connection_test.py` so the host probe suite covers the critical missing inbound firmware surfaces identified in `u64-connection-test-expansion.md`, plus optional modem coverage, without changing the existing runner architecture.

This plan is intentionally additive and local. It keeps the current probe scheduler, retry helpers, and result formatting model intact.

## In-Scope Outcomes

Implement all of the following:

- add a first-class UDP identify probe for the Ultimate identify responder on UDP port `64`
- add a first-class probe for the DMA-capable raw socket endpoint on TCP port `64`
- add one shared `network password` option used by HTTP, Telnet, FTP, and raw64 paths
- add an optional modem smoke probe on the configured modem TCP port, default `3000`
- include `ident` and `raw64` in the default and profile-driven connection-test coverage
- preserve existing positional `RuntimeSettings(...)` call sites by adding only defaulted fields
- add focused unit coverage for the new probes and updated parser/profile behavior

## Explicit Scope Decisions

The user selected these constraints:

- use a single shared network password option across all password-gated ingress surfaces
- HTTP should send `X-Password` only when a password is configured
- raw64 coverage should be based on the real DMA socket command surface from firmware and should support safe `smoke`, `read`, and `readwrite` activities
- document clearly that UDP port `64` is identify only, while TCP port `64` is the DMA-capable command endpoint
- do not invent a general raw-memory-read opcode on socket 64; generic memory reads already live under HTTP `/machine/readmem`
- UDP ident success must validate the JSON reply strictly enough to confirm the real service answered
- modem support is useful and should be included, but it remains optional rather than default-required

The following stay out of scope for this change:

- HTTP `/v1/streams/*` coverage
- debug UDP stream coverage as a default connection-test surface
- destructive HTTP machine lifecycle routes
- broad scheduler refactors or output-format redesigns

## Implementation Strategy

### 1. Extend shared runtime settings

Update `scripts/u64_connection_runtime.py`:

- add `network_password: str = ""`
- add `modem_port: int = 3000`

This keeps existing call sites source-compatible while giving all probe modules access to the new configuration.

### 2. Add new probe modules

Create `scripts/u64_ident.py`:

- send a UDP datagram to port `64` with a unique `json<nonce>` payload
- parse the JSON reply
- require non-empty string fields for at least `product`, `firmware_version`, `hostname`, and `your_string`
- verify `your_string` exactly matches the sent nonce
- expose a smoke operation suitable for the existing `ProbeExecutionContext`

Create `scripts/u64_raw64.py`:

- implement raw command framing helpers for TCP port `64`
- treat `1541ultimate/software/network/socket_dma.cc` as authoritative over helper scripts like `1541ultimate/python/sock.py`
- implement authentication via `SOCKET_CMD_AUTHENTICATE` when a network password is configured
- implement identify via `SOCKET_CMD_IDENTIFY`
- implement debug-register read via `SOCKET_CMD_DEBUG_REG`
- optionally implement safe `SOCKET_CMD_READFLASH` metadata reads if they help the read surface
- do not add a made-up socket64 memory-read command; there is no general raw-memory-read opcode in the current handler
- expose:
  - `smoke` coverage for authenticate plus identify
  - `read` coverage for safe read-only socket commands
  - `readwrite` coverage only for reversible operations such as debug-register write/restore, stream enable/disable with cleanup, or a tightly bounded `SOCKET_CMD_DMAWRITE` write-and-restore when restoration can be verified through a trusted read path
- explicitly avoid destructive or persistent commands such as reset, poweroff, DMA load/run/jump, keyboard injection, REU/kernal writes, mount/run image, cart loads, or full flash page extraction
- if `SOCKET_CMD_DMAWRITE` is chosen for the `readwrite` surface, keep it opt-in and require pre-read, restore, and post-restore verification

Create `scripts/u64_modem.py`:

- connect to `settings.modem_port`
- read an initial banner or initial bytes
- classify the result as `connected`, `busy`, or `offline`
- expose smoke-only coverage

### 3. Wire the new probes into the main CLI

Update `scripts/u64_connection_test.py`:

- import the new probe modules
- define:
  - `DEFAULT_PROBES = ("ping", "http", "ftp", "telnet", "ident", "raw64")`
  - `OPTIONAL_PROBES = ("modem",)`
  - `PROBE_CHOICES = DEFAULT_PROBES + OPTIONAL_PROBES`
- extend `PROBE_SURFACE_CHOICES`:
  - `ident -> smoke`
  - `raw64 -> smoke, read, readwrite`
  - `modem -> smoke`
- extend `PROBE_CORRECTNESS_CHOICES`:
  - `ident/raw64/modem -> complete`
- add CLI/env support for:
  - `--network-password`
  - `--modem-port`
- keep the existing profile model, but add `ident` and `raw64` to soak/stress defaults
- keep `modem` opt-in only

### 4. Add shared authentication plumbing

Update `scripts/u64_http.py`:

- centralize request headers
- add `X-Password` only when `settings.network_password` is non-empty

Update `scripts/u64_telnet.py`:

- detect a password prompt after connect when a password is configured
- send the configured network password
- fail cleanly on incorrect-auth responses

Update `scripts/u64_ftp.py` and CLI argument handling:

- use the same shared `network_password` for FTP `PASS`
- do not introduce or preserve a separate FTP-password model for this feature

Update `scripts/u64_stream.py`:

- extend `StreamRuntimeSettings` with `network_password`
- authenticate the raw64 control socket before sending stream enable/disable commands when required

### 5. Update focused unit coverage

Update `tests/unit/tooling/test_u64_connection_test.py`:

- assert the expanded default/profile probe sets
- assert the new surface/correctness fallback maps
- assert network-password and modem-port argument handling
- assert FTP uses the shared network password model

Update `tests/unit/tooling/test_u64_connection_protocols.py`:

- cover HTTP `X-Password` injection
- cover strict ident JSON success behavior
- cover raw64 safe smoke/read/readwrite behavior for the chosen reversible command path
- assert the probe does not rely on nonexistent socket64 read opcodes beyond the implemented safe set
- cover FTP authentication using the shared network password
- cover modem banner classification

If `SOCKET_CMD_DMAWRITE` is the chosen `readwrite` path:

- cover pre-read of original bytes
- cover write-and-restore sequencing
- cover success only after verified restoration
- keep the behavior out of default profile expectations

Keep existing Telnet and stream tests green with minimal changes.

## Validation Sequence

Run validation in this order:

1. focused tooling tests for the touched slice

```bash
python -m pytest -o addopts='' \
  tests/unit/tooling/test_u64_connection_test.py \
  tests/unit/tooling/test_u64_connection_protocols.py \
  tests/unit/tooling/test_u64_telnet.py \
  tests/unit/tooling/test_u64_stream.py
```

2. full repository validation

```bash
./build
```

## Acceptance Criteria

The change is complete when all of the following are true:

- `u64_connection_test.py` exposes `ident` and `raw64` as first-class default probes
- `modem` is available as an optional probe
- secured devices can be probed through one shared network password option
- FTP authentication uses that same shared network password model
- HTTP sends `X-Password` only when configured
- raw64 probes authenticate successfully before command execution when required
- raw64 surface handling is limited to safe smoke/read/readwrite activities derived from the firmware command set
- the docs and implementation consistently describe TCP 64 as the DMA-capable endpoint and UDP 64 as identify only
- UDP ident validates that the device echoed the sent nonce in JSON
- focused tooling tests pass
- `./build` passes