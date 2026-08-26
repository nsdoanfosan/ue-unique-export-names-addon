"""Regression: the Unreal handoff gate must resolve linked-library textures.

A material linked from a shared material library stores its image paths as
``//``-relative values.  Those are relative to the *library* blend, not to the
asset blend that links them.  ``naming.image_disk_path`` is the resolver that
knows this; the pre-export validator used to resolve the path itself and
therefore reported existing textures as missing.
"""

import sys
import tempfile
from pathlib import Path

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ue_unique_export_names_addon.naming import image_disk_path, material_texture_map
from ue_unique_export_names_addon.pipeline_json import _json_refresh_validation_errors
from ue_unique_export_names_addon.validation import export_validation_rows


TEXTURE_NAME = "T_Wood_plank_03_color"
MATERIAL_NAME = "M_LayerBlend_Wood_plank_03"


def _authored_library(library_dir):
    """Write a material library whose image path is relative to the library."""
    source_texture = library_dir / f"{TEXTURE_NAME}.tga"
    source_texture.write_bytes(b"wood plank base color")

    image = bpy.data.images.new(TEXTURE_NAME, 1, 1)
    # The stored value is the library-relative form Blender writes for a
    # texture sitting beside its library blend.
    image.filepath = f"//{source_texture.name}"
    image.filepath_raw = f"//{source_texture.name}"
    material = bpy.data.materials.new(MATERIAL_NAME)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    texture_node = nodes.new("ShaderNodeTexImage")
    texture_node.image = image
    bsdf = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")
    material.node_tree.links.new(
        texture_node.outputs["Color"],
        bsdf.inputs["Base Color"],
    )

    library_path = library_dir / "wood_materials.blend"
    bpy.data.libraries.write(str(library_path), {image, material}, fake_user=True)
    bpy.data.materials.remove(material)
    bpy.data.images.remove(image)
    return library_path, source_texture


def _mesh_object(name, material):
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


with tempfile.TemporaryDirectory(prefix="ueun_linked_validation_") as temp_dir_value:
    temp_dir = Path(temp_dir_value)
    library_dir = temp_dir / "material_library"
    asset_dir = temp_dir / "asset"
    library_dir.mkdir()
    asset_dir.mkdir()

    library_path, source_texture = _authored_library(library_dir)

    # The asset blend lives in a *different* directory from the library, so a
    # library-relative path resolved against the asset blend cannot exist.
    bpy.ops.wm.save_as_mainfile(
        filepath=str(asset_dir / "asset.blend"),
        relative_remap=False,
    )
    naive_path = Path(bpy.path.abspath(f"//{source_texture.name}"))
    assert not naive_path.is_file(), naive_path

    with bpy.data.libraries.load(str(library_path), link=True) as (data_from, data_to):
        assert MATERIAL_NAME in data_from.materials
        data_to.materials = [MATERIAL_NAME]

    material = bpy.data.materials[MATERIAL_NAME]
    assert material.library is not None
    obj = _mesh_object("Roof_Center_ornament_06", material)

    texture_map = material_texture_map([material])
    image = texture_map[material]["BaseColor"]
    assert image.library is not None
    assert image_disk_path(image) == source_texture.resolve()

    props = type("ValidationProps", (), {"scope": "SCENE"})()
    errors = _json_refresh_validation_errors(
        bpy.context,
        props,
        [obj],
        [material],
        texture_map,
    )
    assert errors == [], errors

    rows = export_validation_rows(
        bpy.context,
        props,
        objects=[obj],
        materials=[material],
        texture_map=texture_map,
        hair_assets=[],
    )
    row = next(row for row in rows if row["object_name"] == obj.name)
    assert row["errors"] == [], row["errors"]
    assert row["status"] == "OK", row

    # A genuinely missing file must still block the handoff.
    source_texture.unlink()
    missing_errors = _json_refresh_validation_errors(
        bpy.context,
        props,
        [obj],
        [material],
        texture_map,
    )
    assert any("Missing texture file" in error for error in missing_errors), (
        missing_errors
    )
    assert any(
        source_texture.resolve().as_posix() in error.replace("\\", "/")
        for error in missing_errors
    ), missing_errors

    missing_rows = export_validation_rows(
        bpy.context,
        props,
        objects=[obj],
        materials=[material],
        texture_map=texture_map,
        hair_assets=[],
    )
    missing_row = next(
        row for row in missing_rows if row["object_name"] == obj.name
    )
    assert any("file missing" in error for error in missing_row["errors"]), (
        missing_row["errors"]
    )

print("linked texture validation smoke: OK")
