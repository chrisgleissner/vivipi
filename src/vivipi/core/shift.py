SHIFT_SEQUENCE = ((0, 0), (1, 0), (1, 1), (0, 1))


class PixelShiftController:
    def __init__(self, interval_s: int = 30):
        if interval_s < 30 or interval_s > 60:
            raise ValueError("interval_s must be between 30 and 60 seconds")
        self.interval_s = interval_s

    def offset_for_tick(self, tick: int) -> tuple[int, int]:
        return SHIFT_SEQUENCE[tick % len(SHIFT_SEQUENCE)]

    def offset_for_elapsed(self, elapsed_s: float) -> tuple[int, int]:
        tick = int(elapsed_s // self.interval_s)
        return self.offset_for_tick(tick)
