import importlib.util
from pathlib import Path


class SourceFallbackExample:
    identifier: str
    name: str
    tags: tuple[str, ...]
    mode: str = "overview"


def _load_firmware_dataclasses_module():
    module_path = Path(__file__).resolve().parents[3] / "firmware" / "dataclasses.py"
    spec = importlib.util.spec_from_file_location("vivipi_firmware_dataclasses", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_dataclass_recovers_field_order_from_source_when_annotations_are_missing():
    dataclasses_module = _load_firmware_dataclasses_module()

    SourceFallbackExample.tags = dataclasses_module.field(default_factory=tuple)
    del SourceFallbackExample.__annotations__

    Example = dataclasses_module.dataclass(frozen=True)(SourceFallbackExample)

    value = Example("check-1", "Example")
    replaced = dataclasses_module.replace(value, mode="detail")

    assert value.identifier == "check-1"
    assert value.name == "Example"
    assert value.tags == ()
    assert value.mode == "overview"
    assert repr(value) == "SourceFallbackExample(identifier='check-1', name='Example', tags=(), mode='overview')"
    assert replaced.mode == "detail"
    assert replaced.tags == ()
    assert value == Example("check-1", "Example")
    assert value != replaced
