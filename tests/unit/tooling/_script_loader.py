from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[3] / "scripts"

if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))


def load_script_module(stem: str) -> types.ModuleType:
    script_path = SCRIPT_ROOT / f"{stem}.py"
    module_name = f"test_{stem}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
