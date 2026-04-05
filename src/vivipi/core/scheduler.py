from vivipi.core.models import AppState


def render_reason(previous: AppState | None, current: AppState) -> str:
    if previous is None:
        return "bootstrap"
    if previous.shift_offset != current.shift_offset:
        return "shift"
    if previous != current:
        return "state"
    return "none"
