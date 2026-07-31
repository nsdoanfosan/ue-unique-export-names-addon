import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ue_unique_export_names_addon.constants import EXPORT_COLLECTION_NAME
from ue_unique_export_names_addon.naming import top_empty_parent
from ue_unique_export_names_addon import pipeline_json


for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
for collection in list(bpy.data.collections):
    bpy.data.collections.remove(collection)

export_collection = bpy.data.collections.new(EXPORT_COLLECTION_NAME)
bpy.context.scene.collection.children.link(export_collection)


def new_empty(name):
    obj = bpy.data.objects.new(name, None)
    export_collection.objects.link(obj)
    return obj


def new_armature(name):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    export_collection.objects.link(obj)
    return obj


def new_mesh(name):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, data)
    export_collection.objects.link(obj)
    return obj


root_nested = new_empty("SK_Branch_01")
armature = new_armature("SK_Branch_01_Armature")
mesh_nested = new_mesh("SK_Branch_01_Mesh")
armature.parent = root_nested
mesh_nested.parent = armature

root_direct = new_empty("SK_Branch_02")
mesh_direct = new_mesh("SK_Branch_02_Mesh")
mesh_direct.parent = root_direct

mesh_standalone = new_mesh("SK_Branch_03")
objects = [mesh_nested, mesh_direct, mesh_standalone]
scope_objects = set(export_collection.all_objects)

assert top_empty_parent(mesh_nested, scope_objects) is root_nested
assert top_empty_parent(mesh_direct, scope_objects) is root_direct
assert top_empty_parent(mesh_standalone, scope_objects) is None

context = SimpleNamespace(
    scene=SimpleNamespace(
        ue_unique_names=SimpleNamespace(last_pipeline_json_path="")
    )
)
material = bpy.data.materials.new("M_Branch")

# Keep the smoke focused on asset-unit naming and sidecar ownership.  The
# material/validation details have their own Blender smoke coverage.
pipeline_json.export_validation_rows = lambda *_args, **_kwargs: [
    {"object_name": obj.name, "asset_unit": root.name if root else obj.name}
    for obj, root in (
        (mesh_nested, root_nested),
        (mesh_direct, root_direct),
        (mesh_standalone, None),
    )
]
pipeline_json.unreal_handoff_material_slot_entries = lambda _obj: [
    (0, material, "OBJECT")
]
pipeline_json._material_json_entry = lambda mat, slot_index, _texture_map: {
    "name": mat.name,
    "slot_name": mat.name,
    "slot_index": slot_index,
    "master_preset": "prop",
    "textures": [],
    "layers": [],
}
pipeline_json.transfer_postprocess_entry = lambda obj: {
    "object_name": obj.name
}

expected_names = {"SK_Branch_01", "SK_Branch_02", "SK_Branch_03"}
assert set(pipeline_json._json_target_names(objects, context=context)) == expected_names

with tempfile.TemporaryDirectory(prefix="ueun_asset_unit_") as temp_dir:
    json_dir = Path(temp_dir)
    stale_child_sidecar = json_dir / "SK_Branch_01_Mesh.json"
    stale_child_sidecar.write_text("{}", encoding="utf-8")

    paths = pipeline_json.write_unreal_pipeline_json(
        context,
        "Branch",
        objects,
        [material],
        {},
        json_dir,
    )
    assert {path.stem for path in paths} == expected_names
    assert not stale_child_sidecar.exists()

    nested_data = json.loads(
        (json_dir / "SK_Branch_01.json").read_text(encoding="utf-8")
    )
    assert nested_data["mesh_name"] == "SK_Branch_01"
    assert nested_data["validation_children"][0]["object_name"] == mesh_nested.name
    assert nested_data["transfer_sources"][0]["object_name"] == mesh_nested.name
    assert not (json_dir / "SK_Branch_02_Mesh.json").exists()
    assert (json_dir / "SK_Branch_03.json").is_file()

print("asset unit sidecar smoke: OK")
