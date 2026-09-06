"""Run in a disposable background Blender; never register or save preferences."""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from ue_unique_export_names_addon import api, pipeline_json, validation
from ue_unique_export_names_addon.constants import EXPORT_COLLECTION_NAME


for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
for collection in list(bpy.data.collections):
    bpy.data.collections.remove(collection)
export = bpy.data.collections.new(EXPORT_COLLECTION_NAME)
bpy.context.scene.collection.children.link(export)


def empty(name, parent=None, collection=export):
    obj = bpy.data.objects.new(name, None)
    collection.objects.link(obj)
    obj.parent = parent
    return obj


def mesh(name, parent, material, collection=export):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    data.materials.append(material)
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    obj.parent = parent
    return obj


wood_material = bpy.data.materials.new("M_Wood")
glass_material = bpy.data.materials.new("M_Glass")
root = empty("window_wood_single_02")
wood = mesh("frame_mesh", root, wood_material)
glass_pivot = empty("window_wood_single_02_glass", root)
glass = mesh("pane_mesh", glass_pivot, glass_material)

ordinary = empty("ordinary")
ordinary_mesh = mesh("ordinary_mesh", ordinary, wood_material)
subcollection = bpy.data.collections.new("ordinary_subcollection")
export.children.link(subcollection)
helper = empty("ordinary_group", ordinary, subcollection)
helper_mesh = mesh("ordinary_detail", helper, glass_material, subcollection)

rig_root = empty("SK_Root")
rig = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
export.objects.link(rig)
rig.parent = rig_root
rig_mesh = mesh("SK_Mesh", rig, wood_material)
standalone = mesh("standalone", None, wood_material)

instance_root = empty("house")
instance_mesh = mesh("house_mesh", instance_root, wood_material)
instance = empty("house_collection_instance", instance_root)
instance.instance_type = "COLLECTION"
instance.instance_collection = bpy.data.collections.new("source_collection")
instance_child = mesh("house_detail", instance, glass_material)

objects = [wood, glass, ordinary_mesh, helper_mesh, rig_mesh, standalone, instance_mesh, instance_child]
context = SimpleNamespace(scene=SimpleNamespace(ue_unique_names=SimpleNamespace(last_pipeline_json_path="")))
expected = {
    wood: root,
    glass: glass_pivot,
    ordinary_mesh: ordinary,
    helper_mesh: ordinary,
    rig_mesh: rig_root,
    standalone: standalone,
    instance_mesh: instance_root,
    instance_child: instance_root,
}
for obj, owner in expected.items():
    assert api.resolve_asset_unit_name(obj, context) == owner.name, obj.name

validation_rows = validation.export_validation_rows(
    bpy.context,
    props=SimpleNamespace(scope="EXPORT_COLLECTION"),
    objects=objects,
    materials=[wood_material, glass_material],
    texture_map={},
    hair_assets=[],
)
for row in validation_rows:
    owner = expected[bpy.data.objects[row["object_name"]]]
    assert row["asset_unit"] == owner.name, row
    assert row["json_name"] == owner.name, row
pipeline_json.export_validation_rows = lambda *_args, **_kwargs: validation_rows
pipeline_json._material_json_entry = lambda mat, slot_index, _texture_map: {
    "name": mat.name,
    "slot_name": mat.name,
    "slot_index": slot_index,
    "master_preset": "prop",
    "textures": [],
    "layers": [],
}
pipeline_json.transfer_postprocess_entry = lambda obj: {"object_name": obj.name}

with tempfile.TemporaryDirectory(prefix="ueun_nested_units_") as temporary:
    output = Path(temporary)
    paths = pipeline_json.write_unreal_pipeline_json(
        context, "Test", objects, [wood_material, glass_material], {}, output
    )
    assert {path.stem for path in paths} == {owner.name for owner in expected.values()}
    payloads = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in paths}
    assert [entry["name"] for entry in payloads[root.name]["materials"]] == ["M_Wood"]
    assert [entry["name"] for entry in payloads[glass_pivot.name]["materials"]] == ["M_Glass"]
    assert payloads[root.name]["transfer_sources"] == [{"object_name": wood.name}]
    assert payloads[glass_pivot.name]["transfer_sources"] == [{"object_name": glass.name}]
    assert payloads[glass_pivot.name]["validation_children"][0]["asset_unit"] == glass_pivot.name
    assert payloads[glass_pivot.name]["validation_children"][0]["json_name"] == glass_pivot.name

    # Legacy sidecars must remain byte-identical with the new gate disabled.
    legacy_objects = [obj for obj in objects if obj not in {wood, glass}]
    baseline = {name: (output / f"{name}.json").read_bytes() for name in payloads if name not in {root.name, glass_pivot.name}}
    saved_helper = pipeline_json.get_asset_pivot
    pipeline_json.get_asset_pivot = lambda *_args: None
    try:
        legacy_paths = pipeline_json.write_unreal_pipeline_json(
            context, "Test", legacy_objects, [wood_material, glass_material], {}, output
        )
    finally:
        pipeline_json.get_asset_pivot = saved_helper
    assert all(path.read_bytes() == baseline[path.stem] for path in legacy_paths)
    saved_validation_helper = validation.get_asset_pivot
    validation.get_asset_pivot = lambda *_args: None
    try:
        legacy_rows = validation.export_validation_rows(
            bpy.context,
            props=SimpleNamespace(scope="EXPORT_COLLECTION"),
            objects=legacy_objects,
            materials=[wood_material, glass_material],
            texture_map={},
            hair_assets=[],
        )
    finally:
        validation.get_asset_pivot = saved_validation_helper
    expected_legacy_rows = [row for row in validation_rows if row["object_name"] not in {wood.name, glass.name}]
    assert legacy_rows == expected_legacy_rows

print("nested pivot sidecar smoke: OK (separate materials, legacy bytes unchanged)")
