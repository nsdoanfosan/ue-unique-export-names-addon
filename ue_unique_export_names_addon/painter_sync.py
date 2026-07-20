import bpy
from bpy.app.handlers import persistent

from .constants import AUTO_PAINTER_EXPORT_LINK_PROP, EXPORT_COLLECTION_NAME
from .utils import baking_low_collection

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


def _current_collection_identity_signature():
    """Cheap identity check run for every depsgraph callback."""
    return (
        _id_pointer(baking_low_collection()),
        _id_pointer(bpy.data.collections.get(EXPORT_COLLECTION_NAME)),
    )


def _current_collection_membership_signature():
    """Membership check run only when Blender reports a Collection update."""
    low_collection = baking_low_collection()
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


def _depsgraph_requires_painter_export_sync(depsgraph):
    if not _painter_export_sync_state_ready:
        return True

    try:
        if _current_collection_identity_signature() != (
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
            and _current_collection_membership_signature()
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


def sync_painter_export(scene=None):
    """Keep Baking/low meshes and their parent chains linked into Export."""
    global _painter_export_sync_running
    if _painter_export_sync_running:
        return {"linked": 0, "unlinked": 0, "desired": 0}

    _painter_export_sync_running = True
    try:
        low_collection = baking_low_collection()
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
    if _depsgraph_requires_painter_export_sync(_depsgraph):
        sync_painter_export(scene)


def sync_painter_export_deferred():
    _clear_painter_export_sync_state()
    sync_painter_export()
    return None
