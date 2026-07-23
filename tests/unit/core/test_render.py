from vivipi.core.models import AppMode, AppState, CheckRuntime, DisplayMode, Status
from vivipi.core.render import InvertedSpan, TextSpan, matrix_row_layout, render_frame


def make_check(
    identifier: str,
    name: str,
    status: Status = Status.OK,
    details: str = "",
    latency_ms: float | None = None,
    last_update_s: float | None = None,
) -> CheckRuntime:
    return CheckRuntime(
        identifier=identifier,
        name=name,
        status=status,
        details=details,
        latency_ms=latency_ms,
        last_update_s=last_update_s,
    )


def test_idle_mode_is_centered_and_uses_the_full_grid():
    frame = render_frame(AppState())

    assert len(frame.rows) == 8
    assert all(len(row) == 16 for row in frame.rows)
    assert frame.rows[3] == "      IDLE      "
    assert frame.inverted_row is None


def test_overview_paginates_with_selected_row_inversion():
    checks = tuple(
        make_check(identifier=name.casefold(), name=name)
        for name in ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India")
    )
    state = AppState(checks=checks, selected_id="india", page_index=1)

    frame = render_frame(state)

    assert frame.inverted_row == 0
    assert frame.rows[0].startswith("India")
    assert all(len(row) == 16 for row in frame.rows)


def test_render_frame_respects_dynamic_grid_dimensions():
    checks = tuple(make_check(identifier=name.casefold(), name=name) for name in ("Alpha", "Bravo", "Charlie", "Delta"))
    state = AppState(checks=checks, selected_id="charlie", row_width=12, page_size=3, page_index=0)

    frame = render_frame(state)

    assert len(frame.rows) == 3
    assert all(len(row) == 12 for row in frame.rows)
    assert frame.rows[2] == "Charlie   OK"
    assert frame.inverted_row == 2


def test_overview_without_selection_does_not_invert_rows():
    state = AppState(checks=(make_check("router", "Router"),), selected_id=None)

    frame = render_frame(state)

    assert frame.inverted_row is None
    assert frame.rows[0].startswith("Router")


def test_overview_can_disable_selection_highlight_without_clearing_selection_state():
    state = AppState(checks=(make_check("router", "Router"),), selected_id="router")

    frame = render_frame(state, highlight_selection=False)

    assert frame.inverted_row is None
    assert frame.inverted_spans == ()
    assert frame.rows[0].startswith("Router")


def test_overview_displays_unknown_status_as_question_mark():
    state = AppState(checks=(make_check("router", "Router", status=Status.UNKNOWN),), selected_id="router")

    frame = render_frame(state)

    assert frame.rows[0].endswith(" ?")


def test_standard_single_column_overview_keeps_legacy_output_exactly():
    state = AppState(
        checks=(make_check("router", "Router", status=Status.FAIL),),
        selected_id="router",
        display_mode=DisplayMode.STANDARD,
        overview_columns=1,
    )

    frame = render_frame(state)

    assert frame.rows[0] == "Router      FAIL"
    assert frame.inverted_row == 0
    assert frame.inverted_spans == ()
    assert frame.failure_spans == (InvertedSpan(row_index=0, start_column=12, end_column=16),)


def test_compact_mode_shows_all_healthy_checks_without_suffixes_when_no_failures_exist():
    state = AppState(
        checks=(
            make_check("bravo", "Bravo", status=Status.OK),
            make_check("alpha", "Alpha", status=Status.OK),
        ),
        selected_id="alpha",
        display_mode=DisplayMode.COMPACT,
        overview_columns=2,
        column_separator="|",
        page_size=1,
    )

    frame = render_frame(state)

    assert frame.rows == ("Alpha   |Bravo  ",)
    assert frame.inverted_spans == (InvertedSpan(row_index=0, start_column=0, end_column=8),)
    assert frame.failure_spans == ()


def test_compact_mode_filters_to_non_healthy_checks_and_marks_only_failed_text_span():
    state = AppState(
        checks=(
            make_check("alpha", "Alpha", status=Status.OK),
            make_check("bravo", "Bravo", status=Status.FAIL),
            make_check("charlie", "Charlie", status=Status.DEG),
        ),
        selected_id="charlie",
        display_mode=DisplayMode.COMPACT,
        overview_columns=2,
        column_separator="|",
        page_size=1,
    )

    frame = render_frame(state)

    assert frame.rows == ("BravoX  |Charli!",)
    assert frame.inverted_spans == (
        InvertedSpan(row_index=0, start_column=9, end_column=16),
    )
    assert frame.failure_spans == (
        InvertedSpan(row_index=0, start_column=0, end_column=6),
    )


def test_compact_multi_column_layout_uses_exact_column_math_and_no_overflow():
    state = AppState(
        checks=(
            make_check("alpha", "Alpha", status=Status.OK),
            make_check("bravo", "Bravo", status=Status.OK),
            make_check("charlie", "Charlie", status=Status.OK),
        ),
        selected_id="alpha",
        display_mode=DisplayMode.COMPACT,
        overview_columns=3,
        column_separator="|",
        page_size=1,
    )

    frame = render_frame(state)

    assert frame.rows == ("Alpha|Bravo|Char",)
    assert len(frame.rows[0]) == 16


def test_detail_view_omits_unavailable_lines():
    state = AppState(
        checks=(make_check("router", "Router", status=Status.OK),),
        selected_id="router",
        mode=AppMode.DETAIL,
    )

    frame = render_frame(state, now_s=100)

    assert frame.rows[0] == "Router          "
    assert frame.rows[1] == "STATUS: OK      "
    assert frame.rows[2] == "                "


def test_detail_view_truncates_details_before_overflowing():
    state = AppState(
        checks=(
            make_check(
                "router",
                "Router",
                status=Status.FAIL,
                details="This details line must be truncated cleanly",
                latency_ms=123.4,
                last_update_s=95,
            ),
        ),
        selected_id="router",
        mode=AppMode.DETAIL,
    )

    frame = render_frame(state, now_s=100)

    assert frame.rows[2] == "LAT: 123ms      "
    assert frame.rows[3] == "AGE: 5s         "
    assert frame.rows[4].endswith("…")
    assert len(frame.rows[4]) == 16
    assert frame.failure_spans == (InvertedSpan(row_index=1, start_column=8, end_column=12),)


def test_diagnostics_view_truncates_without_wrapping():
    state = AppState(mode=AppMode.DIAGNOSTICS, diagnostics=("A" * 20, "B" * 4))

    frame = render_frame(state)

    assert frame.rows[0] == "AAAAAAAAAAAAAAA…"
    assert frame.rows[1] == "BBBB            "


def test_rendering_is_deterministic_for_identical_inputs():
    state = AppState(
        checks=(make_check("router", "Router", status=Status.OK),),
        selected_id="router",
        shift_offset=(1, 0),
    )

    assert render_frame(state, now_s=100) == render_frame(state, now_s=100)


def test_about_page_shows_version_and_build_time():
    state = AppState(mode=AppMode.ABOUT, version="0.1.0", build_time="2025-04-05T12:00Z")

    frame = render_frame(state)

    assert len(frame.rows) == 8
    assert all(len(row) == 16 for row in frame.rows)
    assert "ViviPi" in frame.rows[0]
    assert "VER: 0.1.0" in frame.rows[1]
    assert "BLD: 2025-04-05…" == frame.rows[2]


def test_about_page_omits_empty_version_and_build_time():
    state = AppState(mode=AppMode.ABOUT)

    frame = render_frame(state)

    assert "ViviPi" in frame.rows[0]
    assert all("VER:" not in row for row in frame.rows)
    assert all("BLD:" not in row for row in frame.rows)


def _matrix_state(checks, page_size=7):
    return AppState(
        checks=tuple(checks),
        display_mode=DisplayMode.MATRIX,
        row_width=16,
        page_size=page_size,
    )


def _full_matrix_checks():
    checks = []
    for target in ("C64U", "U64", "U2"):
        for probe in ("PING", "REST", "FTP", "TELNET", "IDENT", "DMA"):
            if target == "U2" and probe == "DMA":
                continue  # the Ultimate-II+ firmware has no DMA/DEBUG_REG
            checks.append(make_check(f"{target}-{probe}".casefold(), f"{target} {probe}"))
    checks.append(make_check("pixel4-adb", "PIXEL4 ADB"))
    return checks


def test_matrix_renders_header_and_one_row_per_target():
    frame = render_frame(_matrix_state(_full_matrix_checks()))

    assert frame.rows[0] == "     P R F T I D"
    assert all(len(row) == 16 for row in frame.rows)
    # Multi-probe targets first (alphabetical), single-probe phone last.
    assert frame.rows[1].startswith("C64U")
    assert frame.rows[2].startswith("U2")
    assert frame.rows[3].startswith("U64")
    assert frame.rows[4].startswith("PIXE")
    # All-healthy => a calm grid of dots, no failure spans.
    assert frame.rows[1] == "C64U . . . . . ."
    assert frame.failure_spans == ()


def test_matrix_omits_cell_for_unconfigured_probe():
    frame = render_frame(_matrix_state(_full_matrix_checks()))

    # U2 has no DMA probe -> the D cell is blank (N/A), not a status glyph.
    u2_row = next(row for row in frame.rows if row.startswith("U2"))
    assert u2_row == "U2   . . . . .  "
    # PIXEL4 only has a P (ADB) probe; the rest are blank.
    pixel_row = next(row for row in frame.rows if row.startswith("PIXE"))
    assert pixel_row == "PIXE .          "


def test_matrix_marks_failed_cells_with_glyph_and_span():
    checks = _full_matrix_checks()
    checks = [
        make_check(c.identifier, c.name, status=Status.FAIL) if c.name == "U2 TELNET" else c
        for c in checks
    ]
    frame = render_frame(_matrix_state(checks))

    u2_row = next(row for row in frame.rows if row.startswith("U2"))
    row_index = frame.rows.index(u2_row)
    # T is the 4th column: cell occupies columns [10, 12) (label 4 + 3*2).
    assert u2_row[11] == "X"
    assert frame.failure_spans == (
        TextSpan(row_index=row_index, start_column=10, end_column=12),
    )


def test_matrix_uses_distinct_glyphs_for_degraded_and_unknown():
    checks = _full_matrix_checks()
    checks = [
        make_check(c.identifier, c.name, status=Status.DEG) if c.name == "U64 FTP" else c
        for c in checks
    ]
    checks = [
        make_check(c.identifier, c.name, status=Status.UNKNOWN) if c.name == "C64U PING" else c
        for c in checks
    ]
    frame = render_frame(_matrix_state(checks))

    u64_row = next(row for row in frame.rows if row.startswith("U64"))
    c64u_row = next(row for row in frame.rows if row.startswith("C64U"))
    assert u64_row[9] == "!"  # FTP column, degraded
    assert c64u_row[5] == "?"  # PING column, unknown


def test_matrix_shows_every_check_on_one_static_page_without_pagination():
    # 17 checks would paginate a standard 7-row list; the matrix packs them into
    # one frame (header + 4 target rows) regardless of page_index.
    checks = _full_matrix_checks()
    page0 = render_frame(_matrix_state(checks))
    page5 = render_frame(replace_state_page(_matrix_state(checks), 5))
    assert page0.rows == page5.rows


def replace_state_page(state, page_index):
    from dataclasses import replace

    return replace(state, page_index=page_index)


def test_matrix_falls_back_to_legacy_when_checks_do_not_parse():
    # Names that are not "<TARGET> <PROBE>" cannot map to the grid; the view
    # falls back to the standard single-column list rather than a blank screen.
    checks = (make_check("router", "Router"), make_check("nas", "NAS"))
    frame = render_frame(_matrix_state(checks))

    # Legacy list sorts by name: NAS before Router.
    assert frame.rows[0].startswith("NAS")
    assert frame.rows[1].startswith("Router")


def test_matrix_row_layout_spreads_rows_across_full_display_height():
    # 5 content rows (header + 4 targets) on a 64px display with a 1px bottom
    # progress band: 63 usable px / 5 rows => 12px pitch, glyph enlarged to 12.
    layout = matrix_row_layout(5, display_height_px=64, reserved_bottom_px=1, base_font_height_px=8)

    assert layout == ((0, 12), (12, 12), (24, 12), (36, 12), (48, 12))


def test_matrix_row_layout_returns_none_when_rows_would_shrink_text():
    # A dense fleet (one row per grid slot) cannot enlarge text, so the layout
    # stays the standard uniform spacing.
    assert matrix_row_layout(8, display_height_px=64, reserved_bottom_px=1, base_font_height_px=8) is None


def test_matrix_frame_with_geometry_omits_padding_and_sets_row_layout():
    checks = _full_matrix_checks()
    frame = render_frame(
        _matrix_state(checks),
        display_height_px=64,
        font_height_px=8,
        reserved_bottom_px=1,
    )

    # Content rows only: header + 4 targets, no blank padding.
    assert len(frame.rows) == 5
    assert frame.row_layout == ((0, 12), (12, 12), (24, 12), (36, 12), (48, 12))
    # Failure span row indices still align with the (unpadded) content rows.
    failing = [make_check(c.identifier, c.name, status=Status.FAIL) if c.name == "U2 TELNET" else c for c in checks]
    failing_frame = render_frame(
        _matrix_state(failing),
        display_height_px=64,
        font_height_px=8,
        reserved_bottom_px=1,
    )
    assert any(span.row_index == 2 for span in failing_frame.failure_spans)
