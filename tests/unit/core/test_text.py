from vivipi.core.text import center_text, overview_row, truncate_text


def test_truncate_text_uses_ellipsis_when_required():
    assert truncate_text("ABCDEFGHIJKLM", 12) == "ABCDEFGHIJK…"


def test_truncate_text_handles_single_character_cells():
    assert truncate_text("LONG", 1) == "…"


def test_center_text_returns_fixed_width_idle_row():
    assert center_text("IDLE") == "      IDLE      "


def test_overview_row_reserves_status_column_and_truncates_name():
    row = overview_row("Android Devices", "FAIL")
    assert len(row) == 16
    assert row.endswith("FAIL")
    assert row.startswith("Android Dev…")
