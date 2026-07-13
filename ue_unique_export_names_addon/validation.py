from pathlib import Path

import bpy

from .armature_repair import armature_validation_warnings
from .constants import MATERIAL_PREFIX
from .gpro import (
    effective_material_slot_entries,
    has_gpro_instance_material_source,
    unreal_handoff_material_slot_entries,
    unreal_handoff_materials_from_objects,
)
from .materials import linked_painter_low_objects, protected_painter_data
from .naming import material_texture_map, top_empty_parent
from .transfer import (
    armature_names_for_object,
    export_action_for_object,
    export_detail_lines_for_object,
    export_kind_for_object,
    texture_roles_for_materials,
    transfer_shape_keys_enabled,
    transfer_source_for_object,
    transfer_source_name_for_object,
    transfer_weights_enabled,
    unique_ordered_names,
)
from .utils import clean_token, export_collection, is_hair_tool_object, parent_chain, validation_scope_objects

def _hair_asset_validation_row(asset, texture_map):
    asset_name = asset["asset_name"]
    asset_parent = asset["asset_parent"]
    generated_mesh_name = f"{asset_name}__S2U_HAIR"
    sources = asset["sources"]
    handoff_materials = list(asset["materials"])
    texture_roles = texture_roles_for_materials(handoff_materials, texture_map)
    errors = []
    warnings = []

    if clean_token(asset_name) != asset_name:
        errors.append(f"JSON name becomes {clean_token(asset_name)}")
    if not handoff_materials:
        errors.append("No Hair Tool profile material")

    for material in handoff_materials:
        if clean_token(material.name) != material.name:
            errors.append(f"Material renames to {clean_token(material.name)}")
        if not clean_token(material.name).startswith(MATERIAL_PREFIX):
            errors.append(f"{material.name} has no {MATERIAL_PREFIX} prefix")
        textures = texture_map.get(material, {})
        for role, image in textures.items():
            source_value = image.filepath_raw or image.filepath
            if not source_value:
                errors.append(f"{image.name} ({role}) has no file path")
                continue
            if not Path(bpy.path.abspath(source_value)).is_file():
                errors.append(f"{image.name} ({role}) file missing")

    transfer_source = ""
    transfer_shape_keys = False
    transfer_weights = False
    for source in sources:
        transfer_shape_keys = transfer_shape_keys or transfer_shape_keys_enabled(source)
        transfer_weights = transfer_weights or transfer_weights_enabled(source)
        if not transfer_source:
            transfer_source = transfer_source_name_for_object(source)

    status = "ERROR" if errors else "WARN" if warnings else "OK"
    return {
        "object_name": generated_mesh_name,
        "mesh_data_name": generated_mesh_name,
        "asset_unit": asset_name,
        "json_name": clean_token(asset_name),
        "parent_chain": [parent.name for parent in parent_chain(asset_parent)],
        "armatures": unique_ordered_names(
            name for source in sources for name in armature_names_for_object(source)
        ),
        "transfer_source": transfer_source,
        "transfer_shape_keys": transfer_shape_keys,
        "transfer_weights": transfer_weights,
        "export_kind": "Hair",
        "export_action": "Hair bake",
        "export_details": [
            f"Hair Tool final asset: {len(sources)} source object(s) bake to {asset_name}__S2U_HAIR.",
            "Profile materials are used for handoff; source guide mesh material slots are ignored.",
        ],
        "material_slots": [material.name for material in handoff_materials],
        "handoff_materials": [material.name for material in handoff_materials],
        "texture_roles": texture_roles,
        "painter_protected": False,
        "painter_low": False,
        "status": status,
        "errors": unique_ordered_names(errors),
        "warnings": unique_ordered_names(warnings),
        "json_ready": status != "ERROR",
    }


def _hair_source_validation_row(asset, source):
    asset_name = asset["asset_name"]
    errors = []
    warnings = []

    if clean_token(source.name) != source.name:
        errors.append(f"JSON name becomes {clean_token(source.name)}")

    profile_materials = []
    seen = set()
    for material in asset["materials"]:
        if material.name in seen:
            continue
        profile_materials.append(material)
        seen.add(material.name)

    if not profile_materials:
        errors.append("No Hair Tool profile material")

    status = "ERROR" if errors else "WARN" if warnings else "OK"
    return {
        "object_name": source.name,
        "mesh_data_name": source.data.name if source.data else "",
        "asset_unit": asset_name,
        "json_name": clean_token(asset_name),
        "parent_chain": [parent.name for parent in parent_chain(source)],
        "armatures": armature_names_for_object(source),
        "transfer_source": transfer_source_name_for_object(source),
        "transfer_shape_keys": transfer_shape_keys_enabled(source),
        "transfer_weights": transfer_weights_enabled(source),
        "export_kind": "Hair Source",
        "export_action": "Hair source",
        "export_details": [
            f"Visible Hair Tool source for {asset_name}__S2U_HAIR.",
            "Source material slots are not Unreal handoff materials; the generated hair mesh is validated separately.",
        ],
        "material_slots": [slot.material.name if slot.material else "" for slot in source.material_slots],
        "handoff_materials": [material.name for material in profile_materials],
        "texture_roles": [],
        "painter_protected": False,
        "painter_low": False,
        "status": status,
        "errors": unique_ordered_names(errors),
        "warnings": unique_ordered_names(warnings),
        "json_ready": True,
    }


def export_validation_rows(context, props=None, objects=None, materials=None, texture_map=None, hair_assets=None):
    if props is None:
        props = context.scene.ue_unique_names
    objects = list(objects) if objects is not None else validation_scope_objects(context, props.scope)
    materials = list(materials) if materials is not None else unreal_handoff_materials_from_objects(objects)
    if texture_map is None:
        texture_map = material_texture_map(materials)
    if hair_assets is None:
        from .utils import hair_tool_asset_groups
        hair_assets = hair_tool_asset_groups(context, props.scope)

    protected = protected_painter_data(context)
    painter_low_objects = linked_painter_low_objects(context)
    export_coll = export_collection(context)
    export_objects = set(export_coll.all_objects) if export_coll else set()
    rows = []
    for asset in hair_assets:
        rows.extend(_hair_source_validation_row(asset, source) for source in asset["sources"])

    for obj in objects:
        material_slots = effective_material_slot_entries(obj)
        handoff_materials = [
            mat
            for _slot_index, mat, _location in unreal_handoff_material_slot_entries(obj)
        ]
        empty_slot_count = len(material_slots) - len(
            [mat for _slot_index, mat, _location in material_slots if mat]
        )
        parent_names = [parent.name for parent in parent_chain(obj)]
        armatures = armature_names_for_object(obj)
        texture_roles = texture_roles_for_materials(handoff_materials, texture_map)
        unit_root = top_empty_parent(obj, export_objects) or obj
        painter_low = obj in painter_low_objects
        errors = []
        warnings = []

        if painter_low:
            pass
        elif not material_slots:
            errors.append("No material slots")
        elif not handoff_materials and not is_hair_tool_object(obj):
            warnings.append("No UE handoff material")
        if empty_slot_count and not painter_low and not has_gpro_instance_material_source(obj):
            errors.append(f"{empty_slot_count} empty material slot")

        if clean_token(obj.name) != obj.name:
            errors.append(f"JSON name becomes {clean_token(obj.name)}")

        if (transfer_shape_keys_enabled(obj) or transfer_weights_enabled(obj)) and not transfer_source_for_object(obj):
            warnings.append("Transfer source not set")
        warnings.extend(armature_validation_warnings(context, obj))

        for material in handoff_materials:
            if clean_token(material.name) != material.name:
                errors.append(f"Material renames to {clean_token(material.name)}")
            if not clean_token(material.name).startswith(MATERIAL_PREFIX):
                errors.append(f"{material.name} has no {MATERIAL_PREFIX} prefix")
            textures = texture_map.get(material, {})
            if not textures:
                continue
            for role, image in textures.items():
                source_value = image.filepath_raw or image.filepath
                if not source_value:
                    errors.append(f"{image.name} ({role}) has no file path")
                    continue
                if not Path(bpy.path.abspath(source_value)).is_file():
                    errors.append(f"{image.name} ({role}) file missing")

        status = "ERROR" if errors else "WARN" if warnings else "OK"
        rows.append(
            {
                "object_name": obj.name,
                "mesh_data_name": obj.data.name if obj.data else "",
                "asset_unit": unit_root.name,
                "json_name": clean_token(unit_root.name),
                "parent_chain": parent_names,
                "armatures": armatures,
                "transfer_source": transfer_source_name_for_object(obj),
                "transfer_shape_keys": transfer_shape_keys_enabled(obj),
                "transfer_weights": transfer_weights_enabled(obj),
                "export_kind": export_kind_for_object(obj, painter_low=painter_low),
                "export_action": export_action_for_object(obj, painter_low=painter_low),
                "export_details": export_detail_lines_for_object(obj),
                "material_slots": [
                    mat.name if mat else ""
                    for _slot_index, mat, _location in material_slots
                ],
                "handoff_materials": [material.name for material in handoff_materials],
                "texture_roles": texture_roles,
                "painter_protected": obj in protected["objects"],
                "painter_low": painter_low,
                "status": status,
                "errors": unique_ordered_names(errors),
                "warnings": unique_ordered_names(warnings),
                "json_ready": status != "ERROR",
            }
        )
    return rows


def validation_summary(rows):
    counts = {"OK": 0, "WARN": 0, "ERROR": 0}
    for row in rows:
        counts[row["status"]] += 1
    return counts


def validation_pipeline_summary(rows):
    hair_units = {
        row.get("asset_unit")
        for row in rows
        if row.get("export_kind") in {"Hair", "Hair Source"}
    }
    return {
        "low": sum(1 for row in rows if row.get("painter_low")),
        "hair": len([unit for unit in hair_units if unit]),
        "transfer": sum(
            1
            for row in rows
            if row.get("transfer_source")
            and (row.get("transfer_shape_keys") or row.get("transfer_weights"))
        ),
    }


def validation_icon(status):
    if status == "OK":
        return "CHECKMARK"
    if status == "WARN":
        return "ERROR"
    return "CANCEL"


def compact_list_label(values, empty="-", limit=3):
    values = [str(value) for value in values if value]
    if not values:
        return empty
    visible = values[:limit]
    suffix = f" +{len(values) - limit}" if len(values) > limit else ""
    return ", ".join(visible) + suffix


def validation_expanded_names(props):
    return {
        name
        for name in props.validation_expanded_rows.splitlines()
        if name
    }


def set_validation_expanded_names(props, names):
    props.validation_expanded_rows = "\n".join(sorted(names))


def compact_name(value, limit=26):
    value = str(value or "")
    if len(value) <= limit:
        return value
    head = max(8, limit - 11)
    tail = 8
    return f"{value[:head]}...{value[-tail:]}"


def mesh_summary_name(value, grouped=False):
    value = str(value or "")
    tokens = [token for token in value.split("_") if token]
    if grouped and len(tokens) > 3:
        return "_".join(tokens[-3:])
    if len(value) <= 24:
        return value
    if len(tokens) > 3:
        return "_".join(tokens[-3:])
    return compact_name(value, limit=24)


def wrapped_text_lines(text, width=54):
    text = str(text or "")
    if len(text) <= width:
        return [text]
    lines = []
    remaining = text
    while len(remaining) > width:
        split_at = remaining.rfind(" ", 0, width + 1)
        if split_at < width // 2:
            split_at = width
        lines.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        lines.append(remaining)
    return lines


def draw_wrapped_label(layout, text, icon="NONE", width=54):
    for index, line in enumerate(wrapped_text_lines(text, width)):
        if index == 0 and icon != "NONE":
            layout.label(text=line, icon=icon)
        else:
            layout.label(text=line)


def fixed_table_column(row, ui_units):
    column = row.column(align=True)
    try:
        column.ui_units_x = ui_units
    except AttributeError:
        pass
    return column


VALIDATION_SPREADSHEET_OBJECT = "_UEUN_Export_Validation_Table"
VALIDATION_SPREADSHEET_MESH = "_UEUN_Export_Validation_Table_Mesh"

def grouped_validation_rows(rows):
    groups = []
    index_by_unit = {}
    for row in rows:
        unit = row["asset_unit"]
        if unit not in index_by_unit:
            index_by_unit[unit] = len(groups)
            groups.append({"unit": unit, "rows": []})
        groups[index_by_unit[unit]]["rows"].append(row)
    return groups


def validation_group_needs_header(group):
    rows = group["rows"]
    return len(rows) > 1 or any(row["object_name"] != group["unit"] for row in rows)
