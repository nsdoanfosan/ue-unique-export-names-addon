import os
from pathlib import Path
import sys

import addon_utils
import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _new_empty(name, collection):
    obj = bpy.data.objects.new(name, None)
    collection.objects.link(obj)
    return obj


def _new_mesh(name, collection):
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.0, 0.5, 0.0)],
        [],
        [(0, 1, 2)],
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def _new_armature(name, collection):
    armature = bpy.data.armatures.new(f"{name}_Armature")
    obj = bpy.data.objects.new(name, armature)
    collection.objects.link(obj)
    return obj


def _matrix_signature(matrix):
    return tuple(
        round(float(matrix[row][column]), 10)
        for row in range(4)
        for column in range(4)
    )


def _parent_preserving_world(child, parent):
    world = child.matrix_world.copy()
    child.parent = parent
    child.matrix_world = world
    bpy.context.view_layer.update()
    return world


def _expect_value_error(action, expected_text):
    try:
        action()
    except ValueError as error:
        assert expected_text.casefold() in str(error).casefold(), str(error)
        return str(error)
    raise AssertionError(f"Expected ValueError containing {expected_text!r}")


def _mutation_snapshot(low_object, export_collection):
    return {
        "objects": tuple(
            sorted((obj.name_full, obj.as_pointer()) for obj in bpy.data.objects)
        ),
        "name": low_object.name_full,
        "parent": (
            low_object.parent.as_pointer() if low_object.parent is not None else 0
        ),
        "world": _matrix_signature(low_object.matrix_world),
        "collections": tuple(
            sorted(
                (collection.name_full, collection.as_pointer())
                for collection in low_object.users_collection
            )
        ),
        "export": tuple(
            sorted(obj.as_pointer() for obj in export_collection.objects)
        ),
        "auto_export_marker": (
            AUTO_PAINTER_EXPORT_LINK_PROP in low_object,
            low_object.get(AUTO_PAINTER_EXPORT_LINK_PROP),
        ),
    }


bpy.ops.wm.read_factory_settings(use_empty=True)

# Registration QA is deliberately non-persistent.  This script never calls
# save_userpref(), and it is run under --factory-startup.
send2ue_module = addon_utils.enable(
    "send2ue",
    default_set=False,
    persistent=False,
)
assert send2ue_module is not None, "Send2UE could not be enabled"

from send2ue.resources.extensions.combine_assets import CombineAssetsExtension
from ue_unique_export_names_addon import api, painter_sync
from ue_unique_export_names_addon.constants import (
    AUTO_PAINTER_EXPORT_LINK_PROP,
    CREATED_EMPTY_PROP,
    EXPORT_COLLECTION_NAME,
    PAINTER_EXPORT_ASSET_PROP,
)


try:
    scene = bpy.context.scene
    baking = bpy.data.collections.new("Baking")
    scene.collection.children.link(baking)
    low = bpy.data.collections.new("low")
    baking.children.link(low)
    outside = bpy.data.collections.new("PainterUnit_Outside")
    scene.collection.children.link(outside)

    combine_property = scene.send2ue.extensions.combine_assets
    combine_property.combine = "off"
    immediate_parent_property = (
        scene.send2ue.extensions.use_immediate_parent_name
    )
    immediate_parent_property.use_immediate_parent_name = True

    # A standalone static Low keeps its Painter suffix and world transform,
    # while an exact canonical Empty becomes the Send2UE asset unit root.
    static_low = _new_mesh("ContractAsset_low", low)
    static_low.location = (3.25, -2.5, 1.75)
    static_low.rotation_euler = (0.31, -0.42, 1.17)
    static_low.scale = (1.5, 0.75, 2.25)
    bpy.context.view_layer.update()
    static_world = static_low.matrix_world.copy()

    # Public calls may not use a global collection name to mutate another
    # scene's Painter classification.
    other_scene = bpy.data.scenes.new("PainterUnit_OtherScene")
    _expect_value_error(
        lambda: api.ensure_painter_low_export_unit(
            static_low,
            "ContractAsset",
            scene=other_scene,
        ),
        "inside Baking/low",
    )
    assert static_low.parent is None
    assert bpy.data.objects.get("ContractAsset") is None

    created = api.ensure_painter_low_export_unit(
        static_low,
        "ContractAsset",
        scene=scene,
    )
    export = bpy.data.collections.get(EXPORT_COLLECTION_NAME)
    export_root = bpy.data.objects.get("ContractAsset")
    assert export is not None
    assert export_root is not None and export_root.type == "EMPTY"
    assert static_low.name == "ContractAsset_low"
    assert static_low.parent is export_root
    assert export_root.parent is None
    assert export.objects.get(export_root.name) is export_root
    assert export.objects.get(static_low.name) is static_low
    assert low.objects.get(export_root.name) is None
    assert scene.collection.objects.get(export_root.name) is export_root
    assert _matrix_signature(static_low.matrix_world) == _matrix_signature(static_world)
    assert export_root.get(CREATED_EMPTY_PROP) is True
    assert export_root.get(PAINTER_EXPORT_ASSET_PROP) == "ContractAsset"
    assert combine_property.combine == "child_meshes"
    assert immediate_parent_property.use_immediate_parent_name is False
    assert created["api_version"] == 2
    assert created["operation"] == "ensure_painter_low_export_unit"
    assert created["status"] == "SUCCESS"
    assert created["unit_status"] == "STATIC_EMPTY_READY"
    assert created["asset_base"] == "ContractAsset"
    assert created["low_object"] == "ContractAsset_low"
    assert created["export_root"] == "ContractAsset"
    assert created["root_name_matches"] is True
    assert created["created_empty"] is True
    assert created["parented"] is True
    assert created["preserved_world_transform"] is True
    assert created["combine_assets"] == {
        "available": True,
        "required": "child_meshes",
        "previous": "off",
        "value": "child_meshes",
        "changed": True,
        "use_immediate_parent_name": {
            "available": True,
            "required": False,
            "previous": True,
            "value": False,
            "changed": True,
        },
    }

    # A callback for an unrelated scene must not clear the main scene's
    # automatically linked Painter export unit.
    export_members_before_other_scene_sync = {
        obj.as_pointer() for obj in export.objects
    }
    assert painter_sync.sync_painter_export(other_scene) == {
        "linked": 0,
        "unlinked": 0,
        "desired": 0,
    }
    assert {obj.as_pointer() for obj in export.objects} == (
        export_members_before_other_scene_sync
    )
    painter_sync.sync_painter_export_on_depsgraph(other_scene, None)
    assert {obj.as_pointer() for obj in export.objects} == (
        export_members_before_other_scene_sync
    )

    # The second call must reuse the same hierarchy without .001 creation or
    # transform drift.
    object_pointers_before = {obj.as_pointer() for obj in bpy.data.objects}
    repeated = api.ensure_painter_low_export_unit(
        static_low,
        "ContractAsset",
        scene=scene,
    )
    assert {obj.as_pointer() for obj in bpy.data.objects} == object_pointers_before
    assert bpy.data.objects.get("ContractAsset") is export_root
    assert bpy.data.objects.get("ContractAsset.001") is None
    assert static_low.parent is export_root
    assert _matrix_signature(static_low.matrix_world) == _matrix_signature(static_world)
    assert repeated["unit_status"] == "EXISTING_EMPTY_READY"
    assert repeated["created_empty"] is False
    assert repeated["parented"] is False
    assert repeated["combine_assets"]["value"] == "child_meshes"
    assert repeated["combine_assets"]["changed"] is False
    assert repeated["combine_assets"]["use_immediate_parent_name"] == {
        "available": True,
        "required": False,
        "previous": False,
        "value": False,
        "changed": False,
    }

    # A user-owned canonical top-level Empty that already directly parents the
    # only Low mesh is a valid unit.  Reuse it without manufacturing a managed
    # replacement or adding a .001 suffix.
    existing_root = _new_empty("ExistingCanonicalAsset", outside)
    existing_low = _new_mesh("ExistingCanonicalAsset_low", low)
    existing_low.location = (-1.75, 4.25, 0.5)
    existing_low.rotation_euler = (0.2, 0.4, -0.6)
    bpy.context.view_layer.update()
    _parent_preserving_world(existing_low, existing_root)
    existing_world = existing_low.matrix_world.copy()
    existing_root_pointer = existing_root.as_pointer()
    existing_receipt = api.ensure_painter_low_export_unit(
        existing_low,
        "ExistingCanonicalAsset",
        scene=scene,
    )
    assert bpy.data.objects.get("ExistingCanonicalAsset") is existing_root
    assert existing_root.as_pointer() == existing_root_pointer
    assert bpy.data.objects.get("ExistingCanonicalAsset.001") is None
    assert existing_low.parent is existing_root
    assert existing_root.parent is None
    assert _matrix_signature(existing_low.matrix_world) == _matrix_signature(
        existing_world
    )
    assert export.objects.get(existing_root.name) is existing_root
    assert export.objects.get(existing_low.name) is existing_low
    assert existing_receipt["unit_status"] == "EXISTING_EMPTY_READY"
    assert existing_receipt["export_root"] == "ExistingCanonicalAsset"
    assert existing_receipt["created_empty"] is False
    assert existing_receipt["parented"] is False
    assert existing_receipt["handoff_ready"] is True
    assert not existing_root.get(CREATED_EMPTY_PROP)
    assert existing_root.get(PAINTER_EXPORT_ASSET_PROP) is None

    # A previously managed Empty may survive as an orphan after an interrupted
    # workflow.  Its ownership markers make it safe to reclaim for the same
    # asset, while the Low mesh's world transform must remain unchanged.
    orphan_root = _new_empty("OrphanManagedAsset", scene.collection)
    orphan_root[CREATED_EMPTY_PROP] = True
    orphan_root[PAINTER_EXPORT_ASSET_PROP] = "OrphanManagedAsset"
    orphan_low = _new_mesh("OrphanManagedAsset_low", low)
    orphan_low.location = (6.0, 1.25, -2.0)
    orphan_low.rotation_euler = (-0.3, 0.15, 0.75)
    bpy.context.view_layer.update()
    orphan_world = orphan_low.matrix_world.copy()
    orphan_root_pointer = orphan_root.as_pointer()
    orphan_receipt = api.ensure_painter_low_export_unit(
        orphan_low,
        "OrphanManagedAsset",
        scene=scene,
    )
    assert bpy.data.objects.get("OrphanManagedAsset") is orphan_root
    assert orphan_root.as_pointer() == orphan_root_pointer
    assert bpy.data.objects.get("OrphanManagedAsset.001") is None
    assert orphan_low.parent is orphan_root
    assert orphan_root.parent is None
    assert _matrix_signature(orphan_low.matrix_world) == _matrix_signature(
        orphan_world
    )
    assert export.objects.get(orphan_root.name) is orphan_root
    assert export.objects.get(orphan_low.name) is orphan_low
    assert orphan_receipt["unit_status"] == "STATIC_EMPTY_READY"
    assert orphan_receipt["export_root"] == "OrphanManagedAsset"
    assert orphan_receipt["created_empty"] is False
    assert orphan_receipt["parented"] is True
    assert orphan_receipt["handoff_ready"] is True

    # Exercise Send2UE's real built-in extension method.  The mesh retains
    # _low in Blender/Painter, while the generated FBX and Unreal asset path
    # use the direct Empty parent name.
    asset_id = "contract-asset"
    asset_data = {
        "_mesh_object_name": static_low.name,
        "file_path": os.path.join("C:\\Temp", f"{static_low.name}.fbx"),
        "asset_folder": "/Game/Contract/",
        "asset_path": f"/Game/Contract/{static_low.name}",
    }
    bpy.context.window_manager.send2ue.asset_id = asset_id
    bpy.context.window_manager.send2ue.asset_data[asset_id] = asset_data
    combine_extension = CombineAssetsExtension()
    combine_extension.combine = "child_meshes"
    combine_extension.pre_mesh_export(asset_data, scene.send2ue)
    combined_asset_data = bpy.context.window_manager.send2ue.asset_data[asset_id]
    assert os.path.basename(combined_asset_data["file_path"]) == "ContractAsset.fbx"
    assert combined_asset_data["asset_path"] == "/Game/Contract/ContractAsset"
    assert combined_asset_data["empty_object_name"] == "ContractAsset"
    assert static_low.name == "ContractAsset_low"

    # An exact user-owned name collision must fail before any object,
    # collection membership, parent, transform, or Send2UE setting changes.
    collision = _new_empty("CollisionAsset", outside)
    collision_low = _new_mesh("CollisionAsset_low", low)
    collision_low.location = (-4.0, 2.0, 0.25)
    bpy.context.view_layer.update()
    collision_before = _mutation_snapshot(collision_low, export)
    _expect_value_error(
        lambda: api.ensure_painter_low_export_unit(
            collision_low,
            "CollisionAsset",
            scene=scene,
        ),
        "exact Painter export Empty name is already in use",
    )
    assert _mutation_snapshot(collision_low, export) == collision_before
    assert bpy.data.objects.get("CollisionAsset") is collision
    assert bpy.data.objects.get("CollisionAsset.001") is None
    assert not collision.get(CREATED_EMPTY_PROP)
    assert collision.get(PAINTER_EXPORT_ASSET_PROP) is None
    assert combine_property.combine == "child_meshes"
    assert immediate_parent_property.use_immediate_parent_name is False

    # A non-skeletal mesh with an existing non-Empty parent is rejected, not
    # silently detached and grouped under a replacement Empty.
    static_parent = _new_mesh("ExistingMeshParent", outside)
    parented_low = _new_mesh("ParentedAsset_low", low)
    parented_world = _parent_preserving_world(parented_low, static_parent)
    parented_before = _mutation_snapshot(parented_low, export)
    _expect_value_error(
        lambda: api.ensure_painter_low_export_unit(
            parented_low,
            "ParentedAsset",
            scene=scene,
        ),
        "existing non-Empty parent",
    )
    assert _mutation_snapshot(parented_low, export) == parented_before
    assert parented_low.parent is static_parent
    assert _matrix_signature(parented_low.matrix_world) == _matrix_signature(
        parented_world
    )
    assert bpy.data.objects.get("ParentedAsset") is None

    # Shape keys are treated as skeletal semantics even without an armature.
    # They are synchronized, but never auto-parented.
    shape_low = _new_mesh("ShapeAsset_low", low)
    shape_low.shape_key_add(name="Basis")
    shape_low.shape_key_add(name="ContractShape")
    shape_world = shape_low.matrix_world.copy()
    shape_keys = shape_low.data.shape_keys
    shape_receipt = api.ensure_painter_low_export_unit(
        shape_low,
        "ShapeAsset",
        scene=scene,
    )
    assert shape_receipt["unit_status"] == "PRESERVED_SKELETAL_HIERARCHY"
    assert shape_receipt["created_empty"] is False
    assert shape_receipt["parented"] is False
    assert shape_receipt["export_root"] is None
    assert shape_receipt["combine_assets"]["reason"] == (
        "skeletal_hierarchy_preserved"
    )
    assert shape_low.parent is None
    assert shape_low.data.shape_keys is shape_keys
    assert len(shape_low.data.shape_keys.key_blocks) == 2
    assert _matrix_signature(shape_low.matrix_world) == _matrix_signature(shape_world)
    assert bpy.data.objects.get("ShapeAsset") is None
    assert export.objects.get(shape_low.name) is shape_low

    # An Armature modifier and its existing parent chain are preserved.  The
    # generic Export sync may link them, but the ensure API must not fabricate
    # or substitute an Empty root.
    rig_root = _new_empty("ArmatureContractRoot", outside)
    rig = _new_armature("ArmatureContractRig", outside)
    rig.parent = rig_root
    armature_low = _new_mesh("ArmatureAsset_low", low)
    armature_world = _parent_preserving_world(armature_low, rig)
    armature_modifier = armature_low.modifiers.new(
        "ArmatureContractModifier",
        "ARMATURE",
    )
    armature_modifier.object = rig
    armature_receipt = api.ensure_painter_low_export_unit(
        armature_low,
        "ArmatureAsset",
        scene=scene,
    )
    assert armature_receipt["unit_status"] == "PRESERVED_SKELETAL_HIERARCHY"
    assert armature_receipt["created_empty"] is False
    assert armature_receipt["parented"] is False
    assert armature_receipt["export_root"] == rig.name
    assert armature_receipt["root_name_matches"] is False
    assert armature_low.parent is rig
    assert armature_modifier.object is rig
    assert rig.parent is rig_root
    assert _matrix_signature(armature_low.matrix_world) == _matrix_signature(
        armature_world
    )
    assert bpy.data.objects.get("ArmatureAsset") is None
    for hierarchy_object in (armature_low, rig, rig_root):
        assert export.objects.get(hierarchy_object.name) is hierarchy_object

    # Even an unbound Armature modifier carries skeletal intent.  It remains
    # untouched so a later rig assignment cannot find that the mesh was
    # silently reparented into a static Empty hierarchy.
    targetless_armature_low = _new_mesh("TargetlessArmatureAsset_low", low)
    targetless_modifier = targetless_armature_low.modifiers.new(
        "TargetlessArmatureContractModifier",
        "ARMATURE",
    )
    assert targetless_modifier.object is None
    targetless_receipt = api.ensure_painter_low_export_unit(
        targetless_armature_low,
        "TargetlessArmatureAsset",
        scene=scene,
    )
    assert targetless_receipt["unit_status"] == (
        "PRESERVED_SKELETAL_HIERARCHY"
    )
    assert targetless_receipt["created_empty"] is False
    assert targetless_receipt["parented"] is False
    assert targetless_armature_low.parent is None
    assert targetless_modifier.object is None
    assert bpy.data.objects.get("TargetlessArmatureAsset") is None

    # An existing Empty must be the exact immediate, top-level asset root.
    # Wrong-name and nested roots are both rejected without reparenting.
    wrong_empty = _new_empty("WrongExistingRoot", outside)
    wrong_low = _new_mesh("WrongEmptyAsset_low", low)
    wrong_world = _parent_preserving_world(wrong_low, wrong_empty)
    wrong_before = _mutation_snapshot(wrong_low, export)
    _expect_value_error(
        lambda: api.ensure_painter_low_export_unit(
            wrong_low,
            "WrongEmptyAsset",
            scene=scene,
        ),
        "Existing Painter Low Empty must be named",
    )
    assert _mutation_snapshot(wrong_low, export) == wrong_before
    assert wrong_low.parent is wrong_empty
    assert _matrix_signature(wrong_low.matrix_world) == _matrix_signature(wrong_world)
    assert bpy.data.objects.get("WrongEmptyAsset") is None

    nested_top = _new_empty("NestedContractTop", outside)
    nested_root = _new_empty("NestedAsset", outside)
    nested_root.parent = nested_top
    nested_low = _new_mesh("NestedAsset_low", low)
    nested_world = _parent_preserving_world(nested_low, nested_root)
    nested_before = _mutation_snapshot(nested_low, export)
    _expect_value_error(
        lambda: api.ensure_painter_low_export_unit(
            nested_low,
            "NestedAsset",
            scene=scene,
        ),
        "must be the top-level object parent",
    )
    assert _mutation_snapshot(nested_low, export) == nested_before
    assert nested_low.parent is nested_root
    assert nested_root.parent is nested_top
    assert _matrix_signature(nested_low.matrix_world) == _matrix_signature(
        nested_world
    )
    assert bpy.data.objects.get("NestedAsset.001") is None

    # A canonical top-level Empty that already encloses another mesh is not a
    # single asset unit and must not be reused by this API.
    multi_root = _new_empty("MultiAsset", outside)
    multi_low = _new_mesh("MultiAsset_low", low)
    _parent_preserving_world(multi_low, multi_root)
    other_mesh = _new_mesh("MultiAssetOtherMesh", outside)
    _parent_preserving_world(other_mesh, multi_root)
    multi_before = _mutation_snapshot(multi_low, export)
    _expect_value_error(
        lambda: api.ensure_painter_low_export_unit(
            multi_low,
            "MultiAsset",
            scene=scene,
        ),
        "contains another mesh",
    )
    assert _mutation_snapshot(multi_low, export) == multi_before
    assert multi_low.parent is multi_root
    assert other_mesh.parent is multi_root
    assert bpy.data.objects.get("MultiAsset.001") is None

    # If synchronization fails after both Send2UE settings and parenting have
    # changed, the API rolls the whole attempted unit back atomically.
    rollback_low = _new_mesh("RollbackAsset_low", low)
    rollback_low.location = (8.0, -3.0, 1.0)
    bpy.context.view_layer.update()
    rollback_before = _mutation_snapshot(rollback_low, export)
    combine_property.combine = "off"
    immediate_parent_property.use_immediate_parent_name = True
    real_sync = painter_sync.sync_painter_export

    def _fail_sync(_scene=None):
        # Perform the real synchronization first so Export membership,
        # ownership markers, and watcher caches are genuinely mutated before
        # the failure exercises the transaction rollback.
        real_sync(_scene)
        raise RuntimeError("forced export sync failure")

    painter_sync.sync_painter_export = _fail_sync
    try:
        try:
            api.ensure_painter_low_export_unit(
                rollback_low,
                "RollbackAsset",
                scene=scene,
            )
        except RuntimeError as error:
            assert "forced export sync failure" in str(error)
        else:
            raise AssertionError("Expected forced export sync failure")
    finally:
        painter_sync.sync_painter_export = real_sync

    assert _mutation_snapshot(rollback_low, export) == rollback_before
    assert rollback_low.parent is None
    assert bpy.data.objects.get("RollbackAsset") is None
    assert bpy.data.objects.get("RollbackAsset.001") is None
    assert combine_property.combine == "off"
    assert immediate_parent_property.use_immediate_parent_name is True

    # The same unit must succeed immediately after rollback.  This verifies
    # that restored Export membership/markers/caches do not poison a retry.
    retry_receipt = api.ensure_painter_low_export_unit(
        rollback_low,
        "RollbackAsset",
        scene=scene,
    )
    retry_root = bpy.data.objects.get("RollbackAsset")
    assert retry_root is not None and retry_root.type == "EMPTY"
    assert bpy.data.objects.get("RollbackAsset.001") is None
    assert rollback_low.parent is retry_root
    assert export.objects.get(retry_root.name) is retry_root
    assert export.objects.get(rollback_low.name) is rollback_low
    assert retry_receipt["unit_status"] == "STATIC_EMPTY_READY"
    assert retry_receipt["created_empty"] is True
    assert retry_receipt["parented"] is True
    assert retry_receipt["handoff_ready"] is True
    assert combine_property.combine == "child_meshes"
    assert immediate_parent_property.use_immediate_parent_name is False

    print("painter Low Empty export-unit contract smoke: OK")
finally:
    painter_sync.reset_painter_export_sync_state()
    addon_utils.disable("send2ue", default_set=False)
