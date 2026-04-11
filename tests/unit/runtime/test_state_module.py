import io
from types import SimpleNamespace

import pytest

import vivipi.runtime.state as runtime_state


@pytest.fixture(autouse=True)
def clear_bound_app():
    runtime_state.clear_bound_app()
    yield
    runtime_state.clear_bound_app()


def test_state_module_requires_bound_app_and_exposes_snapshot_wrappers():
    with pytest.raises(RuntimeError, match="not bound"):
        runtime_state.get_app()

    fake_app = SimpleNamespace(
        get_registered_checks=lambda: ({"id": "router"},),
        get_checks_snapshot=lambda: ({"id": "router", "status": "OK"},),
        get_failures_snapshot=lambda: (),
        get_metrics_snapshot=lambda: {"cycle_ms": {}},
        get_network_state_snapshot=lambda: {"connected": True},
        get_logs=lambda limit=None: ("log",),
        get_errors=lambda limit=None: ({"type": "RuntimeError"},),
        snapshot=lambda: {"ok": True},
    )
    runtime_state.bind_app(fake_app)

    assert runtime_state.get_registered_checks() == ({"id": "router"},)
    assert runtime_state.get_checks() == ({"id": "router", "status": "OK"},)
    assert runtime_state.get_failures() == ()
    assert runtime_state.get_metrics() == {"cycle_ms": {}}
    assert runtime_state.get_network_state() == {"connected": True}
    assert runtime_state.get_logs(limit=1) == ("log",)
    assert runtime_state.get_errors(limit=1) == ({"type": "RuntimeError"},)
    assert runtime_state.snapshot() == {"ok": True}


def test_format_exception_trace_covers_sys_print_exception_and_traceback_fallback(monkeypatch):
    class FakeSys:
        @staticmethod
        def print_exception(exception, writer):
            writer.write("line 1\nline 2\n")

    monkeypatch.setattr(runtime_state, "sys", FakeSys)
    traced = runtime_state.format_exception_trace(RuntimeError("boom"), line_limit=8, max_lines=1)

    assert traced == ("line 1",)

    monkeypatch.setattr(runtime_state, "sys", SimpleNamespace())
    fallback = runtime_state.format_exception_trace(RuntimeError("boom"), line_limit=24, max_lines=2)
    record = runtime_state.make_error_record("check", RuntimeError(""), observed_at_s=1.0, identifier="router")

    assert fallback
    assert record["type"] == "RuntimeError"
    assert record["message"] == "RuntimeError"


def test_format_exception_trace_falls_back_when_sys_print_exception_rejects_writer(monkeypatch):
    class FakeSys:
        @staticmethod
        def print_exception(exception, writer):
            raise OSError("stream operation not supported")

    monkeypatch.setattr(runtime_state, "sys", FakeSys)
    traced = runtime_state.format_exception_trace(RuntimeError("boom"), line_limit=24, max_lines=2)

    assert traced
    assert traced[0] == "RuntimeError: boom"


def test_format_exception_trace_uses_trace_writer_and_final_fallback_paths(monkeypatch):
    class FakeSys:
        @staticmethod
        def print_exception(exception, writer):
            if isinstance(writer, io.StringIO):
                raise OSError("stringio unsupported")
            writer.write("writer line\n")

    monkeypatch.setattr(runtime_state, "sys", FakeSys)

    traced = runtime_state.format_exception_trace(RuntimeError("boom"), line_limit=24, max_lines=2)

    assert traced == ("writer line",)

    monkeypatch.setattr(runtime_state, "sys", SimpleNamespace())
    monkeypatch.setattr(runtime_state, "traceback", SimpleNamespace(print_exception=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no traceback"))))

    fallback = runtime_state.format_exception_trace(RuntimeError("boom"), line_limit=24, max_lines=2)

    assert fallback == ("RuntimeError: boom",)
