import sys
import tempfile
from pathlib import Path

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ue_unique_export_names_addon.naming import image_disk_path, material_texture_map
from ue_unique_export_names_addon.unreal_material_json import _material_json_entry


with tempfile.TemporaryDirectory(prefix="ueun_linked_material_") as temp_dir_value:
    temp_dir = Path(temp_dir_value)
    source_texture = temp_dir / "T_Linked_surface_color.tga"
    source_texture.write_bytes(b"linked texture source")
    library_path = temp_dir / "linked_materials.blend"

    image = bpy.data.images.new("T_Linked_surface_color", 1, 1)
    image.filepath = str(source_texture)
    image.filepath_raw = str(source_texture)
    material = bpy.data.materials.new("M_LayerBlend_Linked_surface")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    texture_node = nodes.new("ShaderNodeTexImage")
    texture_node.image = image
    bsdf = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")
    material.node_tree.links.new(texture_node.outputs["Color"], bsdf.inputs["Base Color"])
    bpy.data.libraries.write(str(library_path), {image, material}, fake_user=True)

    bpy.data.materials.remove(material)
    bpy.data.images.remove(image)
    with bpy.data.libraries.load(str(library_path), link=True) as (data_from, data_to):
        assert "M_LayerBlend_Linked_surface" in data_from.materials
        data_to.materials = ["M_LayerBlend_Linked_surface"]

    linked_material = bpy.data.materials["M_LayerBlend_Linked_surface"]
    texture_map = material_texture_map([linked_material])
    linked_image = texture_map[linked_material]["BaseColor"]
    assert linked_image.library is not None
    assert image_disk_path(linked_image) == source_texture.resolve()

    entry = _material_json_entry(linked_material, 0, texture_map)
    layer_textures = entry["layers"][0]["textures"]
    assert layer_textures == [
        {
            "param": "Albedo",
            "asset_name": "T_Linked_surface_color",
            "file": source_texture.as_posix(),
            "source_param": "BaseColor",
        }
    ]

print("linked material texture smoke: OK")
