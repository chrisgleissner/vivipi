from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path


def _write_fake_python(path: Path, calls_path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        f"path = pathlib.Path({str(calls_path)!r})\n"
        "with path.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'argv': sys.argv[1:], 'network_username': os.environ.get('VIVIPI_NETWORK_USERNAME', ''), 'network_password': os.environ.get('VIVIPI_NETWORK_PASSWORD', ''), 'pythonpath': os.environ.get('PYTHONPATH', '')}) + '\\n')\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _python_script_calls(calls: list[dict]) -> list[dict]:
    return [call for call in calls if call["argv"] and not call["argv"][0].startswith("-")]


def test_c64_health_check_wrapper_invokes_python_script_for_c64u_u64_then_u2(tmp_path: Path):
    fake_python = tmp_path / "fake-python"
    output_path = tmp_path / "calls.jsonl"
    _write_fake_python(fake_python, output_path)

    script_path = Path(__file__).resolve().parents[3] / "scripts" / "c64_health_check"
    environment = dict(os.environ)
    environment["PYTHON_BIN"] = str(fake_python)
    environment["VENV_DIR"] = str(tmp_path / "missing-venv")
    environment.pop("VIVIPI_NETWORK_USERNAME", None)
    environment.pop("VIVIPI_NETWORK_PASSWORD", None)

    completed = subprocess.run(
        [str(script_path), "--build-config", str(tmp_path / "build-deploy.yaml")],
        check=False,
        capture_output=True,
        text=True,
        cwd=script_path.parent.parent,
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    calls = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    script_calls = _python_script_calls(calls)
    assert [Path(call["argv"][0]).name for call in script_calls] == [
        "u64_health_check.py",
        "u64_health_check.py",
        "u64_health_check.py",
    ]
    assert [call["argv"][1:] for call in script_calls] == [
        ["c64u", "--build-config", str(tmp_path / "build-deploy.yaml")],
        ["u64", "--build-config", str(tmp_path / "build-deploy.yaml")],
        ["u2", "--build-config", str(tmp_path / "build-deploy.yaml")],
    ]
    assert str(script_path.parent.parent / "src") in script_calls[0]["pythonpath"]


def test_c64_health_check_wrapper_exports_network_secrets_to_python_invocation(tmp_path: Path):
    fake_python = tmp_path / "fake-python"
    output_path = tmp_path / "calls.jsonl"
    _write_fake_python(fake_python, output_path)

    script_path = Path(__file__).resolve().parents[3] / "scripts" / "c64_health_check"
    secrets_dir = tmp_path / "secrets-isolation"
    secrets_dir.mkdir()
    fake_python_alt = secrets_dir / "fake-python"
    fake_python_alt.write_text(fake_python.read_text(encoding="utf-8"), encoding="utf-8")
    fake_python_alt.chmod(fake_python_alt.stat().st_mode | stat.S_IXUSR)

    environment = dict(os.environ)
    environment["PYTHON_BIN"] = str(fake_python_alt)
    environment["VENV_DIR"] = str(tmp_path / "missing-venv")
    environment.pop("VIVIPI_NETWORK_USERNAME", None)
    environment.pop("VIVIPI_NETWORK_PASSWORD", None)

    completed = subprocess.run(
        [str(script_path), "--build-config", str(tmp_path / "build-deploy.yaml")],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(secrets_dir),
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    calls = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    script_calls = _python_script_calls(calls)
    assert script_calls, "wrapper never invoked python"
    assert script_calls[0]["network_username"] == ""
    assert script_calls[0]["network_password"] == ""


def test_c64_health_check_wrapper_respects_explicit_shell_network_credentials(tmp_path: Path):
    fake_python = tmp_path / "fake-python"
    output_path = tmp_path / "calls.jsonl"
    _write_fake_python(fake_python, output_path)

    script_path = Path(__file__).resolve().parents[3] / "scripts" / "c64_health_check"
    environment = dict(os.environ)
    environment["PYTHON_BIN"] = str(fake_python)
    environment["VENV_DIR"] = str(tmp_path / "missing-venv")
    environment["VIVIPI_NETWORK_USERNAME"] = "shell_user"
    environment["VIVIPI_NETWORK_PASSWORD"] = "shell_pwd"

    completed = subprocess.run(
        [str(script_path), "--build-config", str(tmp_path / "build-deploy.yaml")],
        check=False,
        capture_output=True,
        text=True,
        cwd=script_path.parent.parent,
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    calls = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    script_calls = _python_script_calls(calls)
    assert script_calls[0]["network_username"] == "shell_user"
    assert script_calls[0]["network_password"] == "shell_pwd"