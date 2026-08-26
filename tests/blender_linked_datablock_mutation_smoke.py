"""Linked datablocks must never be renamed or written to.

The Unreal handoff reads paths from linked materials and images on purpose. The
naming workflows must not follow them into a rename or a file write: assigning
``name`` on a library datablock raises ``AttributeError``, while custom
properties and ``filepath`` assignments succeed silently and write into data the
library owns.
"""

import sys
import tempfile
from pathlib import Path

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ue_unique_export_names_addon.constants import (
    BACKUP_FILEPATH_PROP,
    BACKUP_FILEPATH_RAW_PROP,
    BACKUP_PROP,
)
from ue_unique_export_names_addon.materials import linked_images_from_materials
from ue_unique_export_names_addon.naming import (
    datablock_library_name,
    is_mutable_datablock,
    remember_image_path,
    remember_name,
    restore_image_path,
    restore_name,
)
from ue_unique_export_names_addon.pipeline_json import (
    _json_refresh_validation_errors,
)


LIBRARY_FILE = "wood_materials.blend"


def author_library(library_dir, material_name):
    """Write a library holding a material and its image, then unlink locals."""
    texture = library_dir / "wood_plank_color.tga"
    texture.write_bytes(b"wood plank base color")

    image = bpy.data.images.new("wood_plank_color", 1, 1)
    image.filepath = f"//{texture.name}"
    image.filepath_raw = f"//{texture.name}"
    material = bpy.data.materials.new(material_name)
    material.use_nodes = True
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    bsdf = next(
        candidate for candidate in material.node_tree.nodes
        if candidate.type == "BSDF_PRINCIPLED"
    )
    material.node_tree.links.new(
        node.outputs["Color"],
        bsdf.inputs["Base Color"],
    )

    library_path = library_dir / LIBRARY_FILE
    bpy.data.libraries.write(str(library_path), {image, material}, fake_user=True)
    bpy.data.materials.remove(material)
    bpy.data.images.remove(image)
    return library_path


def mesh_object(name, material):
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    mesh.materials.append(material)
    scene_object = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(scene_object)
    return scene_object


with tempfile.TemporaryDirectory(prefix="ueun_linked_mutation_") as temp_dir_value:
    temp_dir = Path(temp_dir_value)
    library_dir = temp_dir / "material_library"
    asset_dir = temp_dir / "asset"
    library_dir.mkdir()
    asset_dir.mkdir()

    # Deliberately missing the M_ prefix, so the naming rules demand a rename
    # that cannot happen in the asset blend.
    library_path = author_library(library_dir, "LayerBlend_Wood plank 03")
    bpy.ops.wm.save_as_mainfile(
        filepath=str(asset_dir / "asset.blend"),
        relative_remap=False,
    )
    with bpy.data.libraries.load(str(library_path), link=True) as (data_from, data_to):
        data_to.materials = ["LayerBlend_Wood plank 03"]

    material = bpy.data.materials["LayerBlend_Wood plank 03"]
    assert material.library is not None
    image = next(
        node.image
        for node in material.node_tree.nodes
        if node.type == "TEX_IMAGE" and node.image is not None
    )
    assert image.library is not None

    local_material = bpy.data.materials.new("M_Local_surface")
    local_material.use_nodes = True

    # 1) The mutability predicate and the library label.
    assert not is_mutable_datablock(material)
    assert not is_mutable_datablock(image)
    assert is_mutable_datablock(local_material)
    assert is_mutable_datablock(None) is False
    assert datablock_library_name(material) == LIBRARY_FILE
    assert datablock_library_name(image) == LIBRARY_FILE
    assert datablock_library_name(local_material) == ""

    # 2) Backup helpers refuse linked data instead of writing into the library.
    assert remember_name(material) is False
    assert remember_name(image) is False
    assert remember_image_path(image) is False
    assert BACKUP_PROP not in material
    assert BACKUP_PROP not in image
    assert BACKUP_FILEPATH_PROP not in image
    assert BACKUP_FILEPATH_RAW_PROP not in image
    assert remember_name(local_material) is True
    assert local_material[BACKUP_PROP] == "M_Local_surface"

    # 3) A stale backup property on library data must not abort a bulk restore.
    #    Custom properties are writable on linked IDs, so an older build could
    #    have left one behind.
    material["__stale"] = 1  # proves custom props really are writable here
    image[BACKUP_PROP] = "something_old"
    image[BACKUP_FILEPATH_RAW_PROP] = "//old.tga"
    assert restore_name(image, bpy.data.images) is False
    assert restore_image_path(image) is False
    assert image.name == "wood_plank_color"
    # The local one still restores normally.
    local_material.name = "M_Local_surface_renamed"
    assert restore_name(local_material, bpy.data.materials) is True
    assert local_material.name == "M_Local_surface"

    # 4) The External workflow sees the linked image and can refuse up front.
    #    A local material referencing a linked image is the real-world case.
    local_node = local_material.node_tree.nodes.new("ShaderNodeTexImage")
    local_node.image = image
    assert linked_images_from_materials([local_material]) == [image]
    assert linked_images_from_materials([]) == []

    local_only = bpy.data.materials.new("M_Local_only")
    local_only.use_nodes = True
    local_image = bpy.data.images.new("T_Local_only_color", 1, 1)
    local_only_node = local_only.node_tree.nodes.new("ShaderNodeTexImage")
    local_only_node.image = local_image
    assert linked_images_from_materials([local_only]) == []

    # 5) The validator explains where the impossible rename has to happen.
    scene_object = mesh_object("Roof_Center_ornament_06", material)
    props = type("ValidationProps", (), {"scope": "SCENE"})()
    errors = _json_refresh_validation_errors(
        bpy.context,
        props,
        [scene_object],
        [material],
        {},
    )
    clean_name_errors = [error for error in errors if "cannot be renamed here" in error]
    assert clean_name_errors, errors
    assert LIBRARY_FILE in clean_name_errors[0], clean_name_errors
    assert "LayerBlend_Wood_plank_03" in clean_name_errors[0], clean_name_errors

    prefix_errors = [error for error in errors if "must use the M_ prefix" in error]
    assert prefix_errors, errors
    assert LIBRARY_FILE in prefix_errors[0], prefix_errors
    assert "M_LayerBlend_Wood_plank_03" in prefix_errors[0], prefix_errors

    # A local material with the same problem keeps the original wording.
    bad_local = bpy.data.materials.new("LayerBlend local surface")
    local_object = mesh_object("Local_ornament", bad_local)
    local_errors = _json_refresh_validation_errors(
        bpy.context,
        props,
        [local_object],
        [bad_local],
        {},
    )
    assert any("Rename it explicitly first." in error for error in local_errors), (
        local_errors
    )
    assert not any("cannot be renamed here" in error for error in local_errors), (
        local_errors
    )

print("linked datablock mutation smoke: OK")
