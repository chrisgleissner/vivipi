import pytest

from vivipi.core.shift import PixelShiftController


def test_shift_cycle_advances_in_the_expected_order():
    controller = PixelShiftController(interval_s=180)

    assert [controller.offset_for_tick(index) for index in range(5)] == [
        (0, 0),
        (1, 0),
        (1, 1),
        (0, 1),
        (0, 0),
    ]
    assert controller.offset_for_elapsed(359) == (1, 0)


def test_shift_interval_validation_enforces_the_spec_range():
    with pytest.raises(ValueError, match="between 120 and 300"):
        PixelShiftController(interval_s=119)

    with pytest.raises(ValueError, match="between 120 and 300"):
        PixelShiftController(interval_s=301)
