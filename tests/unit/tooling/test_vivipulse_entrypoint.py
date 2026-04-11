import os
import stat
import subprocess
from pathlib import Path


def test_vivipulse_wrapper_invokes_module_with_repo_src_on_pythonpath(tmp_path: Path):
    fake_python = tmp_path / "fake-python"
    output_path = tmp_path / "args.json"
    fake_python.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        f"pathlib.Path({str(output_path)!r}).write_text(json.dumps({{'argv': sys.argv[1:], 'pythonpath': os.environ.get('PYTHONPATH', '')}}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    script_path = Path(__file__).resolve().parents[3] / "scripts" / "vivipulse"
    environment = dict(os.environ)
    environment["PYTHON_BIN"] = str(fake_python)
    environment["VENV_DIR"] = str(tmp_path / "missing-venv")

    completed = subprocess.run(
        [
            str(script_path),
            "--mode",
            "plan",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=script_path.parent.parent,
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = __import__("json").loads(output_path.read_text(encoding="utf-8"))
    assert payload["argv"][:2] == ["-m", "vivipi.tooling.vivipulse"]
    assert str(script_path.parent.parent / "src") in payload["pythonpath"]
