import importlib.util
import json
import os
import sys
from functools import lru_cache
from pathlib import Path


def _candidate_paths():
    override = os.environ.get("SUBSTANCE_TOOLS_PIPELINE_CONTRACT")
    if override:
        yield Path(override)

    here = Path(__file__).resolve()
    for parent in here.parents:
        yield parent / "pipeline_contract.json"
        yield parent / "substance-tools" / "pipeline_contract.json"
        yield parent / "substance_tools" / "pipeline_contract.json"

    appdata = os.environ.get("APPDATA")
    if appdata:
        blender_root = Path(appdata) / "Blender Foundation" / "Blender"
        for path in sorted(blender_root.glob("*/scripts/addons/substance_tools/pipeline_contract.json"), reverse=True):
            yield path


@lru_cache(maxsize=1)
def pipeline_contract_path():
    for path in _candidate_paths():
        if path.is_file():
            return path
    return None


@lru_cache(maxsize=1)
def pipeline_contract():
    path = pipeline_contract_path()
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


@lru_cache(maxsize=1)
def speedtree_handoff_contract():
    """Load the dependency-free SpeedTree handoff API beside the shared JSON.

    A standalone exporter install may not include the central runtime module,
    so callers retain their existing local fallbacks when this returns None.
    If the module is present but invalid, let that error surface instead of
    silently running two different contracts.
    """
    contract_path = pipeline_contract_path()
    if contract_path is None:
        return None
    module_path = contract_path.with_name("speedtree_handoff_contract.py")
    if not module_path.is_file():
        return None

    module_name = "_ue_unique_speedtree_handoff_contract"
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Could not load SpeedTree handoff contract API: {module_path}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def collection_name(key, default):
    return pipeline_contract().get("blender_collections", {}).get(key, default)


def naming_value(key, default):
    return pipeline_contract().get("naming", {}).get(key, default)
