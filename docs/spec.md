# ViviPi Specification

Version: 1.2
Status: Active

A minimal, calm, glanceable monitoring system for a 128×64 monochrome display.

---

# 1. Display & Constraints

The system runs on a 128×64 pixel monochrome display (1-bit).

The display is a fixed-width character grid.

Default grid at the default font size:

- 8 rows
- 16 characters per row
- Font: fixed 8×8 bitmap scaled into the configured cell size

Display configuration:

- Character cell width and height are configurable from 6 to 32 pixels
- Visible columns = `floor(128 / cell_width)`
- Visible rows = `floor(64 / cell_height)`

Rules:

- No anti-aliasing
- Monochrome bitmap scaling only
- Rendering remains deterministic and pixel-aligned

[VIVIPI-DISPLAY-001]

Display brightness MUST be configurable at build time.

- SH1107 contrast range: 0 to 255
- Default brightness: medium

[VIVIPI-DISPLAY-002]

Overview rendering MUST support configurable modes and multi-column packing.

- `device.display.mode` supports `standard` and `compact`
- `device.display.columns` supports `1` to `4`
- `device.display.column_separator` MUST be exactly one character

[VIVIPI-DISPLAY-003]

---

# 2. Layout

Each row represents exactly one check.

Format per row:

    <NAME........><STATUS>

Rules:

- NAME is left-aligned
- STATUS is right-aligned
- STATUS occupies up to 4 characters
- NAME is truncated only if necessary using "…"
- Maximum visible checks per page equals the number of rows that fit on screen

Example:

    ROUTER        OK
    NAS           OK
    BACKUP        DEG
    API           FAIL

[VIVIPI-UX-GRID-001]

## Compact Overview Layout

Compact overview mode packs multiple checks into each rendered row.

Given `16` total character cells and `C` columns:

- separators = `C - 1`
- available characters = `16 - separators`
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

# 3. Typography

- The system MUST use a fixed-width bitmap font derived from the 8×8 base glyph set.
- Each character cell uses the configured width and height.

Character rendering rules:

- The bottom row and right-most column of each character cell SHOULD be treated as spacing.
- Glyphs MAY use these pixels only when required for correct shape (e.g. characters such as "Q" or "g").

Layout constraints:

- Visible columns are derived from the configured character cell width
- Visible rows are derived from the configured character cell height
- No text wrapping is allowed

[VIVIPI-UX-TYPO-001]

---

# 4. Idle Mode (No Visible Checks)

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

# 5. Check Model

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
- Optional username and password
- Must log in successfully
- Must list the top-level directory via passive mode
- Failure = login failure, invalid listing, or timeout

### TELNET

- Telnet session
- Optional username and password
- Must log in successfully when prompted
- Must observe valid prompt or session output
- Failure = login failure, invalid output, or timeout

### SERVICE

- HTTP endpoint returning multiple checks
- Each returned check becomes an independent check in the system

[VIVIPI-CHECK-002]

---

## Service JSON Schema

SERVICE endpoints MUST return:

```json
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
````

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

# 6. Check States

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

# 7. Polling & Timing

- Default interval: 15 seconds (configurable)
- Timeout: 10 seconds (configurable)

Constraint:

- Timeout MUST be at least 20% smaller than interval

Timeout is treated as FAIL.

[VIVIPI-CHECK-TIME-001]

---

# 8. Ordering & Pagination

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

# 9. Selection Model

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

# 10. Input Model (2 Buttons)

Button A:

- Move to next check
- Auto-repeat every 500ms
- Wrap cyclically across all checks

Button B:

- Enter detail view
- Return from detail view

Debounce:

- 20–50 ms

[VIVIPI-INPUT-001]

---

# 11. Detail View

Each check has exactly one detail page.

Layout (max visible rows):

```
<CHECK NAME>
STATUS: <STATE>
LAT: <ms>
AGE: <seconds>s
<DETAILS>
```

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

# 12. Diagnostics View

- Structured short messages only
- No raw logs
- No wrapping
- Max visible rows

[VIVIPI-UX-DIAG-001]

---

# 13. Rendering Model

- Event-driven rendering only
- No continuous render loop

Redraw only when:

- state changes
- pixel shift occurs

Rendering must be:

- deterministic
- flicker-free
- stable

[VIVIPI-RENDER-001]

---

# 14. Burn-In Prevention

Global framebuffer shift every 30–60 seconds:

```
(0,0)
(1,0)
(1,1)
(0,1)
```

Rules:

- Entire screen shifts uniformly
- No animation between steps
- Applies to all views

[VIVIPI-RENDER-SHIFT-001]

---

# 15. Architecture

System MUST include:

- State machine (explicit modes)
- Renderer (pure function: state + offset → frame)
- Pixel shift controller
- Input controller
- Check execution layer (no UI logic)

[VIVIPI-ARCH-001]

---

# 16. Performance

- Idle CPU near baseline
- No unnecessary redraws
- Stable memory usage

[VIVIPI-PERF-001]

---

# 17. Determinism

- Same inputs → same outputs
- No randomness allowed

[VIVIPI-DET-001]

---

# 18. Testing

- All requirements must be covered by tests
- ≥ 96% branch coverage
- Each spec section must map to tests

[VIVIPI-TEST-001]

---

# 19. Forbidden

- Animations
- Blinking
- Icons
- Variable-width fonts
- Scrolling text
- Per-element movement
- UI clutter

[VIVIPI-ANTI-001]

---

# END
