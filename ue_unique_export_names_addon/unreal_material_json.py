import re
from pathlib import Path

import bpy

from .constants import PAINTER_ROLE_NAMES, SURFACE_LAYER_PARAM_BY_ROLE, TEXTURE_PREFIX
from .contract import pipeline_contract, speedtree_handoff_contract
from .gpro import effective_material_slot_entries
from .naming import image_disk_path
from .utils import clean_token


HAIR_TEXTURE_ROOT = Path(
    r"D:\OneDrive\Forestportfolio\Characters\MainCharacter\03_Hair\texture"
)
HAIR_TEXTURE_SET_BY_MATERIAL = {
    "ht_default_material": "Hair_Long_01",
    "m_ht_default_material_01": "Hair_Long_01",
    "m_ht_default_material_blow_01": "Hair_Blow_01",
    "m_ht_default_material_short_01": "Hair_Short_01",
    "m_ht_default_material_short_02": "Hair_Short_02",
}
HAIR_TEXTURE_SUFFIXES = {
    "Flow Map": "flow",
    "IRD Map": "IRD",
    "ORM Map": "ORM",
    "Opacity Map": "Opacity",
}
HAIR_TOOL_CONTROL_SOURCE_MATERIAL = "HT_Default_Material"

TREE_PART_ALIASES = {
    "leaf": "leaf",
    "leaves": "leaf",
    "foliage": "leaf",
    "cluster": "leaf",
    "branch": "branch",
    "branches": "branch",
    "twig": "branch",
    "twigs": "branch",
    "stem": "branch",
    "stems": "branch",
    "bark": "bark",
    "trunk": "bark",
    "stump": "bark",
}
TREE_PART_NAME_TOKENS = {
    "leaf": {"leaf", "leaves", "foliage", "cluster"},
    "branch": {"branch", "branches", "twig", "twigs", "stem", "stems"},
    "bark": {"bark", "trunk", "stump"},
}
TREE_SHADING_ALIASES = {
    "wood": "wood",
    "opaque": "wood",
    "foliage": "foliage",
    "subsurface": "foliage",
    "sss": "foliage",
    "stem": "stem",
    "wrap": "stem",
}
TREE_LAYER_PARENT_BY_PART = {
    "bark": "/Game/Material/Tree/AssetTree/Master/MaterialLayer/MY_Tree_Bark",
    "branch": "/Game/Material/Tree/AssetTree/Master/MaterialLayer/MY_Tree_Branch",
    "leaf": "/Game/Material/Tree/AssetTree/Master/MaterialLayer/MY_Tree_Leaf",
}
TREE_LAYER_INSTANCE_FOLDER = "/Game/Material/Tree/AssetTree/MYI"
TREE_IGNORED_TEXTURE_PARAMS = {
    "alpha",
    "opacity",
    "opacity map",
    "transmission",
}
TREE_BASE_COLOR_SUFFIXES = (
    "_base_color",
    "_basecolor",
    "_albedo",
    "_diffuse",
    "_color",
)
TREE_SUBSURFACE_SOURCE_SUFFIXES = (
    "_subsurface",
    "_subsurfacecolor",
    "_translucency",
)
UNREAL_INSTANCE_PROFILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def _normalized_tree_part(value):
    contract_api = speedtree_handoff_contract()
    if contract_api is not None:
        return contract_api.normalize_tree_part(value)
    return TREE_PART_ALIASES.get(str(value or "").strip().casefold())


def _unreal_instance_profile(mat):
    profile = str(mat.get("unreal_instance_profile") or "").strip()
    if not profile:
        return ""
    contract_api = speedtree_handoff_contract()
    if contract_api is not None:
        try:
            return contract_api.normalize_instance_profile(profile)
        except ValueError as exc:
            raise ValueError(
                f"Material '{mat.name}' has an invalid "
                f"unreal_instance_profile: {profile!r}. {exc}"
            ) from exc
    if not UNREAL_INSTANCE_PROFILE_RE.fullmatch(profile):
        raise ValueError(
            f"Material '{mat.name}' has an invalid unreal_instance_profile: "
            f"{profile!r}. Use one key made of letters, numbers, '_' or '-'."
        )
    return profile.casefold()


def _tree_name_tokens(material_name):
    return {
        token
        for token in re.split(r"[^a-z0-9]+", clean_token(material_name).casefold())
        if token
    }


def tree_part_for_material(mat):
    explicit_value = mat.get("unreal_tree_part")
    contract_api = speedtree_handoff_contract()
    if contract_api is not None:
        return contract_api.classify_tree_part(
            mat.name,
            explicit=str(explicit_value or ""),
        )

    explicit_part = _normalized_tree_part(explicit_value)
    if explicit_part:
        return explicit_part

    name_tokens = _tree_name_tokens(mat.name)
    # Leaf-atlas scope wins over a subgroup label.  Materials such as
    # ``M_leaf_parsley_atlas_02_stem`` and ``M_leaf_*_twig`` use the same leaf
    # UV/translucency contract even though their object collection says stem.
    for tree_part in ("leaf", "branch", "bark"):
        matched = bool(name_tokens.intersection(TREE_PART_NAME_TOKENS[tree_part]))
        if tree_part == "branch":
            matched = matched or any(
                token.endswith(("branch", "twig"))
                for token in name_tokens
            )
        if matched:
            return tree_part
    return None


def tree_shading_for_material(mat, tree_part=None):
    explicit_value = (
        mat.get("unreal_tree_shading")
        or mat.get("unreal_tree_master_variant")
        or ""
    )
    contract_api = speedtree_handoff_contract()
    if contract_api is not None:
        return contract_api.classify_tree_shading(
            mat.name,
            explicit=str(explicit_value),
            tree_part=tree_part,
        )

    explicit = TREE_SHADING_ALIASES.get(
        str(explicit_value).strip().casefold()
    )
    if explicit:
        return explicit

    tree_part = tree_part or tree_part_for_material(mat)
    if tree_part == "leaf":
        return "foliage"
    name_tokens = _tree_name_tokens(mat.name)
    if name_tokens.intersection({"stem", "stems"}):
        return "stem"
    return "wood"


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


def is_hair_material_name(material_name):
    name = clean_token(material_name).lower()
    return name == "ht_default_material" or name.startswith("m_ht_")


def _hair_shader_node(mat):
    if not mat.use_nodes or not mat.node_tree:
        return None
    required = {
        "Base Color",
        "Root Color",
        "Root Color Mix Factor",
        "Tip Color",
        "Tip Color Mix Factor",
        "Factor [Map]",
    }
    for node in mat.node_tree.nodes:
        if node.bl_idname != "ShaderNodeGroup" or not node.node_tree:
            continue
        if required.issubset({socket.name for socket in node.inputs}):
            return node
    return None


def _hair_socket_value(node, name, fallback):
    socket = node.inputs.get(name) if node else None
    if socket is None:
        return fallback
    value = socket.default_value
    if hasattr(value, "__len__") and not isinstance(value, str):
        return [float(component) for component in value]
    return float(value)


def _hair_control_source_material(mat):
    """Resolve the editable Hair Tool source for generated M_HT_* card materials."""
    material_name = clean_token(mat.name).lower()
    if not material_name.startswith("m_ht_default_material"):
        return mat
    source = bpy.data.materials.get(HAIR_TOOL_CONTROL_SOURCE_MATERIAL)
    if source is None or _hair_shader_node(source) is None:
        return mat
    return source


def _hair_tool_json(mat):
    node = _hair_shader_node(mat)
    control_material = _hair_control_source_material(mat)
    control_node = _hair_shader_node(control_material)
    base_color = _hair_socket_value(node, "Base Color", [0.8, 0.8, 0.8, 1.0])
    root_color = _hair_socket_value(control_node, "Root Color", [0.0, 0.0, 0.0, 1.0])
    tip_color = _hair_socket_value(control_node, "Tip Color", [0.8, 0.8, 0.8, 1.0])
    return {
        "control_source_material": control_material.name,
        "vertex_color": {
            "name": "RFAOS",
            "R": "Random",
            "G": "Factor",
            "B": "Ambient AO",
            "A": "SystemColor Alpha Mask",
        },
        "vector_parameters": {
            "HT Base Color": base_color,
            "HT Root Color": root_color,
            "HT Tip Color": tip_color,
            "System Color 01": base_color,
            "System Color 02": tip_color,
        },
        "scalar_parameters": {
            "HT Root Mix": _hair_socket_value(control_node, "Root Color Mix Factor", 0.0),
            "HT Root Range": _hair_socket_value(control_node, "Root Color Range", 0.0),
            "HT Root Random Influence": _hair_socket_value(control_node, "Root Texture Overaly", 0.0),
            "HT Root Random Brightness": _hair_socket_value(control_node, "Root  Texture Brightness", 0.0),
            "HT Tip Mix": _hair_socket_value(control_node, "Tip Color Mix Factor", 0.0),
            "HT Tip Range": _hair_socket_value(control_node, "Tip Color Range", 0.0),
            "HT Tip Random Influence": _hair_socket_value(control_node, "Tip Texture Overlay", 0.0),
            "HT Tip Random Brightness": _hair_socket_value(control_node, "Tip  Texture Brightness", 0.0),
            "System Color Influence": 0.0,
            "System Mask Contrast": 1.0,
            "System Mask Bias": 0.0,
            "Roughness Multiplier": 1.0,
            "Roughness Minimum": _hair_socket_value(node, "SpecRoughness", 0.08),
        },
    }


def _hair_texture_json_entries(mat):
    texture_set = HAIR_TEXTURE_SET_BY_MATERIAL.get(clean_token(mat.name).lower())
    if not texture_set:
        return []
    entries = []
    for param, suffix in HAIR_TEXTURE_SUFFIXES.items():
        source_path = HAIR_TEXTURE_ROOT / f"{texture_set}_{suffix}.tga"
        entries.append({
            "param": param,
            "asset_name": source_path.stem,
            "file": source_path.as_posix(),
            "virtual_texture_streaming": param != "Opacity Map",
        })
    return entries


def _material_instance_base_name(material_name):
    contract_api = speedtree_handoff_contract()
    if contract_api is not None:
        return contract_api.material_instance_base_name(material_name)
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
    if key == "tree":
        mapping = _texture_param_map()
        mapping.pop("Transmission", None)
        mapping["Subsurface"] = "Subsurface"
        return mapping
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
    if master_preset == "tree":
        tree_part = tree_part_for_material(mat)
        tree_shading = tree_shading_for_material(mat, tree_part)
        parent = TREE_LAYER_PARENT_BY_PART.get(tree_part)
        if not parent:
            return None
        instance_base = _material_instance_base_name(mat.name)
        return {
            "assignment": "background",
            "tree_part": tree_part,
            "tree_shading": tree_shading,
            "parent": parent,
            "instance_path": f"{TREE_LAYER_INSTANCE_FOLDER}/MYI_{instance_base}",
            "texture_remap": _texture_param_map_for_material_layer_preset("tree"),
        }

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


def _texture_json_entry_from_path(param, source_path):
    source_path = Path(source_path)
    clean_name = clean_token(source_path.stem)
    asset_name = clean_name if clean_name.startswith(TEXTURE_PREFIX) else f"{TEXTURE_PREFIX}{clean_name}"
    return {
        "param": param,
        "asset_name": asset_name,
        "file": source_path.as_posix(),
    }


def texture_asset_name_for_image(image, source_path=None):
    source_path = source_path or bpy.path.abspath(image.filepath_raw or image.filepath)
    stem = Path(source_path).stem if source_path else image.name
    clean_name = clean_token(stem)
    if clean_name.startswith(TEXTURE_PREFIX):
        return clean_name
    return f"{TEXTURE_PREFIX}{clean_name}"


def _tree_texture_param_allowed(param, tree_shading):
    contract_api = speedtree_handoff_contract()
    if contract_api is not None:
        return contract_api.tree_texture_param_allowed(param, tree_shading)
    normalized = str(param or "").strip().casefold()
    if normalized in TREE_IGNORED_TEXTURE_PARAMS:
        return False
    return not (tree_shading == "wood" and normalized == "subsurface")


def _tree_texture_role_is_excluded(role, tree_shading):
    return not (
        _tree_texture_param_allowed(role, tree_shading)
        and _tree_texture_param_allowed(
            surface_layer_param_for_role(role),
            tree_shading,
        )
    )


def _subsurface_sibling_path(source_path):
    source_path = Path(source_path)
    stem_lower = source_path.stem.casefold()
    base_stem = None
    for suffix in TREE_BASE_COLOR_SUFFIXES:
        if stem_lower.endswith(suffix):
            base_stem = source_path.stem[:-len(suffix)]
            break
    if not base_stem:
        return None

    extensions = [source_path.suffix, ".tga", ".png", ".tif", ".tiff", ".exr"]
    seen_extensions = set()
    for source_suffix in TREE_SUBSURFACE_SOURCE_SUFFIXES:
        for extension in extensions:
            extension_key = extension.casefold()
            if not extension or extension_key in seen_extensions:
                continue
            seen_extensions.add(extension_key)
            candidate = source_path.with_name(f"{base_stem}{source_suffix}{extension}")
            if candidate.is_file():
                return candidate.resolve()
        seen_extensions.clear()
    return None


def _tree_subsurface_path(mat, texture_map, tree_shading):
    if tree_shading == "wood":
        return None

    material_textures = list(texture_map.get(mat, {}).items())
    for role, image in material_textures:
        if str(surface_layer_param_for_role(role)).casefold() != "subsurface":
            continue
        source_path = image_disk_path(image)
        if source_path and source_path.is_file():
            return source_path

    preferred = []
    fallback = []
    for role, image in material_textures:
        if str(surface_layer_param_for_role(role)).casefold() != "albedo":
            continue
        target = preferred if str(role).casefold() in {"basecolor", "albedo"} else fallback
        target.append(image)
    for image in preferred + fallback:
        source_path = image_disk_path(image)
        if not source_path:
            continue
        sibling_path = _subsurface_sibling_path(source_path)
        if sibling_path:
            return sibling_path
    return None


def _tree_texture_json_entries(mat, texture_map, tree_shading):
    textures = []
    seen_params = set()
    for role, image in texture_map.get(mat, {}).items():
        if _tree_texture_role_is_excluded(role, tree_shading):
            continue
        param = str(role)
        if param in seen_params:
            continue
        seen_params.add(param)
        textures.append(_texture_json_entry(role, image))

    subsurface_path = _tree_subsurface_path(mat, texture_map, tree_shading)
    if subsurface_path and "Subsurface" not in seen_params:
        textures.append(_texture_json_entry_from_path("Subsurface", subsurface_path))
    return textures


def _material_layer_json_entries(
    mat,
    texture_map,
    master_preset=None,
    tree_shading=None,
):
    textures = []
    seen_params = set()
    for role, image in texture_map.get(mat, {}).items():
        if master_preset == "tree" and _tree_texture_role_is_excluded(role, tree_shading):
            continue
        param = surface_layer_param_for_role(role)
        if param in seen_params:
            continue
        seen_params.add(param)
        textures.append(_texture_json_entry(role, image, param=param))
    if master_preset == "tree":
        subsurface_path = _tree_subsurface_path(mat, texture_map, tree_shading)
        if subsurface_path and "Subsurface" not in seen_params:
            textures.append(_texture_json_entry_from_path("Subsurface", subsurface_path))
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
    if tree_part_for_material(mat):
        return "tree"
    if is_hair_material_name(mat.name):
        return "hair"
    if is_layerblend_material_name(mat.name):
        return "layer"
    if "cloth" in name or "clothes" in name:
        return "cloth"
    return None


def _speedtree_material_intent(mat, instance_profile=None):
    contract_api = speedtree_handoff_contract()
    if contract_api is None:
        return None
    if instance_profile is None:
        instance_profile = _unreal_instance_profile(mat)
    return contract_api.build_material_intent(
        mat.name,
        explicit_tree_part=str(mat.get("unreal_tree_part") or ""),
        explicit_tree_shading=str(
            mat.get("unreal_tree_shading")
            or mat.get("unreal_tree_master_variant")
            or ""
        ),
        instance_profile=instance_profile,
    )


def _material_json_entry(mat, slot_index, texture_map):
    master_preset = master_preset_for_material(mat)
    is_hair = master_preset == "hair"
    tree_part = tree_part_for_material(mat) if master_preset == "tree" else None
    tree_shading = (
        tree_shading_for_material(mat, tree_part)
        if master_preset == "tree"
        else None
    )
    translucent = is_translucent_material(mat)
    instance_profile = ""
    speedtree_intent = None
    if master_preset == "tree":
        instance_profile = _unreal_instance_profile(mat)
        if instance_profile and translucent:
            raise ValueError(
                f"Material '{mat.name}' cannot combine a translucent handoff "
                "with unreal_instance_profile."
            )
        speedtree_intent = _speedtree_material_intent(
            mat,
            instance_profile=instance_profile,
        )
    if is_hair:
        textures = _hair_texture_json_entries(mat)
    elif master_preset == "tree":
        textures = _tree_texture_json_entries(mat, texture_map, tree_shading)
    else:
        textures = []
        for role, image in texture_map.get(mat, {}).items():
            textures.append(_texture_json_entry(role, image))
    entry = {
        "name": mat.name,
        "slot_name": mat.name,
        "slot_index": slot_index,
        "translucent": translucent,
        "textures": textures,
        "layers": [] if is_hair else _material_layer_json_entries(
            mat,
            texture_map,
            master_preset=master_preset,
            tree_shading=tree_shading,
        ),
    }
    if master_preset:
        entry["master_preset"] = master_preset
        if master_preset == "tree":
            entry["tree_part"] = tree_part
            entry["tree_shading"] = tree_shading
            if speedtree_intent is not None:
                entry["speedtree_intent"] = speedtree_intent
            if instance_profile:
                entry["instance_profile"] = instance_profile
                entry["material_instance_mode"] = (
                    speedtree_intent.get("material_instance_mode")
                    if speedtree_intent is not None
                    else "create_or_reuse"
                )
        if master_preset == "hair":
            entry["material_instance_name"] = f"MI_{_material_instance_base_name(mat.name)}"
            entry["create_if_missing"] = True
            entry["hair_tool"] = _hair_tool_json(mat)
        material_layer = material_layer_entry_for_material(mat, master_preset)
        if material_layer:
            entry["material_layer"] = material_layer
    return entry
