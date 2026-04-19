from __future__ import annotations

from math import pi, sin


CONTRAST_UPDATE_INTERVAL_S = 1.0
HEARTBEAT_STEP_PX = 8
MICRO_PIXEL_X = (6, 7)
MICRO_PIXEL_Y = (3, 4)


def clamp_contrast(value: int) -> int:
    return max(0, min(255, int(value)))


def quantized_time(now_s: float, step_s: float) -> float:
    interval_s = float(step_s)
    if interval_s <= 0:
        return float(now_s)
    return float(int(float(now_s) // interval_s) * interval_s)


def contrast_breathing_value(
    base_contrast: int,
    amplitude: int,
    period_s: int,
    now_s: float,
    update_interval_s: float = CONTRAST_UPDATE_INTERVAL_S,
) -> int:
    if int(amplitude) <= 0 or int(period_s) <= 0:
        return clamp_contrast(base_contrast)
    sample_s = quantized_time(now_s, update_interval_s)
    phase = (sample_s % float(period_s)) / float(period_s)
    offset = float(amplitude) * sin(phase * 2.0 * pi)
    return clamp_contrast(int(round(int(base_contrast) + offset)))


def per_row_micro_pixel(row_index: int) -> tuple[int, int]:
    index = abs(int(row_index))
    return (MICRO_PIXEL_X[index % len(MICRO_PIXEL_X)], MICRO_PIXEL_Y[index % len(MICRO_PIXEL_Y)])


def per_row_micro_active(now_s: float, period_s: int, row_index: int = 0, stagger: bool = False) -> bool:
    if int(period_s) <= 0:
        return False
    phase_offset_s = float(abs(int(row_index))) if stagger else 0.0
    bucket = int((float(now_s) + phase_offset_s) // float(period_s))
    return (bucket % 2) == 1


def bottom_heartbeat_active(now_s: float, period_s: int) -> bool:
    if int(period_s) <= 0:
        return False
    return (int(float(now_s) // float(period_s)) % 2) == 0


def _heartbeat_phase(now_s: float, period_s: int, span: int) -> int:
    if int(period_s) <= 0 or span <= 1:
        return 0
    path = tuple(range(span)) + tuple(range(span - 2, 0, -1))
    if not path:
        return 0
    bucket = int(float(now_s) // float(period_s))
    return path[bucket % len(path)]


def bottom_heartbeat_pixels(
    width_px: int,
    pixel_count: int,
    position: str,
    step_index: int | None = None,
    step_px: int = HEARTBEAT_STEP_PX,
    now_s: float | None = None,
    period_s: int | None = None,
) -> tuple[int, ...]:
    width = max(1, int(width_px))
    count = max(1, min(3, int(pixel_count)))
    normalized_position = str(position).strip().lower()
    max_start = max(0, width - count)
    if step_index is not None:
        step = max(1, int(step_px))
        slot_count = max(1, (max_start // step) + 1)
        base_slot = {
            "left": 0,
            "center": slot_count // 2,
            "right": max(0, slot_count - 1),
        }.get(normalized_position, 0)
        start = ((base_slot + int(step_index)) % slot_count) * step
        start = min(max_start, start)
        return tuple(start + offset for offset in range(count))

    anchors = {
        "left": 0,
        "center": max(0, (width - count) // 2),
        "right": max_start,
    }
    start = anchors.get(normalized_position, anchors["left"])
    if now_s is not None and period_s is not None:
        phase = _heartbeat_phase(float(now_s), int(period_s), 5)
        start = min(max_start, max(0, start + phase))
    return tuple(
        max(0, min(width - 1, start + offset))
        for offset in range(count)
    )