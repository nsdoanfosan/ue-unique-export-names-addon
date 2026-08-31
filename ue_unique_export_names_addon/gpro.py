import bpy

from .utils import (
    clean_token,
    geometry_nodes_input_value,
    geometry_nodes_input_values,
)

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
            value = geometry_nodes_input_value(modifier, key)
            if isinstance(value, bpy.types.Collection) and value.name not in seen:
                collections.append(value)
                seen.add(value.name)
        for value in geometry_nodes_input_values(modifier):
            if isinstance(value, bpy.types.Collection) and value.name not in seen:
                collections.append(value)
                seen.add(value.name)
    return collections


def _instance_source_collections(obj):
    collections = list(gpro_instance_collections(obj))
    seen = {collection.name for collection in collections}
    instance_collection = getattr(obj, "instance_collection", None)
    if (
        getattr(obj, "instance_type", "NONE") == "COLLECTION"
        and instance_collection is not None
        and instance_collection.name not in seen
    ):
        collections.append(instance_collection)
    return collections


def _evaluated_material_slot_entries(obj):
    """Return only material slots used by the evaluated gPro mesh.

    A gPro source collection can contain disabled variants and stale material
    slots that never reach the exported geometry.  Sending those candidates to
    Unreal makes the synthetic JSON slot index overwrite an unrelated imported
    slot when the name cannot be found.  The evaluated polygon material indices
    are the same boundary the FBX exporter sees, so prefer them whenever gPro
    produced real geometry.
    """
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = getattr(evaluated, "data", None)
        polygons = getattr(mesh, "polygons", None)
        materials = getattr(mesh, "materials", None)
    except (AttributeError, ReferenceError, RuntimeError):
        return None
    if polygons is None or materials is None or not polygons:
        return None

    used_indices = sorted({int(polygon.material_index) for polygon in polygons})
    entries = []
    for slot_index in used_indices:
        material = materials[slot_index] if slot_index < len(materials) else None
        entries.append(
            (slot_index, material, f"{obj.name} evaluated slot {slot_index}")
        )
    return entries


def _source_mesh_material_slot_entries(obj):
    mesh = getattr(obj, "data", None)
    polygons = getattr(mesh, "polygons", None)
    materials = getattr(mesh, "materials", None)
    if polygons is None or materials is None or not polygons:
        return None
    entries = []
    for slot_index in sorted({int(polygon.material_index) for polygon in polygons}):
        # Some gPro cache meshes keep pre-modifier polygon indices that are
        # remapped only by the evaluated node graph.  An out-of-range source
        # index is not evidence of an exported empty slot.
        if slot_index >= len(materials):
            continue
        material = materials[slot_index]
        entries.append((slot_index, material, f"{obj.name} source slot {slot_index}"))
    return entries


def effective_material_slot_entries(
    obj,
    _visited_objects=None,
    _visited_collections=None,
    _inside_instance_source=False,
):
    if _visited_objects is None:
        _visited_objects = set()
    if _visited_collections is None:
        _visited_collections = set()
    if obj.name in _visited_objects:
        return []
    _visited_objects.add(obj.name)

    source_collections = _instance_source_collections(obj)
    if gpro_instance_collections(obj):
        evaluated_entries = _evaluated_material_slot_entries(obj)
        if evaluated_entries is not None:
            return evaluated_entries

    entries = (
        _source_mesh_material_slot_entries(obj)
        if _inside_instance_source and obj.type == "MESH"
        else None
    )
    if entries is None:
        entries = []
        for slot_index, slot in enumerate(obj.material_slots):
            # Empty slots on a zero-geometry instance wrapper are placeholders.
            # Used empty slots on evaluated/source geometry are retained by the
            # branches above and therefore still block handoff validation.
            if slot.material is not None or not source_collections:
                entries.append((slot_index, slot.material, f"{obj.name} slot {slot_index}"))

    next_index = len(entries)
    for collection in source_collections:
        if collection.name in _visited_collections:
            continue
        _visited_collections.add(collection.name)
        for source in collection.all_objects:
            if source.type != "MESH" and not _instance_source_collections(source):
                continue
            for _nested_slot_index, nested_mat, nested_location in effective_material_slot_entries(
                source,
                _visited_objects=_visited_objects,
                _visited_collections=_visited_collections,
                _inside_instance_source=True,
            ):
                entries.append(
                    (
                        next_index,
                        nested_mat,
                        f"{obj.name} gPro {collection.name}/{nested_location}",
                    )
                )
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
