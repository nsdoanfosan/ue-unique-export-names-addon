from .utils import clean_token, is_hair_tool_object, parent_chain

def unique_ordered_names(values):
    names = []
    seen = set()
    for value in values:
        if not value or value in seen:
            continue
        names.append(value)
        seen.add(value)
    return names


def armature_names_for_object(obj):
    names = []
    for parent in parent_chain(obj):
        if parent.type == "ARMATURE":
            names.append(parent.name)
    for modifier in obj.modifiers:
        if modifier.type != "ARMATURE":
            continue
        target = getattr(modifier, "object", None)
        names.append(target.name if target else modifier.name)
    return unique_ordered_names(names)


def texture_roles_for_materials(materials, texture_map):
    roles = []
    for material in materials:
        roles.extend(texture_map.get(material, {}).keys())
    return unique_ordered_names(roles)


def transfer_source_for_object(obj):
    if not obj or not hasattr(obj, "vdt_object_props"):
        return None
    return getattr(obj.vdt_object_props, "transfer_source", None)


def transfer_source_name_for_object(obj):
    source = transfer_source_for_object(obj)
    return source.name if source else ""


def transfer_shape_keys_enabled(obj):
    return bool(getattr(obj, "ue_unique_transfer_shape_keys", False))


def transfer_weights_enabled(obj):
    return bool(getattr(obj, "ue_unique_transfer_weights", False))


def transfer_check_label(enabled):
    return "Yes" if enabled else "-"


def export_kind_for_object(obj, painter_low=False):
    if is_hair_tool_object(obj):
        return "Hair"
    if painter_low:
        return "Low"
    return "Mesh"


def export_action_for_object(obj, painter_low=False):
    if is_hair_tool_object(obj):
        return "Hair bake"
    if painter_low:
        return "Painter low"
    return "Mesh export"


def export_detail_lines_for_object(obj):
    if not is_hair_tool_object(obj):
        return []
    return [
        "Hair Tool source: Send2UE bakes this object to a temporary mesh before FBX export.",
        "Generated mesh: evaluated cards are joined per export asset, UVMapGN becomes UVMap, and RFAOS is packed as RGBA = Random/Factor/AO/SystemColor Alpha.",
        "Rigging: the export mesh gets the detected head bone vertex group at weight 1.0 plus an Armature modifier.",
        "Transfer: Shape Keys / Weights are read from the JSON sidecar before Send2UE exports the baked mesh.",
    ]


def transfer_postprocess_entry(obj):
    source_name = transfer_source_name_for_object(obj)
    shape_keys = transfer_shape_keys_enabled(obj)
    weights = transfer_weights_enabled(obj)
    return {
        "target": obj.name,
        "source": source_name,
        "shape_keys": shape_keys,
        "weights": weights,
        "enabled": bool(source_name and (shape_keys or weights)),
    }
