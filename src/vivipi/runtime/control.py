from __future__ import annotations

import vivipi.runtime.state as runtime_state


def run_all_checks(now_s: float | None = None):
    return runtime_state.get_app().run_all_checks(now_s=now_s)


def reset_state():
    return runtime_state.get_app().reset_runtime_state()


def reconnect_network():
    return runtime_state.get_app().reconnect_network()


def dump_logs(limit: int | None = None) -> tuple[str, ...]:
    return runtime_state.get_logs(limit=limit)


def set_log_level(level: str):
    return runtime_state.get_app().set_log_level(level).name


def set_debug_mode(enabled: bool = True):
    return runtime_state.get_app().set_debug_mode(enabled)