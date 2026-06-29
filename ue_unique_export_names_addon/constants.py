from .contract import collection_name, naming_value, pipeline_contract


BACKUP_PROP = "_ue_unique_export_original_name"
BACKUP_FILEPATH_PROP = "_ue_unique_export_original_filepath"
BACKUP_FILEPATH_RAW_PROP = "_ue_unique_export_original_filepath_raw"
# Legacy marker from versions that created Painter grouping Empties. Restore
# still recognizes it so old .blend files can clean those generated objects up.
CREATED_EMPTY_PROP = "_ue_unique_export_created_empty"
AUTO_PAINTER_EXPORT_LINK_PROP = "_ue_unique_export_auto_link"

# Send to Unreal exports the objects inside a collection named "Export"; the addon's
# default scope mirrors that so you don't have to manually select every object.
EXPORT_COLLECTION_NAME = collection_name("send_to_unreal_export", "Export")
BAKING_ROOT_COLLECTION_NAME = collection_name("baking_root", "Baking")
BAKING_LOW_COLLECTION_NAME = collection_name("low", "low")
MATERIAL_PREFIX = naming_value("material_prefix", "M_")
TEXTURE_PREFIX = naming_value("texture_prefix", "T_")

ROLE_BY_BSDF_INPUT = {
    "Base Color": "BaseColor",
    "Metallic": "Metallic",
    "Roughness": "Roughness",
    "Normal": "Normal",
    "Emission Color": "Emissive",
    "Emission Strength": "Emissive",
    "Alpha": "Alpha",
    "Sheen Tint": "SheenColor",
    "Sheen Color": "SheenColor",
    "Sheen Weight": "SheenOpacity",
    "Sheen Opacity": "SheenOpacity",
    "Sheen Roughness": "SheenRoughness",
}

ROLE_PRIORITY = [
    "BaseColor",
    "MetallicRoughness",
    "Normal",
    "Height",
    "Emissive",
    "Roughness",
    "Metallic",
    "Occlusion",
    "Alpha",
    "SheenColor",
    "SheenOpacity",
    "SheenRoughness",
    "Texture",
]

PAINTER_ROLE_NAMES = {
    "BaseColor": "Color",
    "MetallicRoughness": "Extra",
    "Normal": "Normal",
    "Emissive": "Emissive",
    "Height": "Height",
    "SheenColor": "SheenColor",
    "SheenOpacity": "SheenOpacity",
    "SheenRoughness": "SheenRoughness",
}

DEFAULT_SURFACE_LAYER_PARAM_BY_ROLE = {
    "BaseColor": "Albedo",
    "MetallicRoughness": "Extra",
    "Roughness": "Extra",
    "Metallic": "Extra",
    "Occlusion": "Extra",
    "Normal": "Normal",
    "Height": "Height",
    "Alpha": "Transmission",
    "Emissive": "Emissive",
    "SheenColor": "Sheen Color",
    "SheenOpacity": "Sheen Opacity",
    "SheenRoughness": "Sheen Roughness",
    "Texture": "Albedo",
}


def _surface_layer_param_by_role():
    mapping = (
        pipeline_contract()
        .get("unreal_handoff_sidecar", {})
        .get("surface_layer_params_by_role")
    )
    if isinstance(mapping, dict) and mapping:
        merged = dict(DEFAULT_SURFACE_LAYER_PARAM_BY_ROLE)
        merged.update({str(key): str(value) for key, value in mapping.items()})
        return merged
    return dict(DEFAULT_SURFACE_LAYER_PARAM_BY_ROLE)


SURFACE_LAYER_PARAM_BY_ROLE = _surface_layer_param_by_role()
