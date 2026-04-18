FULL_FRESHNESS_WIDTH_PX = 8
FRESHNESS_STEP_PX = 2
FRESHNESS_SENTINEL_WIDTH_PX = 0
FRESHNESS_WIDTH_STATES = (0, 2, 4, 6, 8)


def clamp_freshness_width(width_px: int) -> int:
    if width_px <= FRESHNESS_SENTINEL_WIDTH_PX:
        return FRESHNESS_SENTINEL_WIDTH_PX
    if width_px >= FULL_FRESHNESS_WIDTH_PX:
        return FULL_FRESHNESS_WIDTH_PX
    normalized = int(width_px)
    remainder = normalized % FRESHNESS_STEP_PX
    if remainder:
        normalized -= remainder
    return max(FRESHNESS_STEP_PX, normalized)


def decay_freshness_width(width_px: int, steps: int = 1) -> int:
    if steps <= 0:
        return clamp_freshness_width(width_px)
    decayed = clamp_freshness_width(width_px) - (int(steps) * FRESHNESS_STEP_PX)
    return max(FRESHNESS_SENTINEL_WIDTH_PX, decayed)


def reset_freshness_width() -> int:
    return FULL_FRESHNESS_WIDTH_PX


def missed_interval_windows(started_at_s: float | None, now_s: float, interval_s: int, grace_s: float) -> int:
    if started_at_s is None:
        return 0
    if interval_s <= 0:
        return 0
    elapsed_s = float(now_s) - float(started_at_s) - float(grace_s)
    if elapsed_s < float(interval_s):
        return 0
    return int(elapsed_s // float(interval_s))