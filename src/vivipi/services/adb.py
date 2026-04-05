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
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return SimpleNamespace(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def collect_adb_service_payload(run_command=None) -> dict[str, list[dict[str, object]]]:
    command_runner = run_command or _run_adb
    completed = command_runner(["adb", "devices", "-l"])

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

    devices = parse_adb_devices(str(completed.stdout))
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
