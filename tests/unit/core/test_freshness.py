from vivipi.core.freshness import decay_freshness_width, missed_interval_windows, reset_freshness_width


def test_decay_freshness_width_steps_down_and_clamps_at_zero():
    width = reset_freshness_width()

    assert width == 8
    assert decay_freshness_width(width) == 6
    assert decay_freshness_width(6) == 4
    assert decay_freshness_width(4) == 2
    assert decay_freshness_width(2) == 0
    assert decay_freshness_width(0) == 0


def test_missed_interval_windows_uses_grace_and_does_not_double_count_inside_a_window():
    assert missed_interval_windows(0.0, 10.9, interval_s=10, grace_s=1.0) == 0
    assert missed_interval_windows(0.0, 11.0, interval_s=10, grace_s=1.0) == 1
    assert missed_interval_windows(0.0, 19.9, interval_s=10, grace_s=1.0) == 1
    assert missed_interval_windows(0.0, 21.0, interval_s=10, grace_s=1.0) == 2