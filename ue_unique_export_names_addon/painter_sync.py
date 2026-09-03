import bpy
from bpy.app.handlers import persistent

from .constants import (
    AUTO_PAINTER_EXPORT_LINK_PROP,
    CREATED_EMPTY_PROP,
    EXPORT_COLLECTION_NAME,
    PAINTER_EXPORT_ASSET_PROP,
)
from .utils import baking_low_collection, clean_token

_painter_export_sync_running = False
_painter_export_sync_state_ready = False
_painter_export_object_signatures = {}
_painter_export_low_object_pointers = set()
_painter_export_collection_signature = None


def _original_id(id_data):
    original = getattr(id_data, "original", None)
    return original if original is not None else id_data


def _id_pointer(id_data):
    if id_data is None:
        return 0
    return _original_id(id_data).as_pointer()


def _scene_contains_collection_pointer(scene, pointer):
    if scene is None or not pointer:
        return False
    pending = list(scene.collection.children)
    while pending:
        collection = pending.pop()
        if _id_pointer(collection) == pointer:
            return True
        pending.extend(collection.children)
    return False


def _clear_painter_export_sync_state():
    global _painter_export_sync_state_ready
    global _painter_export_object_signatures
    global _painter_export_low_object_pointers
    global _painter_export_collection_signature

    _painter_export_sync_state_ready = False
    _painter_export_object_signatures = {}
    _painter_export_low_object_pointers = set()
    _painter_export_collection_signature = None


def reset_painter_export_sync_state():
    """Cancel pending work and discard every cache owned by this module."""
    if bpy.app.timers.is_registered(sync_painter_export_deferred):
        try:
            bpy.app.timers.unregister(sync_painter_export_deferred)
        except RuntimeError:
            pass
    _clear_painter_export_sync_state()


def _object_relationship_signature(obj, include_low_mesh_relations):
    """Only relationships that can change the desired Export hierarchy.

    Transform and geometry changes intentionally do not participate. They are
    the common depsgraph updates and do not change collection links.
    """
    obj = _original_id(obj)
    parent_pointer = _id_pointer(obj.parent)
    if not include_low_mesh_relations:
        return (parent_pointer,)

    object_type = obj.type
    armature_targets = ()
    if object_type == "MESH":
        armature_targets = tuple(
            _id_pointer(modifier.object)
            for modifier in obj.modifiers
            if modifier.type == "ARMATURE" and modifier.object is not None
        )
    return (parent_pointer, object_type, armature_targets)


def _current_collection_identity_signature(scene=None):
    """Cheap identity check run for every depsgraph callback."""
    return (
        _id_pointer(baking_low_collection(scene)),
        _id_pointer(bpy.data.collections.get(EXPORT_COLLECTION_NAME)),
    )


def _current_collection_membership_signature(scene=None):
    """Membership check run only when Blender reports a Collection update."""
    low_collection = baking_low_collection(scene)
    export_coll = bpy.data.collections.get(EXPORT_COLLECTION_NAME)
    return (
        _id_pointer(low_collection),
        tuple(sorted(_id_pointer(obj) for obj in low_collection.all_objects))
        if low_collection is not None
        else (),
        _id_pointer(export_coll),
        tuple(sorted(_id_pointer(obj) for obj in export_coll.objects))
        if export_coll is not None
        else (),
    )


def _capture_painter_export_sync_state(low_collection, low_objects, desired, export_coll):
    global _painter_export_sync_state_ready
    global _painter_export_object_signatures
    global _painter_export_low_object_pointers
    global _painter_export_collection_signature

    low_pointers = {_id_pointer(obj) for obj in low_objects}
    watched_objects = low_objects | desired
    _painter_export_low_object_pointers = low_pointers
    _painter_export_object_signatures = {
        _id_pointer(obj): _object_relationship_signature(
            obj,
            _id_pointer(obj) in low_pointers,
        )
        for obj in watched_objects
    }
    _painter_export_collection_signature = (
        _id_pointer(low_collection),
        tuple(sorted(low_pointers)),
        _id_pointer(export_coll),
        tuple(sorted(_id_pointer(obj) for obj in export_coll.objects))
        if export_coll is not None
        else (),
    )
    _painter_export_sync_state_ready = True


def _depsgraph_requires_painter_export_sync(depsgraph, scene=None):
    if not _painter_export_sync_state_ready:
        return True

    try:
        if _current_collection_identity_signature(scene) != (
            _painter_export_collection_signature[0],
            _painter_export_collection_signature[2],
        ):
            return True

        collection_updated = False
        for update in depsgraph.updates:
            id_data = _original_id(update.id)
            if isinstance(id_data, bpy.types.Collection):
                collection_updated = True
                continue
            if not isinstance(id_data, bpy.types.Object):
                continue

            pointer = _id_pointer(id_data)
            previous = _painter_export_object_signatures.get(pointer)
            if previous is None:
                continue
            current = _object_relationship_signature(
                id_data,
                pointer in _painter_export_low_object_pointers,
            )
            if current != previous:
                return True

        return (
            collection_updated
            and _current_collection_membership_signature(scene)
            != _painter_export_collection_signature
        )
    except (ReferenceError, RuntimeError):
        # A datablock may disappear while Blender is producing the updates.
        # A conservative resync is safer than retaining stale automatic links.
        return True


def low_export_hierarchy(low_collection):
    """Every object actually contained in Baking/low, with parenting untouched."""
    return set(low_collection.all_objects)


def _painter_export_hierarchy_from_objects(low_objects):
    low_meshes = {
        obj for obj in low_objects
        if obj.type == "MESH"
    }
    hierarchy = set(low_meshes)
    for mesh in low_meshes:
        for modifier in mesh.modifiers:
            if modifier.type != "ARMATURE" or modifier.object is None:
                continue
            rig = modifier.object
            hierarchy.add(rig)
            rig_parent = rig.parent
            while rig_parent is not None:
                hierarchy.add(rig_parent)
                rig_parent = rig_parent.parent
        parent = mesh.parent
        while parent is not None:
            hierarchy.add(parent)
            parent = parent.parent
    return hierarchy


def painter_export_hierarchy(low_collection):
    """Low meshes, their parents, and rigs referenced by Armature modifiers."""
    return _painter_export_hierarchy_from_objects(set(low_collection.all_objects))


def _is_static_standalone_low(obj):
    if obj.type != "MESH" or obj.parent is not None:
        return False
    if getattr(obj.data, "shape_keys", None) is not None:
        return False
    return not any(modifier.type == "ARMATURE" for modifier in obj.modifiers)


def _has_skeletal_semantics(obj):
    if getattr(obj.data, "shape_keys", None) is not None:
        return True
    if any(modifier.type == "ARMATURE" for modifier in obj.modifiers):
        return True
    parent = obj.parent
    while parent is not None:
        if parent.type == "ARMATURE":
            return True
        parent = parent.parent
    return False


def _matrix_matches(first, second, tolerance=1.0e-8):
    return all(
        abs(float(first[row][column]) - float(second[row][column])) <= tolerance
        for row in range(4)
        for column in range(4)
    )


def _descendant_meshes(root):
    meshes = []
    pending = list(root.children)
    while pending:
        child = pending.pop()
        pending.extend(child.children)
        if child.type == "MESH":
            meshes.append(child)
    return meshes


def _configure_send2ue_child_meshes(scene):
    """Select only Send2UE's native Empty/child naming mode when available."""
    send2ue = getattr(scene, "send2ue", None)
    extensions = getattr(send2ue, "extensions", None)
    combine_assets = getattr(extensions, "combine_assets", None)
    if combine_assets is None or not hasattr(combine_assets, "combine"):
        return {
            "available": False,
            "required": "child_meshes",
            "value": None,
            "changed": False,
            "use_immediate_parent_name": {
                "available": False,
                "required": False,
                "value": None,
                "changed": False,
            },
        }

    previous = str(combine_assets.combine)
    immediate_extension = getattr(extensions, "use_immediate_parent_name", None)
    immediate_available = bool(
        immediate_extension is not None
        and hasattr(immediate_extension, "use_immediate_parent_name")
    )
    immediate_previous = (
        bool(immediate_extension.use_immediate_parent_name)
        if immediate_available
        else None
    )
    try:
        if previous != "child_meshes":
            combine_assets.combine = "child_meshes"
        observed = str(combine_assets.combine)
        if observed != "child_meshes":
            raise RuntimeError(
                "Send2UE Combine assets could not be set to Child meshes"
            )
        if immediate_available and immediate_previous:
            immediate_extension.use_immediate_parent_name = False
        immediate_observed = (
            bool(immediate_extension.use_immediate_parent_name)
            if immediate_available
            else None
        )
        if immediate_observed:
            raise RuntimeError(
                "Send2UE Use immediate parent name could not be disabled"
            )
    except Exception:
        combine_assets.combine = previous
        if immediate_available:
            immediate_extension.use_immediate_parent_name = immediate_previous
        raise
    return {
        "available": True,
        "required": "child_meshes",
        "previous": previous,
        "value": observed,
        "changed": previous != observed,
        "use_immediate_parent_name": {
            "available": immediate_available,
            "required": False,
            "previous": immediate_previous,
            "value": immediate_observed,
            "changed": immediate_previous != immediate_observed,
        },
    }


def _capture_export_sync_transaction(low_collection):
    """Capture exactly the state that ``sync_painter_export`` may mutate."""
    export_collection = bpy.data.collections.get(EXPORT_COLLECTION_NAME)
    export_members = (
        tuple(export_collection.objects)
        if export_collection is not None
        else ()
    )
    low_objects = set(low_collection.all_objects)
    desired = _painter_export_hierarchy_from_objects(low_objects)
    marker_objects = set(export_members) | desired
    marker_state = {
        obj: (
            AUTO_PAINTER_EXPORT_LINK_PROP in obj,
            obj.get(AUTO_PAINTER_EXPORT_LINK_PROP),
        )
        for obj in marker_objects
    }
    return {
        "export_collection": export_collection,
        "export_members": export_members,
        "marker_state": marker_state,
        "cache": (
            _painter_export_sync_state_ready,
            dict(_painter_export_object_signatures),
            set(_painter_export_low_object_pointers),
            _painter_export_collection_signature,
        ),
    }


def _restore_export_sync_transaction(snapshot):
    """Restore Export membership, ownership markers, and watcher caches."""
    global _painter_export_sync_state_ready
    global _painter_export_object_signatures
    global _painter_export_low_object_pointers
    global _painter_export_collection_signature

    expected_collection = snapshot["export_collection"]
    current_collection = bpy.data.collections.get(EXPORT_COLLECTION_NAME)
    current_members = (
        tuple(current_collection.objects)
        if current_collection is not None
        else ()
    )
    marker_objects = set(snapshot["marker_state"]) | set(current_members)
    try:
        if expected_collection is None:
            if current_collection is not None:
                bpy.data.collections.remove(current_collection, do_unlink=True)
        else:
            if current_collection is not expected_collection:
                raise RuntimeError(
                    "Export collection identity changed during Painter synchronization"
                )
            expected_members = set(snapshot["export_members"])
            for obj in current_members:
                if obj not in expected_members:
                    expected_collection.objects.unlink(obj)
            for obj in snapshot["export_members"]:
                if expected_collection.objects.get(obj.name) is not obj:
                    expected_collection.objects.link(obj)

        for obj in marker_objects:
            existed, value = snapshot["marker_state"].get(obj, (False, None))
            if existed:
                obj[AUTO_PAINTER_EXPORT_LINK_PROP] = value
            elif AUTO_PAINTER_EXPORT_LINK_PROP in obj:
                del obj[AUTO_PAINTER_EXPORT_LINK_PROP]
    finally:
        (
            _painter_export_sync_state_ready,
            object_signatures,
            low_object_pointers,
            _painter_export_collection_signature,
        ) = snapshot["cache"]
        _painter_export_object_signatures = dict(object_signatures)
        _painter_export_low_object_pointers = set(low_object_pointers)


def _send2ue_handoff_ready(unit_status, combine):
    if unit_status not in {"STATIC_EMPTY_READY", "EXISTING_EMPTY_READY"}:
        return False
    if not combine.get("available") or combine.get("value") != "child_meshes":
        return False
    immediate = combine.get("use_immediate_parent_name") or {}
    return not immediate.get("available") or immediate.get("value") is False


def ensure_painter_low_export_unit(low_object, asset_base, scene=None):
    """Create one safe Empty export root for a standalone static Painter Low.

    The child mesh keeps ``<base>_low`` for Painter's By Mesh Name matching.
    Send2UE's native Combine > Child meshes mode uses the Empty's ``<base>``
    name for the Unreal asset. Existing parent/rig/shape-key hierarchies are
    never renamed or reparented by this function.
    """
    if low_object is None or low_object.type != "MESH":
        raise ValueError("Painter Low export-unit input must be a mesh object")
    scene = scene or bpy.context.scene
    if scene is None:
        raise ValueError("Painter Low export-unit preparation requires a scene")
    low_collection = baking_low_collection(scene)
    if low_collection is None or low_object not in set(low_collection.all_objects):
        raise ValueError("Painter Low export-unit input must be inside Baking/low")

    raw_asset_base = str(asset_base or "").strip()
    canonical_base = clean_token(raw_asset_base)
    if not raw_asset_base or canonical_base != raw_asset_base:
        raise ValueError("Painter Low asset base must already be a canonical name")
    expected_low_name = f"{canonical_base}_low"
    if low_object.name != expected_low_name:
        raise ValueError(
            f"Painter Low name mismatch: expected {expected_low_name!r}, "
            f"found {low_object.name!r}"
        )

    created_empty = False
    parented = False
    unit_status = "PRESERVED_SKELETAL_HIERARCHY"
    export_root = low_object.parent
    original_parent = low_object.parent
    original_world_matrix = low_object.matrix_world.copy()
    combine = None
    sync_snapshot = None

    if _has_skeletal_semantics(low_object):
        pass
    elif _is_static_standalone_low(low_object):
        collision = bpy.data.objects.get(canonical_base)
        if collision is not None:
            reusable = (
                collision.type == "EMPTY"
                and bool(collision.get(CREATED_EMPTY_PROP))
                and collision.get(PAINTER_EXPORT_ASSET_PROP) == canonical_base
                and collision.parent is None
                and not collision.children
            )
            if not reusable:
                raise ValueError(
                    f"Exact Painter export Empty name is already in use: "
                    f"{canonical_base!r}"
                )
            export_root = collision
        else:
            export_root = bpy.data.objects.new(canonical_base, None)
            scene.collection.objects.link(export_root)
            created_empty = True
        unit_status = "STATIC_EMPTY_READY"
    elif low_object.parent is not None and low_object.parent.type == "EMPTY":
        export_root = low_object.parent
        if export_root.name != canonical_base:
            raise ValueError(
                f"Existing Painter Low Empty must be named {canonical_base!r}; "
                f"found {export_root.name!r}"
            )
        if export_root.parent is not None:
            raise ValueError(
                "Painter Low export Empty must be the top-level object parent"
            )
        other_mesh_children = [
            child
            for child in _descendant_meshes(export_root)
            if child is not low_object
        ]
        if other_mesh_children:
            raise ValueError(
                "Painter Low export Empty contains another mesh and cannot be "
                "used as a single Send2UE unit: "
                + ", ".join(child.name for child in other_mesh_children)
            )
        unit_status = "EXISTING_EMPTY_READY"
    else:
        raise ValueError(
            "A non-skeletal Painter Low with an existing non-Empty parent is "
            "not safe to reparent automatically"
        )

    try:
        if unit_status in {"STATIC_EMPTY_READY", "EXISTING_EMPTY_READY"}:
            if low_object.parent is not export_root:
                export_root[CREATED_EMPTY_PROP] = True
                export_root[PAINTER_EXPORT_ASSET_PROP] = canonical_base
                low_object.parent = export_root
                parented = True
                low_object.matrix_world = original_world_matrix
            combine = _configure_send2ue_child_meshes(scene)
        else:
            combine = {
                "available": False,
                "required": "child_meshes",
                "value": None,
                "changed": False,
                "reason": "skeletal_hierarchy_preserved",
                "use_immediate_parent_name": {
                    "available": False,
                    "required": False,
                    "value": None,
                    "changed": False,
                },
            }
        sync_snapshot = _capture_export_sync_transaction(low_collection)
        sync = sync_painter_export(scene)
        preserved_world_transform = _matrix_matches(
            low_object.matrix_world,
            original_world_matrix,
        )
        if not preserved_world_transform:
            raise RuntimeError("Parenting the Painter Low changed its world transform")
    except Exception as error:
        rollback_errors = []

        def rollback_step(label, action):
            try:
                action()
            except Exception as rollback_error:
                rollback_errors.append(f"{label}: {rollback_error}")

        if parented:
            def restore_parent():
                low_object.parent = original_parent
                low_object.matrix_world = original_world_matrix

            rollback_step("parent/world transform", restore_parent)
        if sync_snapshot is not None:
            rollback_step(
                "Export synchronization",
                lambda: _restore_export_sync_transaction(sync_snapshot),
            )
        if created_empty and export_root is not None:
            rollback_step(
                "generated Empty",
                lambda: bpy.data.objects.remove(export_root, do_unlink=True),
            )

        send2ue = getattr(scene, "send2ue", None)
        extensions = getattr(send2ue, "extensions", None)
        if combine and combine.get("available") and combine.get("changed"):
            combine_assets = getattr(extensions, "combine_assets", None)
            if combine_assets is not None and hasattr(combine_assets, "combine"):
                rollback_step(
                    "Send2UE Combine assets",
                    lambda: setattr(combine_assets, "combine", combine["previous"]),
                )
        immediate = (combine or {}).get("use_immediate_parent_name") or {}
        if immediate.get("available") and immediate.get("changed"):
            immediate_extension = getattr(
                extensions,
                "use_immediate_parent_name",
                None,
            )
            if immediate_extension is not None and hasattr(
                immediate_extension,
                "use_immediate_parent_name",
            ):
                rollback_step(
                    "Send2UE Use immediate parent name",
                    lambda: setattr(
                        immediate_extension,
                        "use_immediate_parent_name",
                        immediate["previous"],
                    ),
                )
        if rollback_errors:
            raise RuntimeError(
                f"Painter Low export-unit preparation failed ({error}); "
                "rollback also failed (" + "; ".join(rollback_errors) + ")"
            ) from error
        raise

    handoff_ready = _send2ue_handoff_ready(unit_status, combine)
    return {
        **sync,
        "synced": True,
        "handoff_ready": handoff_ready,
        "unit_status": unit_status,
        "asset_base": canonical_base,
        "low_object": low_object.name,
        "export_root": export_root.name if export_root is not None else None,
        "root_name_matches": bool(
            export_root is not None and export_root.name == canonical_base
        ),
        "unreal_asset_name": canonical_base if handoff_ready else None,
        "created_empty": created_empty,
        "parented": parented,
        "preserved_world_transform": preserved_world_transform,
        "combine_assets": combine,
    }


def sync_painter_export(scene=None):
    """Keep Baking/low meshes and their parent chains linked into Export."""
    global _painter_export_sync_running
    if _painter_export_sync_running:
        return {"linked": 0, "unlinked": 0, "desired": 0}

    _painter_export_sync_running = True
    try:
        if scene is None:
            scene = getattr(bpy.context, "scene", None)
        low_collection = baking_low_collection(scene)
        if low_collection is None:
            cached_low_pointer = (
                _painter_export_collection_signature[0]
                if _painter_export_sync_state_ready
                and _painter_export_collection_signature is not None
                else 0
            )
            if not _scene_contains_collection_pointer(scene, cached_low_pointer):
                # The cache and exact-name Export collection belong to another
                # scene. A handler for this unrelated scene must not interpret
                # its missing Painter classification as a request to unlink
                # those objects. A renamed contract collection in the same
                # scene still carries the cached pointer and is cleaned below.
                return {"linked": 0, "unlinked": 0, "desired": 0}
        low_objects = (
            set(low_collection.all_objects)
            if low_collection is not None
            else set()
        )
        desired = (
            _painter_export_hierarchy_from_objects(low_objects)
            if low_collection is not None
            else set()
        )

        export_coll = bpy.data.collections.get(EXPORT_COLLECTION_NAME)
        if desired and export_coll is None:
            if scene is None:
                scene = bpy.context.scene
            if scene is None and bpy.data.scenes:
                scene = bpy.data.scenes[0]
            if scene is None:
                _capture_painter_export_sync_state(
                    low_collection,
                    low_objects,
                    desired,
                    export_coll,
                )
                return {"linked": 0, "unlinked": 0, "desired": len(desired)}
            export_coll = bpy.data.collections.new(EXPORT_COLLECTION_NAME)
            scene.collection.children.link(export_coll)

        linked = 0
        if export_coll is not None:
            for obj in sorted(desired, key=lambda item: item.name_full):
                if export_coll.objects.get(obj.name) is not obj:
                    export_coll.objects.link(obj)
                    linked += 1
                    obj[AUTO_PAINTER_EXPORT_LINK_PROP] = True

        unlinked = 0
        # Auto-linked objects live directly in Export, so cleanup only needs to
        # inspect that small set instead of every object after every depsgraph update.
        cleanup_candidates = list(export_coll.objects) if export_coll is not None else []
        for obj in cleanup_candidates:
            if not obj.get(AUTO_PAINTER_EXPORT_LINK_PROP):
                continue
            if obj in desired:
                continue
            if export_coll is not None and export_coll.objects.get(obj.name) is obj:
                export_coll.objects.unlink(obj)
                unlinked += 1
            del obj[AUTO_PAINTER_EXPORT_LINK_PROP]

        _capture_painter_export_sync_state(
            low_collection,
            low_objects,
            desired,
            export_coll,
        )

        return {
            "linked": linked,
            "unlinked": unlinked,
            "desired": len(desired),
        }
    finally:
        _painter_export_sync_running = False


@persistent
def sync_painter_export_on_load(_dummy):
    reset_painter_export_sync_state()
    sync_painter_export()


@persistent
def sync_painter_export_on_undo_redo(scene=None):
    if not isinstance(scene, bpy.types.Scene):
        scene = getattr(bpy.context, "scene", None)
    reset_painter_export_sync_state()
    sync_painter_export(scene)


@persistent
def sync_painter_export_on_depsgraph(scene, _depsgraph):
    if _depsgraph_requires_painter_export_sync(_depsgraph, scene):
        sync_painter_export(scene)


def sync_painter_export_deferred():
    _clear_painter_export_sync_state()
    sync_painter_export()
    return None
