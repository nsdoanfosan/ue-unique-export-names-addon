import json
from pathlib import Path
import sys
import tempfile

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ue_unique_export_names_addon.pipeline_json import (
    _json_refresh_validation_errors,
    _write_pipeline_sidecar,
)
from ue_unique_export_names_addon.contract import speedtree_handoff_contract
from ue_unique_export_names_addon.naming import material_texture_map
from ue_unique_export_names_addon.unreal_material_json import (
    TREE_LAYER_PARENT_BY_PART,
    _material_instance_base_name,
    _material_json_entry,
    _tree_texture_role_is_excluded,
    _unreal_instance_profile,
    tree_part_for_material,
    tree_shading_for_material,
)


def _image(name, source_path):
    image = bpy.data.images.new(name, width=1, height=1)
    image.filepath = str(source_path)
    image.filepath_raw = str(source_path)
    return image


def _params(textures):
    return {texture["param"] for texture in textures}


with tempfile.TemporaryDirectory() as temp_dir_value:
    temp_dir = Path(temp_dir_value)
    contract_api = speedtree_handoff_contract()
    assert contract_api is not None
    assert contract_api.contract_version() >= 1
    golden_vectors = contract_api.golden_vectors()

    for vector in golden_vectors["tree_axes"]:
        vector_material = bpy.data.materials.new(vector["name"])
        part = tree_part_for_material(vector_material)
        assert part == vector["tree_part"]
        assert tree_shading_for_material(vector_material, part) == vector["tree_shading"]

    for vector_index, vector in enumerate(golden_vectors["profiles"]):
        profile_material = bpy.data.materials.new(
            f"M_stem_profile_vector_{vector_index:02d}"
        )
        profile_material["unreal_instance_profile"] = vector["value"]
        if vector.get("error"):
            try:
                _unreal_instance_profile(profile_material)
            except ValueError:
                pass
            else:
                raise AssertionError(f"profile vector must fail: {vector!r}")
        else:
            assert _unreal_instance_profile(profile_material) == vector["normalized"]

    for vector in golden_vectors["tree_texture_policy"]:
        assert (
            not _tree_texture_role_is_excluded(
                vector["param"],
                vector["tree_shading"],
            )
        ) == vector["allowed"]
    color_path = temp_dir / "T_contract_color.tga"
    missing_color_path = temp_dir / "T_missing_color.tga"
    opacity_path = temp_dir / "T_contract_opacity.tga"
    subsurface_path = temp_dir / "T_contract_subsurface.tga"
    translucency_color_path = temp_dir / "T_translucency_color.tga"
    translucency_path = temp_dir / "T_translucency_translucency.tga"
    for path in (
        color_path,
        missing_color_path,
        opacity_path,
        subsurface_path,
        translucency_color_path,
        translucency_path,
    ):
        path.touch()

    expected_parts = {
        "M_leaf_contract": "leaf",
        "M_leaves_contract": "leaf",
        "M_foliage_contract": "leaf",
        "M_cluster_contract": "leaf",
        "M_leaf_contract_stem": "leaf",
        "M_leaf_contract_twig": "leaf",
        "M_bark_contract_branch": "branch",
        "M_bark_deadbranch_02": "branch",
        "M_branch_contract": "branch",
        "M_stem_contract": "branch",
        "M_bark_contract": "bark",
        "M_trunk_contract": "bark",
        "M_stump_contract": "bark",
    }
    for material_name, expected_part in expected_parts.items():
        material = bpy.data.materials.new(material_name)
        assert tree_part_for_material(material) == expected_part

    leaf = bpy.data.materials.new("M_bark_explicit_leaf_contract")
    leaf["unreal_tree_part"] = "foliage"
    assert tree_part_for_material(leaf) == "leaf"

    color_image = _image("Contract Color", color_path)
    opacity_image = _image("Contract Opacity", opacity_path)
    subsurface_image = _image("Contract Subsurface", subsurface_path)
    leaf_entry = _material_json_entry(
        leaf,
        0,
        {
            leaf: {
                "BaseColor": color_image,
                "Alpha": opacity_image,
                "Opacity": opacity_image,
                "Opacity Map": opacity_image,
                "Transmission": opacity_image,
            }
        },
    )
    assert leaf_entry["master_preset"] == "tree"
    assert leaf_entry["tree_part"] == "leaf"
    assert leaf_entry["tree_shading"] == "foliage"
    assert leaf_entry["speedtree_intent"]["tree_part"] == "leaf"
    assert leaf_entry["speedtree_intent"]["tree_shading"] == "foliage"
    assert leaf_entry["speedtree_intent"]["instance_profile"] == ""
    contract_api.validate_material_intent(leaf_entry["speedtree_intent"])
    assert leaf_entry["material_layer"]["tree_part"] == "leaf"
    assert leaf_entry["material_layer"]["parent"] == TREE_LAYER_PARENT_BY_PART["leaf"]
    assert "Subsurface" in leaf_entry["material_layer"]["texture_remap"]
    assert "Transmission" not in leaf_entry["material_layer"]["texture_remap"]
    assert _params(leaf_entry["textures"]) == {"BaseColor", "Subsurface"}
    assert _params(leaf_entry["layers"][0]["textures"]) == {"Albedo", "Subsurface"}
    assert next(
        texture for texture in leaf_entry["textures"]
        if texture["param"] == "Subsurface"
    )["file"] == subsurface_path.as_posix()

    leaf_without_subsurface = bpy.data.materials.new("M_leaf_missing_subsurface_contract")
    missing_color_image = _image("Missing Subsurface Color", missing_color_path)
    leaf_without_subsurface_entry = _material_json_entry(
        leaf_without_subsurface,
        1,
        {
            leaf_without_subsurface: {
                "BaseColor": missing_color_image,
                "Alpha": opacity_image,
            }
        },
    )
    assert _params(leaf_without_subsurface_entry["textures"]) == {"BaseColor"}
    assert _params(leaf_without_subsurface_entry["layers"][0]["textures"]) == {"Albedo"}

    translucency_leaf = bpy.data.materials.new("M_leaf_translucency_contract")
    translucency_color_image = _image("Translucency Color", translucency_color_path)
    translucency_leaf_entry = _material_json_entry(
        translucency_leaf,
        2,
        {translucency_leaf: {"BaseColor": translucency_color_image}},
    )
    translucency_texture = next(
        texture for texture in translucency_leaf_entry["textures"]
        if texture["param"] == "Subsurface"
    )
    assert translucency_texture["file"] == translucency_path.as_posix()

    stem = bpy.data.materials.new("M_stem_subsurface_contract")
    stem["unreal_instance_profile"] = "Dead"
    stem_entry = _material_json_entry(
        stem,
        3,
        {
            stem: {
                "BaseColor": color_image,
                "Alpha": opacity_image,
                "Subsurface": subsurface_image,
            }
        },
    )
    assert stem_entry["master_preset"] == "tree"
    assert stem_entry["tree_part"] == "branch"
    assert stem_entry["tree_shading"] == "stem"
    assert stem_entry["instance_profile"] == "dead"
    assert stem_entry["material_instance_mode"] == "create_or_reuse"
    assert stem_entry["speedtree_intent"]["profile_target_name"] == (
        "MI_stem_subsurface_contract_dead"
    )
    contract_api.validate_material_intent(stem_entry["speedtree_intent"])
    assert stem_entry["name"] == "M_stem_subsurface_contract"
    assert stem_entry["material_layer"]["instance_path"].endswith(
        "/MYI_stem_subsurface_contract"
    )
    assert stem_entry["material_layer"]["parent"] == TREE_LAYER_PARENT_BY_PART["branch"]
    assert _params(stem_entry["textures"]) == {"BaseColor", "Subsurface"}
    assert _params(stem_entry["layers"][0]["textures"]) == {"Albedo", "Subsurface"}

    woody_branch = bpy.data.materials.new("M_branch_woody_contract")
    woody_branch_entry = _material_json_entry(
        woody_branch,
        4,
        {
            woody_branch: {
                "BaseColor": color_image,
                "Subsurface": subsurface_image,
            }
        },
    )
    assert woody_branch_entry["tree_part"] == "branch"
    assert woody_branch_entry["tree_shading"] == "wood"
    assert _params(woody_branch_entry["textures"]) == {"BaseColor"}

    explicit_wood_stem = bpy.data.materials.new("M_stem_explicit_wood_contract")
    explicit_wood_stem["unreal_tree_shading"] = "wood"
    explicit_wood_entry = _material_json_entry(
        explicit_wood_stem,
        5,
        {explicit_wood_stem: {"BaseColor": color_image, "Subsurface": subsurface_image}},
    )
    assert explicit_wood_entry["tree_shading"] == "wood"
    assert _params(explicit_wood_entry["textures"]) == {"BaseColor"}

    for excluded_param in ("Alpha", "Opacity", "Opacity Map", "Transmission"):
        assert _tree_texture_role_is_excluded(excluded_param, "foliage")
    assert _tree_texture_role_is_excluded("Subsurface", "wood")
    assert not _tree_texture_role_is_excluded("Subsurface", "foliage")
    assert _material_instance_base_name("M_stem_common_01_Mat.001") == (
        "stem_common_01"
    )

    translucent_profile = bpy.data.materials.new(
        "M_stem_translucent_profile_contract"
    )
    translucent_profile["unreal_instance_profile"] = "dead"
    translucent_profile.surface_render_method = "BLENDED"
    try:
        _material_json_entry(
            translucent_profile,
            6,
            {translucent_profile: {"BaseColor": color_image}},
        )
    except ValueError as exc:
        assert "cannot combine a translucent handoff" in str(exc)
    else:
        raise AssertionError("translucent SpeedTree profile must be blocked")

    validation_mesh = bpy.data.meshes.new("TranslucentProfileValidationMesh")
    validation_mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    validation_object = bpy.data.objects.new(
        "SM_Translucent_Profile_Contract",
        validation_mesh,
    )
    bpy.context.scene.collection.objects.link(validation_object)
    validation_mesh.materials.append(translucent_profile)
    validation_props = type("ValidationProps", (), {"scope": "SCENE"})()
    validation_errors = _json_refresh_validation_errors(
        bpy.context,
        validation_props,
        [validation_object],
        [translucent_profile],
        {translucent_profile: {"BaseColor": color_image}},
    )
    assert any(
        "cannot combine a translucent handoff" in error
        for error in validation_errors
    )

    invalid_profile = bpy.data.materials.new("M_stem_invalid_profile_contract")
    invalid_profile["unreal_instance_profile"] = "../dead"
    try:
        _material_json_entry(
            invalid_profile,
            7,
            {invalid_profile: {"BaseColor": color_image}},
        )
    except ValueError as exc:
        assert "invalid unreal_instance_profile" in str(exc)
    else:
        raise AssertionError("invalid SpeedTree profile must be blocked")

    wind_path = temp_dir / (
        "TreeOnly"
        + contract_api.dynamic_wind_rules()["filename_suffix"]
    )
    wind_path.write_text("{}", encoding="utf-8")
    tree_path = _write_pipeline_sidecar(
        temp_dir,
        "TreeOnly",
        "SM_TreeOnly",
        [leaf_entry, stem_entry, woody_branch_entry],
    )
    tree_payload = json.loads(tree_path.read_text(encoding="utf-8"))
    assert tree_payload["material_master"] == "tree"
    descriptor = tree_payload["speedtree_handoff_contract"]
    contract_api.validate_sidecar_descriptor(
        descriptor,
        expected_mesh_name="TreeOnly",
    )
    assert descriptor["mesh_name"] == "TreeOnly"
    assert tree_payload["dynamic_wind_json"] == wind_path.as_posix()

    mixed_path = _write_pipeline_sidecar(
        temp_dir,
        "Mixed",
        "SM_Mixed",
        [leaf_entry, {"name": "M_prop_contract"}],
    )
    mixed_payload = json.loads(mixed_path.read_text(encoding="utf-8"))
    assert mixed_payload["material_master"] == "prop"
    contract_api.validate_sidecar_descriptor(
        mixed_payload["speedtree_handoff_contract"],
        expected_mesh_name="Mixed",
    )

    prop_path = _write_pipeline_sidecar(
        temp_dir,
        "PropOnly",
        "SM_PropOnly",
        [{"name": "M_prop_contract", "master_preset": "prop"}],
    )
    prop_payload = json.loads(prop_path.read_text(encoding="utf-8"))
    assert "speedtree_handoff_contract" not in prop_payload
    assert "dynamic_wind_json" not in prop_payload

    actual_tree_materials = [
        material
        for material in bpy.data.materials
        if material and "blackgum" in material.name.casefold()
    ]
    if actual_tree_materials:
        actual_texture_map = material_texture_map(actual_tree_materials)
        actual_entries = [
            _material_json_entry(material, index, actual_texture_map)
            for index, material in enumerate(actual_tree_materials)
        ]
        for entry in actual_entries:
            top_params = _params(entry["textures"])
            layer_params = _params(entry["layers"][0]["textures"])
            assert entry["master_preset"] == "tree"
            assert not {"Alpha", "Transmission"}.intersection(top_params | layer_params)
            if entry["tree_shading"] != "wood":
                assert "Subsurface" in top_params
                assert "Subsurface" in layer_params
            else:
                assert "Subsurface" not in top_params
                assert "Subsurface" not in layer_params
        actual_path = _write_pipeline_sidecar(
            temp_dir,
            "ActualTreeOnly",
            "SK_ActualTreeOnly",
            actual_entries,
        )
        assert json.loads(actual_path.read_text(encoding="utf-8"))["material_master"] == "tree"
        print(
            "actual blackgum tree contracts: "
            + json.dumps(
                [
                    {
                        "name": entry["name"],
                        "tree_part": entry["tree_part"],
                        "parent": entry["material_layer"]["parent"],
                        "top_params": sorted(_params(entry["textures"])),
                        "layer_params": sorted(_params(entry["layers"][0]["textures"])),
                    }
                    for entry in actual_entries
                ],
                ensure_ascii=False,
            )
        )

print("tree material contract smoke: OK")
