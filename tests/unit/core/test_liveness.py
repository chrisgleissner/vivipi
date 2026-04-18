from vivipi.core.liveness import (
    bottom_heartbeat_active,
    bottom_heartbeat_pixels,
    contrast_breathing_value,
    per_row_micro_active,
    per_row_micro_pixel,
)


def test_contrast_breathing_value_is_quantized_and_clamped():
    assert contrast_breathing_value(128, 8, 45, 0.4) == 128
    assert contrast_breathing_value(128, 8, 45, 1.0) == 129
    assert contrast_breathing_value(252, 8, 45, 11.0) == 255


def test_per_row_micro_helpers_are_deterministic_and_support_stagger():
    assert per_row_micro_pixel(0) == (6, 3)
    assert per_row_micro_pixel(1) == (7, 4)
    assert per_row_micro_active(14.0, 15, row_index=0, stagger=True) is False
    assert per_row_micro_active(14.0, 15, row_index=1, stagger=True) is True


def test_bottom_heartbeat_helpers_anchor_pixels_by_position():
    assert bottom_heartbeat_active(0.0, 20) is True
    assert bottom_heartbeat_active(20.0, 20) is False
    assert bottom_heartbeat_pixels(128, 1, "right") == (127,)
    assert bottom_heartbeat_pixels(128, 1, "center") == (63,)
    assert bottom_heartbeat_pixels(128, 1, "left") == (0,)
    assert bottom_heartbeat_pixels(128, 1, "left", step_index=0, step_px=1) == (0,)
    assert bottom_heartbeat_pixels(128, 1, "left", step_index=1, step_px=1) == (1,)
    assert bottom_heartbeat_pixels(128, 1, "left", step_index=127, step_px=1) == (127,)
    assert bottom_heartbeat_pixels(128, 1, "left", step_index=128, step_px=1) == (0,)