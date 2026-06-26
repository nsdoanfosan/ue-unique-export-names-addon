BACKUP_PROP = "_ue_unique_export_original_name"
BACKUP_FILEPATH_PROP = "_ue_unique_export_original_filepath"
BACKUP_FILEPATH_RAW_PROP = "_ue_unique_export_original_filepath_raw"
# Legacy marker from versions that created Painter grouping Empties. Restore
# still recognizes it so old .blend files can clean those generated objects up.
CREATED_EMPTY_PROP = "_ue_unique_export_created_empty"
AUTO_PAINTER_EXPORT_LINK_PROP = "_ue_unique_export_auto_link"

# Send to Unreal exports the objects inside a collection named "Export"; the addon's
# default scope mirrors that so you don't have to manually select every object.
EXPORT_COLLECTION_NAME = "Export"
BAKING_LOW_COLLECTION_NAME = "low"

ROLE_BY_BSDF_INPUT = {
    "Base Color": "BaseColor",
    "Metallic": "Metallic",
    "Roughness": "Roughness",
    "Normal": "Normal",
    "Emission Color": "Emissive",
    "Emission Strength": "Emissive",
    "Alpha": "Alpha",
}

ROLE_PRIORITY = [
    "BaseColor",
    "MetallicRoughness",
    "Normal",
    "Emissive",
    "Roughness",
    "Metallic",
    "Occlusion",
    "Alpha",
    "Texture",
]

PAINTER_ROLE_NAMES = {
    "BaseColor": "Color",
    "MetallicRoughness": "Extra",
    "Normal": "Normal",
    "Emissive": "Emissive",
    "Height": "Height",
}

SURFACE_LAYER_PARAM_BY_ROLE = {
    "BaseColor": "Albedo",
    "MetallicRoughness": "Extra",
    "Roughness": "Extra",
    "Metallic": "Extra",
    "Occlusion": "Extra",
    "Normal": "Normal",
    "Height": "Height",
    "Alpha": "Transmission",
    "Emissive": "Emissive",
    "Texture": "Albedo",
}
