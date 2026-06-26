import bpy

from .constants import BACKUP_PROP, ROLE_BY_BSDF_INPUT
from .gpro import materials_from_objects
from .painter_sync import low_export_hierarchy
from .utils import (
    baking_low_collection,
    export_collection,
    parent_chain,
    selected_or_all_mesh_objects,
)

def images_from_node_tree(node_tree, visited_trees=None):
    if node_tree is None:
        return set()
    if visited_trees is None:
        visited_trees = set()
    if node_tree in visited_trees:
        return set()
    visited_trees.add(node_tree)
    images = set()
    for node in node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            images.add(node.image)
        group_tree = getattr(node, "node_tree", None)
        if group_tree is not None and group_tree not in visited_trees:
            images.update(images_from_node_tree(group_tree, visited_trees))
    return images


def images_from_material(material):
    return images_from_node_tree(
        material.node_tree if material is not None else None
    )


def linked_painter_low_objects(context):
    """Meshes simultaneously present in Baking/low and Export."""
    low_collection = baking_low_collection()
    export_coll = export_collection(context)
    if low_collection is None or export_coll is None:
        return set()
    low_meshes = {
        obj for obj in low_export_hierarchy(low_collection)
        if obj.type == "MESH"
    }
    export_objects = set(export_coll.all_objects)
    return low_meshes & export_objects


def protected_painter_data(context):
    """Return all datablocks that must remain untouched by naming workflows."""
    low_objects = linked_painter_low_objects(context)
    export_coll = export_collection(context)
    export_objects = set(export_coll.all_objects) if export_coll else set()
    hierarchy = {
        parent
        for obj in low_objects
        for parent in parent_chain(obj)
        if parent in export_objects
    }
    objects = low_objects | hierarchy
    meshes = {
        obj.data for obj in objects
        if obj.type == "MESH" and obj.data is not None
    }
    low_materials = {
        slot.material
        for obj in objects
        for slot in obj.material_slots
        if slot.material is not None
    }
    images = set()
    for material in low_materials:
        images.update(images_from_material(material))
    materials = set(low_materials)
    materials.update(
        material
        for material in bpy.data.materials
        if images_from_material(material) & images
    )
    # Object protection is only for objects/mesh-data that are actually part of
    # the Painter low hierarchy. Materials and images are protected separately
    # below; sharing a protected material must not make an Export mesh immutable.
    objects.update(
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH"
        and obj.data in meshes
    )
    return {
        "objects": objects,
        "meshes": meshes,
        "materials": materials,
        "images": images,
    }


def mutation_safe_mesh_objects(context, scope):
    candidates = selected_or_all_mesh_objects(context, scope)
    protected = protected_painter_data(context)
    safe = [obj for obj in candidates if obj not in protected["objects"]]
    skipped = [obj for obj in candidates if obj in protected["objects"]]
    return safe, skipped, protected


def external_materials_from_objects(context, objects, protected=None):
    """Materials safe to mutate without affecting linked Painter Low data."""
    if protected is None:
        protected = protected_painter_data(context)
    materials = []
    skipped = []
    for material in materials_from_objects(objects):
        shared_images = images_from_material(material) & protected["images"]
        if material in protected["materials"] or shared_images:
            skipped.append(material)
            continue
        materials.append(material)
    return materials, skipped
