from pathlib import Path
import sys

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ue_unique_export_names_addon.constants import (
    AUTO_PAINTER_EXPORT_LINK_PROP,
    EXPORT_COLLECTION_NAME,
)
import ue_unique_export_names_addon as addon
from ue_unique_export_names_addon import painter_sync


class _FakeUpdate:
    def __init__(self, id_data):
        self.id = id_data


class _FakeDepsgraph:
    def __init__(self, *id_data):
        self.updates = [_FakeUpdate(item) for item in id_data]


def _new_empty(name, collection):
    obj = bpy.data.objects.new(name, None)
    collection.objects.link(obj)
    return obj


def _new_mesh(name, collection):
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def _new_armature(name, collection):
    armature = bpy.data.armatures.new(f"{name}_Armature")
    obj = bpy.data.objects.new(name, armature)
    collection.objects.link(obj)
    return obj


def _capture_updates(action):
    records = []

    def capture(_scene, depsgraph):
        for update in depsgraph.updates:
            id_data = getattr(update.id, "original", None) or update.id
            records.append((type(id_data).__name__, id_data.name_full))

    bpy.context.view_layer.update()
    bpy.app.handlers.depsgraph_update_post.append(capture)
    try:
        action()
        bpy.context.view_layer.update()
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(capture)
    return records


def _assert_direct_members(collection, expected):
    actual = set(collection.objects)
    assert actual == set(expected), (
        f"Unexpected {collection.name} members: "
        f"{sorted(obj.name_full for obj in actual)}"
    )


def _is_direct_member(collection, obj):
    return collection.objects.get(obj.name) is obj


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

baking = bpy.data.collections.new("Baking")
scene.collection.children.link(baking)
low = bpy.data.collections.new("low")
baking.children.link(low)
outside = bpy.data.collections.new("Contract_Outside")
scene.collection.children.link(outside)

character_root = _new_empty("Contract_CharacterRoot", outside)
mesh_parent = _new_empty("Contract_MeshParent", outside)
mesh_parent.parent = character_root
low_mesh = _new_mesh("Contract_LowMesh", low)
low_mesh.parent = mesh_parent

rig_root = _new_empty("Contract_RigRoot", outside)
rig = _new_armature("Contract_Rig", outside)
rig.parent = rig_root
armature_modifier = low_mesh.modifiers.new("Contract_Armature", "ARMATURE")
armature_modifier.object = rig

sibling = _new_mesh("Contract_Sibling", outside)
sibling.parent = mesh_parent
sibling_modifier = sibling.modifiers.new("Contract_SiblingArmature", "ARMATURE")
sibling_modifier.object = rig

manual_low = _new_mesh("Contract_ManualLow", low)
external_manual = _new_mesh("Contract_ExternalManual", outside)

export = bpy.data.collections.new(EXPORT_COLLECTION_NAME)
scene.collection.children.link(export)
export.objects.link(manual_low)
export.objects.link(external_manual)

initial = painter_sync.sync_painter_export(scene)
expected_initial = {
    low_mesh,
    mesh_parent,
    character_root,
    rig,
    rig_root,
    manual_low,
    external_manual,
}
_assert_direct_members(export, expected_initial)
assert initial == {"linked": 5, "unlinked": 0, "desired": 6}
assert not _is_direct_member(export, sibling)
assert not manual_low.get(AUTO_PAINTER_EXPORT_LINK_PROP)
assert not external_manual.get(AUTO_PAINTER_EXPORT_LINK_PROP)
for auto_linked in {low_mesh, mesh_parent, character_root, rig, rig_root}:
    assert auto_linked.get(AUTO_PAINTER_EXPORT_LINK_PROP)

# Blender 5.1 must report the ID types the filter relies on.
transform_updates = _capture_updates(
    lambda: setattr(low_mesh.location, "x", low_mesh.location.x + 1.0)
)
assert ("Object", low_mesh.name_full) in transform_updates, transform_updates

membership_probe = _new_mesh("Contract_MembershipProbe", outside)
membership_updates = _capture_updates(lambda: low.objects.link(membership_probe))
assert ("Collection", low.name_full) in membership_updates, membership_updates
low.objects.unlink(membership_probe)

# Common transform/geometry ticks do not request a full hierarchy scan.
low_mesh.location.y += 1.0
assert not painter_sync._depsgraph_requires_painter_export_sync(
    _FakeDepsgraph(low_mesh)
)
assert not bpy.app.timers.is_registered(painter_sync.sync_painter_export_deferred)
full_sync_calls = []
real_sync_painter_export = painter_sync.sync_painter_export


def _record_full_sync(*args, **kwargs):
    full_sync_calls.append((args, kwargs))
    return real_sync_painter_export(*args, **kwargs)


painter_sync.sync_painter_export = _record_full_sync
try:
    painter_sync.sync_painter_export_on_depsgraph(scene, _FakeDepsgraph(low_mesh))
finally:
    painter_sync.sync_painter_export = real_sync_painter_export
assert full_sync_calls == []
assert not bpy.app.timers.is_registered(painter_sync.sync_painter_export_deferred)
low_mesh.data.update()
assert not painter_sync._depsgraph_requires_painter_export_sync(
    _FakeDepsgraph(low_mesh.data)
)

# Reparenting is relationship work and rebuilds exact links immediately.
replacement_parent = _new_empty("Contract_ReplacementParent", outside)
reparent_updates = _capture_updates(lambda: setattr(low_mesh, "parent", replacement_parent))
assert ("Object", low_mesh.name_full) in reparent_updates, reparent_updates
assert painter_sync._depsgraph_requires_painter_export_sync(
    _FakeDepsgraph(low_mesh)
)
reparented = painter_sync.sync_painter_export(scene)
assert reparented == {"linked": 1, "unlinked": 2, "desired": 5}
assert _is_direct_member(export, replacement_parent)
assert not _is_direct_member(export, mesh_parent)
assert not _is_direct_member(export, character_root)

# Changing the Armature modifier target updates the rig and its parent chain.
replacement_rig_root = _new_empty("Contract_ReplacementRigRoot", outside)
replacement_rig = _new_armature("Contract_ReplacementRig", outside)
replacement_rig.parent = replacement_rig_root
modifier_updates = _capture_updates(
    lambda: setattr(armature_modifier, "object", replacement_rig)
)
assert ("Object", low_mesh.name_full) in modifier_updates, modifier_updates
assert painter_sync._depsgraph_requires_painter_export_sync(
    _FakeDepsgraph(low_mesh)
)
rig_changed = painter_sync.sync_painter_export(scene)
assert rig_changed == {"linked": 2, "unlinked": 2, "desired": 5}
assert _is_direct_member(export, replacement_rig)
assert _is_direct_member(export, replacement_rig_root)
assert not _is_direct_member(export, rig)
assert not _is_direct_member(export, rig_root)

# Reparenting the referenced rig updates its complete parent chain as well.
new_rig_parent = _new_empty("Contract_NewRigParent", outside)
rig_parent_updates = _capture_updates(
    lambda: setattr(replacement_rig, "parent", new_rig_parent)
)
assert ("Object", replacement_rig.name_full) in rig_parent_updates, rig_parent_updates
assert painter_sync._depsgraph_requires_painter_export_sync(
    _FakeDepsgraph(replacement_rig)
)
rig_reparented = painter_sync.sync_painter_export(scene)
assert rig_reparented == {"linked": 1, "unlinked": 1, "desired": 5}
assert _is_direct_member(export, new_rig_parent)
assert not _is_direct_member(export, replacement_rig_root)

# Collection updates detect Low membership changes, including nested objects.
nested_low = bpy.data.collections.new("Contract_NestedLow")
low.children.link(nested_low)
nested_mesh = _new_mesh("Contract_NestedLowMesh", nested_low)
assert painter_sync._depsgraph_requires_painter_export_sync(
    _FakeDepsgraph(nested_low)
)
nested_added = painter_sync.sync_painter_export(scene)
assert nested_added == {"linked": 1, "unlinked": 0, "desired": 6}
assert _is_direct_member(export, nested_mesh)

low.children.unlink(nested_low)
assert painter_sync._depsgraph_requires_painter_export_sync(_FakeDepsgraph(low))
nested_removed = painter_sync.sync_painter_export(scene)
assert nested_removed == {"linked": 0, "unlinked": 1, "desired": 5}
assert not _is_direct_member(export, nested_mesh)
low.children.link(nested_low)
assert painter_sync._depsgraph_requires_painter_export_sync(_FakeDepsgraph(low))
nested_restored = painter_sync.sync_painter_export(scene)
assert nested_restored == {"linked": 1, "unlinked": 0, "desired": 6}

# Send2UE may read Export immediately after view_layer.update(), so a relevant
# depsgraph callback must repair a manually removed automatic link synchronously.
export.objects.unlink(low_mesh)
assert painter_sync._depsgraph_requires_painter_export_sync(_FakeDepsgraph(export))
painter_sync.sync_painter_export_on_depsgraph(scene, _FakeDepsgraph(export))
assert _is_direct_member(export, low_mesh)
assert not bpy.app.timers.is_registered(painter_sync.sync_painter_export_deferred)

# Deleting watched Low hierarchy objects invalidates cached relationships and
# collection membership without retaining stale automatic links.
delete_parent = _new_empty("Contract_DeleteParent", outside)
delete_mesh = _new_mesh("Contract_DeleteLow", low)
delete_mesh.parent = delete_parent
delete_added = painter_sync.sync_painter_export(scene)
assert delete_added == {"linked": 2, "unlinked": 0, "desired": 8}
bpy.data.objects.remove(delete_parent, do_unlink=True)
assert painter_sync._depsgraph_requires_painter_export_sync(_FakeDepsgraph(export))
parent_deleted = painter_sync.sync_painter_export(scene)
assert parent_deleted == {"linked": 0, "unlinked": 0, "desired": 7}
assert export.objects.get("Contract_DeleteParent") is None
bpy.data.objects.remove(delete_mesh, do_unlink=True)
assert painter_sync._depsgraph_requires_painter_export_sync(_FakeDepsgraph(export))
mesh_deleted = painter_sync.sync_painter_export(scene)
assert mesh_deleted == {"linked": 0, "unlinked": 0, "desired": 6}
assert export.objects.get("Contract_DeleteLow") is None

low.objects.unlink(low_mesh)
assert painter_sync._depsgraph_requires_painter_export_sync(_FakeDepsgraph(low))
low_removed = painter_sync.sync_painter_export(scene)
assert low_removed == {"linked": 0, "unlinked": 4, "desired": 2}
for stale in {low_mesh, replacement_parent, replacement_rig, new_rig_parent}:
    assert not _is_direct_member(export, stale)
    assert not stale.get(AUTO_PAINTER_EXPORT_LINK_PROP)

# A pre-existing direct Export link is manual ownership and must survive.
low.objects.unlink(manual_low)
assert painter_sync._depsgraph_requires_painter_export_sync(_FakeDepsgraph(low))
manual_preserved = painter_sync.sync_painter_export(scene)
assert manual_preserved == {"linked": 0, "unlinked": 0, "desired": 1}
assert _is_direct_member(export, manual_low)
assert _is_direct_member(export, external_manual)
assert not manual_low.get(AUTO_PAINTER_EXPORT_LINK_PROP)
assert not external_manual.get(AUTO_PAINTER_EXPORT_LINK_PROP)

# Renaming the contract collection is caught by the cheap per-callback identity
# check even when the reported depsgraph update is not the Collection itself.
low.name = "Contract_LowDisabled"
assert painter_sync._depsgraph_requires_painter_export_sync(
    _FakeDepsgraph(scene)
)
disabled = painter_sync.sync_painter_export(scene)
assert disabled == {"linked": 0, "unlinked": 1, "desired": 0}
assert not _is_direct_member(export, nested_mesh)
assert _is_direct_member(export, manual_low)
assert _is_direct_member(export, external_manual)

# Load and deferred entry points remain unconditional full synchronizations.
low.name = "low"
low.objects.link(low_mesh)
assert painter_sync.sync_painter_export_on_load(None) is None
assert _is_direct_member(export, low_mesh)
export.objects.unlink(low_mesh)
assert painter_sync.sync_painter_export_deferred() is None
assert _is_direct_member(export, low_mesh)

# Registration cancels stale work/cache from a previous lifecycle before
# installing exactly one copy of every handler and the initial full-sync timer.
bpy.app.timers.register(
    painter_sync.sync_painter_export_deferred,
    first_interval=60.0,
)
painter_sync._painter_export_sync_state_ready = True
painter_sync._painter_export_object_signatures = {123: (456,)}
addon.register()
assert not painter_sync._painter_export_sync_state_ready
assert painter_sync._painter_export_object_signatures == {}
assert painter_sync.sync_painter_export_on_load in bpy.app.handlers.load_post
assert (
    painter_sync.sync_painter_export_on_depsgraph
    in bpy.app.handlers.depsgraph_update_post
)
assert painter_sync.sync_painter_export_on_undo_redo in bpy.app.handlers.undo_post
assert painter_sync.sync_painter_export_on_undo_redo in bpy.app.handlers.redo_post
assert bpy.app.timers.is_registered(painter_sync.sync_painter_export_deferred)

# Undo immediately repairs a missing desired link, while redo immediately
# cleans a restored stale automatic link. Both rebuild exact state and cancel
# the startup timer so no previous lifecycle work can fire afterward.
expected_final = {
    low_mesh,
    replacement_parent,
    replacement_rig,
    new_rig_parent,
    nested_mesh,
    manual_low,
    external_manual,
}
export.objects.unlink(low_mesh)
painter_sync.sync_painter_export_on_undo_redo(None)
assert painter_sync._painter_export_sync_state_ready
assert not bpy.app.timers.is_registered(painter_sync.sync_painter_export_deferred)
_assert_direct_members(export, expected_final)

export.objects.link(sibling)
sibling[AUTO_PAINTER_EXPORT_LINK_PROP] = True
painter_sync.sync_painter_export_on_undo_redo(scene)
assert not _is_direct_member(export, sibling)
assert not sibling.get(AUTO_PAINTER_EXPORT_LINK_PROP)
_assert_direct_members(export, expected_final)

# Unregister cancels pending work and erases cache state so a later register
# cannot inherit references from the previous file/lifecycle.
bpy.app.timers.register(
    painter_sync.sync_painter_export_deferred,
    first_interval=60.0,
)
addon.unregister()
assert not painter_sync._painter_export_sync_state_ready
assert painter_sync._painter_export_object_signatures == {}
assert painter_sync._painter_export_low_object_pointers == set()
assert painter_sync._painter_export_collection_signature is None
assert painter_sync.sync_painter_export_on_load not in bpy.app.handlers.load_post
assert (
    painter_sync.sync_painter_export_on_depsgraph
    not in bpy.app.handlers.depsgraph_update_post
)
assert painter_sync.sync_painter_export_on_undo_redo not in bpy.app.handlers.undo_post
assert painter_sync.sync_painter_export_on_undo_redo not in bpy.app.handlers.redo_post
assert not bpy.app.timers.is_registered(painter_sync.sync_painter_export_deferred)

addon.register()
assert not painter_sync._painter_export_sync_state_ready
assert bpy.app.timers.is_registered(painter_sync.sync_painter_export_deferred)
addon.unregister()

print("painter export sync contract smoke: OK")
