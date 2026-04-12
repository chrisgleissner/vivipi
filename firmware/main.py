"""MicroPython entrypoint for the ViviPi firmware bundle."""

try:
    from runtime import run_forever
except ImportError as error:  # pragma: no cover - used by CPython tests
    if getattr(error, "name", None) != "runtime":
        raise
    from firmware.runtime import run_forever


def main():
    run_forever()


if __name__ == "__main__":
    main()
