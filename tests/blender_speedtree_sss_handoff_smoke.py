"""Regress SpeedTree's suffixless Color -> SubsurfaceColor handoff.

Run in background Blender. Does not register add-ons, save preferences, or
change any source blend/texture files.
"""
from pathlib import Path
import sys
import tempfile
import xml.etree.ElementTree as ET

import bpy

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from ue_unique_export_names_addon.unreal_material_json import _material_json_entry


def image(path):
    result = bpy.data.images.new(path.stem, width=1, height=1)
    result.filepath = str(path)
    result.filepath_raw = str(path)
    return result


def parameters(entry, layered):
    textures = entry["layers"][0]["textures"] if layered else entry["textures"]
    return {t["param"]: t for t in textures}


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    color = root / "M_leaf_elm_01.png"
    sss = root / "M_leaf_elm_01_SubsurfaceColor.png"
    opacity = root / "M_leaf_elm_01_Opacity.png"
    unrelated = root / "M_leaf_elm_02_SubsurfaceColor.png"
    for path in (color, sss, opacity, unrelated):
        path.touch()
    material = bpy.data.materials.new("M_leaf_elm_01_Mat")
    textures = {material: {"BaseColor": image(color)}}
    entry = _material_json_entry(material, 0, textures)
    for layered in (False, True):
        params = parameters(entry, layered)
        assert "Subsurface" in params, (layered, params)
        assert params["Subsurface"]["file"] == sss.as_posix()
        assert params["Subsurface"]["asset_name"] == "T_M_leaf_elm_01_SubsurfaceColor"
        # SSS discovery must not reconnect a deliberately unbound Opacity map.
        assert "Opacity Map" not in params, params

    # Missing exact sibling must not select another material's SSS.
    sss.unlink()
    entry = _material_json_entry(material, 0, textures)
    assert "Subsurface" not in parameters(entry, True)

    # Explicit authored SSS remains authoritative.
    textures[material]["Subsurface"] = image(unrelated)
    entry = _material_json_entry(material, 0, textures)
    assert parameters(entry, True)["Subsurface"]["file"] == unrelated.as_posix()

    # Original STMAT is authoritative even with arbitrary filenames, and must
    # use the same File/Source domain as the actual Albedo (UV layout).
    material["codex_source_fbx"] = str(root / "SK_tree_elm_01.fbx")
    document = ET.Element("Materials")
    authored = ET.SubElement(document, "Material", Name=material.name)
    original_color = root / "original_color.tga"
    original_sss = root / "original_sss.tga"
    original_color.touch()
    original_sss.touch()
    ET.SubElement(authored, "Map", Name="Color", File=color.name, Source=str(original_color))
    sss_map = ET.SubElement(authored, "Map", Name="SubsurfaceColor", File=unrelated.name, Source=str(original_sss))
    stmat = root / "SK_tree_elm_01.stmat"
    ET.ElementTree(document).write(stmat)
    textures = {material: {"BaseColor": image(color)}}
    entry = _material_json_entry(material, 0, textures)
    assert parameters(entry, True)["Subsurface"]["file"] == unrelated.as_posix()
    textures = {material: {"BaseColor": image(original_color)}}
    entry = _material_json_entry(material, 0, textures)
    assert parameters(entry, True)["Subsurface"]["file"] == original_sss.as_posix()
    sss_map.set("Source", str(original_color))
    ET.ElementTree(document).write(stmat)
    entry = _material_json_entry(material, 0, textures)
    assert parameters(entry, True)["Subsurface"]["file"] == original_color.as_posix()
    del material["codex_source_fbx"]

    # Existing suffixed inputs and wood exclusion keep their behavior.
    suffixed = root / "T_cluster_densiflora_01_color.tga"
    original_sss = root / "T_cluster_densiflora_01_subsurface.tga"
    suffixed.touch()
    original_sss.touch()
    textures = {material: {"BaseColor": image(suffixed)}}
    entry = _material_json_entry(material, 0, textures)
    assert parameters(entry, True)["Subsurface"]["file"] == original_sss.as_posix()
    material["unreal_tree_shading"] = "wood"
    entry = _material_json_entry(material, 0, textures)
    assert "Subsurface" not in parameters(entry, True)

print("SPEEDTREE_SSS_HANDOFF_OK")
