"""Public integration API for Send2UE and other pipeline consumers."""

from pathlib import Path

import bpy

from .gpro import is_unreal_handoff_material, unreal_handoff_materials_from_objects
from .naming import material_texture_map, resolve_export_dir, top_empty_parent
from .pipeline_json import _json_refresh_validation_errors, write_unreal_pipeline_json
from .painter_sync import (
    ensure_painter_low_export_unit as _ensure_painter_low_export_unit,
    sync_painter_export,
)
from .utils import (
    asset_prefix,
    clean_token,
    export_collection,
    hair_tool_asset_groups,
    validation_scope_objects,
)

__all__ = (
    "collect_handoff_data",
    "validate_handoff",
    "refresh_handoff_json",
    "resolve_export_directory",
    "resolve_asset_unit_name",
    "resolve_sidecar_json_path",
    "ensure_painter_low_export_unit",
    "get_painter_low_export_api",
    "sync_painter_low_export",
    "PAINTER_LOW_EXPORT_API_VERSION",
    "PAINTER_LOW_EXPORT_SERVICE_ID",
)


PAINTER_LOW_EXPORT_API_VERSION = 2
PAINTER_LOW_EXPORT_SERVICE_ID = "unreal-handoff.painter-low-export"


def get_painter_low_export_api(version=PAINTER_LOW_EXPORT_API_VERSION):
    """Return the exact supported Low-to-Unreal handoff service."""
    if version != PAINTER_LOW_EXPORT_API_VERSION:
        raise ValueError(
            f"Painter Low export API {version!r} is incompatible with "
            f"{PAINTER_LOW_EXPORT_API_VERSION}"
        )
    return {
        "service_id": PAINTER_LOW_EXPORT_SERVICE_ID,
        "version": PAINTER_LOW_EXPORT_API_VERSION,
        "ensure_painter_low_export_unit": ensure_painter_low_export_unit,
        "sync_painter_low_export": sync_painter_low_export,
    }


def _context(context=None):
    return context or bpy.context


def _props(context):
    return context.scene.ue_unique_names


def collect_handoff_data(context=None, scope=None):
    context = _context(context)
    props = _props(context)
    effective_scope = scope or props.scope
    objects = validation_scope_objects(context, effective_scope)
    hair_assets = hair_tool_asset_groups(context, effective_scope)
    materials = unreal_handoff_materials_from_objects(objects)
    seen_materials = {material.name for material in materials}
    for asset in hair_assets:
        for material in asset["materials"]:
            if (
                is_unreal_handoff_material(material)
                and material.name not in seen_materials
            ):
                materials.append(material)
                seen_materials.add(material.name)
    texture_map = material_texture_map(materials)
    return {
        "context": context,
        "props": props,
        "scope": effective_scope,
        "objects": objects,
        "hair_assets": hair_assets,
        "materials": materials,
        "texture_map": texture_map,
    }


def validate_handoff(context=None, scope=None):
    data = collect_handoff_data(context, scope=scope)
    data["errors"] = _json_refresh_validation_errors(
        data["context"],
        data["props"],
        data["objects"],
        data["materials"],
        data["texture_map"],
        hair_assets=data["hair_assets"],
    )
    return data


def refresh_handoff_json(context=None, scope=None):
    data = validate_handoff(context, scope=scope)
    props = data["props"]
    export_dir = resolve_export_dir(props.texture_export_dir)
    data["export_dir"] = str(export_dir)
    data["json_paths"] = []
    if data["errors"]:
        return data

    prefix = asset_prefix(data["context"], props.prefix_mode, props.custom_prefix)
    json_paths = write_unreal_pipeline_json(
        data["context"],
        prefix,
        data["objects"],
        data["materials"],
        data["texture_map"],
        export_dir,
        hair_assets=data["hair_assets"],
    )
    data["json_paths"] = [str(path) for path in json_paths]
    return data


def resolve_export_directory(context=None):
    props = _props(_context(context))
    return str(resolve_export_dir(props.texture_export_dir))


def resolve_asset_unit_name(obj, context=None):
    """Return the asset name Send to Unreal derives for a mesh object."""
    if obj is None:
        return ""
    context = _context(context)
    collection = export_collection(context)
    scope_objects = set(collection.all_objects) if collection else set()
    if obj not in scope_objects:
        scope_objects.add(obj)
        parent = obj.parent
        while parent is not None:
            scope_objects.add(parent)
            parent = parent.parent
    root = top_empty_parent(obj, scope_objects)
    return clean_token((root or obj).name)


def _asset_name_from_value(value):
    if not value:
        return ""
    value = str(value).replace("\\", "/").rstrip("/")
    name = value.rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name


def resolve_sidecar_json_path(candidates, context=None):
    if isinstance(candidates, (str, bytes)):
        candidates = [candidates]
    export_dir = Path(resolve_export_directory(context))
    seen = set()
    for value in candidates or []:
        name = _asset_name_from_value(value)
        if not name or name in seen:
            continue
        seen.add(name)
        path = export_dir / f"{name}.json"
        if path.exists():
            return str(path)
    return None


def sync_painter_low_export(scene=None):
    """Synchronize ``Baking/low`` into Send2UE's ``Export`` collection.

    This is the public cross-add-on entry point.  Producers should place Low
    objects in ``Baking/low`` and call this function instead of duplicating
    Export collection membership rules.
    """
    receipt = dict(sync_painter_export(scene))
    receipt["service_id"] = PAINTER_LOW_EXPORT_SERVICE_ID
    receipt["api_version"] = PAINTER_LOW_EXPORT_API_VERSION
    receipt["operation"] = "sync_painter_low_export"
    receipt["status"] = "SUCCESS"
    receipt["synced"] = True
    return receipt


def ensure_painter_low_export_unit(low_object, asset_base, scene=None):
    """Prepare one standalone static Low for Empty-based Send2UE naming.

    ``low_object`` keeps its ``_low`` suffix for Painter matching.  A safe
    static standalone mesh is parented under an exact ``asset_base`` Empty,
    then the whole hierarchy is synchronized into ``Export``. Existing rigged
    or parented hierarchies are preserved and described in the receipt.
    """
    receipt = dict(
        _ensure_painter_low_export_unit(low_object, asset_base, scene=scene)
    )
    receipt["service_id"] = PAINTER_LOW_EXPORT_SERVICE_ID
    receipt["api_version"] = PAINTER_LOW_EXPORT_API_VERSION
    receipt["operation"] = "ensure_painter_low_export_unit"
    if receipt.get("handoff_ready"):
        receipt["status"] = "SUCCESS"
    elif receipt.get("unit_status") == "PRESERVED_SKELETAL_HIERARCHY":
        receipt["status"] = "PRESERVED_SKELETAL_HIERARCHY"
    else:
        receipt["status"] = "PENDING_SEND2UE"
    return receipt
