"""MicroPython entrypoint for the ViviPi firmware bundle."""

try:
    from runtime import run_forever
except ImportError:  # pragma: no cover - used by CPython tests
    from firmware.runtime import run_forever


def main():
    run_forever()


if __name__ == "__main__":
    main()
