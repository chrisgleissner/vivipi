# ViviPi Specification

Version: 1.5
Status: Active

A minimal, calm, glanceable monitoring system for supported Pico display modules while preserving deterministic fixed-width text rendering.

---

## 1. Display & Constraints

The system runs on a supported Pico SPI display selected by `device.display.type`.

Built-in display types:

- `waveshare-pico-oled-1.3` = 128×64 monochrome OLED (`SH1107`)
- `waveshare-pico-oled-2.23` = 128×32 monochrome OLED (`SSD1305` compatible)
- `waveshare-pico-lcd-0.96` = 160×80 color LCD (`ST7735S`)
- `waveshare-pico-lcd-1.14` = 240×135 color LCD (`ST7789`)
- `waveshare-pico-lcd-1.14-v2` = 240×135 color LCD (`ST7789`)
- `waveshare-pico-lcd-1.3` = 240×240 color LCD (`ST7789`)
- `waveshare-pico-lcd-1.44` = 128×128 color LCD (`ST7735S`)
- `waveshare-pico-lcd-1.8` = 160×128 color LCD (`ST7735S`)
- `waveshare-pico-lcd-2.0` = 320×240 color LCD (`ST7789`)
- `waveshare-pico-epaper-2.13-v3` = 250×122 black/white e-paper
- `waveshare-pico-epaper-2.13-v4` = 250×122 black/white e-paper
- `waveshare-pico-epaper-2.13-v2` = 250×122 black/white e-paper
- `waveshare-pico-epaper-2.13-b-v4` = 250×122 black/white/red e-paper
- `waveshare-pico-epaper-2.7` = 264×176 black/white e-paper
- `waveshare-pico-epaper-2.7-v2` = 264×176 black/white e-paper
- `waveshare-pico-epaper-2.9` = 296×128 black/white e-paper
- `waveshare-pico-epaper-3.7` = 480×280 black/white e-paper
- `waveshare-pico-epaper-4.2` = 400×300 black/white e-paper
- `waveshare-pico-epaper-4.2-v2` = 400×300 black/white e-paper
- `waveshare-pico-epaper-7.5-b-v2` = 800×480 black/white/red e-paper

The display is a fixed-width character grid.

Default font behavior:

- `device.display.font` accepts `extrasmall`, `small`, `medium`, `large`, or `extralarge`
- `medium` is the default when no font size is configured
- The preset resolves to a character cell size derived from the display diagonal and pixel geometry so the physical glyph size stays approximately consistent across supported displays
- Visible columns = `floor(display_width_px / cell_width)`
- Visible rows = `floor(display_height_px / cell_height)`
- The default 1.3 inch OLED still resolves to the legacy `16 × 8` grid at the `medium` preset

Display configuration:

- `device.display.type` selects the display backend and MUST infer controller, interface, SPI mode, pixel geometry, and default pins
- Controller-native visible-window calibration such as SH1107 column origin MAY be inferred from `device.display.type` and MAY be overridden for advanced hardware tuning
- `device.display.font` SHOULD use symbolic size presets at build time
- Exact character cell width and height MAY still be overridden for backward-compatible advanced tuning and remain constrained to 6 to 32 pixels
- Visible columns = `floor(display_width_px / cell_width)`
- Visible rows = `floor(display_height_px / cell_height)`

Rules:

- No anti-aliasing
- Bitmap scaling only
- Rendering remains deterministic and pixel-aligned

[VIVIPI-DISPLAY-001]

Display brightness MUST be configurable at build time for display types that support brightness control.

- SH1107 contrast range: 0 to 255
- LCD backlight PWM range: 0 to 255
- Default brightness: medium
- E-paper display types do not expose brightness control
- `device.display.liveness` MAY configure an optional bottom-row device-health indicator
- When enabled on the default OLED overview, the bottom heartbeat SHOULD use a single pixel that advances by one pixel for each completed probe
- If `device.display.liveness` is omitted, all liveness indicators MUST default to disabled

[VIVIPI-DISPLAY-002]

Overview rendering MUST support configurable modes and multi-column packing.

- `device.display.mode` supports `standard` and `compact`
- `standard` mode renders exactly one check per row and therefore requires `device.display.columns = 1`
- `compact` mode supports `device.display.columns` from `1` to `4`
- `device.display.column_separator` MUST be exactly one character

[VIVIPI-DISPLAY-003]

Failed checks MUST use a dedicated display accent color.

- `device.display.failure_color` configures the accent color name
- Default accent color: `red`
- Color-capable displays render failed check text in the configured accent color
- Monochrome displays fall back to the existing high-priority monochrome emphasis

[VIVIPI-DISPLAY-004]

Display support MUST remain modular and type-driven.

- Firmware backends are selected from `device.display.type`
- Core rendering emits backend-agnostic layout and emphasis intent
- Adding a new display type SHOULD require registry-style extension instead of display-specific conditionals throughout the codebase

[VIVIPI-DISPLAY-005]

---

## 2. Layout

Each row represents exactly one check.

Format per row:

  <NAME.......> <STATUS>

Rules:

- NAME is left-aligned
- STATUS is right-aligned
- STATUS occupies up to 4 characters
- NAME is truncated only if necessary using "…"
- Maximum visible checks per page equals the number of rows that fit on screen

Example:

  ROUTER        OK
  NAS           OK
  BACKUP       DEG
  API         FAIL

[VIVIPI-UX-GRID-001]

## Compact Overview Layout

Compact overview mode packs multiple checks into each rendered row.

Given `W` total visible character cells on the current display page and `C` columns:

- separators = `C - 1`
- available characters = `W - separators`
- base width = `floor(available / C)`
- remainder = `available % C`
- the first `remainder` columns use `base width + 1`
- the remaining columns use `base width`

Per rendered column:

- `OK` displays `NAME`
- `DEG` displays `NAME!`
- `FAIL` displays `NAMEX`
- `UNKNOWN` displays `NAME?`
- STATUS suffixes are appended with no padding
- Truncation is hard truncation only, with no ellipsis
- Separators appear only between columns, never after the last column

[VIVIPI-UX-COLUMNS-001]

---

## 3. Typography

- The system MUST use a fixed-width bitmap font derived from the 8×8 base glyph set.
- Each character cell uses the resolved width and height for the selected display and font preset.
- The `medium` preset SHOULD remain approximately the same physical size across supported displays.

Character rendering rules:

- The bottom row and right-most column of each character cell SHOULD be treated as spacing.
- Glyphs MAY use these pixels only when required for correct shape (e.g. characters such as "Q" or "g").

Layout constraints:

- Visible columns are derived from the configured character cell width
- Visible rows are derived from the configured character cell height
- No text wrapping is allowed

[VIVIPI-UX-TYPO-001]

---

## 4. Idle Mode (No Visible Checks)

Display:

        IDLE

Rules:

- Centered horizontally and vertically
- No other elements
- No animation
- Indicates system is running

Checks are not visible until first result is received.

[VIVIPI-UX-IDLE-001]

---

## 5. Check Model

A check represents a single monitored condition.

Checks are defined via YAML configuration at build time.

Checks are evaluated periodically.

[VIVIPI-CHECK-001]

---

## Check Types

### PING

- ICMP ping
- Latency measured locally
- Failure = no response or timeout

### HTTP

- HTTP request
- Status OK if response is 2xx or 3xx
- Latency measured locally
- Failure = non-2xx/3xx or timeout

### FTP

- FTP control session
- Must emit a valid FTP greeting on connection
- Failure = missing or invalid greeting, or timeout

### TELNET

- Telnet session
- Must accept a TCP session
- `OK` requires meaningful TELNET interaction: either IAC negotiation bytes, visible non-whitespace session output, or another successful post-connect read path that proves the session is usable
- `DEG` applies when the TCP session stays open for at least `500 ms` after connect but no TELNET negotiation or visible session output is observed
- Failure = connection failure, explicit login failure text, timeout before establishment, or remote close/reset within `100 ms` before meaningful TELNET interaction

### SERVICE

- HTTP endpoint returning multiple checks
- Each returned check becomes an independent check in the system

[VIVIPI-CHECK-002]

---

## Service JSON Schema

SERVICE endpoints MUST return:

    {
      "checks": [
        {
          "name": "string",
          "status": "OK|DEG|FAIL|?",
          "details": "string",
          "latency_ms": number
        }
      ]
    }

- Payloads MUST contain at most 64 checks.

[VIVIPI-CHECK-SCHEMA-001]

---

## Check Identity

Each check MUST have a stable unique ID.

- Direct checks: ID derived from config name
- Service checks:
  ID = `<service_prefix>:<check_name>`
  If prefix is omitted, use `<check_name>` only

Selection MUST track check identity, not index.

[VIVIPI-CHECK-ID-001]

---

## 6. Check States

Internal states:

- OK
- DEG (degraded)
- FAIL
- UNKNOWN

Display values:

- OK
- DEG
- FAIL
- ?

Rules:

- UNKNOWN is displayed as "?"
- No animation
- FAIL has highest visual priority
- The visible DEG phase MUST be configurable independently from the internal hysteresis state

[VIVIPI-UX-STATUS-001]

---

## State Transitions (Hysteresis)

- OK → DEG after 1 failure
- DEG → FAIL after 2 failures
- FAIL → OK after 1 success
- UNKNOWN → OK on first success

Thresholds MUST be configurable.

[VIVIPI-CHECK-STATE-001]

---

## 7. Polling & Timing

- Default interval: 10 seconds (configurable)
- Timeout: 8 seconds (configurable)

Constraint:

- Timeout MUST be at least 20% smaller than interval

Timeout is treated as FAIL.

[VIVIPI-CHECK-TIME-001]

Probe pacing against the same device MUST be configurable and deterministic.

- Same-device identity is derived from the normalized target host name or IP address.
- On-device direct probes MUST execute without overlap.
- Concurrent probes against the same device MUST default to disabled.
- The minimum time between the end of one probe and the start of the next probe against the same device MUST default to 250ms.
- Same-device concurrency and backoff MUST both be configurable from settings.

[VIVIPI-CHECK-SCHED-001]

Probe transport failures MUST use single-attempt classification with stable failure detail.

- Applies to direct HTTP, FTP, and TELNET transport failures.
- Failure details MUST distinguish at least `timeout`, `dns`, `refused`, `network`, `reset`, and generic `io` failures when those classes can be determined.
- Each scheduled probe attempt MUST run at most once; later retries are deferred to the next configured interval.

[VIVIPI-NET-001]

---

## 8. Ordering & Pagination

- Checks are sorted alphabetically by display name
- Maximum visible checks per page equals the number of rows that fit on screen
- Unlimited total checks supported

If more checks exist than fit on the current page:

- Pagination is used
- Pages are cyclic
- Default automatic page interval: 15 seconds
- Automatic page cycling MAY be disabled with a `0s` interval

[VIVIPI-UX-PAGE-001]

---

## 9. Selection Model

- Exactly one check is selected at all times (if checks exist)
- Selection is identity-based
- Selected check must always be visible

Visual indicator:

- Full row inversion

[VIVIPI-UX-SELECT-001]

In compact overview mode, failed checks invert only the glyph pixels of `NAME + STATUS`.

- Background pixels remain unchanged
- Padding remains unchanged
- Separators remain unchanged

[VIVIPI-RENDER-INVERT-001]

---

## 10. Input Model (2 Buttons)

Button A:

- Move to next check
- Auto-repeat every 500ms
- Wrap cyclically across all checks

Button B:

- Enter detail view
- Return from detail view

- Each press MUST produce a visible acknowledgement, either by changing selection/page state or by showing button feedback
Debounce:

- 20–50 ms

[VIVIPI-INPUT-001]

---

## 11. Detail View

Each check has exactly one detail page.

Layout (max visible rows):

  [CHECK NAME]
  STATUS: [STATE]
  LAT: [ms]
  AGE: [seconds]s
  [DETAILS]

Rules:

- No scrolling
- Lines omitted if data not available
- If overflow occurs, DETAILS is truncated first

Priority:

1. Status
2. Latency
3. Details

[VIVIPI-UX-DETAIL-001]

---

## Detail Navigation

- Button A: next check (cycles)
- Button B: return to overview

Returning preserves selection.

[VIVIPI-INPUT-DETAIL-001]

---

## 12. Diagnostics View

- Structured short messages only
- No raw logs
- No wrapping
- Max visible rows

[VIVIPI-UX-DIAG-001]

---

## 13. Rendering Model

- Event-driven rendering only
- No continuous render loop

Redraw only when:

- state changes
- pixel shift occurs
- bottom-row heartbeat progress occurs

Rendering must be:

- deterministic
- flicker-free
- stable
- visually static while probes are healthy
- Bottom-row heartbeat MAY move a 1 to 3 pixel cluster along the unused bottom scanline without altering layout or text.
- When enabled, the bottom-row heartbeat MUST advance left-to-right and wrap back to the left after reaching the final slot.
- Bottom-row heartbeat progress MUST be driven by completed probes so continued movement proves the runtime is still issuing probes.

[VIVIPI-RENDER-001]

---

## 14. Burn-In Prevention

Global framebuffer shift every 120–300 seconds:

  (0,0)
  (1,0)
  (1,1)
  (0,1)

Rules:

- Entire screen shifts uniformly
- No animation between steps
- Applies to all views

[VIVIPI-RENDER-SHIFT-001]

---

## 15. Architecture

System MUST include:

- State machine (explicit modes)
- Renderer (pure function: state + offset → frame)
- Pixel shift controller
- Input controller
- Check execution layer (no UI logic)

[VIVIPI-ARCH-001]

---

## 16. Boot Logo

On device startup, the display MUST show a boot logo for 4 seconds before the first overview frame replaces it.

Layout:

- "ViviPi" in a large font, centered horizontally and vertically
- Version string below in a smaller font, also centered

Version format:

- On a tagged commit: `<tag>` (e.g. `0.1.0`)
- After a tagged commit: `<tag>-<8-char git hash>` (e.g. `0.1.0-12345678`)

Font sizing:

- Font sizes MUST be calculated dynamically from the screen dimensions
- The configured application font MUST NOT affect boot logo font sizing
- Title font ≤ 55% of screen height and ≤ screen width / title length
- Version font ≤ 2/3 of title font
- All sizes clamped to the valid font range (6–32 pixels)

The boot logo is shown before the first overview frame replaces it.

- WiFi connection MAY begin while the boot logo remains visible.
- After the 4-second boot-logo interval, the firmware proceeds with startup checks and the first overview frame.

[VIVIPI-BOOT-001]

---

## 17. About View

After cycling past the last check in detail view, the system MUST show an About page.

Layout:

      ViviPi
    VER: <version>
    BLD: <build_time>

Rules:

- Lines are omitted if the corresponding value is empty
- Button A returns to the first check's detail view
- Button B returns to overview

The build time is recorded at firmware build time in UTC.

[VIVIPI-UX-ABOUT-001]

---

## 18. Performance

- Idle CPU near baseline
- No unnecessary redraws
- Stable memory usage

[VIVIPI-PERF-001]

---

## 19. Determinism

- Same inputs → same outputs
- No randomness allowed

[VIVIPI-DET-001]

---

## 20. Testing

- All requirements must be covered by tests
- ≥ 96% branch coverage
- Each spec section must map to tests

[VIVIPI-TEST-001]

---

## 21. Forbidden

- Animations
- Blinking
- Icons
- Variable-width fonts
- Scrolling text
- Per-element movement
- UI clutter

[VIVIPI-ANTI-001]

---

## 22. Observability & REPL

Runtime observability MUST provide structured, bounded, machine-parseable logs.

- Supported levels: `DEBUG`, `INFO`, `WARN`, `ERROR`
- Every line MUST be prefixed with `[vivipi]`
- Every line MUST include a component tag
- Log records MUST be retained in a fixed-size in-memory ring buffer
- Runtime logs MUST continue to emit to the local serial/REPL sink and MAY additionally mirror to a configured UDP syslog sink without changing primary runtime behavior
- When syslog mirroring is enabled but unavailable, the device MUST emit at most one bounded warning about syslog unavailability while continuing to retry later deliveries
- Hot paths MUST avoid per-iteration debug spam and MUST keep repeated healthy-check summaries sampled and bounded
- Unhealthy checks MUST log a summary plus additional bounded failure detail
- Every completed probe attempt MUST emit a concise summary including the check identity, type, target, status, and timing details
- Probe-end logs MUST include the probe latency in milliseconds plus per-check-type issued, succeeded, and failed counters that reset on device restart
- Button presses and resulting navigation transitions MUST be logged as distinct bounded events

[VIVIPI-OBS-001]

Critical runtime state MUST be inspectable through REPL-safe APIs.

- Registered checks
- Latest check state
- Current failures
- Network state
- Retained logs
- Retained errors
- Effective syslog configuration and delivery behavior MUST remain diagnosable from retained logs even when the remote sink is unavailable

[VIVIPI-OBS-002]

REPL control MUST expose safe runtime operations without reflashing firmware.

- Run all checks manually
- Reset runtime state
- Reconnect Wi-Fi
- Change log level
- Toggle debug mode
- Query memory metrics and trigger GC collection

[VIVIPI-OBS-003]

Runtime instrumentation MUST retain enough information to reproduce failures deterministically.

- Per-check duration and latency metrics
- Cycle timing metrics
- Captured exceptions with retained trace lines
- Periodic and manual memory snapshots
- Deterministic state snapshots and bounded log retrieval

[VIVIPI-OBS-004]

---

## 23. Fail-Safe Operation

Boot and rendering failures MUST degrade safely instead of aborting the runtime.

- Missing or malformed `config.json` MUST boot with a bounded fallback config that preserves REPL inspection.
- Invalid runtime definition loading MUST degrade to a diagnosable empty-check state instead of crashing startup.
- Display initialization SHOULD fall back to the default OLED backend when possible; if that also fails, the runtime MUST continue in headless mode with retained diagnostics.
- Frame draw failures MUST be retained as errors, activate diagnostics when a display is available, and retry with bounded backoff.

[VIVIPI-FAILSAFE-001]

---

## END
