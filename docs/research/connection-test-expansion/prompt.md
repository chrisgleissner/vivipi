ROLE

You are the implementation engineer responsible for expanding `scripts/u64_connection_test.py` to cover the critical missing inbound network surfaces documented in `docs/research/connection-test-expansion/u64-connection-test-expansion.md`.

This is a strict execution task.

Do not stop at analysis.
Do not stop after proposing a design.
Do not stop after adding placeholder modules.
Do not stop until the new probes, shared auth plumbing, focused tests, and final validation are complete.

PRIMARY GOAL

Implement the smallest justified additive change set so the host-side U64 connection test covers:

- UDP identify on port `64`
- the DMA-capable raw socket control endpoint on TCP port `64`
- password-protected HTTP, Telnet, FTP, and raw64 deployments through one shared device network password
- the optional modem listener on its configured TCP port

Keep the existing connection-test structure intact.
Do not redesign the scheduler.
Do not broaden scope to unrelated HTTP route families.

REPOSITORY AND SOURCE OF TRUTH

Work in:

- `/home/chris/dev/vivipi`

Use these documents as the task source of truth:

- `/home/chris/dev/vivipi/docs/research/connection-test-expansion/u64-connection-test-expansion.md`
- `/home/chris/dev/vivipi/docs/research/connection-test-expansion/plan.md`

IMPLEMENTATION CONSTRAINTS

You must follow these decisions exactly:

1. Use one shared `network password` option for HTTP, Telnet, FTP, and raw64.
2. Do not model FTP as having a separate password for this feature; firmware `PASS` auth uses the same `CFG_NETWORK_PASSWORD`.
3. HTTP must send `X-Password` only when a network password is configured.
4. Before finalizing raw64 behavior, read `1541ultimate/software/network/socket_dma.cc` and treat its command list as the source of truth for what the endpoint exposes.
5. Document and implement the transport split correctly:
  - UDP port `64` is identify only
  - TCP port `64` is the DMA-capable command endpoint
6. Do not invent a general raw-memory-read opcode on socket 64. Current firmware exposes generic memory read via HTTP `/machine/readmem`, not via `socket_dma.cc`.
7. Raw64 coverage must support safe `smoke`, `read`, and `readwrite` activities only:
  - `smoke`: authenticate when needed, then identify
  - `read`: safe read-only commands such as debug-register read and optionally flash metadata reads
  - `readwrite`: reversible actions only, such as debug-register write/restore, stream enable/disable with cleanup, or a tightly bounded `SOCKET_CMD_DMAWRITE` write-and-restore if restoration can be proved through a trusted read path
8. Do not use destructive or persistent raw64 commands such as reset, poweroff, DMA load/write/jump, keyboard injection, image mount/run, REU/kernal writes, cart loads, or full flash-page extraction.
9. Do not rely on `1541ultimate/python/sock.py` when it diverges from `socket_dma.cc`; helper scripts may contain stale opcode assumptions such as `0xFF73` / `0xFF74` that are not implemented in the current handler.
10. UDP ident success must strictly validate the JSON reply, including that the nonce sent by the client is echoed back.
11. Modem coverage is in scope, but it remains optional and smoke-only.
12. Keep changes additive and local. Prefer new probe modules over scheduler rewrites.
13. Preserve positional compatibility for `RuntimeSettings(...)` by adding only defaulted fields.

REQUIRED PATCH SET

1. Update `scripts/u64_connection_runtime.py`

- add `network_password: str = ""`
- add `modem_port: int = 3000`

2. Add `scripts/u64_ident.py`

- send a UDP `json<nonce>` request to port `64`
- parse the JSON reply
- require valid string values for `product`, `firmware_version`, `hostname`, and `your_string`
- require `your_string == nonce`
- expose a smoke probe compatible with `ProbeExecutionContext`

3. Add `scripts/u64_raw64.py`

- implement raw64 command framing helpers
- authenticate via `SOCKET_CMD_AUTHENTICATE` when a password is configured
- implement identify via `SOCKET_CMD_IDENTIFY`
- implement safe read coverage via `SOCKET_CMD_DEBUG_REG` and optionally `SOCKET_CMD_READFLASH` metadata reads
- do not add a socket64 memory-read command that is not present in `socket_dma.cc`
- expose:
  - smoke = authenticate if needed plus identify
  - read = safe read-only socket activity
  - readwrite = reversible socket activity only
- if `SOCKET_CMD_DMAWRITE` is used for `readwrite`, require all of the following:
  - a known safe scratch address
  - pre-read of the original bytes through a trusted read path
  - restore in the same probe session
  - post-restore verification before reporting success
  - opt-in behavior only, never default soak/stress behavior

4. Add `scripts/u64_modem.py`

- connect to the configured modem port
- read and classify the initial response
- return a smoke result with `connected`, `busy`, or `offline`

5. Update `scripts/u64_connection_test.py`

- import the new probe modules
- expand the default probe set to include `ident` and `raw64`
- expose `modem` as an optional probe choice
- add `--network-password`
- add `--modem-port`
- wire profile defaults and surface/correctness fallback maps for the new probes, including `raw64` readwrite support and the correct TCP64/UDP64 split in help and docs

6. Update auth-aware modules

- `scripts/u64_http.py`: centralized headers with optional `X-Password`
- `scripts/u64_telnet.py`: authenticate on password-protected sessions
- `scripts/u64_ftp.py`: use the same shared network password for FTP `PASS`
- `scripts/u64_stream.py`: authenticate raw64 control commands before stream enable/disable when required

7. Update focused tests

- `tests/unit/tooling/test_u64_connection_test.py`
- `tests/unit/tooling/test_u64_connection_protocols.py`
- any adjacent Telnet or stream tests needed to keep the touched slice green
- add or update FTP auth tests so they reflect the shared network-password model

NON-GOALS

Do not:

- add destructive HTTP lifecycle coverage
- add HTTP `/v1/streams/*` coverage in this task
- add debug stream CLI coverage in this task
- rewrite the scheduling model or summary format
- broaden beyond the connection-test tooling slice

VALIDATION REQUIREMENTS

After the first substantive code edit, run the narrow tooling tests first:

```bash
python -m pytest -o addopts='' \
  tests/unit/tooling/test_u64_connection_test.py \
  tests/unit/tooling/test_u64_connection_protocols.py \
  tests/unit/tooling/test_u64_telnet.py \
  tests/unit/tooling/test_u64_stream.py
```

If that passes, run:

```bash
./build
```

DELIVERABLE

Return with:

- the implemented code changes
- the focused test result
- the `./build` result
- any remaining risk that was deliberately left out of scope