import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


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


def _prepare_secrets_capture(
    tmp_path: Path,
) -> tuple[Path, dict[str, str], Path, Path]:
    """Create a build-deploy config plus a shadow ``build_deploy`` module that
    records the resolved network environment.

    Returns ``(build_path, environment, config_path, env_dump_path)`` ready to
    drive ``./build render-config`` as a subprocess.
    """

    config_path = tmp_path / "build-deploy.yaml"
    config_path.write_text(
        "device:\n  board: pico2w\nchecks_config: checks.yaml\n",
        encoding="utf-8",
    )
    (tmp_path / "checks.yaml").write_text("checks: []\n", encoding="utf-8")

    secrets_yaml = tmp_path / "config" / "secrets.local"
    secrets_yaml.parent.mkdir(parents=True, exist_ok=True)
    secrets_yaml.write_text(
        "VIVIPI_NETWORK_USERNAME: yaml_user\nVIVIPI_NETWORK_PASSWORD: yaml_pwd\n",
        encoding="utf-8",
    )

    fake_module_python = tmp_path / "fake-module-python"
    env_dump = tmp_path / "env.json"
    _write_secrets_aware_fake_python(fake_module_python, env_dump)

    package_dir = tmp_path / "shadow" / "vivipi" / "tooling"
    package_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "shadow" / "vivipi" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "shadow" / "vivipi" / "tooling" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(fake_module_python, package_dir / "build_deploy.py")

    repo_root = Path(__file__).resolve().parents[3]
    build_path = repo_root / "build"
    environment = dict(os.environ)
    environment["VIVIPI_WIFI_SSID"] = "wifi"
    environment["VIVIPI_WIFI_PASSWORD"] = "secret"
    environment.pop("VIVIPI_NETWORK_USERNAME", None)
    environment.pop("VIVIPI_NETWORK_PASSWORD", None)
    environment["VIVIPI_REPO_ROOT"] = str(tmp_path)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path / "shadow"), str(repo_root / "src")]
    )
    return build_path, environment, config_path, env_dump


def _run_render_config(
    build_path: Path,
    environment: dict[str, str],
    config_path: Path,
    runtime_config: Path,
    python_arg: str,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(build_path),
            "render-config",
            "--no-venv",
            "--python",
            python_arg,
            "--config",
            str(config_path),
            "--runtime-config",
            str(runtime_config),
            *(extra_args or []),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(config_path.parent),
        env=environment,
        timeout=30,
    )


def test_build_exports_network_secrets_to_python_invocation(tmp_path: Path):
    build_path, environment, config_path, env_dump = _prepare_secrets_capture(tmp_path)

    completed = _run_render_config(
        build_path,
        environment,
        config_path,
        tmp_path / "config.json",
        _resolve_real_python(),
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    env_captured = json.loads(env_dump.read_text(encoding="utf-8"))
    assert env_captured["username"] == "yaml_user"
    assert env_captured["password"] == "yaml_pwd"
    assert "--config" in env_captured["argv"]
    assert str(config_path) in env_captured["argv"]


def test_build_resolves_secrets_when_python_is_bare_command(tmp_path: Path):
    # Regression: ./build must resolve config/secrets.local even when the
    # interpreter is selected by a bare command name rather than an absolute
    # path. load_local_secrets previously short-circuited because
    # `[[ -x python3 ]]` checks the filesystem, not PATH. We expose the
    # already-installed interpreter under a bare command name on PATH using a
    # wrapper script (a symlink would drop venv context and trip PEP 668).
    real_interpreter = sys.executable
    if not real_interpreter or not Path(real_interpreter).is_absolute():
        pytest.skip("could not resolve an absolute interpreter")

    bare_dir = tmp_path / "bin"
    bare_dir.mkdir()
    bare_name = "bare-vivipi-python"
    wrapper = bare_dir / bare_name
    wrapper.write_text(
        f"#!/bin/sh\nexec {shlex.quote(real_interpreter)} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    build_path, environment, config_path, env_dump = _prepare_secrets_capture(tmp_path)
    environment["PATH"] = os.pathsep.join([str(bare_dir), environment.get("PATH", "")])

    completed = _run_render_config(
        build_path,
        environment,
        config_path,
        tmp_path / "config.json",
        bare_name,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    env_captured = json.loads(env_dump.read_text(encoding="utf-8"))
    assert env_captured["username"] == "yaml_user"
    assert env_captured["password"] == "yaml_pwd"


def test_build_cli_network_flags_override_resolved_secrets(tmp_path: Path):
    build_path, environment, config_path, env_dump = _prepare_secrets_capture(tmp_path)

    completed = _run_render_config(
        build_path,
        environment,
        config_path,
        tmp_path / "config.json",
        _resolve_real_python(),
        extra_args=["--network-username", "cli_user", "--network-password", "cli_pwd"],
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    env_captured = json.loads(env_dump.read_text(encoding="utf-8"))
    assert env_captured["username"] == "cli_user"
    assert env_captured["password"] == "cli_pwd"
