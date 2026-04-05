import pytest

from vivipi.core.text import center_text, column_widths, compact_overview_cell, compact_status_suffix, overview_row, truncate_text


def test_truncate_text_uses_ellipsis_when_required():
    assert truncate_text("ABCDEFGHIJKLM", 12) == "ABCDEFGHIJK…"


def test_truncate_text_handles_single_character_cells():
    assert truncate_text("LONG", 1) == "…"


def test_text_helpers_handle_zero_or_negative_widths():
    assert truncate_text("LONG", 0) == ""
    assert center_text("IDLE", 0) == ""
    assert compact_overview_cell("Router", "FAIL", 0) == ""
    assert overview_row("Router", "FAIL", total_width=0) == ""


def test_center_text_returns_fixed_width_idle_row():
    assert center_text("IDLE") == "      IDLE      "


def test_overview_row_reserves_status_column_and_truncates_name():
    row = overview_row("Android Devices", "FAIL")
    assert len(row) == 16
    assert row.endswith("FAIL")
    assert row.startswith("Android Dev…")


def test_column_widths_distribute_remainder_across_supported_column_counts():
    assert column_widths(16, 1) == (16,)
    assert column_widths(16, 2) == (8, 7)
    assert column_widths(16, 3) == (5, 5, 4)
    assert column_widths(16, 4) == (4, 3, 3, 3)


def test_column_widths_reject_invalid_requests():
    with pytest.raises(ValueError, match="between 1 and 4"):
        column_widths(16, 5)

    with pytest.raises(ValueError, match="must not be negative"):
        column_widths(16, 2, separator_width=-1)

    with pytest.raises(ValueError, match="too small"):
        column_widths(4, 4, separator_width=1)


def test_center_text_clips_before_padding_when_width_is_smaller_than_value():
    assert center_text("LONGVALUE", 4) == "LON…"


def test_compact_status_suffix_maps_only_the_supported_states():
    assert compact_status_suffix("OK") == ""
    assert compact_status_suffix("DEG") == "!"
    assert compact_status_suffix("FAIL") == "X"
    assert compact_status_suffix("?") == "?"


def test_compact_overview_cell_hard_truncates_with_and_without_status_suffix():
    assert compact_overview_cell("Alphabet", "OK", 4) == "Alph"
    assert compact_overview_cell("Alphabet", "FAIL", 4) == "AlpX"
    assert compact_overview_cell("Router", "DEG", 8) == "Router!"
