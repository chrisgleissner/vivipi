"""MicroPython entrypoint for the ViviPi firmware bundle."""

try:
    from machine import WDT  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - used by CPython tests
    WDT = None

import json

try:
    from vivipi.core.display import normalize_display_config
except ImportError:  # pragma: no cover - used by CPython tests
    from firmware.display import normalize_display_config


def _bootstrap_watchdog():
    if WDT is None:
        return None
    try:
        watchdog = WDT(timeout=8388)
    except TypeError:
        watchdog = WDT(8388)
    except Exception:
        return None
    try:
        watchdog.feed()
    except Exception:
        return None
    return watchdog


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
    display_family = _display_family_from_config()
    watchdog = None
    if display_family != "eink":
        watchdog = _bootstrap_watchdog()
        if watchdog is not None:
            watchdog.feed()
    if display_family == "eink":
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
    if watchdog is not None:
        watchdog.feed()
    run_forever()


if __name__ == "__main__":
    main()
