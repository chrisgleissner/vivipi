import os
import stat
import subprocess
from pathlib import Path


def test_explicit_config_bypasses_automatic_local_override(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text("device:\n  board: pico2w\n", encoding="utf-8")
    (tmp_path / "build-deploy.local.yaml").write_text("device:\n  board: pico2w\n", encoding="utf-8")

    fake_python = tmp_path / "fake-python"
    args_path = tmp_path / "args.txt"
    fake_python.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n"
        f"pathlib.Path({str(args_path)!r}).write_text('\\n'.join(sys.argv[1:]) + '\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    build_path = Path(__file__).resolve().parents[3] / "build"
    environment = dict(os.environ)
    environment["VIVIPI_WIFI_SSID"] = "wifi"
    environment["VIVIPI_WIFI_PASSWORD"] = "secret"

    completed = subprocess.run(
        [
            str(build_path),
            "build-firmware",
            "--no-venv",
            "--python",
            str(fake_python),
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "release"),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=build_path.parent,
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    forwarded_args = args_path.read_text(encoding="utf-8").splitlines()
    assert "--prefer-local-config" not in forwarded_args
    assert str(config_path) in forwarded_args