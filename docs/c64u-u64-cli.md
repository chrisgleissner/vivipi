# C64U / U64 CLI tools

Use these host-side commands when you want to perform dedicated smoke, soak or stress tests. 

## Start here

If you just want the right command, start with this table:

| Goal | Command | Best when |
| --- | --- | --- |
| Quick check, both targets | `./scripts/c64_health_check` | You want the fastest answer |
| Quick check, one target | `./scripts/u64_health_check.py u64` | You only care about `u64` or `c64u` |
| Longer ViviPi-style run with artifacts | `./scripts/vivipulse_stress_test.sh` | You want a shared-config soak |
| Direct per-host control | `./scripts/u64_connection_test.py --profile soak -H u64` | You want exact control over probes and behavior |

## How the tools fit together

Under the hood:

- `c64_health_check` is a thin wrapper that runs `u64_health_check.py` twice: once for `c64u`, once for `u64`.
- `u64_health_check.py` and `vivipulse` both reuse ViviPi's shared runtime definition and executor path.
- `vivipulse_stress_test.sh` is a thin wrapper around `vivipulse --mode soak`.
- `u64_connection_test.py` is the separate direct path. It talks to protocol drivers directly instead of going through the shared ViviPi scheduler.

```mermaid
flowchart TD
    A[c64_health_check] -->|runs c64u, then u64| B[u64_health_check.py]
    B -->|reuses| C[shared runtime definitions<br/>+ executor]
    D[vivipulse_stress_test.sh] -->|wraps| E[vivipulse]
    E -->|reuses| C
    F[u64_connection_test.py] -->|calls| G[direct protocol drivers]
```

## Quick health checks

Use `./scripts/c64_health_check` for the fastest "what is the state right now?" check. It runs the concise ViviPi-compatible probe set for both configured targets and prints one line per probe, such as `PING`, `REST`, `IDENT`, `DMA`, `FTP`, and `TELNET`.

```bash
./scripts/c64_health_check
./scripts/u64_health_check.py u64
./scripts/u64_health_check.py c64u --build-config config/build-deploy.local.yaml
./scripts/u64_health_check.py u64 --checks-config config/checks.local.yaml
```

These commands resolve targets from the active build and checks configs. Keep real device addresses current in `config/build-deploy.local.yaml`.

## Longer runs with `vivipulse`

Start with `./scripts/vivipulse_stress_test.sh` when you want a longer run that stays aligned with ViviPi's shared config and artifact model. The wrapper runs `vivipulse` in `soak` mode with parity enabled, writes JSON artifacts under `artifacts/vivipulse/`, and exits non-zero on transport failures, unexpected exceptions, blocked hosts, or parity mismatches.

```bash
./scripts/vivipulse_stress_test.sh
DURATION=2h ./scripts/vivipulse_stress_test.sh
ARTIFACTS_DIR=artifacts/vivipulse/c64u-u64 ./scripts/vivipulse_stress_test.sh
BUILD_CONFIG=config/build-deploy.local.yaml ./scripts/vivipulse_stress_test.sh
```

Use `./scripts/vivipulse` directly when you want more control:

```bash
./scripts/vivipulse --mode plan
./scripts/vivipulse --mode local
./scripts/vivipulse --mode reproduce --passes 2
./scripts/vivipulse --mode search --passes 2 --ultimate-repo ../1541ultimate
./scripts/vivipulse --mode soak --duration 2h --same-host-backoff-ms 1000
./scripts/vivipulse --mode soak --duration 30m --stop-on-failure --json
```

Useful `vivipulse` flags:

- **Mode:** `--mode plan`, `--mode local`, `--mode reproduce`, `--mode search`, `--mode soak`
- **Run length and pacing:** `--duration 30m`, `--passes 2`, `--same-host-backoff-ms 1000`, `--allow-concurrent-same-host`
- **Artifacts and logging:** `--artifacts-dir ...`, `--json`, `--debug`, `--parity-mode`
- **Failure handling:** `--stop-on-failure`, `--interactive-recovery --resume-after-recovery`
- **Config inputs:** `--build-config ...`, `--checks-config ...`, `--runtime-config ...`

For the full `vivipulse` flag reference, see [reference.md](reference.md#vivipulse).

## Direct per-host control with `u64_connection_test.py`

Use `./scripts/u64_connection_test.py` when you want to talk to one host directly and control the probe mix, concurrency, protocol depth, stream checks, and intentionally degraded behavior.

Always spell out the host so it is obvious whether you are targeting `u64` or `c64u`:

```bash
./scripts/u64_connection_test.py --profile soak -H u64
./scripts/u64_connection_test.py --profile stress -H u64
./scripts/u64_connection_test.py --profile soak -H c64u --duration-s 300
```

Profile defaults:

| Profile | Default shape | Default duration | Best for |
| --- | --- | --- | --- |
| `soak` | Concurrent read/write-capable probe loop with audio and video stream monitoring | `12h` | Stability validation |
| `stress` | Concurrent multi-runner direct loop with more hostile FTP/Telnet behavior | `120s` | Reproducing listener or session-lifecycle failures |

Useful flags:

- **Probe set:** `--probes ping,ident,dma,telnet,ftp,http[,modem]`
- **Concurrency:** `--schedule sequential|concurrent`, `--runners 4`
- **Protocol depth:** `--surface smoke|read|readwrite`, `--http-surface read`, `--ftp-surface readwrite`
- **Intentional degradation:** `--mode open|incomplete|invalid`, `--ftp-mode invalid`, `--telnet-mode incomplete`, `--http-mode incomplete`
- **Streams:** `--stream`, `--stream audio`, `--stream video`
- **Auth and endpoints:** `-H u64`, `--network-password ...`, `--http-port ...`, `--ftp-port ...`, `--telnet-port ...`

Quick recipes:

```bash
./scripts/u64_connection_test.py --profile stress -H u64 --runners 4
./scripts/u64_connection_test.py --profile soak -H c64u --probes ping,ident,dma,telnet,ftp,http --stream audio
./scripts/u64_connection_test.py --profile soak -H u64 --surface read --http-surface readwrite
./scripts/u64_connection_test.py --schedule concurrent --runners 2 --ftp-mode invalid --telnet-mode incomplete -H u64
# Force the HTTP listener's connection-error teardown path (client-slot exhaustion):
# each http probe opens a connection, sends a partial request, then aborts with a TCP RST.
./scripts/u64_connection_test.py --probes http --http-mode incomplete --schedule concurrent --runners 5 -H u64
./scripts/u64_connection_test.py --profile stress --http-mode incomplete -H u64
```

`--http-mode incomplete` makes the HTTP probe drive the server's `recv()<0` path
(an established connection that resets mid-request) instead of issuing clean
requests. A server that does not free the client slot on a read error will exhaust
its connection table after a handful of aborts and stop accepting connections; a
correct server reclaims the slot and stays responsive.

If you set `--probes` without `--stream`, the profile-default stream checks are disabled. Add `--stream` explicitly when you want stream verification.

## Deterministic memory-write benchmark with `u64_dma_rest_benchmark.py`

Use `./scripts/u64_dma_rest_benchmark.py` when you want to compare REST `/v1/machine:writemem` against U64 TCP/64 socket-DMA (`SOCKET_CMD_DMAWRITE = 0xFF06`, `SOCKET_CMD_REUWRITE = 0xFF07`) under phase-fair, deterministic, JSON-defined workload. This is **not** a soak/stress/availability tool — it is a workload generator that emits an ordered stream of `(address, byte_count)` writes per logical unit and reports latency, throughput, payload rate, and call-shape for each transport.

| Aspect | `u64_connection_test.py` | `u64_dma_rest_benchmark.py` |
| --- | --- | --- |
| Purpose | Direct protocol exerciser, soak/stress | Deterministic memory-write benchmark |
| Workload shape | Per-probe surface flags | JSON traffic profile (`--traffic`) |
| Failure handling | Retry transient transport errors | Single-shot; failed requests are counted, not retried |
| Output | Human-readable line per probe | JSONL on stdout only; diagnostics on stderr |
| Default scheduling | Concurrent probe mix | Sequential REST then DMA (primary comparison) |

Workload shape is fully defined by the selected JSON profile; the CLI has **no** c64cast-specific workload flags. Built-in profiles:

- `c64cast-hires` — c64cast's **real default** display mode `hires_edges` (mono): bitmap `$2000` 8000 B then screen `$0400` 1000 B per frame (9000 B, 2 writes, no color RAM).
- `c64cast` — heavier multicolor `mhires` variant: screen `$0400` 1000 B, color `$D800` 1000 B, bitmap `$2000` 8000 B per frame (10000 B, 3 writes).
- `c64cast-with-audio` — `c64cast` plus one 1024 B audio-ring write to `$4000` per frame (STATEFUL; touches the audio ring).
- `single-write` — one 64-byte write to screen RAM `$0400`, 100 iterations, no pacing (protocol sanity).

For the full REST-vs-DMA analysis and decision (should the firmware drop DMA writes for REST-only?), see [`docs/research/c64cast/dma-vs-rest-writemem.md`](research/c64cast/dma-vs-rest-writemem.md).

Default behavior:

- traffic config: `config/u64_dma_rest_benchmark_traffic.json`
- traffic name: `c64cast`
- probes: `rest,dma`
- schedule: `sequential`
- runners: `1`
- REST method: `auto` (PUT for ≤128 bytes, POST otherwise)
- DMA timing: `barrier` (write-frame send → zero-length `SOCKET_CMD_DEBUG_REG = 0xFF76` response on the same socket)
- HTTP connection: `close`
- DMA connection: `persistent`

Examples:

```bash
./scripts/u64_dma_rest_benchmark.py -H u64
./scripts/u64_dma_rest_benchmark.py -H u64 --traffic c64cast --duration-s 60
./scripts/u64_dma_rest_benchmark.py -H u64 --traffic-config config/u64_dma_rest_benchmark_traffic.json --traffic single-write
./scripts/u64_dma_rest_benchmark.py -H u64 --probes rest --rest-method post
./scripts/u64_dma_rest_benchmark.py -H u64 --probes rest --rest-method put --traffic single-write
./scripts/u64_dma_rest_benchmark.py -H u64 --probes dma --dma-ack-mode barrier
./scripts/u64_dma_rest_benchmark.py -H u64 --schedule sequential --runners 1 --duration-s 60
```

**Safety.** This tool writes C64 RAM, color RAM (`$D800`–`$DBE7`), and — depending on the JSON profile — I/O, VIC/CIA/REC registers, vectors, or REU space. Default profiles write only RAM/screen/color/bitmap regions and do **not** touch VIC/CIA/vectors/REC. The tool does **not** restore modified memory after the run. Reject writes past `$FFFF` and reject non-positive byte counts before any transport request is sent. Pick a target (`-H u64` or `-H c64u`) deliberately and stop the run with Ctrl-C if you need to abort mid-traffic.

**JSONL output contract.** stdout contains JSONL only. Each request event includes `ts`, `event:"request"`, `phase_probe`, `logical_write_id`, `space`, `address`, `request_bytes`, `latency_ms`, `ok`, `warmup`, plus protocol-specific fields (`method`, `path`, `status` for REST; `command`, `ack_mode`, `barrier`, `dma_connection` for DMA). The `request_bytes` field is **raw C64/REU payload bytes only** — it excludes TCP/64 envelope bytes, DMA address prefix bytes, REU offset prefix bytes, HTTP headers, REST query-string hex expansion, JSONL serialization bytes, and barrier command bytes. A short example:

```json
{"event":"request","phase_probe":"rest","address":"0400","request_bytes":1000,"latency_ms":12.4,"ok":true}
{"event":"request","phase_probe":"dma","command":"0xFF06","barrier":"debugreg","address":"0400","request_bytes":1000,"latency_ms":8.1,"ok":true}
```

A final summary event compares REST vs DMA with `requests`, `failed_requests`, `request_bytes`, `elapsed_s`, `requests_per_s`, `payload_bytes_per_s`, `min_ms`, `median_ms`, `p90_ms`, `p95_ms`, `p99_ms`, `max_ms`. DMA uses a same-socket command-serialization barrier (zero-length `SOCKET_CMD_DEBUG_REG`) by default; it is **not** a `DMAWRITE` write-ack (the firmware source sends no such response). Use `--dma-ack-mode send-only` only as an explicit opt-in (a JSON warning event is emitted) — it measures host/socket-buffer latency, not device-side completion.

## Which one should you use?

- Use `c64_health_check` or `u64_health_check.py` when you want a fast answer.
- Use `vivipulse` when you want ViviPi's shared config, scheduling rules, and artifacts.
- Use `u64_connection_test.py` when you want the most direct control over one host.
- Use `u64_dma_rest_benchmark.py` when you want a deterministic, JSON-defined comparison of REST vs DMA write transports.

## Full help: `u64_connection_test.py`

The full current `--help` output is included here for reference.

```text
usage: u64_connection_test.py [-h] [-H HOST] [-d DELAY_MS] [-n LOG_EVERY] [-u FTP_USER] [-P FTP_PASS]
                              [--network-password NETWORK_PASSWORD] [--http-path HTTP_PATH] [--http-port HTTP_PORT]
                              [--ftp-port FTP_PORT] [--telnet-port TELNET_PORT] [--modem-port MODEM_PORT] [-v]
                              [--profile {soak,stress}] [--probes PROBES] [--schedule {sequential,concurrent}]
                              [--runners RUNNERS] [--duration-s DURATION_S] [--surface {smoke,read,readwrite}]
                              [--mode {complete,open,incomplete,invalid}] [--http-surface {smoke,read,readwrite}]
                              [--ftp-surface {smoke,read,readwrite}] [--telnet-surface {smoke,read,readwrite}]
                              [--dma-surface {smoke,read,readwrite}] [--ping-mode {complete,open,incomplete,invalid}]
                              [--http-mode {complete,open,incomplete,invalid}]
                              [--ftp-mode {complete,open,incomplete,invalid}]
                              [--telnet-mode {complete,open,incomplete,invalid}] [--stream [{audio,video} ...]]

Repeated U64 connectivity checks. Default: 12h soak with concurrent readwrite probes and audio+video streams. ident targets UDP port 64 JSON discovery; dma targets the DMA-capable TCP port 64 command endpoint.

options:
  -h, --help            show this help message and exit
  -H HOST, --host HOST  Target host or IP
  -d DELAY_MS, --delay-ms DELAY_MS
                        Delay between checks in milliseconds
  -n LOG_EVERY, --log-every LOG_EVERY
                        Log every Nth successful iteration
  -u FTP_USER, --ftp-user FTP_USER
                        FTP username
  -P FTP_PASS, --ftp-pass FTP_PASS
                        Legacy alias for the shared device network password.
  --network-password NETWORK_PASSWORD
                        Shared device network password used for HTTP, Telnet, FTP, and dma.
  --http-path HTTP_PATH
                        HTTP path
  --http-port HTTP_PORT
                        HTTP port
  --ftp-port FTP_PORT   FTP port
  --telnet-port TELNET_PORT
                        Telnet port
  --modem-port MODEM_PORT
                        Optional modem listener port
  -v, --verbose         Log every successful check
  --profile {soak,stress}
                        Preset profile. Explicit --probes, --schedule, --runners, --surface, --mode, --*-surface, and
                        --*-mode flags override the profile.
  --probes PROBES       Ordered non-empty comma-separated probe list using ping,ident,dma,telnet,ftp,http,modem; ident
                        is UDP/64 and dma is TCP/64. When set without --stream, profile-default streams are disabled.
  --schedule {sequential,concurrent}
                        Per-runner scheduling mode.
  --runners RUNNERS     Logical runner count >= 1.
  --duration-s DURATION_S
                        Optional total run duration in seconds. Soak defaults to 43200 (12h); stress defaults to 120.
  --surface {smoke,read,readwrite}
                        Apply the same surface to all probes, falling back per protocol to the nearest supported lower
                        surface.
  --mode {complete,open,incomplete,invalid}
                        Apply the same correctness mode to all probes, falling back per protocol to the nearest
                        supported lower mode. Correctness degradation: complete (finish and close cleanly), open
                        (finish and skip orderly teardown), incomplete (abort before completion), invalid (send
                        malformed or unsupported input).
  --http-surface {smoke,read,readwrite}
                        HTTP probe surface.
  --ftp-surface {smoke,read,readwrite}
                        FTP probe surface.
  --telnet-surface {smoke,read,readwrite}
                        Telnet probe surface.
  --dma-surface {smoke,read,readwrite}
                        DMA TCP/64 probe surface.
  --ping-mode {complete,open,incomplete,invalid}
                        Ping probe correctness.
  --http-mode {complete,open,incomplete,invalid}
                        HTTP probe correctness.
  --ftp-mode {complete,open,incomplete,invalid}
                        FTP probe correctness.
  --telnet-mode {complete,open,incomplete,invalid}
                        Telnet probe correctness.
  --stream [{audio,video} ...]
                        Verify audio, video, or both UDP streams. Omit values to select both audio and video.

Profile precedence: if --profile is supplied, explicit --probes, --schedule, --runners, --surface, --mode, --*-surface, and --*-mode values override the profile.

Correctness degradation: complete (finish and close cleanly), open (finish and skip orderly teardown), incomplete (abort before completion), invalid (send malformed or unsupported input).

Examples:
  ./u64_connection_test.py
  ./u64_connection_test.py --profile soak
  ./u64_connection_test.py --profile stress
  ./u64_connection_test.py --profile soak --duration-s 300
  ./u64_connection_test.py --surface readwrite
  ./u64_connection_test.py --mode open
  ./u64_connection_test.py --mode incomplete
  ./u64_connection_test.py --probes ping,ident,dma,telnet,ftp,http
  ./u64_connection_test.py --schedule concurrent --runners 3
  ./u64_connection_test.py --http-surface read
  ./u64_connection_test.py --ftp-surface readwrite --telnet-surface read
  ./u64_connection_test.py --telnet-mode incomplete
  ./u64_connection_test.py --schedule concurrent --runners 2 --ftp-mode invalid --telnet-mode incomplete
  ./u64_connection_test.py --profile stress --runners 4
  ./u64_connection_test.py --profile soak --probes ping,http
```
