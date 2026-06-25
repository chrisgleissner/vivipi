ROLE

You are an expert ViviPi engineer, benchmark engineer, network protocol engineer, and U64 / C64 Ultimate firmware integrator. You are working in the `chrisgleissner/vivipi` repository.

Implement a new generic benchmark CLI for comparing Ultimate-family memory-write transports, starting with 1541Ultimate REST `/v1/machine:writemem` and U64 TCP/64 socket-DMA. This must be a reusable workload runner, not a c64cast-specific tool. The benchmark-relevant request shape is target address space, target address, and payload byte count; optional labels such as `audio`, `screen`, or `bitmap` are human annotations only. The only place c64cast should appear is as a named traffic profile in JSON, for example `"name": "c64cast"`.

This is a benchmark / workload-generator task, not a soak-test task. The output must support latency, throughput, payload-rate, call-shape, and transport-overhead analysis. Call-shape means the ordered stream of target address + byte-count requests. It does not mean domain-specific reasons for those writes. The tool must not become another availability probe, retry wrapper, or stress profile bolted onto `u64_connection_test.py`.

Work autonomously to completion. Do not stop after planning. Ask for clarification only if repository state makes implementation literally impossible. Make conservative, explicit assumptions in implementation notes where source inspection or hardware behavior leaves uncertainty.

SOURCE OF TRUTH

Follow the repo instructions in `AGENTS.md`.

Product / repo truth:

- `docs/spec.md`
- `docs/spec-traceability.md`

Existing U64-targeting CLI and protocol tools to inspect before implementation:

- `docs/c64u-u64-cli.md`
- `docs/reference.md`
- `scripts/c64_health_check`
- `scripts/u64_health_check.py`
- `scripts/u64_connection_test.py`
- `scripts/u64_connection_runtime.py`
- `scripts/u64_http.py`
- `scripts/u64_raw64.py`
- `scripts/u64_stream.py`
- `tests/unit/tooling/test_u64_connection_test.py`
- `tests/unit/tooling/test_u64_connection_protocols.py`
- related unit-test loader patterns under `tests/unit/tooling/`

c64cast traffic source for the initial JSON profile:

- `docs/research/c64cast/traffic.md`

1541Ultimate firmware source to inspect:

- `/home/chris/dev/c64/1541ultimate/software/network/socket_dma.cc`
- `/home/chris/dev/c64/1541ultimate/software/network/socket_dma.h`
- `/home/chris/dev/c64/1541ultimate/software/api/route_machine.cc`
- `/home/chris/dev/c64/1541ultimate/software/api/routes.h`
- `/home/chris/dev/c64/1541ultimate/software/api/routes.cc`
- `/home/chris/dev/c64/1541ultimate/software/io/c64/c64_subsys.cc`
- `/home/chris/dev/c64/1541ultimate/software/io/c64/c64.h`

Analyze the firmware checkout before implementation. In `PLANS.md`, cite source paths and line ranges for every protocol assumption that affects benchmark semantics. If that checkout is not present, record which assumptions remain `requires-hardware-confirmation` or `requires-upstream-source-confirmation` in `PLANS.md` and `WORKLOG.md`. Do not invent protocol behavior.

REPOSITORY CONTEXT TO PRESERVE

The repo already has U64 / C64U smoke, soak, stress, and direct protocol tools:

- `vivipulse` and `vivipulse_stress_test.sh` are ViviPi-aligned soak / parity tools with artifacts.
- `u64_connection_test.py` is a direct protocol exerciser for ping, REST/HTTP, UDP/64 ident, TCP/64 DMA, FTP, Telnet, optional modem, and UDP stream monitoring.
- `u64_connection_test.py` has `-H/--host`, `-d/--delay-ms`, `-n/--log-every`, `-P/--ftp-pass`, `--network-password`, `--http-port`, `-v/--verbose`, `--probes`, `--schedule`, `--runners`, `--duration-s`, `--surface`, and per-protocol `--*-surface` / `--*-mode` flags.
- Existing protocol helpers already know about shared `NETWORK_PASSWORD`, HTTP `X-Password`, DMA `SOCKET_CMD_AUTHENTICATE = 0xFF1F`, DMA `SOCKET_CMD_DEBUG_REG = 0xFF76`, and the TCP/64 frame envelope.

Learn from those tools, but do not reuse their soak/stress semantic model. The benchmark needs deterministic workload generation, phase fairness, payload accounting, JSONL event streams, and statistically useful latency samples. It should not contain probe retries intended to mask transient availability failures.

SOURCE-CONFIRMED FIRMWARE FACTS

The implementation plan must include these facts after verifying them in `/home/chris/dev/c64/1541ultimate`:

- TCP/64 socket-DMA command constants are defined in `software/network/socket_dma.cc`: `SOCKET_CMD_DMAWRITE = 0xFF06`, `SOCKET_CMD_REUWRITE = 0xFF07`, `SOCKET_CMD_IDENTIFY = 0xFF0E`, `SOCKET_CMD_AUTHENTICATE = 0xFF1F`, and U64-only `SOCKET_CMD_DEBUG_REG = 0xFF76`.
- Socket-DMA normal command frames carry a 16-bit little-endian payload length. Mount/run image commands have special 24-bit length handling, but `DMAWRITE`, `REUWRITE`, `AUTHENTICATE`, `IDENTIFY`, and `DEBUG_REG` do not.
- `SOCKET_BUFFER_SIZE` is `200000`, but normal benchmark write commands are still bounded by the 16-bit payload length. Therefore one `DMAWRITE` frame can carry at most `65533` C64 payload bytes (`65535 - 2 address bytes`), and one `REUWRITE` frame can carry at most `65532` REU payload bytes (`65535 - 3 offset bytes`). Larger JSON-defined writes must be rejected or explicitly split according to JSON policy; do not silently truncate.
- The socket-DMA server sets 1 second receive and send timeouts on accepted sockets. Client-side timeout defaults should be explicit and configurable; failures should be logged as benchmark errors, not retried away.
- If `CFG_NETWORK_PASSWORD` is empty, DMA sessions start authenticated. If non-empty, any non-authentication command before successful `0xFF1F` authentication disconnects the socket. Authentication replies with one byte: `0x01` success, `0x00` failure, and failed authentication is throttled by firmware.
- `DMAWRITE` parses the first two payload bytes as little-endian C64 address and executes `SubsysCommand(..., C64_DMA_RAW_WRITE, address, data, len - 2)`. It sends no write-specific response.
- `REUWRITE` parses a 24-bit little-endian REU offset and writes bytes directly into `REU_MEMORY_BASE + ((offset + i) & 0xffffff)`. It sends no write-specific response.
- `DEBUG_REG` returns one byte containing the current U64 debug register value, then writes `buf[0]` only if the command payload length is nonzero. Use zero-length `DEBUG_REG` for a read-only barrier.
- REST `PUT /v1/machine:writemem` requires `address` and `data`, parses hex data into a fixed 128-byte buffer, rejects decoded data length greater than 128 bytes, rejects decoded data length less than 1, and rejects writes that exceed `$FFFF`.
- REST `POST /v1/machine:writemem` requires `address`, receives the request body through `attachment_writer`, loads up to 65536 bytes into a heap buffer, rejects writes that exceed `$FFFF`, and executes the same C64 raw-write subsystem command as REST PUT.
- REST route authentication validates the `X-Password` header against `CFG_NETWORK_PASSWORD`; an incorrect or missing password on a password-protected endpoint returns HTTP 403 with `"Forbidden."`.
- REST PUT, REST POST, and socket-DMA `DMAWRITE` all converge on the C64 subsystem `C64_DMA_RAW_WRITE` path for C64 address-space writes.
- `C64_DMA_RAW_WRITE` in `software/io/c64/c64_subsys.cc` stops the C64 if it is not already stopped, performs `memcpy` into `C64_MEMORY_BASE + offset`, resumes if it stopped the machine, and returns synchronously from the subsystem call. Benchmark latency therefore includes front-door transport overhead plus this stop / memory-copy / resume path.

NON-GOALS

- Do not modify 1541Ultimate firmware.
- Do not add a new Pico firmware feature.
- Do not make this a new `u64_connection_test.py` profile.
- Do not hard-code c64cast-specific CLI flags, parser fields, or command examples.
- Do not make benchmark behavior depend on human labels such as `audio`, `bitmap`, `screen`, or `color`. Labels are for reports and JSON readability only.
- Do not hide benchmark failures behind soak-style retry loops.
- Do not emit human-formatted benchmark stdout. Stdout must be JSONL only.
- Do not require third-party Python dependencies.
- Do not require hardware for unit-test success.
- Do not benchmark reads as a first-class workload. Reads may be used only for optional verification or DMA command-serialization barriers.
- Do not silently restore modified memory by default; restoration changes the measured workload.

AUTHORITATIVE EXECUTION PLAN

Create or update `PLANS.md` immediately. Treat it as the authoritative execution plan for this change.

`PLANS.md` must contain:

1. Short problem statement.
2. Repository facts discovered from current code.
3. Firmware source facts discovered from `/home/chris/dev/c64/1541ultimate`, with file paths and line ranges for REST route handling, REST authentication, socket-DMA command handling, socket-DMA authentication, and C64 raw-write execution.
4. Protocol assumptions, each marked as one of:
   - `verified-from-source`
   - `verified-from-existing-vivipi-code`
   - `requires-upstream-source-confirmation`
   - `requires-hardware-confirmation`
5. Phase plan with concrete implementation tasks.
6. Acceptance-criteria checklist.
7. Decision log for every choice that affects benchmark semantics.
8. Final status section.

Maintain `WORKLOG.md` as you work. Append concise dated entries for meaningful discoveries, implementation steps, tests, failures, and fixes.

IMPLEMENTATION TARGET

Add a new executable host-side script:

```bash
scripts/u64_dma_rest_benchmark.py
```

Add a generic JSON traffic configuration:

```bash
config/u64_dma_rest_benchmark_traffic.json
```

The script must:

- run directly from the repository root with Python 3.12;
- use only the standard library plus existing repo-local modules where they reduce duplication without importing soak/stress semantics;
- expose `main(argv: list[str] | None = None) -> int` for tests;
- keep pure parsing, JSON traffic loading, workload generation, protocol framing, event construction, and summary-stat code testable without hardware;
- be executable (`chmod +x`);
- have `--help` output styled like `scripts/u64_connection_test.py --help`;
- produce valid JSONL on stdout and send all human diagnostics to stderr.

GENERIC CLI CONTRACT

The CLI must be generic. It must not expose c64cast-specific flags such as `--scenario c64cast`, `--traffic-shape host-dma-mhires-single-buffer`, `--display-mode`, `--bitmap-address`, `--screen-bytes`, `--enable-vic-regs`, `--vic-reg`, `--dirty-policy`, or `--audio-profile`.

All workload-specific shape details must come from JSON. The CLI selects a named traffic profile and controls generic execution behavior only.

Required basic command:

```bash
./scripts/u64_dma_rest_benchmark.py -H u64
```

Default behavior:

- traffic config: `config/u64_dma_rest_benchmark_traffic.json`
- traffic name: `c64cast`
- probes / transports: `rest,dma`
- schedule: `sequential`
- runners: `1`
- duration: from JSON if set; otherwise frame / iteration count from JSON
- REST method: `auto`
- host: `-H/--host`, default `u64`
- HTTP port: `80`
- DMA port: `64`
- network password: optional, from `--network-password`, `NETWORK_PASSWORD`, or `-P/--ftp-pass` as a legacy shared-password alias, matching `u64_connection_test.py`
- delay between writes: `0 ms`
- log every: `1`
- warmup units: `0`
- verification: disabled
- DMA acknowledgement: `barrier`
- DMA barrier command: `debugreg`
- HTTP connection mode: `close`
- DMA connection mode: `persistent`

Required options and naming style:

```text
-H HOST, --host HOST
-d DELAY_MS, --delay-ms DELAY_MS
-n LOG_EVERY, --log-every LOG_EVERY
-P FTP_PASS, --ftp-pass FTP_PASS
--network-password NETWORK_PASSWORD
--http-port HTTP_PORT
--dma-port DMA_PORT
-v, --verbose
--traffic TRAFFIC
--traffic-config TRAFFIC_CONFIG
--probes PROBES
--schedule sequential|concurrent
--runners RUNNERS
--duration-s DURATION_S
--iterations ITERATIONS
--warmup-iterations WARMUP_ITERATIONS
--rest-method auto|post|put
--http-connection close|persistent
--dma-ack-mode barrier|send-only
--dma-barrier debugreg|identify
--dma-connection persistent|per-request
--payload-pattern zero|increment|random|frame-counter
--seed SEED
--report REPORT
```

Use `--probes` rather than `--transport` to align with `u64_connection_test.py`. In this benchmark, `--probes` is an ordered non-empty comma-separated protocol list using `rest,dma`; the default is `rest,dma`. If you keep `--transport` as a compatibility alias, hide it from help and normalize it to `--probes`.

Use `--schedule sequential|concurrent` and `--runners N` with the same parser vocabulary as `u64_connection_test.py`:

- `sequential`: phase-based benchmark; one protocol phase at a time. This is the default and required for primary REST vs DMA comparison.
- `concurrent`: optional exploratory mode; may run configured probes concurrently, but must be clearly marked non-primary in JSON start events and final reports because it changes offered load and comparability.

Use `--duration-s` for time-bounded runs, matching `u64_connection_test.py`. If `--duration-s` is omitted, use the selected JSON traffic profile's `iterations`, `frames`, or equivalent unit count. If both CLI duration and JSON count are present, CLI duration wins.

Help text requirements:

- Build the parser with `argparse.RawDescriptionHelpFormatter`, as `u64_connection_test.py` does.
- The usage and option names should visually match `u64_connection_test.py` where concepts overlap.
- Description must be concise and generic, for example:

```text
Deterministic U64 memory-write benchmark. Default: JSON traffic 'c64cast' with sequential REST and DMA phases. rest targets /v1/machine:writemem; dma targets the DMA-capable TCP port 64 command endpoint.
```

- Include an epilog with "Probe precedence" / "Examples" style similar to `u64_connection_test.py`.
- Help text must mention that stdout is JSONL only and that traffic shape comes from JSON, not CLI workload flags.

Required help examples:

```bash
./scripts/u64_dma_rest_benchmark.py -H u64
./scripts/u64_dma_rest_benchmark.py -H u64 --traffic c64cast
./scripts/u64_dma_rest_benchmark.py -H u64 --traffic c64cast --duration-s 60
./scripts/u64_dma_rest_benchmark.py -H u64 --traffic-config config/u64_dma_rest_benchmark_traffic.json --traffic single-write
./scripts/u64_dma_rest_benchmark.py -H u64 --probes rest --rest-method post
./scripts/u64_dma_rest_benchmark.py -H u64 --probes rest --rest-method put --traffic single-write
./scripts/u64_dma_rest_benchmark.py -H u64 --probes dma --dma-ack-mode barrier
./scripts/u64_dma_rest_benchmark.py -H u64 --schedule sequential --runners 1 --duration-s 60
```

Do not include c64cast shape details in CLI examples. If a user wants a different c64cast variant, they edit or select a different JSON traffic profile.

JSON TRAFFIC CONFIG MODEL

The benchmark must load traffic profiles from JSON. The JSON is the only place where precise traffic shape is configured.

The top-level JSON shape:

```json
{
  "version": 1,
  "default": "c64cast",
  "traffic": [
    {
      "name": "c64cast",
      "description": "c64cast mhires single-buffer host-DMA video writes",
      "unit": "frame",
      "rate_hz": 20,
      "iterations": 200,
      "pacing": "unit",
      "payload_pattern": "frame-counter",
      "seed": 1,
      "writes": [
        {"label": "screen", "space": "c64", "address": "0400", "bytes": 1000, "write_kind": "dmawrite"},
        {"label": "color", "space": "c64", "address": "D800", "bytes": 1000, "write_kind": "dmawrite"},
        {"label": "bitmap", "space": "c64", "address": "2000", "bytes": 8000, "write_kind": "dmawrite"}
      ]
    }
  ]
}
```

Traffic profile fields:

- `name`: stable CLI selector used by `--traffic`.
- `description`: human-readable report text.
- `unit`: logical unit name, such as `frame`, `iteration`, `audio_tick`, or `batch`.
- `rate_hz`: optional pacing target for units per second.
- `iterations`: optional logical unit count.
- `duration_s`: optional default time-bounded run length.
- `pacing`: `unit` or `none`.
- `inter_write_delay_ms`: optional delay between writes within a unit.
- `payload_pattern`: `zero`, `increment`, `random`, or `frame-counter`.
- `seed`: deterministic payload seed.
- `writes`: ordered write templates emitted per unit.
- `metadata`: optional object for domain-specific annotations, such as display mode, buffering, dirty policy, or source workload. Metadata must be logged and reported but must not become CLI flags.

Write template fields:

- `space`: `c64` or `reu`.
- `address`: uppercase hex string without `0x`; C64 addresses are 16-bit, REU offsets are 24-bit.
- `bytes`: positive payload byte count.
- `write_kind`: `dmawrite`, `reuwrite`, or `rest-writemem`.
- `label`: optional human-readable label, for example `screen`, `color`, `bitmap`, `audio`, `vic_regs`, `tracker`, or `single`.
- `enabled`: optional boolean, default `true`.
- `repeat`: optional positive integer call multiplier.
- `bank_cycle`: optional list of address values, selected by logical unit index.
- `dirty`: optional object describing deterministic sub-ranges; supported policies are `full`, `one-span`, `slabs`, and `skip-repeated`.
- `rest_policy`: optional `write`, `skip`, or `c64-shadow` for writes that cannot be represented by REST.
- `split`: optional object. Default policy is `reject`; explicit split policy may divide oversize writes into multiple emitted logical writes while preserving write order, label annotation, and per-emitted-write `request_bytes`.

Rules:

- The JSON loader must validate schema shape, unknown required field types, duplicate traffic names, duplicate write IDs when provided, non-positive byte counts, invalid hex addresses, and address ranges before traffic starts.
- The JSON loader must preserve write order exactly.
- Workload metadata may use c64cast terms, but parser and CLI code must treat it as opaque metadata.
- Labels are optional and must be treated as opaque report annotations. Changing a label must not change emitted address/byte requests, payload bytes, scheduling, phase order, latency accounting, throughput accounting, or pass/fail behavior.
- Benchmark identity and deterministic payload generation must use write order / emitted write index, address space, target address, byte count, unit index, traffic name, seed, and payload pattern. Do not use label text as an input to payload bytes or benchmark behavior.
- Default `c64cast` JSON must emit 10000 request bytes per unit at 20 units/s: screen `$0400` 1000 bytes, color `$D800` 1000 bytes, bitmap `$2000` 8000 bytes.
- A `single-write` JSON profile must also exist for protocol sanity checks: C64 `$C000`, 128 bytes, 100 iterations, no pacing.
- Optional additional c64cast variants, such as double-buffer, REU-staged, dirty-span, or audio-ring profiles, must be represented as additional JSON traffic entries, not CLI flags.

PHASE MODEL AND FAIRNESS

Use phase-based benchmarking by default.

Default execution:

1. round 1, REST phase, selected JSON traffic
2. round 1, DMA phase, identical selected JSON traffic

Fairness requirements:

- REST and DMA phases must use the same emitted logical writes where both protocols can represent the write.
- Payload bytes must be deterministic from `seed`, traffic name, unit index, address space, target address, byte count, dirty offset, emitted write index, and payload pattern.
- Payload bytes and request ordering must not depend on optional label text.
- Do not mutate the RNG across phases in a way that changes payloads by protocol.
- Each request event must include a stable `logical_write_id`.
- Warmup events must be logged with `"warmup":true` and must not pollute measured summary statistics unless summary includes separate warmup stats.
- Phase summaries must distinguish measured units from warmup units.
- If JSON marks a write as unsupported for a protocol and `rest_policy` or `dma_policy` says `skip`, log a non-request skip event. Skips must not be counted as successful requests.

PROTOCOL REQUIREMENTS

DMA write protocol:

- transport: TCP port 64
- frame envelope: `<uint16-le command><uint16-le payload_length><payload>`
- `DMAWRITE` command: `0xFF06`
- `DMAWRITE` payload: `<uint16-le address> + raw data`
- maximum single-frame `DMAWRITE` raw data length: `65533` bytes
- `REUWRITE` command: `0xFF07`
- `REUWRITE` payload: `<uint24-le reu_offset> + raw data`
- maximum single-frame `REUWRITE` raw data length: `65532` bytes
- writes larger than the single-frame maximum must be rejected unless JSON explicitly selects split mode
- authentication command: `0xFF1F`
- authentication payload: UTF-8 password bytes
- successful authentication response is exactly one byte `0x01`; failed authentication response is exactly one byte `0x00`
- log `command:"0xFF06"` or `command:"0xFF07"` and `request_bytes == len(raw data)`, not envelope bytes

DMA timing requirement:

- Do not report only local `sendall()` duration as default DMA latency.
- Default `--dma-ack-mode barrier` must measure from immediately before sending the write frame until a same-connection command-serialization barrier response is received.
- Default U64 barrier: zero-length `SOCKET_CMD_DEBUG_REG = 0xFF76`, which returns one byte and does not modify the debug register when payload length is zero.
- Treat the debug-register barrier as a same-socket serialization point after the firmware has returned from the preceding command handler. Do not describe it as a write-specific acknowledgement from `DMAWRITE`, because the source sends no such response.
- Optional fallback barrier: identify (`0xFF0E`) only when debug-reg is unavailable or disabled. Log the fallback explicitly because identify has different response size and product-string work.
- `--dma-ack-mode send-only` is allowed only as an explicit opt-in and must emit a JSON warning event stating that send-only latency is host/socket-buffer timing and is not REST-response-equivalent.
- Optional readback verification, if implemented, must be a separate `--verify readback` mode and must not be included in default latency unless explicitly documented in event fields.

REST write protocol:

- use `http.client`
- endpoint: `/v1/machine:writemem`
- `PUT` shape: `/v1/machine:writemem?address=XXXX&data=HEX`
- `POST` shape: `/v1/machine:writemem?address=XXXX` with raw bytes as body
- `--rest-method put` must reject writes larger than `128` bytes before sending
- `--rest-method put` must generate even-length uppercase hex and reject any odd-length or non-hex request construction bug before sending
- `--rest-method post` always sends raw payload bytes in the request body with `Content-Type: application/octet-stream` and a correct `Content-Length`
- `--rest-method post` must reject C64 writes larger than the remaining visible address space because firmware rejects `address + datalen > 65536`
- `--rest-method auto` uses `PUT` only for writes of `128` bytes or less when useful, and `POST` for larger writes
- include `X-Password` when `--network-password` is configured
- missing or incorrect `X-Password` on a password-protected REST endpoint produces HTTP 403 and must be recorded as a failed request, not retried as a transient transport error
- count any non-2xx REST status as a failed benchmark request and include `status`, response-byte count when available, and concise error text
- default HTTP connection mode is `close`
- if persistent HTTP is implemented, make it explicit via `--http-connection close|persistent`, default `close`, and log the mode in the start event

REQUEST-BYTES ACCOUNTING

Every benchmark write event must record `request_bytes` as the number of C64 or REU payload bytes requested for that logical write.

Do not count:

- TCP/64 command envelope bytes
- DMA address prefix bytes
- REU offset prefix bytes
- HTTP headers
- REST query-string hex expansion
- JSONL serialization bytes
- barrier command bytes

Those may be added later as separate overhead fields, but `request_bytes` must stay the raw memory-write payload length.

SAFETY AND DEVICE-STATE GUARDRAILS

This benchmark writes to C64 RAM, color RAM, I/O registers if JSON says so, REC registers if JSON says so, and possibly REU address space. It can disturb a running machine.

Implement guardrails:

- Default JSON traffic must not write VIC registers, CIA registers, vectors, REC registers, or tracker bytes unless that traffic profile is explicitly named and documented as stateful.
- Reject C64 address ranges where `address + length - 1 > 0xFFFF`.
- Reject REU offsets where a single emitted write is outside the supported 24-bit offset semantics unless JSON explicitly requests wrap behavior and the implementation documents it.
- Reject negative or zero byte counts.
- Reject malformed hex addresses.
- Clearly identify color RAM and I/O-adjacent ranges in `--help` safety text.
- Include target addresses and byte counts in the JSON start event.
- Do not write human-readable warnings to stdout during benchmark runs.
- Do not silently restore memory after the benchmark.

JSONL OUTPUT CONTRACT

Stdout must contain JSONL only. Each line must be one complete JSON object.

Emit a start event before traffic:

```json
{"run":"start","tool":"u64_dma_rest_benchmark","run_id":"...","traffic":"c64cast","traffic_config":"config/u64_dma_rest_benchmark_traffic.json","probes":["rest","dma"],"schedule":"sequential","rounds":1,"seed":1}
```

Start event must include shared context, not repeated on every request:

- `tool`
- `run_id`
- `traffic`
- `traffic_config`
- `traffic_description`
- `traffic_unit`
- `traffic_metadata`
- `probes`
- `schedule`
- `runners`
- `rounds`
- `iterations` or `duration_s`
- `rate_hz`
- `pacing`
- `host`
- `http_port`
- `dma_port`
- `rest_method`
- `http_connection`
- `dma_ack_mode`
- `dma_barrier`
- `dma_connection`
- `seed`
- `firmware_source`: object summarizing inspected 1541Ultimate source checkout path and git commit if available
- `workload`: ordered summary of JSON writes, addresses, byte counts, repeat counts, and protocol support

Emit one JSON event per write request.

Each request event must include:

- `ts`: UTC ISO-8601 with millisecond precision and `Z`
- `run_id`
- `event`: `request`
- `round`
- `phase_index`
- `phase_probe`
- `traffic`
- `unit`
- `unit_index`
- `iter`: monotonically increasing per measured phase
- `logical_write_id`
- `label`: optional human-readable JSON label when present
- `space`: `c64` or `reu`
- `write_kind`
- `type`: `rest` or `dma`
- `operation`: `writemem`
- `address`: uppercase hex without `0x`; this is the benchmark target address
- `request_bytes`
- `latency_ms`
- `ok`
- `warmup`
- `traffic_metadata` only if useful and compact; otherwise keep metadata in the start event

REST request events must also include:

- `method`
- `path`
- `status` when available
- `http_connection`

DMA request events must also include:

- `command`: `"0xFF06"` for DMAWRITE or `"0xFF07"` for REUWRITE
- `ack_mode`
- `barrier` when barrier mode is used
- `dma_connection`

Failure events must include:

- `ok:false`
- `error`
- `error_type`

Emit warning events as JSON, not stderr, when they affect benchmark interpretation:

```json
{"event":"warning","run_id":"...","code":"DMA_SEND_ONLY_LATENCY","message":"send-only DMA timing measures host/socket-buffer latency, not device-side completion"}
```

Emit end summary after traffic:

```json
{"run":"end","event":"summary","run_id":"...","ok":true,"summary":{"rest":{"requests":600,"failed_requests":0,"request_bytes":2000000,"elapsed_s":10.1,"requests_per_s":59.4,"payload_bytes_per_s":198019.8,"min_ms":8.1,"median_ms":14.7,"p90_ms":18.5,"p95_ms":20.2,"p99_ms":31.4,"max_ms":38.6},"dma":{"requests":600,"failed_requests":0,"request_bytes":2000000,"elapsed_s":10.0,"requests_per_s":60.0,"payload_bytes_per_s":200000.0,"min_ms":5.4,"median_ms":10.9,"p90_ms":13.5,"p95_ms":14.1,"p99_ms":19.8,"max_ms":22.7}}}
```

Summary stats must include at least:

- request count
- failed request count
- total `request_bytes`
- elapsed seconds
- requests per second
- payload bytes per second
- min latency
- median latency
- p90 latency
- p95 latency
- p99 latency
- max latency

DOCUMENTATION REQUIREMENTS

Update `docs/c64u-u64-cli.md`.

Document:

- how `u64_dma_rest_benchmark.py` differs from `u64_connection_test.py` and `vivipulse`
- that it is a deterministic benchmark / workload generator, not a soak/stress availability tool
- that precise workload shape is JSON-defined and selected by `--traffic`
- the default JSON traffic entries, including `c64cast` and `single-write`
- safety warnings for memory writes, color RAM, VIC/CIA/REC registers, vectors, and REU writes
- JSONL stdout contract and stderr diagnostics
- `request_bytes` meaning
- examples using generic CLI flags only

Required examples:

```bash
./scripts/u64_dma_rest_benchmark.py -H u64
./scripts/u64_dma_rest_benchmark.py -H u64 --traffic c64cast --duration-s 60
./scripts/u64_dma_rest_benchmark.py -H u64 --traffic-config config/u64_dma_rest_benchmark_traffic.json --traffic single-write
./scripts/u64_dma_rest_benchmark.py -H u64 --probes rest --rest-method post
./scripts/u64_dma_rest_benchmark.py -H u64 --probes rest --rest-method put --traffic single-write
./scripts/u64_dma_rest_benchmark.py -H u64 --probes dma --dma-ack-mode barrier
./scripts/u64_dma_rest_benchmark.py -H u64 --schedule sequential --runners 1 --duration-s 60
```

Also document a short JSONL example with `request_bytes` present in every request event.

TESTING REQUIREMENTS

Add unit tests without requiring real hardware.

At minimum test:

1. CLI parser defaults, including default traffic config, traffic name, probes, schedule, runners, REST method, and DMA ack mode.
2. `--help` contains option names and examples aligned with `u64_connection_test.py`, including `-H`, `-d`, `-n`, `-P`, `--network-password`, `--probes`, `--schedule`, `--runners`, and `--duration-s`.
3. `--help` does not contain c64cast-specific workload flags such as `--bitmap-address`, `--display-mode`, `--enable-vic-regs`, or `--dirty-policy`.
4. JSON traffic loader accepts the default config and selects `c64cast`.
5. JSON traffic loader rejects duplicate traffic names.
6. JSON traffic loader rejects malformed addresses, non-positive byte counts, invalid spaces, and invalid write kinds.
7. Default `c64cast` JSON profile expands to the ordered target requests `$0400`/1000, `$D800`/1000, `$2000`/8000, with labels used only for readability.
8. Default `c64cast` JSON profile sums to `10000` bytes per unit.
9. `single-write` JSON profile generation.
10. JSON `bank_cycle`, `repeat`, and dirty sub-range expansion if implemented.
11. Deterministic payload generation from seed and logical write identity across REST and DMA phases.
12. Changing only JSON labels does not change emitted requests, payloads, summaries, or pass/fail behavior.
13. REST PUT path building and 128-byte limit.
14. REST PUT rejects odd-length or non-hex generated query data before sending.
15. REST POST path building, `application/octet-stream`, `Content-Length`, and raw payload handling.
16. REST non-2xx response handling records failed request events, including HTTP 403.
17. HTTP `X-Password` header behavior and `-P/--ftp-pass` legacy alias behavior.
18. DMA command frame construction for `0xFF06` with little-endian address prefix.
19. DMA rejects single-frame `DMAWRITE` payloads above `65533` raw bytes unless explicit JSON split mode is implemented.
20. DMA REU command frame construction for `0xFF07`.
21. DMA rejects single-frame `REUWRITE` payloads above `65532` raw bytes unless explicit JSON split mode is implemented.
22. DMA authentication frame construction for `0xFF1F`.
23. DMA authentication success and failure response handling.
24. Barrier-mode DMA timing path using fake socket responses and zero-length debug-register barrier.
25. Send-only DMA warning event.
26. JSON request events include target address and `request_bytes` for every request.
27. Failure event shape.
28. Summary percentile calculations.
29. Warmup events are labeled and excluded from measured summaries.
30. Address range validation rejects writes past `$FFFF` for REST and DMA before sending.
31. Start events include the inspected firmware source checkout path and commit when available.

Use fake HTTP and fake socket classes for tests. Do not sleep in unit tests. Abstract time and sleep enough to keep tests deterministic.

VALIDATION COMMANDS

Run:

```bash
./build lint
./build test
```

If the repository workflow is reasonably fast, also run:

```bash
./build
```

Real-device benchmark validation is required for Definition of Done:

```bash
./scripts/u64_dma_rest_benchmark.py -H u64 --traffic c64cast --duration-s 60 > artifacts/u64_dma_rest_benchmark/u64-c64cast-1min.jsonl
```

Rules for this real-device run:

- It must target host name `u64`.
- It must run for at least 60 seconds of measured workload.
- It must include both REST and DMA phases unless a protocol is impossible on the real target; if one protocol is impossible, document the exact failure and do not mark Definition of Done complete.
- It must finish with no request errors, no malformed JSONL, and a final summary where `ok` is `true`.
- Stdout must be captured as JSONL under `artifacts/u64_dma_rest_benchmark/`.
- Any stderr diagnostics must be captured or summarized in `WORKLOG.md`.
- The run must not use `--dma-ack-mode send-only` for the primary comparison; use barrier-mode DMA latency.

If any command cannot be run in the current environment, document the exact command, exact reason, and what was run instead in `WORKLOG.md` and the final response.

FINAL BENCHMARK REPORT

Create a concise final comparison report after the real-device run:

```text
artifacts/u64_dma_rest_benchmark/u64-c64cast-comparison.md
```

The report must make the REST vs DMA result easy to scan. Include:

- exact command line used
- target host and ports
- inspected 1541Ultimate firmware checkout path and commit, plus whether the checkout was dirty
- traffic config path, traffic name, logical unit, rate target, duration / iterations, payload pattern, seed, probes, schedule, REST method, DMA ack mode, and barrier
- a compact table of the logical calls emitted per unit, including optional label, protocol support, command / REST method, target address space, target address, request bytes, repeat count, and calls per unit
- a transport comparison table with requests, failed requests, total request bytes, elapsed seconds, requests/s, payload bytes/s, median latency, p90, p95, p99, and max latency
- a one-sentence source-grounded comparability note: REST PUT/POST and socket-DMA `DMAWRITE` all reach `C64_DMA_RAW_WRITE` for C64 address-space writes, so the comparison is primarily HTTP front door plus attachment handling versus TCP/64 command framing plus barrier semantics, not different final memory-copy routines
- a concise explanation of whether either transport met the configured rate without backlog
- estimated achievable units/s for REST and DMA, derived from measured throughput and the configured per-unit request-byte budget
- for traffic metadata that declares `unit: "frame"`, label units/s as estimated FPS
- a second unit-rate estimate derived from per-unit serialized latency budget, using the sum or representative percentile of per-write latencies grouped by emitted write index and address/bytes, and clearly label the method used
- a short interpretation section explaining the performance difference without overstating causality

Rate estimation requirements:

- For payload-throughput unit rate, compute:

```text
payload_units_per_s = payload_bytes_per_s / measured_request_bytes_per_unit
```

- For serialized-latency unit rate, compute from the per-unit write set for each transport. If the tool records per-unit aggregate latency directly, use that. Otherwise estimate:

```text
serialized_units_per_s_p50 = 1000 / sum(per_write_median_latency_ms)
serialized_units_per_s_p95 = 1000 / sum(per_write_p95_latency_ms)
```

- Clearly state that serialized-latency rate assumes writes are issued serially on one client connection with the configured barrier / response semantics.
- If dirty policies, skipped units, warmup, failed requests, or REST/REU incompatibilities affect the denominator, document exactly how `measured_request_bytes_per_unit` was computed.

ACCEPTANCE CRITERIA

The task is complete only when all are true:

- `scripts/u64_dma_rest_benchmark.py` exists, is executable, and has `--help`.
- `config/u64_dma_rest_benchmark_traffic.json` exists and contains at least `c64cast` and `single-write` traffic entries.
- CLI flags are generic and aligned with `u64_connection_test.py` naming/style where concepts overlap.
- CLI does not expose c64cast-specific workload-shape flags.
- Default invocation selects JSON traffic `c64cast`.
- Workload shape is JSON-defined and configurable without code changes.
- Benchmark behavior is driven by target address space, target address, byte count, write kind, and ordering; labels are optional human annotations only.
- REST and DMA phases use identical logical writes where both protocols can represent the write.
- REST supports `POST`, `PUT`, and `auto`.
- DMA supports `0xFF06` writes, `0xFF07` REU writes where JSON uses REU space, and authenticates with `0xFF1F` when configured.
- DMA default timing uses a same-socket command-serialization barrier or fails clearly if no safe barrier is available.
- Stdout is valid JSONL only.
- Every per-request event contains `request_bytes`.
- Final summary compares REST and DMA using request counts, bytes, throughput, and latency percentiles.
- Documentation explains how this differs from existing soak/stress tooling and how JSON traffic selection works.
- Unit tests cover parser/help, JSON traffic loading, protocol framing, JSON output, source-confirmed limits, auth handling, and summary statistics.
- `PLANS.md` and `WORKLOG.md` are updated.
- `./build lint` and `./build test` pass, or any failure is fully documented with exact output and root-cause notes.
- A real-device benchmark against host `u64` runs for at least 1 minute with both REST and DMA phases, barrier-mode DMA, valid JSONL, zero request errors, and `ok:true` in the final summary.
- `artifacts/u64_dma_rest_benchmark/u64-c64cast-comparison.md` exists and concisely compares calls made, latency, throughput, and estimated achievable units/s / FPS for REST and DMA.

FINAL RESPONSE REQUIREMENTS

When finished, report:

1. Files changed.
2. Main command examples.
3. Validation commands and outcomes, including the mandatory at-least-1-minute `-H u64` benchmark result.
4. Path to the final comparison report and the headline REST vs DMA latency, throughput, and estimated units/s or FPS numbers.
5. Any assumptions that still require upstream source or real U64/C64U hardware confirmation.
6. A concise note explaining why this is a generic benchmark tool rather than a c64cast-specific or soak/stress tool.
