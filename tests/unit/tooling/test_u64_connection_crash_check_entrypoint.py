import importlib.machinery
import importlib.util
import itertools
import sys
import uuid
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "u64_connection_crash_check.py"
    loader = importlib.machinery.SourceFileLoader(f"u64_connection_crash_check_{uuid.uuid4().hex}", str(script_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[loader.name] = module
    spec.loader.exec_module(module)
    return module


def _outcome(module, result: str, detail: str = "detail"):
    return module.stress.ProbeOutcome(result, detail, 1.0)


class FakePopen:
    def __init__(self, poll_values: list[int | None], wait_value: int = 0):
        self._poll_values = list(poll_values)
        self._wait_value = wait_value
        self.pid = 4321
        self.terminated = False
        self.killed = False
        self._current = None

    def poll(self):
        if self.terminated:
            return -15
        if self._poll_values:
            self._current = self._poll_values.pop(0)
            return self._current
        return self._current

    def wait(self, timeout=None):
        return -15 if self.terminated else self._wait_value

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.terminated = True


def test_u64_connection_crash_check_detects_crash_and_forwards_stress_flags(monkeypatch, capsys):
    module = _load_module()
    sleeps: list[int] = []
    commands: list[list[str]] = []
    process = FakePopen([None, None], wait_value=0)

    popen_kwargs = {}

    def fake_popen(command, **kwargs):
        commands.append(command)
        popen_kwargs.update(kwargs)
        return process

    outcomes = itertools.chain(
        [
            {protocol: _outcome(module, "FAIL") for protocol in module.PROTOCOLS},
            {protocol: _outcome(module, "FAIL") for protocol in module.PROTOCOLS},
            {protocol: _outcome(module, "FAIL") for protocol in module.PROTOCOLS},
        ]
    )

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(module, "find_python", lambda: "python3")
    monkeypatch.setattr(module, "run_probe_round", lambda settings: next(outcomes))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = module.main(["--delay-ms", "0", "--log-every", "1"])
    output = capsys.readouterr().out

    assert result == 0
    assert commands == [[
        "python3",
        str(module.STRESS_SCRIPT),
        "--delay-ms",
        "0",
        "--log-every",
        "1",
        "-H",
        module.stress.DEFAULT_PROFILE_HOST,
        "--http-path",
        module.stress.HTTP_PATH,
        "--http-port",
        str(module.stress.HTTP_PORT),
        "--ftp-port",
        str(module.stress.FTP_PORT),
        "--telnet-port",
        str(module.stress.TELNET_PORT),
        "-u",
        module.stress.FTP_USER,
        "-P",
        module.stress.FTP_PASS,
        "--profile",
        module.stress.PROFILE_STRESS,
        "--duration-s",
        "5",
    ]]
    assert popen_kwargs == {}
    assert sleeps == [1, 5]
    assert "crash_detected checkpoints_s=5" in output

def test_u64_connection_crash_check_reports_no_crash_when_any_protocol_survives(monkeypatch, capsys):
    module = _load_module()
    sleeps: list[int] = []
    process = FakePopen([None, 0], wait_value=0)
    stress_outcomes = {protocol: _outcome(module, "FAIL") for protocol in module.PROTOCOLS}
    post_outcomes = {protocol: _outcome(module, "FAIL") for protocol in module.PROTOCOLS}
    post_outcomes["ping"] = _outcome(module, "OK", "reply")
    outcomes = itertools.chain([stress_outcomes, post_outcomes])

    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: process)
    monkeypatch.setattr(module, "find_python", lambda: "python3")
    monkeypatch.setattr(module, "run_probe_round", lambda settings: next(outcomes))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = module.main([])
    output = capsys.readouterr().out

    assert result == 1
    assert sleeps == [5]
    assert "crash_not_detected survivors=after_s=5:ping" in output

def test_u64_connection_crash_check_reports_stress_command_failure(monkeypatch, capsys):
    module = _load_module()
    sleeps: list[int] = []
    process = FakePopen([9], wait_value=9)

    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: process)
    monkeypatch.setattr(module, "find_python", lambda: "python3")
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = module.main([])
    output = capsys.readouterr().out

    assert result == 2
    assert sleeps == []
    assert "phase=stress failed_returncode=9" in output

def test_u64_connection_crash_check_stops_stress_early_after_full_degradation(monkeypatch, capsys):
    module = _load_module()
    sleeps: list[int] = []
    process = FakePopen([None, None], wait_value=0)
    outcomes = itertools.chain(
        [
            {protocol: _outcome(module, "FAIL") for protocol in module.PROTOCOLS},
            {protocol: _outcome(module, "FAIL") for protocol in module.PROTOCOLS},
            {protocol: _outcome(module, "FAIL") for protocol in module.PROTOCOLS},
        ]
    )

    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: process)
    monkeypatch.setattr(module, "find_python", lambda: "python3")
    monkeypatch.setattr(module, "run_probe_round", lambda settings: next(outcomes))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = module.main([])
    output = capsys.readouterr().out

    assert result == 0
    assert process.terminated is True
    assert sleeps == [1, 5]
    assert "phase=stress full_degradation_detected stopping_stress=1" in output


def test_u64_connection_crash_check_ignores_transient_104_failures_for_early_stop(monkeypatch, capsys):
    module = _load_module()
    sleeps: list[int] = []
    process = FakePopen([None, None, None, None, 0], wait_value=0)
    transient = {protocol: _outcome(module, "FAIL", "[Errno 104] Connection reset by peer") for protocol in module.PROTOCOLS}
    post_outcomes = {protocol: _outcome(module, "FAIL") for protocol in module.PROTOCOLS}
    post_outcomes["ping"] = _outcome(module, "OK", "reply")
    outcomes = itertools.chain([transient, transient, post_outcomes])

    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: process)
    monkeypatch.setattr(module, "find_python", lambda: "python3")
    monkeypatch.setattr(module, "run_probe_round", lambda settings: next(outcomes))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = module.main([])
    output = capsys.readouterr().out

    assert result == 1
    assert process.terminated is False
    assert sleeps == [1, 1, 5]
    assert "full_degradation_detected" not in output


def test_u64_connection_crash_check_returns_zero_on_keyboard_interrupt(monkeypatch, capsys):
    module = _load_module()
    process = FakePopen([None], wait_value=0)

    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: process)
    monkeypatch.setattr(module, "find_python", lambda: "python3")
    monkeypatch.setattr(module, "monitor_stress_process", lambda process, settings: (_ for _ in ()).throw(KeyboardInterrupt()))

    result = module.main([])
    output = capsys.readouterr().out

    assert result == 0
    assert process.terminated is True
    assert "protocol=crash-check result=INFO detail=\"cancelled\"" in output