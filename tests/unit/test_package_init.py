import importlib
import importlib.metadata

import vivipi


def test_package_init_uses_installed_version_when_available(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "1.2.3")

    module = importlib.reload(vivipi)

    assert module.__version__ == "1.2.3"


def test_package_init_falls_back_when_distribution_metadata_is_missing(monkeypatch):
    def raise_not_found(name):
        raise importlib.metadata.PackageNotFoundError()

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)

    module = importlib.reload(vivipi)

    assert module.__version__ == "0.0.0"