import bpy

from .utils import clean_token

def is_gpro_instance_modifier(modifier):
    modifier_name = clean_token(getattr(modifier, "name", "")).casefold()
    node_group = getattr(modifier, "node_group", None)
    node_group_name = clean_token(getattr(node_group, "name", "")).casefold() if node_group else ""
    return "gpro_instance" in {modifier_name, node_group_name}


def gpro_instance_collections(obj):
    collections = []
    seen = set()
    for modifier in obj.modifiers:
        if not is_gpro_instance_modifier(modifier):
            continue
        for key in ("Socket_2",):
            value = modifier.get(key)
            if isinstance(value, bpy.types.Collection) and value.name not in seen:
                collections.append(value)
                seen.add(value.name)
        for key in modifier.keys():
            value = modifier.get(key)
            if isinstance(value, bpy.types.Collection) and value.name not in seen:
                collections.append(value)
                seen.add(value.name)
    return collections


def effective_material_slot_entries(obj, _visited_objects=None, _visited_collections=None):
    if _visited_objects is None:
        _visited_objects = set()
    if _visited_collections is None:
        _visited_collections = set()
    if obj.name in _visited_objects:
        return []
    _visited_objects.add(obj.name)

    entries = []
    for slot_index, slot in enumerate(obj.material_slots):
        entries.append((slot_index, slot.material, f"{obj.name} slot {slot_index}"))

    next_index = len(entries)
    for collection in gpro_instance_collections(obj):
        if collection.name in _visited_collections:
            continue
        _visited_collections.add(collection.name)
        for source in collection.all_objects:
            if source.type != "MESH":
                continue
            for source_slot_index, slot in enumerate(source.material_slots):
                entries.append(
                    (
                        next_index,
                        slot.material,
                        f"{obj.name} gPro {collection.name}/{source.name} slot {source_slot_index}",
                    )
                )
                next_index += 1
            for _nested_slot_index, nested_mat, nested_location in effective_material_slot_entries(
                source,
                _visited_objects=_visited_objects,
                _visited_collections=_visited_collections,
            ):
                entries.append((next_index, nested_mat, f"{obj.name} gPro {nested_location}"))
                next_index += 1
    return entries


def effective_material_names(obj):
    return [mat.name if mat else "" for _slot_index, mat, _location in effective_material_slot_entries(obj)]


def has_gpro_instance_material_source(obj):
    return bool(gpro_instance_collections(obj))


def materials_from_objects(objects):
    materials = []
    seen = set()
    for obj in objects:
        for _slot_index, mat, _location in effective_material_slot_entries(obj):
            if mat and not mat.library and mat.name not in seen:
                materials.append(mat)
                seen.add(mat.name)
    return materials


def materials_from_objects_readonly(objects):
    materials = []
    seen = set()
    for obj in objects:
        for _slot_index, mat, _location in effective_material_slot_entries(obj):
            if mat and mat.name not in seen:
                materials.append(mat)
                seen.add(mat.name)
    return materials


def is_unreal_handoff_material(mat):
    if mat is None:
        return False
    return not clean_token(mat.name).upper().startswith("HT_")


def unreal_handoff_material_slot_entries(obj):
    return [
        (slot_index, mat, location)
        for slot_index, mat, location in effective_material_slot_entries(obj)
        if mat and is_unreal_handoff_material(mat)
    ]


def unreal_handoff_materials_from_objects(objects):
    materials = []
    seen = set()
    for obj in objects:
        for _slot_index, mat, _location in unreal_handoff_material_slot_entries(obj):
            if (
                mat
                and mat.name not in seen
            ):
                materials.append(mat)
                seen.add(mat.name)
    return materials


def material_usage_lookup(objects):
    usage = {}
    for obj in objects:
        for _slot_index, mat, location in unreal_handoff_material_slot_entries(obj):
            if mat is None:
                continue
            usage.setdefault(mat, []).append(location)
    return usage


def material_usage_text(material, usage):
    locations = usage.get(material, [])
    if not locations:
        return "not assigned to target meshes"
    visible = locations[:3]
    suffix = f", +{len(locations) - len(visible)} more" if len(locations) > len(visible) else ""
    return ", ".join(visible) + suffix
