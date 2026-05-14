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


def test_help_lists_top_level_commands_without_subitem_indentation(tmp_path: Path):
    build_path = Path(__file__).resolve().parents[3] / "build"

    completed = subprocess.run(
        [str(build_path), "help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=build_path.parent,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "\n  list-devices     Show configured device selectors and their current state\n" in completed.stdout
    assert "\n  provision        Provision configured BOOTSEL devices with a base UF2\n" in completed.stdout
    assert "\n  deploy           Build the firmware bundle and copy it to the configured Pico target(s) via mpremote\n" in completed.stdout
    assert "\n  release-assets   Build the versioned GitHub release artifacts\n" in completed.stdout
    assert "\n  --device-port PATH         Optional serial or USB path for single-device mpremote deploy; defaults to auto\n" in completed.stdout


def test_multi_device_deploy_rejects_device_port_in_build_wrapper(tmp_path: Path):
    build_path = Path(__file__).resolve().parents[3] / "build"
    fake_python = tmp_path / "fake-python"
    fake_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    environment = dict(os.environ)
    environment["VIVIPI_WIFI_SSID"] = "wifi"
    environment["VIVIPI_WIFI_PASSWORD"] = "secret"

    completed = subprocess.run(
        [
            str(build_path),
            "deploy",
            "--no-venv",
            "--python",
            str(fake_python),
            "--config",
            str(tmp_path / "build-deploy.yaml"),
            "--output-dir",
            str(tmp_path / "release"),
            "--device",
            "epaper",
            "--device-port",
            "/dev/ttyACM0",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=build_path.parent,
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 2
    assert "--device-port is only supported for single-device deploy" in completed.stdout