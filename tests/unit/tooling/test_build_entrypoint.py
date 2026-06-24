import json
import os
import shutil
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
    assert "this is the default for fleet-capable commands" in completed.stdout


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


def _write_secrets_aware_fake_python(path: Path, env_dump: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        f"pathlib.Path({str(env_dump)!r}).write_text(\n"
        "    json.dumps({\n"
        "        'argv': sys.argv[1:],\n"
        "        'username': os.environ.get('VIVIPI_NETWORK_USERNAME', ''),\n"
        "        'password': os.environ.get('VIVIPI_NETWORK_PASSWORD', ''),\n"
        "    }),\n"
        "    encoding='utf-8',\n"
        ")\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _resolve_real_python() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)
    return "python3"


def test_build_exports_network_secrets_to_python_invocation(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "device:\n  board: pico2w\nchecks_config: checks.yaml\n",
        encoding="utf-8",
    )
    checks_path = tmp_path / "checks.yaml"
    checks_path.write_text("checks: []\n", encoding="utf-8")

    secrets_yaml = tmp_path / "config" / "secrets.local"
    secrets_yaml.parent.mkdir(parents=True, exist_ok=True)
    secrets_yaml.write_text(
        "VIVIPI_NETWORK_USERNAME: yaml_user\nVIVIPI_NETWORK_PASSWORD: yaml_pwd\n",
        encoding="utf-8",
    )

    # Use the real interpreter so the secrets probe runs as a normal module
    # invocation. We still redirect the final module call (the
    # vivipi.tooling.build_deploy invocation) through a fake python that just
    # records its environment so we can assert what was exported.
    fake_module_python = tmp_path / "fake-module-python"
    env_dump = tmp_path / "env.json"
    _write_secrets_aware_fake_python(fake_module_python, env_dump)

    build_path = Path(__file__).resolve().parents[3] / "build"
    real_python = _resolve_real_python()
    environment = dict(os.environ)
    environment["VIVIPI_WIFI_SSID"] = "wifi"
    environment["VIVIPI_WIFI_PASSWORD"] = "secret"
    environment.pop("VIVIPI_NETWORK_USERNAME", None)
    environment.pop("VIVIPI_NETWORK_PASSWORD", None)
    environment["VIVIPI_REPO_ROOT"] = str(tmp_path)

    # Run the build using the real python, but intercept the final module
    # invocation by symlinking vivipi.tooling.build_deploy -> fake_module_python.
    package_dir = tmp_path / "shadow" / "vivipi" / "tooling"
    package_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "shadow" / "vivipi" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "shadow" / "vivipi" / "tooling" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(fake_module_python, package_dir / "build_deploy.py")
    env_pythonpath_entries = [
        str(tmp_path / "shadow"),
        str(Path(__file__).resolve().parents[3] / "src"),
    ]
    environment["PYTHONPATH"] = os.pathsep.join(env_pythonpath_entries)

    completed = subprocess.run(
        [
            str(build_path),
            "render-config",
            "--no-venv",
            "--python",
            real_python,
            "--config",
            str(config_path),
            "--runtime-config",
            str(tmp_path / "config.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    env_captured = json.loads(env_dump.read_text(encoding="utf-8"))
    assert env_captured["username"] == "yaml_user"
    assert env_captured["password"] == "yaml_pwd"
    assert "--config" in env_captured["argv"]
    assert str(config_path) in env_captured["argv"]


def test_build_cli_network_flags_override_resolved_secrets(tmp_path: Path):
    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "device:\n  board: pico2w\nchecks_config: checks.yaml\n",
        encoding="utf-8",
    )
    checks_path = tmp_path / "checks.yaml"
    checks_path.write_text("checks: []\n", encoding="utf-8")

    secrets_yaml = tmp_path / "config" / "secrets.local"
    secrets_yaml.parent.mkdir(parents=True, exist_ok=True)
    secrets_yaml.write_text(
        "VIVIPI_NETWORK_USERNAME: yaml_user\nVIVIPI_NETWORK_PASSWORD: yaml_pwd\n",
        encoding="utf-8",
    )

    fake_module_python = tmp_path / "fake-module-python"
    env_dump = tmp_path / "env.json"
    _write_secrets_aware_fake_python(fake_module_python, env_dump)

    build_path = Path(__file__).resolve().parents[3] / "build"
    real_python = _resolve_real_python()
    environment = dict(os.environ)
    environment["VIVIPI_WIFI_SSID"] = "wifi"
    environment["VIVIPI_WIFI_PASSWORD"] = "secret"
    environment.pop("VIVIPI_NETWORK_USERNAME", None)
    environment.pop("VIVIPI_NETWORK_PASSWORD", None)
    environment["VIVIPI_REPO_ROOT"] = str(tmp_path)

    package_dir = tmp_path / "shadow" / "vivipi" / "tooling"
    package_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "shadow" / "vivipi" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "shadow" / "vivipi" / "tooling" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(fake_module_python, package_dir / "build_deploy.py")
    env_pythonpath_entries = [
        str(tmp_path / "shadow"),
        str(Path(__file__).resolve().parents[3] / "src"),
    ]
    environment["PYTHONPATH"] = os.pathsep.join(env_pythonpath_entries)

    completed = subprocess.run(
        [
            str(build_path),
            "render-config",
            "--no-venv",
            "--python",
            real_python,
            "--config",
            str(config_path),
            "--runtime-config",
            str(tmp_path / "config.json"),
            "--network-username",
            "cli_user",
            "--network-password",
            "cli_pwd",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    env_captured = json.loads(env_dump.read_text(encoding="utf-8"))
    assert env_captured["username"] == "cli_user"
    assert env_captured["password"] == "cli_pwd"
