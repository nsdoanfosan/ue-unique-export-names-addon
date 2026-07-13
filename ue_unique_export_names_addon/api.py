"""Public integration API for Send2UE and other pipeline consumers."""

from pathlib import Path

import bpy

from .gpro import is_unreal_handoff_material, unreal_handoff_materials_from_objects
from .naming import material_texture_map, resolve_export_dir
from .pipeline_json import _json_refresh_validation_errors, write_unreal_pipeline_json
from .utils import asset_prefix, hair_tool_asset_groups, validation_scope_objects

__all__ = (
    "collect_handoff_data",
    "validate_handoff",
    "refresh_handoff_json",
    "resolve_export_directory",
    "resolve_sidecar_json_path",
)


def _context(context=None):
    return context or bpy.context


def _props(context):
    return context.scene.ue_unique_names


def collect_handoff_data(context=None):
    context = _context(context)
    props = _props(context)
    objects = validation_scope_objects(context, props.scope)
    hair_assets = hair_tool_asset_groups(context, props.scope)
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
        "objects": objects,
        "hair_assets": hair_assets,
        "materials": materials,
        "texture_map": texture_map,
    }


def validate_handoff(context=None):
    data = collect_handoff_data(context)
    data["errors"] = _json_refresh_validation_errors(
        data["context"],
        data["props"],
        data["objects"],
        data["materials"],
        data["texture_map"],
        hair_assets=data["hair_assets"],
    )
    return data


def refresh_handoff_json(context=None):
    data = validate_handoff(context)
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
