# ViviPi Specification

Version: 1.0
Status: Active

A minimal, calm, glanceable monitoring system for a 128×64 monochrome display.

---

# 1. Display & Constraints

The system runs on a 128×64 pixel monochrome display (1-bit).

The display is a strict character grid:

- 8 rows
- 16 characters per row
- Font: fixed 8×8 bitmap

Rules:

- No anti-aliasing
- No scaling
- Pixel-perfect rendering only

[VIVIPI-DISPLAY-001]

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
- Maximum 8 checks visible at once

Example:

    ROUTER        OK
    NAS           OK
    BACKUP        DEG
    API           FAIL

[VIVIPI-UX-GRID-001]

---

# 3. Typography

- The system MUST use a fixed-width 8×8 bitmap font.
- Each character cell is 8×8 pixels.

Character rendering rules:

- The bottom row (row 7) and right-most column (column 7) of each character cell SHOULD be treated as spacing.
- Glyphs MAY use these pixels only when required for correct shape (e.g. characters such as "Q" or "g").

Layout constraints:

- Exactly 16 characters per row (128 px / 8 px)
- Exactly 8 rows (64 px / 8 px)
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

### REST

- HTTP request
- Status OK if response is 2xx or 3xx
- Latency measured locally
- Failure = non-2xx/3xx or timeout

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
- Maximum 8 visible per page
- Unlimited total checks supported

If more than 8 checks:

- Pagination is used
- Pages are cyclic

[VIVIPI-UX-PAGE-001]

---

# 9. Selection Model

- Exactly one check is selected at all times (if checks exist)
- Selection is identity-based
- Selected check must always be visible

Visual indicator:

- Full row inversion

[VIVIPI-UX-SELECT-001]

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

Layout (max 8 rows):

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
- Max 8 rows

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
- ≥ 91% branch coverage
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
