from __future__ import annotations

import subprocess
from dataclasses import dataclass
from types import SimpleNamespace


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    description: str = ""


def parse_adb_devices(output: str) -> tuple[AdbDevice, ...]:
    devices: list[AdbDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices attached") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = parts[0]
        state = parts[1]
        description = " ".join(parts[2:])
        devices.append(AdbDevice(serial=serial, state=state, description=description))
    return tuple(devices)


def _run_adb(command: list[str]) -> SimpleNamespace:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as error:
        return SimpleNamespace(returncode=127, stdout="", stderr=str(error))
    return SimpleNamespace(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def _target_device(devices: tuple[AdbDevice, ...], target_serial: str) -> AdbDevice | None:
    for device in devices:
        if device.serial == target_serial:
            return device
    return None


def _should_recover(completed: SimpleNamespace, devices: tuple[AdbDevice, ...], target_serial: str | None = None) -> bool:
    if completed.returncode != 0:
        return True
    if target_serial is not None:
        target = _target_device(devices, target_serial)
        return target is None or target.state != "device"
    return any(device.state != "device" for device in devices)


def _recover_adb_transport(command_runner) -> None:
    command_runner(["adb", "start-server"])
    command_runner(["adb", "reconnect", "offline"])


def _collect_devices(run_command=None, *, target_serial: str | None = None) -> tuple[SimpleNamespace, tuple[AdbDevice, ...]]:
    command_runner = run_command or _run_adb
    completed = command_runner(["adb", "devices", "-l"])
    devices = parse_adb_devices(str(completed.stdout)) if completed.returncode == 0 else ()

    if _should_recover(completed, devices, target_serial=target_serial):
        _recover_adb_transport(command_runner)
        completed = command_runner(["adb", "devices", "-l"])
        devices = parse_adb_devices(str(completed.stdout)) if completed.returncode == 0 else ()

    return completed, devices


def collect_adb_service_payload(run_command=None) -> dict[str, list[dict[str, object]]]:
    completed, devices = _collect_devices(run_command=run_command)

    if completed.returncode != 0:
        return {
            "checks": [
                {
                    "name": "ADB",
                    "status": "FAIL",
                    "details": str(completed.stderr).strip() or "adb command failed",
                    "latency_ms": 0,
                }
            ]
        }

    if not devices:
        return {
            "checks": [
                {
                    "name": "ADB",
                    "status": "DEG",
                    "details": "No devices connected",
                    "latency_ms": 0,
                }
            ]
        }

    checks: list[dict[str, object]] = []
    for device in devices:
        status = "OK" if device.state == "device" else "FAIL"
        details = device.description or device.state
        if status == "OK" and details == "device":
            details = "Connected"
        checks.append(
            {
                "name": device.serial,
                "status": status,
                "details": details,
                "latency_ms": 0,
            }
        )
    return {"checks": checks}


def collect_adb_device_status(target_serial: str, target_name: str, run_command=None) -> tuple[int, dict[str, object]]:
    completed, devices = _collect_devices(run_command=run_command, target_serial=target_serial)

    if completed.returncode != 0:
        return 503, {
            "name": target_name,
            "serial": target_serial,
            "status": "FAIL",
            "details": str(completed.stderr).strip() or "adb command failed",
        }

    for device in devices:
        if device.serial != target_serial:
            continue
        if device.state == "device":
            return 200, {
                "name": target_name,
                "serial": target_serial,
                "status": "OK",
                "details": device.description or "Connected",
            }
        return 503, {
            "name": target_name,
            "serial": target_serial,
            "status": "FAIL",
            "details": device.description or device.state,
        }

    return 503, {
        "name": target_name,
        "serial": target_serial,
        "status": "FAIL",
        "details": "Target device not connected",
    }
