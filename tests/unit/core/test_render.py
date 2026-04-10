from vivipi.core.models import AppMode, AppState, CheckRuntime, DisplayMode, Status
from vivipi.core.render import InvertedSpan, render_frame


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


def test_overview_paginates_without_row_inversion():
    checks = tuple(
        make_check(identifier=name.casefold(), name=name)
        for name in ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India")
    )
    state = AppState(checks=checks, selected_id="india", page_index=1)

    frame = render_frame(state)

    assert frame.inverted_row is None
    assert frame.rows[0].startswith("India")
    assert all(len(row) == 16 for row in frame.rows)


def test_render_frame_respects_dynamic_grid_dimensions():
    checks = tuple(make_check(identifier=name.casefold(), name=name) for name in ("Alpha", "Bravo", "Charlie", "Delta"))
    state = AppState(checks=checks, selected_id="charlie", row_width=12, page_size=3, page_index=0)

    frame = render_frame(state)

    assert len(frame.rows) == 3
    assert all(len(row) == 12 for row in frame.rows)
    assert frame.rows[2].startswith("Charlie")


def test_overview_selection_does_not_invert_rows():
    state = AppState(checks=(make_check("router", "Router"),), selected_id=None)

    frame = render_frame(state)

    assert frame.inverted_row is None
    assert frame.rows[0].startswith("Router")


def test_overview_displays_unknown_status_as_question_mark():
    state = AppState(checks=(make_check("router", "Router", status=Status.UNKNOWN),), selected_id="router")

    frame = render_frame(state)

    assert frame.rows[0].endswith("   ?")


def test_standard_single_column_overview_keeps_legacy_output_exactly():
    state = AppState(
        checks=(make_check("router", "Router", status=Status.FAIL),),
        selected_id="router",
        display_mode=DisplayMode.STANDARD,
        overview_columns=1,
    )

    frame = render_frame(state)

    assert frame.rows[0] == "Router      FAIL"
    assert frame.inverted_row is None
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

    assert frame.rows == ("Bravo   |Alpha  ",)
    assert frame.inverted_spans == ()
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
