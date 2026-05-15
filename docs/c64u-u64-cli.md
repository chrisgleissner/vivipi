# C64U / U64 CLI tools

Use these host-side tools when you want a fast read on real C64U / U64 health, a longer soak run, or an intentionally more aggressive direct stress loop.

## Which tool to use

| Tool | Use it for | Good default |
| --- | --- | --- |
| `./scripts/c64_health_check` | One concise pass across both configured C64U and U64 targets | `./scripts/c64_health_check` |
| `./scripts/u64_health_check.py` | The same concise pass, but for one target only | `./scripts/u64_health_check.py u64` |
| `./scripts/vivipulse_stress_test.sh` | A repeatable artifact-producing soak run against the configured checks | `DURATION=30m ./scripts/vivipulse_stress_test.sh` |
| `./scripts/u64_connection_test.py` | Direct protocol-level soak or stress against one host | `./scripts/u64_connection_test.py --profile soak -H u64` |

## Quick health check

`./scripts/c64_health_check` is the fastest "is the box healthy right now?" command. It runs the concise ViviPi-compatible probe set for both targets and prints one line per probe, for example `PING`, `REST`, `IDENT`, `DMA`, `FTP`, and `TELNET`.

```bash
./scripts/c64_health_check
```

Use `./scripts/u64_health_check.py` when you only want one target:

```bash
./scripts/u64_health_check.py c64u
./scripts/u64_health_check.py u64
```

Useful overrides:

```bash
./scripts/u64_health_check.py u64 --build-config config/build-deploy.local.yaml
./scripts/u64_health_check.py c64u --checks-config config/checks.local.yaml
```

These health-check commands resolve targets from the active build config and checks config, so local host aliases in `config/build-deploy.local.yaml` are the normal place to keep real-device addresses current.

## Soak test

`./scripts/vivipulse_stress_test.sh` is the simplest long-run regression check. Despite the historical name, it currently runs `vivipulse` in `soak` mode with parity enabled, emits JSON, writes artifacts under `artifacts/vivipulse/`, and exits non-zero if transport failures, unexpected exceptions, blocked hosts, or parity mismatches occur.

```bash
./scripts/vivipulse_stress_test.sh
```

Common overrides:

```bash
DURATION=2h ./scripts/vivipulse_stress_test.sh
ARTIFACTS_DIR=artifacts/vivipulse/c64u-u64 ./scripts/vivipulse_stress_test.sh
BUILD_CONFIG=config/build-deploy.local.yaml ./scripts/vivipulse_stress_test.sh
```

If you want the underlying command directly, use:

```bash
./scripts/vivipulse --mode soak --duration 2h
```

For the full `vivipulse` flag reference, see [reference.md](reference.md#vivipulse).

## Direct soak and stress loops

`./scripts/u64_connection_test.py` is the low-level direct exerciser. It talks straight to one host and is the right choice when you want explicit protocol mix, runner count, probe surface, or degraded-session behavior.

Be explicit about the host so it is obvious whether you are targeting `u64` or `c64u`:

```bash
./scripts/u64_connection_test.py --profile soak -H u64
./scripts/u64_connection_test.py --profile stress -H u64
./scripts/u64_connection_test.py --profile soak -H c64u --duration-s 300
```

Profile defaults:

| Profile | Default shape | Default duration | Notes |
| --- | --- | --- | --- |
| `soak` | Concurrent read/write-capable probe loop with audio and video stream monitoring | `12h` | Best for stability validation |
| `stress` | Concurrent multi-runner direct loop with more hostile FTP/Telnet behavior | `120s` | Best for reproducing listener or session-lifecycle failures |

Useful options:

```bash
./scripts/u64_connection_test.py --profile stress -H u64 --runners 4
./scripts/u64_connection_test.py --profile soak -H c64u --probes ping,ident,dma,telnet,ftp,http
./scripts/u64_connection_test.py --profile soak -H u64 --network-password '...'
```

## Choosing between `vivipulse` and `u64_connection_test.py`

- Use `vivipulse` when you want runs that stay aligned with ViviPi's shared check configuration and artifact model.
- Use `u64_connection_test.py` when you want direct per-host protocol exercise and tighter control over how aggressive the loop becomes.
