"""MicroPython entrypoint for the ViviPi firmware bundle."""

import json

try:
    from vivipi.core.display import normalize_display_config
except ImportError:  # pragma: no cover - used by CPython tests
    from firmware.display import normalize_display_config


def _display_family_from_config(path="config.json"):
    try:
        with open(path, "r") as handle:
            config = json.load(handle)
    except Exception:
        return ""
    display_input = dict(config.get("device", {})).get("display", {}) if isinstance(config, dict) else {}
    display_config = normalize_display_config(display_input)
    return str(display_config.get("family", "")).strip().lower()


def main():
    if _display_family_from_config() == "eink":
        try:
            from eink_runtime import run_forever
        except ImportError as error:  # pragma: no cover - used by CPython tests
            if getattr(error, "name", None) != "eink_runtime":
                raise
            from firmware.eink_runtime import run_forever
    else:
        try:
            from runtime import run_forever
        except ImportError as error:  # pragma: no cover - used by CPython tests
            if getattr(error, "name", None) != "runtime":
                raise
            from firmware.runtime import run_forever
    run_forever()


if __name__ == "__main__":
    main()
