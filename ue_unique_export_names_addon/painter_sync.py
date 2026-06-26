import bpy
from bpy.app.handlers import persistent

from .constants import AUTO_PAINTER_EXPORT_LINK_PROP, EXPORT_COLLECTION_NAME
from .utils import baking_low_collection, ensure_export_collection, export_collection

_painter_export_sync_running = False

def low_export_hierarchy(low_collection):
    """Every object actually contained in Baking/low, with parenting untouched."""
    return set(low_collection.all_objects)


def painter_export_hierarchy(low_collection):
    """Low meshes, their parents, and rigs referenced by Armature modifiers."""
    low_meshes = {
        obj for obj in low_collection.all_objects
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


def sync_painter_export(scene=None):
    """Keep Baking/low meshes and their parent chains linked into Export."""
    global _painter_export_sync_running
    if _painter_export_sync_running:
        return {"linked": 0, "unlinked": 0, "desired": 0}

    _painter_export_sync_running = True
    try:
        low_collection = baking_low_collection()
        desired = (
            painter_export_hierarchy(low_collection)
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

        return {
            "linked": linked,
            "unlinked": unlinked,
            "desired": len(desired),
        }
    finally:
        _painter_export_sync_running = False


@persistent
def sync_painter_export_on_load(_dummy):
    sync_painter_export()


@persistent
def sync_painter_export_on_depsgraph(scene, _depsgraph):
    sync_painter_export(scene)


def sync_painter_export_deferred():
    sync_painter_export()
    return None
