import importlib.util
import json
import os
import sys
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


def _file_stamp(path):
    """Identity of the file contents, used to detect an updated contract."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


# Caches are keyed by file stamp rather than memoized once. substance-tools owns
# these files and can be installed, moved or updated while Blender is running,
# and an add-on reload re-imports this module but leaves sys.modules entries in
# place. Caching a miss forever, or a stale module, makes this add-on disagree
# with the other consumers of the same contract - and the descriptor carries a
# fingerprint, so disagreement surfaces as a mismatch error rather than as
# something obviously cache-shaped.
_CONTRACT_CACHE = {}
_HANDOFF_API_CACHE = {}


def pipeline_contract_path():
    for path in _candidate_paths():
        if path.is_file():
            return path
    return None


def pipeline_contract():
    path = pipeline_contract_path()
    if path is None:
        return {}
    stamp = _file_stamp(path)
    cached = _CONTRACT_CACHE.get("value")
    if cached is not None and cached[0] == stamp:
        return cached[1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    _CONTRACT_CACHE["value"] = (stamp, payload)
    return payload


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

    stamp = _file_stamp(module_path)
    cached = _HANDOFF_API_CACHE.get("value")
    if cached is not None and cached[0] == stamp:
        return cached[1]

    module_name = "_ue_unique_speedtree_handoff_contract"
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
    _HANDOFF_API_CACHE["value"] = (stamp, module)
    return module


def collection_name(key, default):
    return pipeline_contract().get("blender_collections", {}).get(key, default)


def naming_value(key, default):
    return pipeline_contract().get("naming", {}).get(key, default)
