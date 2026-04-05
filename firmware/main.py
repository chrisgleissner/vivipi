"""MicroPython entrypoint for the ViviPi firmware bundle."""

import json


def load_config(path="config.json"):
    with open(path, "r") as handle:
        return json.load(handle)


def main():
    config = load_config()
    print("ViviPi firmware scaffold loaded for", config["device"]["board"])


if __name__ == "__main__":
    main()
