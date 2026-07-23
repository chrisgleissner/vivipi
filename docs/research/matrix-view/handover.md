# Matrix View — Handover

> Authoritative remaining-work brief for the next agent. `docs/spec.md` is the
> product source of truth; keep business logic in `src/vivipi/core`
> (CPython-testable) per `AGENTS.md`. Be decisive: execute the numbered tasks in
> order, run the gates in §5, and do not declare done without the hardware proof.

## Headline

The **matrix single-page view**, the **extra probe types** (`PING`/`IDENT`/`DMA`),
and the **matrix vertical-fill** are all **implemented and deployed to the OLED
Pico.** Critically, **the "Pico not using the full screen" issue is RESOLVED** —
on the live 128×64 SH1107 in `matrix` mode the header + target rows now span the
full height with enlarged glyphs, down to just above the bottom progress pixel.

There are **two** open items, in priority order:
1. **[CRASH] The Pico periodically restarts (hardware watchdog reset), especially
   when a probed device is unreachable** — it began when U64 was powered off and
   the reset happens as the probe sweep reaches U64. This is the top priority and
   needs careful root-causing + a targeted fix (§2.1).
2. Probe-reliability soak validation, repo gates, traceability, and an optional
   3px polish (§2.2–§2.5).

---

## 1. DONE (do not redo)

### 1.1 Matrix view — DONE
- `src/vivipi/core/models.py` — `DisplayMode.MATRIX`.
- `src/vivipi/core/display.py` — `DISPLAY_MODES` + validator accept `matrix`.
- `src/vivipi/core/render.py` — matrix renderer: `_matrix_parse_check`,
  `_matrix_cell_glyph`, `_matrix_overview_frame`; column model `P R F T I D`
  (`MATRIX_COLUMN_KEYWORDS`); glyphs `.`/`!`/`X`/`?`/space; multi-probe targets
  first, single-probe (PIXEL4) last; `failure_spans` mark failed cells; matrix
  never paginates (`runtime/app.py::_advance_page` short-circuits for `MATRIX`).
- `config/checks.local.yaml` — added `PING`/`IDENT`/`DMA` for C64U & U64 and
  `PING`/`IDENT` for U2; `PIXEL4 ADB` is the lone `P`-class phone row.
- `config/build-deploy.local.yaml` — `devices.oled` uses `mode: matrix`.

### 1.2 Full-screen vertical fill — DONE (this was the reported defect; now resolved)
The matrix now uses the entire vertical real estate (minus the 1px progress
pixel). The mechanism, all deployed and unit-tested:
- `Frame.row_layout: tuple[(y_origin_px, glyph_height_px), ...] | None`
  (`src/vivipi/core/render.py`).
- `matrix_row_layout(content_row_count, display_height_px, reserved_bottom_px,
  base_font_height_px)` — spreads the configured rows across the full height and
  enlarges each glyph. On the OLED: 5 rows × 12px pitch, glyphs scaled 8×12.
- Geometry plumbing: `render_frame(..., display_height_px, font_height_px,
  reserved_bottom_px)` → `_overview_frame(..., geometry)` →
  `_matrix_overview_frame(..., geometry)` → `_resolve_matrix_row_layout`.
- `runtime/app.py::render_once` passes the live `self.display.height`,
  `self.display.font_height`, and `RuntimeApp._bottom_indicator_reserved_px()`.
- `firmware/displays/rendering.py::render_to_surface(..., glyph_builder=None)`
  honors `frame.row_layout` (per-row y origin + per-row glyph height, with a
  height→glyph-lookup cache). **All** backends pass
  `glyph_builder=_build_glyph_lookup`
  (`sh1107/ssd1305/st77xx/waveshare_epaper*/waveshare_epaper_mono`).
- Verified on-device: deployed `:vivipi/core/render.py`,
  `:displays/rendering.py`, `:vivipi/runtime/app.py` all contain the new
  plumbing. The screen now fills as required.

### 1.3 Probe reliability fix — DONE (deployed; currently green)
`src/vivipi/runtime/checks.py`:
- Added `ICMP_RECV_BUFFER_BYTES=512`, `IDENT_RECV_BUFFER_BYTES=1024`,
  `SOCKET_STREAM_RECV_BYTES=1024`, replacing the old 4096-byte buffers that
  failed to allocate on the fragmented Pico heap under multi-probe load
  ("memory allocation failed, allocating 4096") and flipped healthy
  `PING`/`IDENT` to FAIL.
- `_maybe_collect_gc()` at entry/`finally` of `_raw_icmp_ping`,
  `portable_ident_runner`, `portable_dma_runner`.
- Host truth source (`scripts/c64_health_check`, the `h` alias) shows all
  surfaces `OK`; deployed Pico serial shows `PING`/`IDENT`/`DMA` `OK`.

### 1.4 Tests added — DONE (passing)
`tests/unit/core/test_render.py` (matrix + layout), `tests/unit/firmware/test_display.py`
(`render_to_surface` row_layout), `tests/unit/tooling/test_build_deploy.py`
(mode validation). `pytest tests/unit/runtime tests/unit/core tests/unit/firmware`
= **606 passed**.

---

## 2. REMAINING WORK (in priority order)

### 2.1 [CRITICAL] Eliminate watchdog resets when a probed device is unreachable

**Symptom (reproduced by the user).** The Pico periodically restarts on its own
(board reset, not a clean re-render). It started when U64 was powered off, and
the restart occurs as the probe sweep progresses to U64. This is a hard reboot,
so it needs careful, evidence-based root-causing — do not guess-and-patch.

**Root cause (strong, code-evidenced — confirm before fixing).** The board runs
a hardware watchdog:
- Armed at **~8388ms** — `firmware/runtime.py::_watchdog_timeout_ms` computes a
  large value from the sum of probe timeouts then clamps to
  `WATCHDOG_MAX_TIMEOUT_MS = 8388` (the RP2040 WDT rejects anything higher; see
  WORKLOG 2026-04-20). So the window is ~8.4s and cannot be raised.
- Fed via `set_probe_activity_callback(runtime_watchdog.feed)`
  (`firmware/runtime.py:697`), and `_emit_probe_activity()` is called inside the
  **sliced `_socket_wait` poll loop** (`src/vivipi/runtime/checks.py` ~1037/1049/1056).
  Therefore the **TCP connect paths (DMA/HTTP/FTP/TELNET) feed the watchdog every
  ~1s while waiting** — they are safe even against an unreachable host.
- **Two probe paths block on a single `recvfrom` with NO mid-wait feed:**
  - `_raw_icmp_ping` (`src/vivipi/runtime/checks.py`): `handle.settimeout(timeout_s)`
    then a blocking `recvfrom()` loop. Against an unreachable host no ICMP reply
    arrives, so `recvfrom` blocks the full probe timeout (~8s). The only feed is
    one `_emit_probe_activity()` at `_single_ping` entry (line ~565).
  - `portable_ident_runner` (`src/vivipi/runtime/checks.py`): a blocking
    `handle.recvfrom()` per attempt across `IDENT_MAX_ATTEMPTS`, no mid-recv feed.

When U64 is off, `u64-ping` (the first U64 probe) blocks ~8s without feeding the
watchdog; with the same-host backoff (`probe_schedule.same_host_backoff_ms=1000`)
+ GC/overhead, the gap since the last feed exceeds the **8388ms** window →
**hardware watchdog reset**. This exactly matches "restart as it starts probing
U64." `u64-ident` has the same defect and would be the next trigger.

**Careful root-causing procedure (do all of this):**
1. **Capture the reset reason + full serial across a restart with U64 off.** Use
   raw serial capture (NOT `mpremote resume`, which masks resets by attaching to
   the REPL): 
   ```
   timeout 240 cat /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_740c0800366c92bb-if00 \
     | tee /tmp/u64-off-restart.log
   ```
   Confirm a watchdog-initiated reboot: a second boot banner
   (`[vivipi] [BOOT][WDT] armed timeout_ms=8388` / `[BOOT][BOOT] loading config`)
   appearing abruptly mid-sweep, and identify the **last probe logged before the
   gap** (expect `u64-ping` probe-start with no matching probe-end, or
   `u64-ident`). Capture the raw `machine.reset_cause()`/WDT reason if exposed.
2. Confirm the armed window on the deployed board is 8388ms (from the boot log)
   and that the last-feed→reset gap is ~8s+.
3. Confirm which path owns the gap (ping vs ident) from the final probe-start
   line before the reboot.

**Fix (minimal, targeted — preserve all probe classification/latency/detail
semantics):** make the blocking recv loops feed the watchdog mid-wait, exactly
the way `_socket_wait` does, so the max gap between feeds across **every** probe
path is bounded well under 8388ms (target ≤ ~2s) even against unreachable hosts:
- `_raw_icmp_ping`: replace `settimeout(timeout_s)` + single blocking `recvfrom`
  with a deadline-bounded loop using a short socket timeout (e.g., `settimeout(1.0)`)
  that calls `_emit_probe_activity()` and re-checks the probe deadline each
  iteration; on `recvfrom` timeout without a matching reply, continue until the
  deadline, then return `PingProbeResult(ok=False, details="timeout")`.
- `portable_ident_runner`: slice the UDP `recvfrom` wait the same way and feed
  the watchdog each slice.
- Do NOT add retry loops or change single-attempt semantics; this is bounded
  *progress* during a single attempt, like the existing `_socket_wait` slicing.

Add focused unit tests proving: (a) the ping recv loop feeds the activity
callback at least once mid-wait when no reply arrives before the deadline, and
(b) it still returns a `timeout` result (not a crash) when the target never
answers.

**Verification (must reproduce the unreachable case, not just healthy hosts):**
- With U64 (and then each other target in turn) **powered OFF** and the watchdog
  armed, run a sustained soak (≥5 min) and assert **ZERO restarts** (no second
  boot banner in the serial log) and that the unreachable probes classify cleanly
  as `FAIL`/`timeout` without rebooting.
- Then re-run with all targets on to confirm no regression.

> Note: this subsumes the healthy-host soak — the §2.2 soak must include an
> unreachable target from now on.

### 2.2 [REQUIRED] Validate probe reliability with a sustained soak
The buffer/GC fix (§1.3) is green now but the original failure was intermittent
under sustained multi-probe heap pressure. Combine with §2.1's unreachable-target
case.
1. Run a ≥2-minute Pico serial soak with **all targets on** and assert zero
   unhealthy lines and zero allocation failures:
   ```
   timeout 150 cat /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_740c0800366c92bb-if00 \
     | grep -aE 'status=FAIL|status=DEG|memory allocation failed|allocat'
   ```
   Expected: no output (clean). All `c64u/u64/u2`
   `{ping,ident,dma,ftp,rest,telnet}` and `pixel4-adb` must stay `OK`.
2. Cross-check the host truth source: `scripts/c64_health_check` reports all `OK`.
3. If intermittent `PING`/`IDENT`/`DMA` FAILs reappear (with hosts on), do not
   widen scope — levers, in order: raise `_maybe_collect_gc` aggressiveness,
   shrink recv buffers only where payload-safe, or ease per-host pressure via
   `probe_schedule.same_host_backoff_ms` in `config/build-deploy.local.yaml`.

### 2.3 [OPTIONAL] Absorb the last ~3px for a truly edge-to-edge fill
Current fill is floor-pitch: 5 rows × 12px = 60px, progress pixel at y=63, ~3px
gap above it. Accepted as resolved by the user — polish only. If desired, give
the **last** row the remainder in `matrix_row_layout`:
```
for i in range(content_row_count):
    y = i * pitch
    h = (usable - y) if i == content_row_count - 1 else pitch
    layout.append((y, h))
```
→ `((0,12),(12,12),(24,12),(36,12),(48,15))`, `PIXE` touching the progress pixel.
Update `test_matrix_row_layout_spreads_rows_across_full_display_height` and assert
`layout[-1][0] + layout[-1][1] == usable` for several row counts.

### 2.4 [REQUIRED] Spec traceability + WORKLOG
- Matrix is a new display mode. Review `docs/spec-traceability.md`; add a matrix
  mapping if `[VIVIPI-DISPLAY-002]`/`[VIVIPI-DISPLAY-005]` coverage shifts; keep
  every requirement mapped.
- Append a `WORKLOG.md` entry: matrix view + full-screen fill + probe buffer/GC
  fix + the §2.1 watchdog fix, with all validation evidence (unreachable-target
  soak, healthy-host soak, build/parity gates, hardware proof).

### 2.5 [REQUIRED] Full repo gates + hardware proof — see §3.

---

## 3. Definition of done

- [ ] **[CRITICAL] No watchdog resets with a target powered off:** with U64 off
      (and each other target off in turn) and the watchdog armed, a ≥5-min soak
      shows **zero** board restarts (no second boot banner); unreachable probes
      classify as `FAIL`/`timeout` without rebooting — §2.1.
- [ ] ≥2-min Pico probe soak clean (no FAIL/DEG/allocation errors) with all
      targets on — §2.2.
- [ ] `h` alias shows all surfaces `OK`.
- [ ] `./build lint`, `./build test`, `./build coverage` (≥96% branch).
- [ ] Python parity: `./build ci --venv .venv-3.12` **and** `.venv-3.13`
      (`.github/workflows/ci.yml` is the source of truth for the matrix).
- [ ] `./build deploy` succeeds for the OLED device (epaper offline — its error
      is expected/acceptable here).
- [ ] Hardware proof: live framebuffer dump or camera capture showing the matrix
      filling the full OLED height with enlarged glyphs (required by `AGENTS.md`).
- [ ] `WORKLOG.md` updated; `docs/spec-traceability.md` current.

---

## 4. How to capture hardware proof (matrix fill + probe health)

- **Frame layout sanity (quick, non-visual):** replicate the live `render_once`
  geometry on-device with deployed modules and confirm `row_layout` is non-None:
  ```
  mpremote connect auto eval "
  import sys; sys.path.insert(0,'')
  import runtime as rt
  app = rt.build_runtime_app_from_path('config.json')
  st = app._display_state()
  from vivipi.core.render import render_frame
  f = render_frame(st, display_height_px=int(getattr(app.display,'height',0)) or None,
                   font_height_px=int(getattr(app.display,'font_height',0)) or None,
                   reserved_bottom_px=app._bottom_indicator_reserved_px())
  print('mode', st.display_mode, 'rows', len(f.rows), 'row_layout', f.row_layout)
  "
  ```
  Expect `row_layout=((0,12),(12,12),(24,12),(36,12),(48,12))` (or `…,(48,15)`
  if §2.2 is applied). If it prints `None`, the live `app.display` lacks
  `.height`/`.font_height` (headless fallback) or `config.json` mode isn't
  `matrix` — fix the OLED init/config, not the layout math.
- **Live pixels (definitive):** the running app has no global handle, so either
  temporarily add `global _LAST_APP` after `app = build_runtime_app_from_path(...)`
  in `firmware/runtime.py::run_forever`, deploy, then
  `mpremote ... eval "print(bytes(_LAST_APP.display.buffer).hex())"` and decode
  the 128×64 MONO_VLSB buffer (the lowest lit y of the `PIXE` row must be ≈ y=60),
  or photograph the OLED. Remove the temporary global before finishing.

---

## 5. Key anchors

- Matrix + layout: `src/vivipi/core/render.py` (`matrix_row_layout`,
  `_matrix_overview_frame`, `_resolve_matrix_row_layout`, `_overview_frame`,
  `render_frame`, `Frame.row_layout`, `MATRIX_DEFAULT_COLUMNS`).
- Live render entry (geometry): `src/vivipi/runtime/app.py::render_once` + 
  `RuntimeApp._bottom_indicator_reserved_px`.
- Pixel renderer (honors `row_layout`): `firmware/displays/rendering.py::render_to_surface`.
- OLED backend: `firmware/displays/sh1107.py::draw_frame`.
- Geometry source: `firmware/runtime.py::build_runtime_app` (font_width/height,
  text_height_px, page_size).
- Config: `config/build-deploy.local.yaml` (`devices.oled`, `mode: matrix`),
  `config/checks.local.yaml`.
- Probes: `src/vivipi/runtime/checks.py` (`_raw_icmp_ping`, `portable_ident_runner`,
  `portable_dma_runner`, `_maybe_collect_gc`, `*_RECV_BUFFER_BYTES`).

---

## 6. TL;DR

The matrix view, full-screen fill, and probe buffer/GC fix are **done and
deployed**; the full-screen issue is **resolved**. Two open items, in priority
order: **(1) [CRITICAL] fix the watchdog reset that reboots the Pico when a
probed device is unreachable** — root cause is the blocking `recvfrom` loops in
`_raw_icmp_ping` and `portable_ident_runner` not feeding the ~8.4s hardware
watchdog; slice those waits to feed the watchdog mid-wait and verify with U64 off
(§2.1); **(2)** run the healthy-host soak (§2.2), optionally edge-to-edge the
last row (§2.3), update traceability + WORKLOG (§2.4), and pass the full
`./build` + Python 3.12/3.13 parity + hardware-proof gates (§3, §5).
