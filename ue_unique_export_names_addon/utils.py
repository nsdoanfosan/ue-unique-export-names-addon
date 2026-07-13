import re
from pathlib import Path

import bpy

from .constants import (
    BAKING_LOW_COLLECTION_NAME,
    BAKING_ROOT_COLLECTION_NAME,
    EXPORT_COLLECTION_NAME,
)

def clean_token(value):
    value = str(value or "").strip()
    value = re.sub(r"[^0-9A-Za-z_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "Asset"


def asset_prefix(context, prefix_mode, custom_prefix):
    if prefix_mode == "CUSTOM" and custom_prefix.strip():
        return clean_token(custom_prefix)
    if bpy.data.filepath:
        return clean_token(Path(bpy.data.filepath).stem)
    return clean_token(context.scene.name)


def export_collection(context):
    """Return the exact collection Send to Unreal exports from."""
    return bpy.data.collections.get(EXPORT_COLLECTION_NAME)


def ensure_export_collection(context):
    coll = export_collection(context)
    if coll is not None:
        return coll
    coll = bpy.data.collections.new(EXPORT_COLLECTION_NAME)
    context.scene.collection.children.link(coll)
    return coll


def baking_low_collection():
    baking = bpy.data.collections.get(BAKING_ROOT_COLLECTION_NAME)
    if baking is not None:
        for child in baking.children:
            if child.name == BAKING_LOW_COLLECTION_NAME:
                return child
    return None


def selected_or_all_mesh_objects(context, scope):
    if scope == "SELECTED":
        objects = context.selected_objects
    elif scope == "EXPORT_COLLECTION":
        coll = export_collection(context)
        objects = coll.all_objects if coll else []
    else:  # "SCENE"
        objects = context.scene.objects
    return [
        obj
        for obj in objects
        if obj.type == "MESH"
    ]


def json_scope_mesh_objects(context, scope):
    """Mesh objects to describe in JSON. This is read-only, so protected Painter
    data is included instead of being filtered out."""
    if scope == "SELECTED":
        objects = context.selected_objects
    elif scope == "EXPORT_COLLECTION":
        coll = export_collection(context)
        objects = coll.all_objects if coll else []
    else:  # "SCENE"
        objects = context.scene.objects
    return [obj for obj in objects if obj.type == "MESH"]


def scope_objects_for_validation(context, scope):
    if scope == "SELECTED":
        return [obj for obj in context.selected_objects if obj.visible_get()]
    if scope == "EXPORT_COLLECTION":
        coll = export_collection(context)
        if not coll:
            return []
        return [
            obj
            for obj in coll.all_objects
            if coll in obj.users_collection and obj.visible_get()
        ]
    return [obj for obj in context.scene.objects if obj.visible_get()]


def is_edit_mesh_modifier(modifier):
    modifier_name = clean_token(getattr(modifier, "name", "")).casefold()
    node_group = getattr(modifier, "node_group", None)
    node_group_name = clean_token(getattr(node_group, "name", "")).casefold()
    return "edit_mesh" in {modifier_name, node_group_name}


def is_hair_tool_object(obj):
    if not obj or obj.type not in {"CURVES", "MESH"}:
        return False
    if any(is_edit_mesh_modifier(modifier) for modifier in obj.modifiers):
        return False
    node_group_names = {
        modifier.node_group.name
        for modifier in obj.modifiers
        if modifier.type == "NODES" and modifier.node_group
    }
    return (
        any(name.startswith("Hair_System_Setup") for name in node_group_names)
        and any(name.startswith("Hair_System_Profile") for name in node_group_names)
    )


def hair_tool_input_object(obj):
    for modifier in obj.modifiers:
        if (
            modifier.type != "NODES"
            or not modifier.node_group
            or not modifier.node_group.name.startswith("Hair_System_Setup")
        ):
            continue
        try:
            input_object = modifier.get("Input_3")
        except (KeyError, TypeError):
            input_object = None
        if isinstance(input_object, bpy.types.Object):
            return input_object
    return None


def hair_tool_profile_materials(obj):
    materials = []
    seen = set()
    for modifier in obj.modifiers:
        node_group = getattr(modifier, "node_group", None)
        if (
            modifier.type != "NODES"
            or not node_group
            or not node_group.name.startswith("Hair_System_Profile")
        ):
            continue
        for key in modifier.keys():
            value = modifier.get(key)
            if not isinstance(value, bpy.types.Material):
                continue
            if value.name in seen:
                continue
            materials.append(value)
            seen.add(value.name)
    return materials


def hair_tool_asset_group_key(context, obj):
    """Mirror Send2UE Hair Tool grouping: nearest exported Empty ancestor."""
    coll = export_collection(context)
    exported_objects = set(coll.all_objects) if coll else set()
    parent = obj.parent
    while parent is not None:
        if parent.type == "EMPTY" and parent in exported_objects:
            return parent
        parent = parent.parent
    return obj.parent or obj


def hair_tool_asset_groups(context, scope):
    coll = export_collection(context)
    if coll is None:
        return []
    scope_objects = set(scope_objects_for_validation(context, scope))
    source_candidates = [
        obj
        for obj in coll.all_objects
        if (
            obj in scope_objects
            and coll in obj.users_collection
            and is_hair_tool_object(obj)
        )
    ]
    upstream_hair = {
        input_object
        for input_object in (hair_tool_input_object(obj) for obj in source_candidates)
        if input_object in source_candidates
    }
    source_objects = [
        obj
        for obj in source_candidates
        if obj not in upstream_hair
    ]

    groups = []
    group_by_name = {}
    for obj in source_objects:
        asset_parent = hair_tool_asset_group_key(context, obj)
        asset_name = asset_parent.name if asset_parent != obj else obj.name
        group = group_by_name.get(asset_name)
        if group is None:
            group = {
                "asset_name": asset_name,
                "asset_parent": asset_parent,
                "sources": [],
                "materials": [],
            }
            group_by_name[asset_name] = group
            groups.append(group)
        group["sources"].append(obj)
        seen_materials = {material.name for material in group["materials"]}
        for material in hair_tool_profile_materials(obj):
            if material.name in seen_materials:
                continue
            group["materials"].append(material)
            seen_materials.add(material.name)
    return groups


def validation_scope_objects(context, scope):
    objects = scope_objects_for_validation(context, scope)
    hair_sources = {
        source
        for group in hair_tool_asset_groups(context, scope)
        for source in group["sources"]
    }
    mesh_objects = [
        obj
        for obj in objects
        if obj.type == "MESH" and obj not in hair_sources
    ]
    rows = []
    seen = set()
    for obj in mesh_objects:
        if obj.name in seen:
            continue
        rows.append(obj)
        seen.add(obj.name)
    return rows


def parent_chain(obj):
    """Return all parents from the top-most parent down to the direct parent."""
    chain = []
    parent = obj.parent
    while parent is not None:
        chain.append(parent)
        parent = parent.parent
    chain.reverse()
    return chain
