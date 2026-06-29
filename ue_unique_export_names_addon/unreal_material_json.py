from pathlib import Path

import bpy

from .constants import PAINTER_ROLE_NAMES, SURFACE_LAYER_PARAM_BY_ROLE, TEXTURE_PREFIX
from .contract import pipeline_contract
from .gpro import effective_material_slot_entries
from .naming import image_disk_path
from .utils import clean_token

def first_slot_index_for_material(objects, material):
    for obj in objects:
        for slot_index, mat, _location in effective_material_slot_entries(obj):
            if mat == material:
                return slot_index
    return 0


def is_translucent_material(mat):
    """블렌더 머티리얼이 반투명인지 판정. EEVEE Next(4.2+)와 구버전 모두 대응.
    언리얼 파이프라인이 이 플래그를 보고 해당 메쉬의 Nanite 를 끈다."""
    # Blender 4.2+ (EEVEE Next): surface_render_method ∈ {'DITHERED', 'BLENDED'}
    method = getattr(mat, "surface_render_method", None)
    if method is not None:
        return method == "BLENDED"
    # 구버전: blend_method ∈ {'OPAQUE','CLIP','HASHED','BLEND'}
    legacy = getattr(mat, "blend_method", "OPAQUE")
    return legacy in {"BLEND", "HASHED"}


def surface_layer_param_for_role(role):
    return SURFACE_LAYER_PARAM_BY_ROLE.get(role, role)


def is_layerblend_material_name(material_name):
    return clean_token(material_name).lower().startswith("m_layerblend_")


def _material_instance_base_name(material_name):
    name = clean_token(material_name)
    if name.startswith("M_"):
        return name[2:]
    if name.startswith("MI_"):
        return name[3:]
    return name


def _strip_first_prefix(value, prefixes):
    value = str(value or "")
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


DEFAULT_MATERIAL_LAYER_PRESETS = {
    "layer": {
        "parent": "/Game/Material/AssetSurface/Master/MaterialLayer/MY_Mesh_UV0",
        "folder": "/Game/Material/AssetSurface/MYI/LayerBlend",
        "strip_prefixes": ("LayerBlend_",),
    },
    "cloth": {
        "parent": "/Game/Material/AssetSurface/Master/MaterialLayer/MY_Cloth",
        "folder": "/Game/Material/AssetSurface/MYI/Cloth",
        "strip_prefixes": (),
    },
}


COMMON_TEXTURE_PARAM_BY_LAYER_PARAM = {
    "Albedo": "Albedo",
    "Extra": "Extra",
    "Normal": "Normal",
    "Height": "Height",
    "Transmission": "Transmission",
    "Emissive": "Emissive",
    "Moss Blend Mask": "Moss Blend Mask",
}
MATERIAL_LAYER_TEXTURE_PARAM_OVERRIDES = {
    "cloth": {
        "Albedo": "BaseColor",
        "Extra": "ORM",
    },
}
CLOTH_TEXTURE_PARAM_EXTRAS = {
    "Sheen Color": "Fuzz Color Map",
    "Sheen Opacity": "Fuzz Mask",
    "Sheen Roughness": "Fuzz Roughness Map",
}


def _texture_param_map(overrides=None, extras=None):
    mapping = dict(COMMON_TEXTURE_PARAM_BY_LAYER_PARAM)
    if overrides:
        mapping.update({str(key): str(value) for key, value in overrides.items()})
    if extras:
        mapping.update({str(key): str(value) for key, value in extras.items()})
    return mapping


def _texture_param_map_for_material_layer_preset(key):
    if key == "cloth":
        return _texture_param_map(
            MATERIAL_LAYER_TEXTURE_PARAM_OVERRIDES.get("cloth"),
            CLOTH_TEXTURE_PARAM_EXTRAS,
        )
    return _texture_param_map()


def _contract_cloth_material_layer():
    return (
        pipeline_contract()
        .get("unreal_handoff_sidecar", {})
        .get("cloth_master_param_remap", {})
        .get("material_layer", {})
    )


def _material_layer_presets():
    presets = {
        key: {
            preset_key: (dict(preset_value) if isinstance(preset_value, dict) else preset_value)
            for preset_key, preset_value in preset.items()
        }
        for key, preset in DEFAULT_MATERIAL_LAYER_PRESETS.items()
    }
    for key, preset in presets.items():
        preset["texture_remap"] = _texture_param_map_for_material_layer_preset(key)
    cloth_layer = _contract_cloth_material_layer()
    if isinstance(cloth_layer, dict) and cloth_layer:
        cloth = presets.setdefault("cloth", {})
        if cloth_layer.get("parent"):
            cloth["parent"] = str(cloth_layer["parent"])
        if cloth_layer.get("instance_folder"):
            cloth["folder"] = str(cloth_layer["instance_folder"])
        texture_remap = cloth_layer.get("texture_remap")
        if isinstance(texture_remap, dict) and texture_remap:
            merged = dict(cloth.get("texture_remap", {}))
            merged.update({str(key): str(value) for key, value in texture_remap.items()})
            cloth["texture_remap"] = merged
    return presets


def material_layer_entry_for_material(mat, master_preset):
    preset = _material_layer_presets().get(master_preset)
    if not preset:
        return None
    base_name = _material_instance_base_name(mat.name)
    instance_base = _strip_first_prefix(base_name, preset["strip_prefixes"])
    return {
        "assignment": "background",
        "parent": preset["parent"],
        "instance_path": f'{preset["folder"]}/MYI_{instance_base}',
        "texture_remap": dict(preset["texture_remap"]),
    }


def _texture_json_entry(role, image, param=None):
    source_path = bpy.path.abspath(image.filepath_raw or image.filepath).replace("\\", "/")
    entry = {
        "param": param or role,
        "asset_name": texture_asset_name_for_image(image, source_path),
        "file": source_path,
    }
    if param and param != role:
        entry["source_param"] = role
    return entry


def texture_asset_name_for_image(image, source_path=None):
    source_path = source_path or bpy.path.abspath(image.filepath_raw or image.filepath)
    stem = Path(source_path).stem if source_path else image.name
    clean_name = clean_token(stem)
    if clean_name.startswith(TEXTURE_PREFIX):
        return clean_name
    return f"{TEXTURE_PREFIX}{clean_name}"


def _material_layer_json_entries(mat, texture_map):
    textures = []
    seen_params = set()
    for role, image in texture_map.get(mat, {}).items():
        param = surface_layer_param_for_role(role)
        if param in seen_params:
            continue
        seen_params.add(param)
        textures.append(_texture_json_entry(role, image, param=param))
    if not textures:
        return []
    return [
        {
            "name": "Base",
            "index": 0,
            "textures": textures,
        }
    ]


def master_preset_for_material(mat):
    name = clean_token(mat.name).lower()
    if is_layerblend_material_name(mat.name):
        return "layer"
    if "cloth" in name or "clothes" in name:
        return "cloth"
    return None


def _material_json_entry(mat, slot_index, texture_map):
    textures = []
    for role, image in texture_map.get(mat, {}).items():
        textures.append(_texture_json_entry(role, image))
    entry = {
        "name": mat.name,
        "slot_name": mat.name,
        "slot_index": slot_index,
        "translucent": is_translucent_material(mat),
        "textures": textures,
        "layers": _material_layer_json_entries(mat, texture_map),
    }
    master_preset = master_preset_for_material(mat)
    if master_preset:
        entry["master_preset"] = master_preset
        material_layer = material_layer_entry_for_material(mat, master_preset)
        if material_layer:
            entry["material_layer"] = material_layer
    return entry
