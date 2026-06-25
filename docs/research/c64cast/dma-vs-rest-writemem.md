# U64 memory writes: socket-DMA vs REST `/v1/machine:writemem`

**Question being decided:** should the 1541Ultimate expose memory writes over **REST only** and drop the
TCP/64 socket-DMA write path? This document benchmarks both transports on the c64cast video-streaming
workload, against a live Ultimate 64, and grounds the result in the firmware source. See https://github.com/GideonZ/1541ultimate/issues/710 for more details.

**Verdict:** on the current firmware, REST-only would drop c64cast from **~36–46 FPS to ~7.5 FPS** for its
default display mode — well below its 20–30 FPS target. The gap is **not** the memory copy (REST and DMA
reach the identical `C64_DMA_RAW_WRITE` routine); it is the HTTP front door. Removing socket-DMA would break
c64cast video streaming **unless** the HTTP server first gains keep-alive and a non-disk write path (§5).

---

## TL;DR — live `u64` (192.168.1.13), 60 s per phase, fresh runs

Default c64cast mode (`hires_edges`, mono, 2 writes/frame = bitmap `$2000` 8000 B + screen `$0400` 1000 B = **9000 B/frame**):

| Transport (device-confirmed write) | req/s | payload | median/write | p99/write | **achievable FPS** |
| --- | --- | --- | --- | --- | --- |
| REST POST (close — the only mode the firmware allows) | 15.0 | 67 KB/s | 68.2 ms | 94.1 ms | **7.5** |
| socket-DMA `0xFF06` + `0xFF76` barrier | 72.2 | 325 KB/s | 16.9 ms | 24.7 ms | **36.1** |
| socket-DMA fire-and-forget (what c64cast actually does, throughput only) | 91.9 | 413 KB/s | n/a* | n/a* | **45.9** |

DMA is **4.8×** faster than REST device-confirmed, **6.1×** faster fire-and-forget. The result reproduces on
the heavier multicolor `mhires` mode (3 writes/frame, 10000 B): REST **5.2 FPS** vs DMA **29.0 / 39.1 FPS**
(5.6× / 7.5×). c64cast targets 20 FPS with audio, 25–30 FPS video-only — **DMA clears it, REST does not.**

\* fire-and-forget latency is host socket-buffer time, **not** device completion; only its sustained
throughput is meaningful (see §2.1).

---

## 1. What is actually being compared

All three converge on the **same** firmware memory-copy routine, so this is a transport comparison, not a
"which write is correct" comparison. Source-verified in `/home/chris/dev/c64/1541ultimate`
(commit `7304ce87`, working tree dirty; quoted regions are the live tree):

- REST `PUT`/`POST /v1/machine:writemem` and socket-DMA `DMAWRITE (0xFF06)` all build
  `SubsysCommand(..., C64_DMA_RAW_WRITE, address, buffer, len)`
  (`route_machine.cc:120,158`, `socket_dma.cc:133-137`).
- `C64_DMA_RAW_WRITE` → `dma_load_raw_buffer` (`c64_subsys.cc:578-602`): **stop the C64 if running →
  `memcpy` into `C64_MEMORY_BASE+offset` → resume if it stopped it → return synchronously.** Per call;
  no batching anywhere in the firmware.

So the **device-side cost is identical** for both transports. Everything that differs is *around* the memcpy.

## 2. Method

### 2.1 Tool and what each mode measures

`scripts/u64_dma_rest_benchmark.py` — a deterministic, JSON-workload-driven memory-write benchmark (generic
CLI; workload shape comes only from `config/u64_dma_rest_benchmark_traffic.json`). stdout is JSONL only.
Three measurement modes, each with a precise meaning:

| Mode | CLI | Measures | Matches c64cast? |
| --- | --- | --- | --- |
| **REST** | default `--rest-method auto` → POST for >128 B | Full HTTP request→response. The response **is** the device acknowledgement (firmware returns only after the memcpy). | n/a — c64cast does not use REST for writes |
| **DMA barrier** | `--dma-ack-mode barrier` (default) | Write frame, then a zero-length `DEBUG_REG (0xFF76)` on the **same socket**; its 1-byte reply cannot arrive until the prior `DMAWRITE` memcpy has completed and the C64 resumed. **Device-confirmed per-write latency.** | Conservative: c64cast does *not* barrier per write |
| **DMA send-only** | `--dma-ack-mode send-only` | `sendall()` of the write frame, no wait. Per-write latency is host-buffer time only and is **not** device completion. Over 60 s, TCP backpressure makes **sustained throughput** converge to the device drain rate. | **Yes** — this is c64cast's real pattern (fire-and-forget) |

Why the barrier is valid (firmware-verified): the socket-DMA server is a **single FreeRTOS task** with a
strictly sequential per-connection loop — recv command → run handler synchronously → recv next
(`socket_dma.cc:453-489`). `DMAWRITE` itself sends no response (`socket_dma.cc:133-137`); the
`DEBUG_REG` reply is a same-socket serialization point, not a write ACK.

**Fairness note for the decision:** the honest *latency* comparison is **REST vs DMA-barrier** (both
device-confirmed). The honest *throughput / FPS* comparison uses REST and **DMA send-only** (c64cast's real
fire-and-forget usage), with DMA-barrier shown as a conservative device-confirmed lower bound. We report all
three so neither transport is flattered.

### 2.2 Workload

c64cast's hot write path is **100% socket-DMA, fire-and-forget**, one persistent connection with
`TCP_NODELAY`, paced to a wall-clock deadline — never a per-write or per-frame network round-trip
(`c64cast` `socket_dma.py:214-249`, `backend.py:144` `writes_are_acked=False`, commit `46cc06d`). The
benchmark mirrors this: persistent DMA socket, `TCP_NODELAY=1`.

Two JSON profiles, both **full-frame worst case**:

- **`c64cast-hires`** — c64cast's *real default* mode `hires_edges` (mono): bitmap `$2000` 8000 B, screen
  `$0400` 1000 B, **no color RAM**. (`c64cast` `config.py:633`, `modes.py:2097-2098`.)
- **`c64cast`** — the heavier multicolor `mhires` variant: screen + color `$D800` + bitmap = 10000 B,
  3 writes/frame. Kept as an upper bound.

Honest caveats baked into the profiles' metadata:
- c64cast diffs each region and a steady frame usually uploads **far fewer** than 9–10 KB
  (`c64cast` `backend.py:476-553`). This does **not** rescue REST: dirty-region cuts *bytes*, but REST is
  bound by *write count* per frame (§4), which stays ~2–3.
- On an REU-equipped U64, c64cast stages bitmap+screen via `REUWRITE (0xFF07)` + a vblank flip rather than
  direct `DMAWRITE`; this benchmark models the direct host-DMA fallback.

### 2.3 Hardware / firmware

| Item | Value |
| --- | --- |
| Target | `u64` = 192.168.1.13, HTTP :80, DMA :64, no network password |
| Firmware | `/home/chris/dev/c64/1541ultimate` @ `7304ce87` (**dirty** tree; quoted source matches the working tree) |
| c64cast ref | `/home/chris/dev/c64/c64cast` @ `46cc06d` (clean) |
| Benchmark | `vivipi` branch `feat/dma-rest-write-mem-benchmark` |

### 2.4 Exact commands

```bash
# Default mode, REST then DMA (device-confirmed), 60 s/phase
./scripts/u64_dma_rest_benchmark.py -H u64 --traffic c64cast-hires --duration-s 60 --dma-ack-mode barrier
# c64cast's real fire-and-forget DMA throughput
./scripts/u64_dma_rest_benchmark.py -H u64 --traffic c64cast-hires --duration-s 60 --probes dma --dma-ack-mode send-only
# heavier multicolor variant
./scripts/u64_dma_rest_benchmark.py -H u64 --traffic c64cast --duration-s 60 --dma-ack-mode barrier
```

Artifacts: `artifacts/u64_dma_rest_benchmark/{hires,mhires}-{barrier,sendonly}.{jsonl,report.json}`. Every
run finished `ok:true` with **zero** failed requests and valid JSONL.

## 3. Results

### 3.1 Throughput and achievable FPS (60 s/phase)

| Profile (B/frame) | Transport | req/s | payload B/s | FPS = payload ÷ B/frame | × vs REST |
| --- | --- | --- | --- | --- | --- |
| hires (9000) | REST POST close | 15.0 | 67,498 | **7.5** | 1.0 |
| hires (9000) | DMA barrier | 72.2 | 325,115 | **36.1** | 4.8× |
| hires (9000) | DMA send-only | 91.9 | 413,420 | **45.9** | 6.1× |
| mhires (10000) | REST POST close | 15.7 | 52,199 | **5.2** | 1.0 |
| mhires (10000) | DMA barrier | 87.0 | 289,865 | **29.0** | 5.6× |
| mhires (10000) | DMA send-only | 117.2 | 390,515 | **39.1** | 7.5× |

REST is **connection-bound at ~15 writes/s regardless of payload size or profile** — the tell that the
bottleneck is per-request HTTP setup, not the memory copy.

### 3.2 Per-write device-confirmed latency (barrier vs REST)

| Write | Bytes | DMA median | REST median | REST − DMA (HTTP front-door tax) |
| --- | --- | --- | --- | --- |
| screen `$0400` | 1000 | 5.5 ms | 54.8 ms | **~49 ms** |
| color `$D800` | 1000 | 5.6 ms | 55.1 ms | **~50 ms** |
| bitmap `$2000` | 8000 | 19.0 ms | 73.5 ms | **~55 ms** |

The shared device-side cost shows up cleanly: a 1 KB write is ~5.5 ms (stop/resume + small memcpy), an 8 KB
write is ~19 ms (same stop/resume + a larger FPGA-shadow memcpy). **REST adds a roughly flat ~50 ms per
write on top** — independent of payload — which is exactly the signature of per-request TCP connect/teardown
plus (for POST) the temp-file disk round-trip.

## 4. Why REST is slower

Both paths pay the identical `dma_load_raw_buffer` stop/memcpy/resume. The REST overhead is everything the
HTTP front door adds and the persistent DMA socket avoids:

1. **No HTTP keep-alive.** Every response is `Connection: close` and the server `shutdown()`+`close()`s the
   socket (`httpd .../routes.h:101,126,135`, `middleware.c:82,135`, `server.c:201-214`). So **every REST
   write pays a full TCP 3-way handshake + teardown.** This caps REST at ~15 writes/s and is payload-
   independent — the single biggest factor. The DMA socket is opened **once per phase**.
2. **POST does a disk round-trip.** The body is streamed to a **temp file**, then `load_file()` reads it
   back into a fresh `new uint8_t[65536]`, then memcpy'd into C64 RAM (`route_machine.cc:126-163`,
   `attachment_writer.h:80-142`). PUT avoids the disk but is capped at 128 bytes (a hex stack buffer), so
   any real frame must use POST.
3. **No pipelining.** REST is strictly request→response serialized; DMA fire-and-forget streams writes back
   to back and lets TCP backpressure throttle.
4. **HTTP parsing.** Header parse, URL/query decode, linear route-table scan, multipart parse (POST) — all
   absent on the DMA path (fixed 4-byte header, single recv into a persistent buffer).

This is why removing DMA is not a wash: c64cast needs ~2–3 device-confirmed writes per frame at 20–30 FPS =
**~40–90 writes/s**, and REST tops out near **15 writes/s** on this firmware.

## 5. Could REST be made as fast as DMA?

Ranked by expected impact for this workload. None were implemented (firmware is out of scope here); each is
a concrete, source-located lever.

1. **HTTP keep-alive (biggest win).** Stop sending `Connection: close` / tearing down the socket per request
   (`server.c:201-214`, `routes.h:101`). This removes the per-request TCP handshake that pins REST at
   ~15 writes/s and is the dominant tax in §3.2. Alone, this likely lifts REST several-fold.
2. **Eliminate the POST temp-file detour.** Stream the request body straight into the write buffer instead
   of disk→read-back (`route_machine.cc:137-139`, `attachment_writer.h`). Removes the payload-dependent disk
   cost on every large write.
3. **A batch / scatter-write endpoint.** One HTTP request carrying several `(address, len, data)` regions →
   one connection **and** one `stop/resume` for a whole frame. The firmware has no multi-region write today
   and `dma_load_raw_buffer` stops/resumes per call; bracketing a frame with a single pause/resume amortizes
   the stop handshake that dominates small writes (this helps DMA too).
4. **`TCP_NODELAY` on the HTTP socket.** The HTTP server sets no `TCP_NODELAY` (`server.c`), unlike the
   c64cast DMA socket; small responses can sit in Nagle.
5. **Binary bulk PUT.** Raise the 128-byte hex cap or add a raw-binary PUT variant so large writes skip both
   the hex expansion and POST's disk path.

Realistic conclusion: **(1)+(2) could make REST viable as a streaming write path; (3) is needed to truly
match DMA.** Until then, the firmware reviewer's summary holds — the memcpy is identical, but *everything
around it* favors the persistent, parse-free DMA socket.

## 6. Validity and caveats

- **send-only latency is not device completion.** We use it only for sustained throughput (TCP backpressure
  bounds it to the device drain rate over 60 s); its per-write latency is excluded from any latency claim.
- **Dirty-region diffing** reduces *bytes/frame* in real c64cast but not *writes/frame*; since REST is
  write-count-bound, the ~15 writes/s ceiling — and thus the FPS gap — survives.
- **REU path not measured.** On REU-equipped U64s c64cast uses `REUWRITE`+vblank flip for bitmap modes; this
  compares the direct host-DMA fallback, which is the path most comparable to a REST replacement.
- **Firmware tree is dirty** (`7304ce87`, 31 modified files). The quoted protocol regions match the working
  tree; `socket_dma.cc` is unmodified, `route_machine.cc`/`c64_subsys.cc` are modified but the quoted code is
  the live state.
- **Benchmark trust.** A prior version stored `perf_counter()` deltas (seconds) in a `latency_ms` field —
  every latency was 1000× too small. It is fixed, and a new hermetic end-to-end test suite
  (`tests/unit/tooling/test_u64_dma_rest_benchmark_e2e.py`) drives the tool against in-process mock U64
  listeners that decode every frame the way the firmware does, asserting request counts, payload bytes,
  addresses, methods, and that latency is reported in milliseconds (a mutation test confirms it catches the
  seconds-bug). Numbers here were independently reproduced across two profiles and match prior `mhires`
  measurements.
