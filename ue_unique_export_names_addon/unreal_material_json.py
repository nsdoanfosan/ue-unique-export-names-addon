from pathlib import Path

import bpy

from .constants import PAINTER_ROLE_NAMES, SURFACE_LAYER_PARAM_BY_ROLE
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
    if clean_name.startswith("T_"):
        return clean_name
    return f"T_{clean_name}"


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
    return entry
