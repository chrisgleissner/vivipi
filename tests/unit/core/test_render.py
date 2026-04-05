from vivipi.core.models import AppMode, AppState, CheckRuntime, Status
from vivipi.core.render import render_frame


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


def test_overview_paginates_and_inverts_the_selected_row():
    checks = tuple(
        make_check(identifier=name.casefold(), name=name)
        for name in ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India")
    )
    state = AppState(checks=checks, selected_id="india")

    frame = render_frame(state)

    assert frame.inverted_row == 0
    assert frame.rows[0].startswith("India")
    assert all(len(row) == 16 for row in frame.rows)


def test_overview_normalizes_selection_when_checks_exist():
    state = AppState(checks=(make_check("router", "Router"),), selected_id=None)

    frame = render_frame(state)

    assert frame.inverted_row == 0


def test_overview_displays_unknown_status_as_question_mark():
    state = AppState(checks=(make_check("router", "Router", status=Status.UNKNOWN),), selected_id="router")

    frame = render_frame(state)

    assert frame.rows[0].endswith("   ?")


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
