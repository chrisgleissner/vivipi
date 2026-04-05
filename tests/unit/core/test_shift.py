import pytest

from vivipi.core.shift import PixelShiftController


def test_shift_cycle_advances_in_the_expected_order():
    controller = PixelShiftController(interval_s=30)

    assert [controller.offset_for_tick(index) for index in range(5)] == [
        (0, 0),
        (1, 0),
        (1, 1),
        (0, 1),
        (0, 0),
    ]
    assert controller.offset_for_elapsed(59) == (1, 0)


def test_shift_interval_validation_enforces_the_spec_range():
    with pytest.raises(ValueError, match="between 30 and 60"):
        PixelShiftController(interval_s=29)

    with pytest.raises(ValueError, match="between 30 and 60"):
        PixelShiftController(interval_s=61)
