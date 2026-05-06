import json
import os
import stat
import subprocess
from pathlib import Path


def test_c64_health_check_wrapper_invokes_python_script_for_c64u_then_u64(tmp_path: Path):
    fake_python = tmp_path / "fake-python"
    output_path = tmp_path / "calls.jsonl"
    fake_python.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        f"path = pathlib.Path({str(output_path)!r})\n"
        "with path.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'argv': sys.argv[1:], 'pythonpath': os.environ.get('PYTHONPATH', '')}) + '\\n')\n",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    script_path = Path(__file__).resolve().parents[3] / "scripts" / "c64_health_check"
    environment = dict(os.environ)
    environment["PYTHON_BIN"] = str(fake_python)
    environment["VENV_DIR"] = str(tmp_path / "missing-venv")

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
    assert [Path(call["argv"][0]).name for call in calls] == ["u64_health_check.py", "u64_health_check.py"]
    assert [call["argv"][1:] for call in calls] == [
        ["c64u", "--build-config", str(tmp_path / "build-deploy.yaml")],
        ["u64", "--build-config", str(tmp_path / "build-deploy.yaml")],
    ]
    assert str(script_path.parent.parent / "src") in calls[0]["pythonpath"]
