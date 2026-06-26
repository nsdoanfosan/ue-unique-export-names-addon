bl_info = {
    "name": "UE Unique Export Names",
    "author": "Codex",
    "version": (2, 8, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > UE Names",
    "description": "Rename Blender materials/textures for Send to Unreal and write an Unreal postprocess manifest.",
    "category": "Import-Export",
}

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, StringProperty


BACKUP_PROP = "_ue_unique_export_original_name"
BACKUP_FILEPATH_PROP = "_ue_unique_export_original_filepath"
BACKUP_FILEPATH_RAW_PROP = "_ue_unique_export_original_filepath_raw"
# Legacy marker from versions that created Painter grouping Empties. Restore
# still recognizes it so old .blend files can clean those generated objects up.
CREATED_EMPTY_PROP = "_ue_unique_export_created_empty"
AUTO_PAINTER_EXPORT_LINK_PROP = "_ue_unique_export_auto_link"
_painter_export_sync_running = False

# Send to Unreal exports the objects inside a collection named "Export"; the addon's
# default scope mirrors that so you don't have to manually select every object.
EXPORT_COLLECTION_NAME = "Export"
BAKING_LOW_COLLECTION_NAME = "low"

ROLE_BY_BSDF_INPUT = {
    "Base Color": "BaseColor",
    "Metallic": "Metallic",
    "Roughness": "Roughness",
    "Normal": "Normal",
    "Emission Color": "Emissive",
    "Emission Strength": "Emissive",
    "Alpha": "Alpha",
}

ROLE_PRIORITY = [
    "BaseColor",
    "MetallicRoughness",
    "Normal",
    "Emissive",
    "Roughness",
    "Metallic",
    "Occlusion",
    "Alpha",
    "Texture",
]

PAINTER_ROLE_NAMES = {
    "BaseColor": "Color",
    "MetallicRoughness": "Extra",
    "Normal": "Normal",
    "Emissive": "Emissive",
    "Height": "Height",
}

SURFACE_LAYER_PARAM_BY_ROLE = {
    "BaseColor": "Albedo",
    "MetallicRoughness": "Extra",
    "Roughness": "Extra",
    "Metallic": "Extra",
    "Occlusion": "Extra",
    "Normal": "Normal",
    "Height": "Height",
    "Alpha": "Transmission",
    "Emissive": "Emissive",
    "Texture": "Albedo",
}
globals().pop("ASSET_SURFACE_PARAM_BY_ROLE", None)
globals().pop("asset_surface_param_for_role", None)

def clean_token(value):
    value = str(value or "").strip()
    value = re.sub(r"[^0-9A-Za-z_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "Asset"


def asset_prefix(context, prefix_mode, custom_prefix):
    if prefix_mode == "CUSTOM" and custom_prefix.strip():
        return clean_token(custom_prefix)
    if bpy.data.filepath:
        return clean_token(Path(bpy.data.filepath).stem)
    return clean_token(context.scene.name)


def export_collection(context):
    """Return the exact collection Send to Unreal exports from."""
    return bpy.data.collections.get(EXPORT_COLLECTION_NAME)


def ensure_export_collection(context):
    coll = export_collection(context)
    if coll is not None:
        return coll
    coll = bpy.data.collections.new(EXPORT_COLLECTION_NAME)
    context.scene.collection.children.link(coll)
    return coll


def baking_low_collection():
    baking = bpy.data.collections.get("Baking")
    if baking is not None:
        for child in baking.children:
            if child.name == BAKING_LOW_COLLECTION_NAME:
                return child
    return None


def selected_or_all_mesh_objects(context, scope):
    if scope == "SELECTED":
        objects = context.selected_objects
    elif scope == "EXPORT_COLLECTION":
        coll = export_collection(context)
        objects = coll.all_objects if coll else []
    else:  # "SCENE"
        objects = context.scene.objects
    protected_objects = linked_painter_low_objects(context)
    return [
        obj
        for obj in objects
        if obj.type == "MESH"
        and obj not in protected_objects
    ]


def json_scope_mesh_objects(context, scope):
    """Mesh objects to describe in JSON. This is read-only, so protected Painter
    data is included instead of being filtered out."""
    if scope == "SELECTED":
        objects = context.selected_objects
    elif scope == "EXPORT_COLLECTION":
        coll = export_collection(context)
        objects = coll.all_objects if coll else []
    else:  # "SCENE"
        objects = context.scene.objects
    return [obj for obj in objects if obj.type == "MESH"]


def scope_objects_for_validation(context, scope):
    if scope == "SELECTED":
        return list(context.selected_objects)
    if scope == "EXPORT_COLLECTION":
        coll = export_collection(context)
        return list(coll.all_objects) if coll else []
    return list(context.scene.objects)


def is_edit_mesh_modifier(modifier):
    modifier_name = clean_token(getattr(modifier, "name", "")).casefold()
    node_group = getattr(modifier, "node_group", None)
    node_group_name = clean_token(getattr(node_group, "name", "")).casefold()
    return "edit_mesh" in {modifier_name, node_group_name}


def is_hair_tool_object(obj):
    if not obj or obj.type not in {"CURVES", "MESH"}:
        return False
    if any(is_edit_mesh_modifier(modifier) for modifier in obj.modifiers):
        return False
    node_group_names = {
        modifier.node_group.name
        for modifier in obj.modifiers
        if modifier.type == "NODES" and modifier.node_group
    }
    return (
        any(name.startswith("Hair_System_Setup") for name in node_group_names)
        and any(name.startswith("Hair_System_Profile") for name in node_group_names)
    )


def hair_tool_input_object(obj):
    for modifier in obj.modifiers:
        if (
            modifier.type != "NODES"
            or not modifier.node_group
            or not modifier.node_group.name.startswith("Hair_System_Setup")
        ):
            continue
        try:
            input_object = modifier.get("Input_3")
        except (KeyError, TypeError):
            input_object = None
        if isinstance(input_object, bpy.types.Object):
            return input_object
    return None


def validation_scope_objects(context, scope):
    objects = scope_objects_for_validation(context, scope)
    mesh_objects = [obj for obj in objects if obj.type == "MESH"]
    hair_candidates = [obj for obj in objects if is_hair_tool_object(obj)]
    upstream_hair = {
        input_object
        for input_object in (hair_tool_input_object(obj) for obj in hair_candidates)
        if input_object in hair_candidates
    }
    rows = []
    seen = set()
    for obj in [*mesh_objects, *hair_candidates]:
        if obj in upstream_hair:
            continue
        if obj.name in seen:
            continue
        rows.append(obj)
        seen.add(obj.name)
    return rows


def parent_chain(obj):
    """Return all parents from the top-most parent down to the direct parent."""
    chain = []
    parent = obj.parent
    while parent is not None:
        chain.append(parent)
        parent = parent.parent
    chain.reverse()
    return chain


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


def is_gpro_instance_modifier(modifier):
    modifier_name = clean_token(getattr(modifier, "name", "")).casefold()
    node_group = getattr(modifier, "node_group", None)
    node_group_name = clean_token(getattr(node_group, "name", "")).casefold() if node_group else ""
    return "gpro_instance" in {modifier_name, node_group_name}


def gpro_instance_collections(obj):
    collections = []
    seen = set()
    for modifier in obj.modifiers:
        if not is_gpro_instance_modifier(modifier):
            continue
        for key in ("Socket_2",):
            value = modifier.get(key)
            if isinstance(value, bpy.types.Collection) and value.name not in seen:
                collections.append(value)
                seen.add(value.name)
        for key in modifier.keys():
            value = modifier.get(key)
            if isinstance(value, bpy.types.Collection) and value.name not in seen:
                collections.append(value)
                seen.add(value.name)
    return collections


def effective_material_slot_entries(obj, _visited_objects=None, _visited_collections=None):
    if _visited_objects is None:
        _visited_objects = set()
    if _visited_collections is None:
        _visited_collections = set()
    if obj.name in _visited_objects:
        return []
    _visited_objects.add(obj.name)

    entries = []
    for slot_index, slot in enumerate(obj.material_slots):
        entries.append((slot_index, slot.material, f"{obj.name} slot {slot_index}"))

    next_index = len(entries)
    for collection in gpro_instance_collections(obj):
        if collection.name in _visited_collections:
            continue
        _visited_collections.add(collection.name)
        for source in collection.all_objects:
            if source.type != "MESH":
                continue
            for source_slot_index, slot in enumerate(source.material_slots):
                entries.append(
                    (
                        next_index,
                        slot.material,
                        f"{obj.name} gPro {collection.name}/{source.name} slot {source_slot_index}",
                    )
                )
                next_index += 1
            for _nested_slot_index, nested_mat, nested_location in effective_material_slot_entries(
                source,
                _visited_objects=_visited_objects,
                _visited_collections=_visited_collections,
            ):
                entries.append((next_index, nested_mat, f"{obj.name} gPro {nested_location}"))
                next_index += 1
    return entries


def effective_material_names(obj):
    return [mat.name if mat else "" for _slot_index, mat, _location in effective_material_slot_entries(obj)]


def has_gpro_instance_material_source(obj):
    return bool(gpro_instance_collections(obj))


def materials_from_objects(objects):
    materials = []
    seen = set()
    for obj in objects:
        for _slot_index, mat, _location in effective_material_slot_entries(obj):
            if mat and not mat.library and mat.name not in seen:
                materials.append(mat)
                seen.add(mat.name)
    return materials


def materials_from_objects_readonly(objects):
    materials = []
    seen = set()
    for obj in objects:
        for _slot_index, mat, _location in effective_material_slot_entries(obj):
            if mat and mat.name not in seen:
                materials.append(mat)
                seen.add(mat.name)
    return materials


def is_unreal_handoff_material(mat):
    if mat is None:
        return False
    return not clean_token(mat.name).upper().startswith("HT_")


def unreal_handoff_materials_from_objects(objects):
    materials = []
    seen = set()
    for obj in objects:
        for _slot_index, mat, _location in effective_material_slot_entries(obj):
            if (
                mat
                and is_unreal_handoff_material(mat)
                and mat.name not in seen
            ):
                materials.append(mat)
                seen.add(mat.name)
    return materials


def material_usage_lookup(objects):
    usage = {}
    for obj in objects:
        for _slot_index, mat, location in effective_material_slot_entries(obj):
            if mat is None:
                continue
            usage.setdefault(mat, []).append(location)
    return usage


def material_usage_text(material, usage):
    locations = usage.get(material, [])
    if not locations:
        return "not assigned to target meshes"
    visible = locations[:3]
    suffix = f", +{len(locations) - len(visible)} more" if len(locations) > len(visible) else ""
    return ", ".join(visible) + suffix


def remember_name(datablock):
    if BACKUP_PROP not in datablock:
        datablock[BACKUP_PROP] = datablock.name


def remember_image_path(image):
    if BACKUP_FILEPATH_PROP not in image:
        image[BACKUP_FILEPATH_PROP] = image.filepath
    if BACKUP_FILEPATH_RAW_PROP not in image:
        image[BACKUP_FILEPATH_RAW_PROP] = image.filepath_raw


def unique_name(collection, desired, datablock=None):
    if datablock and datablock.name == desired:
        return desired
    existing = {item.name for item in collection if item is not datablock}
    if desired not in existing:
        return desired
    index = 1
    while True:
        candidate = f"{desired}_{index:02d}"
        if candidate not in existing:
            return candidate
        index += 1


def restore_name(datablock, collection):
    original = datablock.get(BACKUP_PROP)
    if not original:
        return False
    datablock.name = unique_name(collection, original, datablock)
    del datablock[BACKUP_PROP]
    return True


def restore_image_path(image):
    restored = False
    if BACKUP_FILEPATH_PROP in image:
        image.filepath = image[BACKUP_FILEPATH_PROP]
        del image[BACKUP_FILEPATH_PROP]
        restored = True
    if BACKUP_FILEPATH_RAW_PROP in image:
        image.filepath_raw = image[BACKUP_FILEPATH_RAW_PROP]
        del image[BACKUP_FILEPATH_RAW_PROP]
        restored = True
    return restored


def reset_previous_prepare(materials, images):
    for mat in materials:
        restore_name(mat, bpy.data.materials)
    for image in images:
        restore_image_path(image)
        restore_name(image, bpy.data.images)


def image_suffix(image):
    raw_path = (image.filepath_raw or image.filepath or "").replace("\\", "/")
    suffix = Path(raw_path).suffix
    if suffix:
        return suffix

    file_format = getattr(image, "file_format", "").upper()
    if file_format in {"JPEG", "JPG"}:
        return ".jpg"
    if file_format == "PNG":
        return ".png"
    if file_format == "TARGA":
        return ".tga"
    if file_format == "TIFF":
        return ".tif"
    if file_format == "OPEN_EXR":
        return ".exr"
    return ".png"


def resolve_export_dir(path_value):
    if path_value.strip():
        return Path(bpy.path.abspath(path_value)).resolve()
    if bpy.data.filepath:
        return Path(bpy.data.filepath).resolve().parent / "texture"
    return Path(bpy.app.tempdir).resolve() / "texture"


def find_image_node_from_socket(socket, visited=None):
    if visited is None:
        visited = set()
    if not socket or not socket.is_linked:
        return None

    for link in socket.links:
        node = link.from_node
        if node in visited:
            continue
        visited.add(node)
        if node.type == "TEX_IMAGE" and node.image and not node.image.library:
            return node
        for input_socket in getattr(node, "inputs", []):
            found = find_image_node_from_socket(input_socket, visited)
            if found:
                return found
    return None


def fallback_role_from_node(node):
    text = clean_token(f"{node.label}_{node.name}").lower()
    if "base" in text or "albedo" in text or "diffuse" in text or "color" in text:
        return "BaseColor"
    if "extra" in text:
        return "MetallicRoughness"
    if "metal" in text and ("rough" in text or "orm" in text or "mra" in text):
        return "MetallicRoughness"
    if "rough" in text:
        return "Roughness"
    if "metal" in text:
        return "Metallic"
    if "normal" in text or "nrm" in text:
        return "Normal"
    if "emiss" in text:
        return "Emissive"
    if "height" in text or "displace" in text:
        return "Height"
    if "ao" in text or "occlusion" in text:
        return "Occlusion"
    return "Texture"


def material_texture_map(materials):
    mapping = {}
    for mat in materials:
        textures = {}
        if not mat.node_tree:
            mapping[mat] = textures
            continue

        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                for input_name, role in ROLE_BY_BSDF_INPUT.items():
                    socket = node.inputs.get(input_name)
                    image_node = find_image_node_from_socket(socket)
                    if image_node and role not in textures:
                        textures[role] = image_node.image

        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image and not node.image.library:
                role = fallback_role_from_node(node)
                textures.setdefault(role, node.image)

        if (
            textures.get("Metallic")
            and textures.get("Roughness")
            and textures["Metallic"] == textures["Roughness"]
        ):
            textures["MetallicRoughness"] = textures["Metallic"]
            del textures["Metallic"]
            del textures["Roughness"]

        used_images = set()
        deduped = {}
        for role in ROLE_PRIORITY:
            image = textures.get(role)
            if image and image.name not in used_images:
                deduped[role] = image
                used_images.add(image.name)
        for role, image in textures.items():
            if image and image.name not in used_images:
                deduped[role] = image
                used_images.add(image.name)
        textures = deduped

        mapping[mat] = textures
    return mapping


def ordered_unique_images(texture_map):
    images = []
    seen = set()
    for textures in texture_map.values():
        for role in ROLE_PRIORITY:
            image = textures.get(role)
            if image and image.name not in seen:
                images.append(image)
                seen.add(image.name)
        for image in textures.values():
            if image and image.name not in seen:
                images.append(image)
                seen.add(image.name)
    return images


def role_counts(texture_map):
    counts = {}
    for textures in texture_map.values():
        for role, image in textures.items():
            counts[role] = counts.get(role, 0) + 1
    return counts


def image_role_lookup(texture_map):
    lookup = {}
    for textures in texture_map.values():
        for role, image in textures.items():
            lookup.setdefault(image, role)
    return lookup


def image_material_role_lookup(texture_map):
    lookup = {}
    for material, textures in texture_map.items():
        for role, image in textures.items():
            lookup.setdefault(image, (material, role))
    return lookup


def image_is_writable(image):
    """True when the image can be written to PNG: it has pixels in memory, or its
    filepath points to a real file on disk we can copy. Used as a pre-flight check so
    the External Textures workflow doesn't rename half the datablocks and then abort
    on an empty image (e.g. an unrendered bake target, or a Painter texture that
    hasn't been exported yet)."""
    if image.has_data:
        return True
    source_value = image.filepath_raw or image.filepath
    if not source_value:
        return False
    return Path(bpy.path.abspath(source_value)).is_file()


def image_disk_path(image):
    source_value = image.filepath_raw or image.filepath
    if not source_value:
        return None
    return Path(bpy.path.abspath(source_value)).resolve()


def write_or_copy_image_file(image, new_name, export_dir):
    export_dir.mkdir(parents=True, exist_ok=True)
    target = export_dir / f"{new_name}.png"
    old_filepath = image.filepath
    old_filepath_raw = image.filepath_raw
    old_format = image.file_format
    source_value = old_filepath_raw or old_filepath
    source = Path(bpy.path.abspath(source_value)).resolve() if source_value else None
    if not image.has_data and source is not None and source.is_file():
        if source != target.resolve():
            shutil.copy2(source, target)
        image.filepath = str(target)
        image.filepath_raw = str(target)
        image.file_format = "PNG"
        return target
    try:
        image.filepath = str(target)
        image.filepath_raw = str(target)
        image.file_format = "PNG"
        image.save()
    except RuntimeError as error:
        image.filepath = old_filepath
        image.filepath_raw = old_filepath_raw
        image.file_format = old_format
        if source is not None and source.is_file() and "image data" in str(error).lower():
            if source != target.resolve():
                shutil.copy2(source, target)
            image.filepath = str(target)
            image.filepath_raw = str(target)
            image.file_format = "PNG"
            return target
        raise
    image.file_format = "PNG"
    return target


def cleanup_export_files(export_dir, prefix, preserve_paths=None):
    if not export_dir.exists():
        return
    preserve_paths = {
        Path(path).resolve()
        for path in (preserve_paths or ())
    }
    for path in export_dir.glob(f"T_{prefix}_*"):
        if path.is_file() and path.resolve() not in preserve_paths:
            path.unlink()


def material_name_for(prefix, index, material_count):
    return f"M_{prefix}" if material_count == 1 else f"M_{prefix}_{index + 1:02d}"


def mesh_name_for(prefix, index, object_count):
    return prefix if object_count == 1 else f"{prefix}_{index + 1:02d}"


def external_workflow_preview_rows(context, props, objects):
    prefix = asset_prefix(context, props.prefix_mode, props.custom_prefix)
    rows = []
    units = export_naming_units(context, props.scope, objects)
    for unit_index, unit in enumerate(units):
        unit_name = mesh_name_for(prefix, unit_index, len(units))
        root = unit["root"]
        meshes = unit["meshes"]
        if root.type == "EMPTY":
            for child_index, mesh in enumerate(meshes, 1):
                rows.append(
                    {
                        "object": mesh.name,
                        "group": root.name,
                        "planned": f"{unit_name}_{child_index:02d}",
                    }
                )
        else:
            rows.append(
                {
                    "object": root.name,
                    "group": "",
                    "planned": unit_name,
                }
            )
    return rows


def top_empty_parent(obj, scope_objects):
    """Highest Empty ancestor that is still inside the naming scope."""
    top = None
    parent = obj.parent
    while parent is not None and parent in scope_objects:
        if parent.type != "EMPTY":
            break
        top = parent
        parent = parent.parent
    return top


def export_naming_units(context, scope, objects):
    """Build ordered top-level naming units from the requested scope."""
    object_set = set(objects)
    if scope == "EXPORT_COLLECTION":
        collection = export_collection(context)
        scope_order = list(collection.all_objects) if collection else []
    elif scope == "SELECTED":
        scope_order = list(context.selected_objects)
    else:
        scope_order = list(context.scene.objects)
    scope_objects = set(scope_order)

    units_by_root = {}
    for mesh in objects:
        root = top_empty_parent(mesh, scope_objects) or mesh
        unit = units_by_root.setdefault(root, {"root": root, "meshes": []})
        unit["meshes"].append(mesh)

    ordered_units = []
    seen_roots = set()
    for obj in scope_order:
        for root, unit in units_by_root.items():
            if root in seen_roots:
                continue
            if obj is root or obj in unit["meshes"]:
                ordered_units.append(unit)
                seen_roots.add(root)
    for root, unit in units_by_root.items():
        if root not in seen_roots:
            ordered_units.append(unit)

    for unit in ordered_units:
        unit["meshes"].sort(
            key=lambda mesh: scope_order.index(mesh)
            if mesh in scope_order else len(scope_order)
        )
    return ordered_units


def material_instance_name(material_name):
    clean_name = clean_token(material_name)
    if clean_name.startswith("M_"):
        return f"MI_{clean_name[2:]}"
    if clean_name.startswith("MI_"):
        return clean_name
    return f"MI_{clean_name}"


def texture_set_name(material):
    name = clean_token(material.name)
    return name[2:] if name.startswith("M_") else name


def texture_name_for(material, role, index, count):
    output_role = PAINTER_ROLE_NAMES.get(role, role)
    name = f"T_{texture_set_name(material)}_{output_role}"
    if count > 1:
        name = f"{name}_{index + 1:02d}"
    return name


def texture_name_for_material_name(material_name, role):
    output_role = PAINTER_ROLE_NAMES.get(role, role)
    clean_name = clean_token(material_name)
    texture_set = clean_name[2:] if clean_name.startswith("M_") else clean_name
    return f"T_{texture_set}_{output_role}"


def write_manifest(context, prefix, objects, materials, texture_map, export_dir):
    export_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "asset_prefix": prefix,
        "blend_file": bpy.data.filepath,
        "texture_folder": str(export_dir),
        "objects": [],
        "materials": [],
    }

    for obj in objects:
        manifest["objects"].append(
            {
                "name": obj.name,
                "material_slots": effective_material_names(obj),
            }
        )

    for mat in materials:
        textures = {}
        for role, image in texture_map.get(mat, {}).items():
            textures[role] = {
                "image_name": image.name,
                "file_path": bpy.path.abspath(image.filepath_raw or image.filepath),
            }
        manifest["materials"].append(
            {
                "material_name": mat.name,
                "original_material_name": mat.get(BACKUP_PROP, mat.name),
                "material_instance_name": material_instance_name(mat.name),
                "textures": textures,
            }
        )

    manifest_path = export_dir / f"{prefix}_ue_material_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    context.scene.ue_unique_names.last_manifest_path = str(manifest_path)
    return manifest_path


def first_slot_index_for_material(objects, material):
    for obj in objects:
        for slot_index, mat, _location in effective_material_slot_entries(obj):
            if mat == material:
                return slot_index
    return 0


def is_translucent_material(mat):
    """블렌더 머티리얼이 반투명인지 판정. EEVEE Next(4.2+)와 구버전 모두 대응.
    언리얼 파이프라인이 이 플래그를 보고 해당 메쉬의 Nanite 를 끈다."""
    # Blender 4.2+ (EEVEE Next): surface_render_method ∈ {'DITHERED', 'BLENDED'}
    method = getattr(mat, "surface_render_method", None)
    if method is not None:
        return method == "BLENDED"
    # 구버전: blend_method ∈ {'OPAQUE','CLIP','HASHED','BLEND'}
    legacy = getattr(mat, "blend_method", "OPAQUE")
    return legacy in {"BLEND", "HASHED"}


def surface_layer_param_for_role(role):
    return SURFACE_LAYER_PARAM_BY_ROLE.get(role, role)


def is_layerblend_material_name(material_name):
    return clean_token(material_name).lower().startswith("m_layerblend_")


def _texture_json_entry(role, image, param=None):
    source_path = bpy.path.abspath(image.filepath_raw or image.filepath).replace("\\", "/")
    entry = {
        "param": param or role,
        "asset_name": texture_asset_name_for_image(image, source_path),
        "file": source_path,
    }
    if param and param != role:
        entry["source_param"] = role
    return entry


def texture_asset_name_for_image(image, source_path=None):
    source_path = source_path or bpy.path.abspath(image.filepath_raw or image.filepath)
    stem = Path(source_path).stem if source_path else image.name
    clean_name = clean_token(stem)
    if clean_name.startswith("T_"):
        return clean_name
    return f"T_{clean_name}"


def _material_layer_json_entries(mat, texture_map):
    textures = []
    seen_params = set()
    for role, image in texture_map.get(mat, {}).items():
        param = surface_layer_param_for_role(role)
        if param in seen_params:
            continue
        seen_params.add(param)
        textures.append(_texture_json_entry(role, image, param=param))
    if not textures:
        return []
    return [
        {
            "name": "Base",
            "index": 0,
            "textures": textures,
        }
    ]


def master_preset_for_material(mat):
    name = clean_token(mat.name).lower()
    if is_layerblend_material_name(mat.name):
        return "layer"
    if "cloth" in name or "clothes" in name:
        return "cloth"
    return None


def _material_json_entry(mat, slot_index, texture_map):
    textures = []
    for role, image in texture_map.get(mat, {}).items():
        textures.append(_texture_json_entry(role, image))
    entry = {
        "name": mat.name,
        "slot_name": mat.name,
        "slot_index": slot_index,
        "translucent": is_translucent_material(mat),
        "textures": textures,
        "layers": _material_layer_json_entries(mat, texture_map),
    }
    master_preset = master_preset_for_material(mat)
    if master_preset:
        entry["master_preset"] = master_preset
    return entry


def unique_ordered_names(values):
    names = []
    seen = set()
    for value in values:
        if not value or value in seen:
            continue
        names.append(value)
        seen.add(value)
    return names


def armature_names_for_object(obj):
    names = []
    for parent in parent_chain(obj):
        if parent.type == "ARMATURE":
            names.append(parent.name)
    for modifier in obj.modifiers:
        if modifier.type != "ARMATURE":
            continue
        target = getattr(modifier, "object", None)
        names.append(target.name if target else modifier.name)
    return unique_ordered_names(names)


def texture_roles_for_materials(materials, texture_map):
    roles = []
    for material in materials:
        roles.extend(texture_map.get(material, {}).keys())
    return unique_ordered_names(roles)


def transfer_source_for_object(obj):
    if not obj or not hasattr(obj, "vdt_object_props"):
        return None
    return getattr(obj.vdt_object_props, "transfer_source", None)


def transfer_source_name_for_object(obj):
    source = transfer_source_for_object(obj)
    return source.name if source else ""


def transfer_shape_keys_enabled(obj):
    return bool(getattr(obj, "ue_unique_transfer_shape_keys", False))


def transfer_weights_enabled(obj):
    return bool(getattr(obj, "ue_unique_transfer_weights", False))


def transfer_check_label(enabled):
    return "Yes" if enabled else "-"


def export_kind_for_object(obj, painter_low=False):
    if is_hair_tool_object(obj):
        return "Hair"
    if painter_low:
        return "Low"
    return "Mesh"


def export_action_for_object(obj, painter_low=False):
    if is_hair_tool_object(obj):
        return "Hair bake"
    if painter_low:
        return "Painter low"
    return "Mesh export"


def export_detail_lines_for_object(obj):
    if not is_hair_tool_object(obj):
        return []
    return [
        "Hair Tool source: Send2UE bakes this object to a temporary mesh before FBX export.",
        "Generated mesh: evaluated cards are joined per export asset, UVMapGN becomes UVMap, and RSAO is packed from Random/SystemColor/AO.",
        "Rigging: the export mesh gets the detected head bone vertex group at weight 1.0 plus an Armature modifier.",
        "Transfer: Shape Keys / Weights are read from the JSON sidecar before Send2UE exports the baked mesh.",
    ]


def transfer_postprocess_entry(obj):
    source_name = transfer_source_name_for_object(obj)
    shape_keys = transfer_shape_keys_enabled(obj)
    weights = transfer_weights_enabled(obj)
    return {
        "target": obj.name,
        "source": source_name,
        "shape_keys": shape_keys,
        "weights": weights,
        "enabled": bool(source_name and (shape_keys or weights)),
    }


def export_validation_rows(context, props=None, objects=None, materials=None, texture_map=None):
    if props is None:
        props = context.scene.ue_unique_names
    objects = list(objects) if objects is not None else validation_scope_objects(context, props.scope)
    materials = list(materials) if materials is not None else unreal_handoff_materials_from_objects(objects)
    if texture_map is None:
        texture_map = material_texture_map(materials)

    protected = protected_painter_data(context)
    painter_low_objects = linked_painter_low_objects(context)
    export_coll = export_collection(context)
    export_objects = set(export_coll.all_objects) if export_coll else set()
    rows = []

    for obj in objects:
        material_slots = effective_material_slot_entries(obj)
        handoff_materials = [
            mat
            for _slot_index, mat, _location in material_slots
            if mat and is_unreal_handoff_material(mat)
        ]
        empty_slot_count = len(material_slots) - len(
            [mat for _slot_index, mat, _location in material_slots if mat]
        )
        parent_names = [parent.name for parent in parent_chain(obj)]
        armatures = armature_names_for_object(obj)
        texture_roles = texture_roles_for_materials(handoff_materials, texture_map)
        unit_root = top_empty_parent(obj, export_objects) or obj
        painter_low = obj in painter_low_objects
        errors = []
        warnings = []

        if painter_low:
            pass
        elif not material_slots:
            errors.append("No material slots")
        elif not handoff_materials and not is_hair_tool_object(obj):
            warnings.append("No UE handoff material")
        if empty_slot_count and not painter_low and not has_gpro_instance_material_source(obj):
            errors.append(f"{empty_slot_count} empty material slot")

        if clean_token(obj.name) != obj.name:
            errors.append(f"JSON name becomes {clean_token(obj.name)}")

        if (transfer_shape_keys_enabled(obj) or transfer_weights_enabled(obj)) and not transfer_source_for_object(obj):
            warnings.append("Transfer source not set")

        for material in handoff_materials:
            if clean_token(material.name) != material.name:
                errors.append(f"Material renames to {clean_token(material.name)}")
            if not clean_token(material.name).startswith("M_"):
                errors.append(f"{material.name} has no M_ prefix")
            textures = texture_map.get(material, {})
            if not textures:
                continue
            for role, image in textures.items():
                source_value = image.filepath_raw or image.filepath
                if not source_value:
                    errors.append(f"{image.name} ({role}) has no file path")
                    continue
                if not Path(bpy.path.abspath(source_value)).is_file():
                    errors.append(f"{image.name} ({role}) file missing")

        status = "ERROR" if errors else "WARN" if warnings else "OK"
        rows.append(
            {
                "object_name": obj.name,
                "mesh_data_name": obj.data.name if obj.data else "",
                "asset_unit": unit_root.name,
                "json_name": clean_token(unit_root.name),
                "parent_chain": parent_names,
                "armatures": armatures,
                "transfer_source": transfer_source_name_for_object(obj),
                "transfer_shape_keys": transfer_shape_keys_enabled(obj),
                "transfer_weights": transfer_weights_enabled(obj),
                "export_kind": export_kind_for_object(obj, painter_low=painter_low),
                "export_action": export_action_for_object(obj, painter_low=painter_low),
                "export_details": export_detail_lines_for_object(obj),
                "material_slots": [
                    mat.name if mat else ""
                    for _slot_index, mat, _location in material_slots
                ],
                "handoff_materials": [material.name for material in handoff_materials],
                "texture_roles": texture_roles,
                "painter_protected": obj in protected["objects"],
                "painter_low": painter_low,
                "status": status,
                "errors": unique_ordered_names(errors),
                "warnings": unique_ordered_names(warnings),
                "json_ready": status != "ERROR",
            }
        )
    return rows


def validation_summary(rows):
    counts = {"OK": 0, "WARN": 0, "ERROR": 0}
    for row in rows:
        counts[row["status"]] += 1
    return counts


def validation_pipeline_summary(rows):
    return {
        "low": sum(1 for row in rows if row.get("painter_low")),
        "hair": sum(1 for row in rows if row.get("export_kind") == "Hair"),
        "transfer": sum(
            1
            for row in rows
            if row.get("transfer_source")
            and (row.get("transfer_shape_keys") or row.get("transfer_weights"))
        ),
    }


def validation_icon(status):
    if status == "OK":
        return "CHECKMARK"
    if status == "WARN":
        return "ERROR"
    return "CANCEL"


def compact_list_label(values, empty="-", limit=3):
    values = [str(value) for value in values if value]
    if not values:
        return empty
    visible = values[:limit]
    suffix = f" +{len(values) - limit}" if len(values) > limit else ""
    return ", ".join(visible) + suffix


def validation_expanded_names(props):
    return {
        name
        for name in props.validation_expanded_rows.splitlines()
        if name
    }


def set_validation_expanded_names(props, names):
    props.validation_expanded_rows = "\n".join(sorted(names))


def compact_name(value, limit=26):
    value = str(value or "")
    if len(value) <= limit:
        return value
    head = max(8, limit - 11)
    tail = 8
    return f"{value[:head]}...{value[-tail:]}"


def mesh_summary_name(value, grouped=False):
    value = str(value or "")
    tokens = [token for token in value.split("_") if token]
    if grouped and len(tokens) > 3:
        return "_".join(tokens[-3:])
    if len(value) <= 24:
        return value
    if len(tokens) > 3:
        return "_".join(tokens[-3:])
    return compact_name(value, limit=24)


def wrapped_text_lines(text, width=54):
    text = str(text or "")
    if len(text) <= width:
        return [text]
    lines = []
    remaining = text
    while len(remaining) > width:
        split_at = remaining.rfind(" ", 0, width + 1)
        if split_at < width // 2:
            split_at = width
        lines.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        lines.append(remaining)
    return lines


def draw_wrapped_label(layout, text, icon="NONE", width=54):
    for index, line in enumerate(wrapped_text_lines(text, width)):
        if index == 0 and icon != "NONE":
            layout.label(text=line, icon=icon)
        else:
            layout.label(text=line)


def fixed_table_column(row, ui_units):
    column = row.column(align=True)
    try:
        column.ui_units_x = ui_units
    except AttributeError:
        pass
    return column


VALIDATION_SPREADSHEET_OBJECT = "_UEUN_Export_Validation_Table"
VALIDATION_SPREADSHEET_MESH = "_UEUN_Export_Validation_Table_Mesh"


def validation_spreadsheet_rows(context, props):
    objects = validation_scope_objects(context, props.scope)
    materials = unreal_handoff_materials_from_objects(objects)
    texture_map = material_texture_map(materials)
    rows = export_validation_rows(
        context,
        props=props,
        objects=objects,
        materials=materials,
        texture_map=texture_map,
    )
    table_rows = []
    for group in grouped_validation_rows(rows):
        for row_data in group["rows"]:
            table_rows.append(
                {
                    "Status": row_data["status"],
                    "Group": row_data["asset_unit"] or "-",
                    "Kind": row_data["export_kind"],
                    "Export_Action": row_data["export_action"],
                    "Mesh_Object": row_data["object_name"],
                    "Rig": compact_list_label(row_data["armatures"], limit=2),
                    "Transfer_Source": row_data["transfer_source"] or "-",
                    "Shape_Keys": transfer_check_label(row_data["transfer_shape_keys"]),
                    "Weights": transfer_check_label(row_data["transfer_weights"]),
                    "Materials": compact_list_label(row_data["handoff_materials"], limit=2),
                    "Textures": compact_list_label(row_data["texture_roles"], limit=4),
                    "JSON": "Ready" if row_data["json_ready"] else "Blocked",
                    "JSON_Name": row_data["json_name"] or "-",
                    "Issues": str(len(row_data["errors"]) + len(row_data["warnings"])),
                    "Errors": " | ".join(row_data["errors"]) or "-",
                    "Warnings": " | ".join(row_data["warnings"]) or "-",
                }
            )
    return table_rows


def set_string_point_attribute(mesh, name, values):
    attr = mesh.attributes.new(name, "STRING", "POINT")
    for index, value in enumerate(values):
        attr.data[index].value = str(value or "-").encode("utf-8")


def create_validation_spreadsheet_object(context, props):
    old_object = bpy.data.objects.get(VALIDATION_SPREADSHEET_OBJECT)
    if old_object is not None:
        bpy.data.objects.remove(old_object, do_unlink=True)
    old_mesh = bpy.data.meshes.get(VALIDATION_SPREADSHEET_MESH)
    if old_mesh is not None:
        bpy.data.meshes.remove(old_mesh)

    rows = validation_spreadsheet_rows(context, props)
    if not rows:
        rows = [
            {
                "Status": "-",
                "Group": "-",
                "Kind": "-",
                "Export_Action": "-",
                "Mesh_Object": "No export targets to validate.",
                "Rig": "-",
                "Transfer_Source": "-",
                "Shape_Keys": "-",
                "Weights": "-",
                "Materials": "-",
                "Textures": "-",
                "JSON": "-",
                "JSON_Name": "-",
                "Issues": "0",
                "Errors": "-",
                "Warnings": "-",
            }
        ]

    mesh = bpy.data.meshes.new(VALIDATION_SPREADSHEET_MESH)
    vertices = [(float(index), 0.0, 0.0) for index in range(len(rows))]
    mesh.from_pydata(vertices, [], [])
    mesh.update()
    for column_name in rows[0].keys():
        set_string_point_attribute(
            mesh,
            column_name,
            [row.get(column_name, "-") for row in rows],
        )

    obj = bpy.data.objects.new(VALIDATION_SPREADSHEET_OBJECT, mesh)
    obj.hide_render = True
    obj.show_name = True
    obj["_ue_unique_validation_table"] = True
    context.scene.collection.objects.link(obj)
    return obj


def spreadsheet_window_area(window):
    screen = getattr(window, "screen", None)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type in {"VIEW_3D", "SPREADSHEET"}:
            return area
    return screen.areas[0] if screen.areas else None


def open_validation_spreadsheet_window(context, operator):
    props = context.scene.ue_unique_names
    obj = create_validation_spreadsheet_object(context, props)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    window_manager = context.window_manager
    existing_windows = list(window_manager.windows)
    result = bpy.ops.wm.window_new()
    if "CANCELLED" in result:
        operator.report({"ERROR"}, "Could not open a new Blender window.")
        return {"CANCELLED"}

    new_window = next(
        (window for window in window_manager.windows if window not in existing_windows),
        context.window,
    )
    area = spreadsheet_window_area(new_window)
    if area is None:
        operator.report({"ERROR"}, "Could not find an editor area for the validation table.")
        return {"CANCELLED"}

    with context.temp_override(
        window=new_window,
        screen=new_window.screen,
        area=area,
    ):
        area.ui_type = "SPREADSHEET"
        obj.select_set(True)
        context.view_layer.objects.active = obj

    operator.report({"INFO"}, "Opened Export Validation Table in Blender Spreadsheet.")
    return {"FINISHED"}


def draw_validation_wide_header(layout):
    header = layout.row(align=True)
    fixed_table_column(header, 1.1).label(text="")
    fixed_table_column(header, 4.5).label(text="Status")
    fixed_table_column(header, 22.0).label(text="Mesh Object")
    fixed_table_column(header, 10.0).label(text="Export")
    fixed_table_column(header, 5.5).label(text="Rig")
    fixed_table_column(header, 7.0).label(text="Mat")
    fixed_table_column(header, 5.0).label(text="Tex")
    fixed_table_column(header, 3.8).label(text="JSON")


def draw_validation_wide_row(layout, row_data, has_group_header, expanded):
    row = layout.row(align=True)
    toggle_column = fixed_table_column(row, 1.1)
    toggle = toggle_column.operator(
        "ue_unique_names.toggle_validation_detail",
        text="",
        icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
        emboss=False,
    )
    toggle.object_name = row_data["object_name"]
    fixed_table_column(row, 4.5).label(
        text=row_data["status"],
        icon=validation_icon(row_data["status"]),
    )
    mesh_text = (
        f"  {row_data['object_name']}"
        if has_group_header else row_data["object_name"]
    )
    fixed_table_column(row, 22.0).label(text=mesh_text, icon="OUTLINER_OB_MESH")
    fixed_table_column(row, 10.0).label(
        text=row_data["export_action"],
        icon="INFO" if row_data["export_kind"] == "Hair" else "CHECKMARK",
    )
    fixed_table_column(row, 5.5).label(
        text=compact_list_label(row_data["armatures"], limit=2),
        icon="OUTLINER_OB_ARMATURE" if row_data["armatures"] else "BLANK1",
    )
    fixed_table_column(row, 7.0).label(
        text=compact_list_label(row_data["handoff_materials"], limit=2),
        icon="MATERIAL",
    )
    fixed_table_column(row, 5.0).label(
        text=compact_list_label(row_data["texture_roles"], limit=4),
        icon="TEXTURE",
    )
    fixed_table_column(row, 3.8).label(
        text="Ready" if row_data["json_ready"] else "Blocked",
        icon="CHECKMARK" if row_data["json_ready"] else "CANCEL",
    )
    if expanded:
        draw_validation_detail(layout, row_data)


def draw_validation_detail(layout, row_data):
    detail = layout.box()
    draw_wrapped_label(
        detail,
        f"Mesh: {row_data['object_name']}",
        icon="OUTLINER_OB_MESH",
    )
    draw_wrapped_label(detail, f"Export Group: {row_data['asset_unit']}")
    draw_wrapped_label(detail, f"JSON Name: {row_data['json_name']}")
    draw_wrapped_label(
        detail,
        "Parent Chain: "
        + (" > ".join(row_data["parent_chain"]) if row_data["parent_chain"] else "-"),
    )
    draw_wrapped_label(detail, "Armature: " + compact_list_label(row_data["armatures"]))
    draw_wrapped_label(
        detail,
        "Materials: " + compact_list_label(row_data["handoff_materials"], limit=6),
    )
    draw_wrapped_label(
        detail,
        "Texture Roles: " + compact_list_label(row_data["texture_roles"], limit=6),
    )
    draw_wrapped_label(detail, f"Export Action: {row_data['export_action']}")
    for line in row_data["export_details"]:
        draw_wrapped_label(detail, "  " + line, icon="INFO")
    if row_data["transfer_shape_keys"] or row_data["transfer_weights"] or row_data["transfer_source"]:
        transfer_bits = []
        if row_data["transfer_shape_keys"]:
            transfer_bits.append("Shape Keys")
        if row_data["transfer_weights"]:
            transfer_bits.append("Weights")
        draw_wrapped_label(
            detail,
            "Transfer Source: "
            + (row_data["transfer_source"] or "-")
            + " / "
            + compact_list_label(transfer_bits),
            icon="MOD_DATA_TRANSFER",
        )
    source = []
    if row_data["painter_low"]:
        source.append("Painter Low")
    if row_data["painter_protected"]:
        source.append("Painter Protected")
    draw_wrapped_label(detail, "Source: " + compact_list_label(source))
    if row_data["errors"]:
        detail.label(text="Errors", icon="CANCEL")
        for error in row_data["errors"]:
            draw_wrapped_label(detail, "  " + error)
    if row_data["warnings"]:
        detail.label(text="Warnings", icon="ERROR")
        for warning in row_data["warnings"]:
            draw_wrapped_label(detail, "  " + warning)


def grouped_validation_rows(rows):
    groups = []
    index_by_unit = {}
    for row in rows:
        unit = row["asset_unit"]
        if unit not in index_by_unit:
            index_by_unit[unit] = len(groups)
            groups.append({"unit": unit, "rows": []})
        groups[index_by_unit[unit]]["rows"].append(row)
    return groups


def validation_group_needs_header(group):
    rows = group["rows"]
    return len(rows) > 1 or any(row["object_name"] != group["unit"] for row in rows)


def _write_pipeline_sidecar(
    json_dir,
    mesh_name,
    prefix,
    material_entries,
    validation=None,
    validation_children=None,
    transfer_source=None,
    transfer_sources=None,
):
    data = {
        "schema_version": 2,
        "material_pipeline": "surface_layers",
        "material_master": "prop",
        "mesh_name": mesh_name,
        "asset_prefix": prefix,
        "materials": material_entries,
    }
    if validation is not None:
        data["validation"] = validation
    if validation_children:
        data["validation_children"] = validation_children
    if transfer_source is not None:
        data["transfer_source"] = transfer_source
    if transfer_sources:
        data["transfer_sources"] = transfer_sources
    json_path = json_dir / f"{mesh_name}.json"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path


def write_unreal_pipeline_json(
    context,
    prefix,
    objects,
    materials,
    texture_map,
    json_dir,
    combined_only=False,
):
    json_dir.mkdir(parents=True, exist_ok=True)
    json_paths = []
    object_names = {clean_token(obj.name) for obj in objects}
    validation_rows = export_validation_rows(
        context,
        objects=objects,
        materials=materials,
        texture_map=texture_map,
    )
    validation_by_object_name = {
        row["object_name"]: row
        for row in validation_rows
    }

    if not combined_only:
        # Per standalone mesh-object sidecar. Child meshes under an Empty export
        # as the Empty/group asset, so their contract lives in the Empty JSON.
        for obj in objects:
            if obj.parent and obj.parent.type == "EMPTY":
                continue
            mesh_name = clean_token(obj.name)
            entries = []
            seen_materials = set()
            for slot_index, mat, _location in effective_material_slot_entries(obj):
                if (
                    not mat
                    or not is_unreal_handoff_material(mat)
                    or mat in seen_materials
                ):
                    continue
                seen_materials.add(mat)
                entries.append(_material_json_entry(mat, slot_index, texture_map))
            json_paths.append(
                _write_pipeline_sidecar(
                    json_dir,
                    mesh_name,
                    prefix,
                    entries,
                    validation=validation_by_object_name.get(obj.name),
                    transfer_source=transfer_postprocess_entry(obj),
                )
            )

    # 2) Per EMPTY-parent sidecar. Send to Unreal "Combine > Child meshes" merges an empty's child
    #    meshes into ONE asset named after the empty (combine_assets.py pre_mesh_export), so without
    #    this the combined mesh has no matching <name>.json and its materials break. We aggregate all
    #    child materials (unique, first-encounter order). The Unreal side matches slots by material
    #    name, so slot_index here is only a best-effort hint for the combined slot order.
    children_by_empty = {}
    empties_in_order = []
    for obj in objects:
        parent = obj.parent
        if parent and parent.type == "EMPTY":
            if parent.name not in children_by_empty:
                children_by_empty[parent.name] = []
                empties_in_order.append(parent)
            children_by_empty[parent.name].append(obj)

    for empty in empties_in_order:
        empty_name = clean_token(empty.name)
        # If a mesh object already owns this name, its per-object sidecar wins — don't clobber it.
        if empty_name in object_names:
            continue
        entries = []
        seen_materials = set()
        slot_index = 0
        for obj in children_by_empty[empty.name]:
            for _source_slot_index, mat, _location in effective_material_slot_entries(obj):
                if (
                    not mat
                    or not is_unreal_handoff_material(mat)
                    or mat in seen_materials
                ):
                    continue
                seen_materials.add(mat)
                entries.append(_material_json_entry(mat, slot_index, texture_map))
                slot_index += 1
        if entries:
            child_validation = [
                validation_by_object_name[obj.name]
                for obj in children_by_empty[empty.name]
                if obj.name in validation_by_object_name
            ]
            json_paths.append(
                _write_pipeline_sidecar(
                    json_dir,
                    empty_name,
                    prefix,
                    entries,
                    validation_children=child_validation,
                    transfer_sources=[
                        transfer_postprocess_entry(obj)
                        for obj in children_by_empty[empty.name]
                    ],
                )
            )

    if json_paths:
        cleanup_stale_pipeline_sidecars(json_dir, objects, json_paths)
        context.scene.ue_unique_names.last_pipeline_json_path = str(json_paths[-1])
    return json_paths


def cleanup_stale_pipeline_sidecars(json_dir, objects, keep_paths):
    keep = {Path(path).resolve() for path in keep_paths}
    candidate_names = {clean_token(obj.name) for obj in objects}
    for obj in objects:
        parent = obj.parent
        if parent and parent.type == "EMPTY":
            candidate_names.add(clean_token(parent.name))
    for name in candidate_names:
        path = (json_dir / f"{name}.json").resolve()
        if path in keep or not path.exists():
            continue
        try:
            path.unlink()
        except OSError:
            pass


def _json_target_names(objects, combined_only=False):
    names = []
    if not combined_only:
        names.extend(
            obj.name
            for obj in objects
            if not (obj.parent and obj.parent.type == "EMPTY")
        )

    object_names = {clean_token(obj.name) for obj in objects}
    children_by_empty = {}
    empties_in_order = []
    for obj in objects:
        parent = obj.parent
        if parent and parent.type == "EMPTY":
            if parent.name not in children_by_empty:
                children_by_empty[parent.name] = []
                empties_in_order.append(parent)
            children_by_empty[parent.name].append(obj)

    for empty in empties_in_order:
        if clean_token(empty.name) in object_names:
            continue
        names.append(empty.name)
    return names


def _validate_clean_name(label, name, errors):
    clean = clean_token(name)
    if clean != name:
        errors.append(f"{label} '{name}' would be written as '{clean}'. Rename it explicitly first.")


def _json_refresh_validation_errors(context, props, objects, materials, texture_map):
    errors = []
    material_usage = material_usage_lookup(objects)
    if props.scope == "EXPORT_COLLECTION" and export_collection(context) is None:
        errors.append("Export collection does not exist.")
    if not objects:
        errors.append("No export objects in the selected JSON scope.")
        return errors
    if not materials:
        errors.append("No materials found in the selected JSON scope.")

    target_names = _json_target_names(objects)
    for name in target_names:
        _validate_clean_name("JSON target", name, errors)
    duplicated_targets = sorted(
        name for name in set(target_names) if target_names.count(name) > 1
    )
    if duplicated_targets:
        errors.append("Duplicate JSON target names: " + ", ".join(duplicated_targets))

    for obj in objects:
        handoff_slots = [
            (slot_index, mat)
            for slot_index, mat, _location in effective_material_slot_entries(obj)
            if mat and is_unreal_handoff_material(mat)
        ]
        effective_slots = effective_material_slot_entries(obj)
        if not effective_slots:
            errors.append(f"Mesh '{obj.name}' has no material slots.")
            continue
        if not handoff_slots:
            continue
        for slot_index, mat, _location in effective_slots:
            if mat is None and not has_gpro_instance_material_source(obj):
                errors.append(f"Mesh '{obj.name}' slot {slot_index} has no material.")

    for material in materials:
        usage = material_usage_text(material, material_usage)
        _validate_clean_name("Material", material.name, errors)
        if not clean_token(material.name).startswith("M_"):
            errors.append(
                f"Material '{material.name}' must use the M_ prefix. Used by: {usage}."
            )

        textures = texture_map.get(material, {})
        if not textures:
            # Texture-less handoff materials are valid: Unreal can still create
            # and assign a material instance, leaving texture parameters empty.
            continue

        for role, image in textures.items():
            source_value = image.filepath_raw or image.filepath
            if not source_value:
                errors.append(
                    f"Texture '{image.name}' ({role}) has no file path. "
                    f"Material: {material.name}. Used by: {usage}."
                )
                continue
            source_path = Path(bpy.path.abspath(source_value))
            if not source_path.is_file():
                errors.append(
                    f"Missing texture file: {image.name} ({role}). "
                    f"Material: {material.name}. Used by: {usage}. Path: {source_path}"
                )
    return errors


def _report_validation_errors(operator, errors):
    for error in errors:
        print(f"[UE Unique Names] Unreal handoff validation: {error}")
    first = errors[0] if errors else "Unknown validation error."
    if len(errors) == 1:
        operator.report({"ERROR"}, f"Unreal handoff blocked: {first}")
    else:
        operator.report(
            {"ERROR"},
            f"Unreal handoff blocked: {len(errors)} issues. First: {first}",
        )


def draw_export_validation_table(layout, context, props, objects, materials, texture_map):
    rows = export_validation_rows(
        context,
        props=props,
        objects=objects,
        materials=materials,
        texture_map=texture_map,
    )
    counts = validation_summary(rows)
    pipeline_counts = validation_pipeline_summary(rows)
    expanded_names = validation_expanded_names(props)

    table = layout.box()
    table.label(text="Export Validation Table", icon="VIEWZOOM")
    summary = table.row(align=True)
    summary.label(text=f"Targets {len(rows)}", icon="OUTLINER_OB_MESH")
    summary.label(text=f"OK {counts['OK']}", icon="CHECKMARK")
    summary.label(text=f"Warn {counts['WARN']}", icon="ERROR")
    summary.label(text=f"Err {counts['ERROR']}", icon="CANCEL")
    pipeline = table.row(align=True)
    pipeline.label(text=f"Low {pipeline_counts['low']}", icon="LINKED")
    pipeline.label(text=f"Hair bake {pipeline_counts['hair']}", icon="INFO")
    pipeline.label(text=f"Transfer {pipeline_counts['transfer']}", icon="MOD_DATA_TRANSFER")
    table.operator(
        "ue_unique_names.open_validation_sheet",
        text="Open Spreadsheet Window",
        icon="SPREADSHEET",
    )

    header = table.row(align=True)
    header.label(text="")
    header.label(text="Status")
    header.label(text="Mesh")
    header.label(text="Export")
    header.label(text="Rig")
    header.label(text="M")
    header.label(text="T")
    header.label(text="JSON")

    if not rows:
        table.label(text="No export targets to validate.", icon="ERROR")
        return

    for group in grouped_validation_rows(rows):
        has_group_header = validation_group_needs_header(group)
        if has_group_header:
            group_row = table.row(align=True)
            group_row.label(
                text=f"Empty/Group: {group['unit']} ({len(group['rows'])} meshes)",
                icon="EMPTY_AXIS",
            )

        for row_data in group["rows"]:
            expanded = row_data["object_name"] in expanded_names
            row = table.row(align=True)
            toggle = row.operator(
                "ue_unique_names.toggle_validation_detail",
                text="",
                icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
                emboss=False,
            )
            toggle.object_name = row_data["object_name"]
            row.label(
                text={
                    "OK": "OK",
                    "WARN": "WARN",
                    "ERROR": "ERR",
                }.get(row_data["status"], row_data["status"]),
                icon=validation_icon(row_data["status"]),
            )
            display_name = mesh_summary_name(
                row_data["object_name"],
                grouped=has_group_header,
            )
            mesh_text = f"  {display_name}" if has_group_header else display_name
            row.label(text=mesh_text, icon="OUTLINER_OB_MESH")
            row.label(
                text=row_data["export_action"],
                icon="INFO" if row_data["export_kind"] == "Hair" else "CHECKMARK",
            )
            row.label(
                text="Arm" if row_data["armatures"] else "-",
                icon="OUTLINER_OB_ARMATURE" if row_data["armatures"] else "BLANK1",
            )
            row.label(text=str(len(row_data["handoff_materials"])), icon="MATERIAL")
            row.label(text=str(len(row_data["texture_roles"])), icon="TEXTURE")
            row.label(
                text="Yes" if row_data["json_ready"] else "No",
                icon="CHECKMARK" if row_data["json_ready"] else "CANCEL",
            )
            if expanded:
                draw_validation_detail(table, row_data)


def draw_export_transfer_source(layout, context):
    box = layout.box()
    box.label(text="Export Transfer Source", icon="MOD_DATA_TRANSFER")
    obj = context.active_object

    if not hasattr(bpy.types.Object, "vdt_object_props"):
        box.label(text="Enable Vertex Data Tools to set transfer sources.", icon="INFO")
        return

    if not obj or obj.type not in {"MESH", "CURVES"}:
        box.label(text="Select an active mesh or curves object.", icon="INFO")
        return

    object_props = obj.vdt_object_props
    box.label(text=f"Target: {obj.name}")
    box.prop(object_props, "transfer_source", text="Source")

    source = object_props.transfer_source
    if source:
        box.label(text=f"Source set: {source.name}", icon="CHECKMARK")
    else:
        box.label(text="Source not set.", icon="ERROR")

    if obj.type in {"MESH", "CURVES"}:
        if hasattr(context.scene, "vdt_props"):
            box.prop(context.scene.vdt_props, "overwrite_shape_keys")

        row = box.row(align=True)
        row.prop(obj, "ue_unique_transfer_shape_keys", text="Shape Keys", toggle=True, icon="SHAPEKEY_DATA")
        row.prop(obj, "ue_unique_transfer_weights", text="Weights", toggle=True, icon="MOD_VERTEX_WEIGHT")
        if obj.type == "CURVES":
            box.label(text="Checked items apply to the generated export mesh.", icon="INFO")
        else:
            box.label(text="Checked items are written for Unreal postprocess.", icon="INFO")


def draw_external_workflow_preview(layout, context, props):
    objects, painter_objects, _protected = mutation_safe_mesh_objects(context, props.scope)
    rows = external_workflow_preview_rows(context, props, objects)
    total = len(objects) + len(painter_objects)
    layout.label(
        text=f"Classified meshes {total}",
        icon="INFO" if total else "ERROR",
    )
    layout.label(
        text=f"External targets {len(objects)}",
        icon="INFO" if objects else "ERROR",
    )
    if not objects:
        layout.label(text="No external mesh targets.", icon="ERROR")
        return

    preview = layout.column(align=True)
    preview.label(text="Mesh rename preview", icon="VIEWZOOM")
    for row_data in rows[:8]:
        draw_wrapped_label(preview, row_data["object"], icon="OUTLINER_OB_MESH", width=34)
        after = compact_name(row_data["planned"], limit=22)
        preview.label(text=f"  -> {after}", icon="CHECKMARK")
        if row_data.get("group"):
            preview.label(
                text=f"  in {compact_name(row_data['group'], limit=34)}",
                icon="EMPTY_AXIS",
            )
    if len(rows) > 8:
        preview.label(text=f"+{len(rows) - 8} more", icon="INFO")

    if painter_objects:
        painter = layout.column(align=True)
        painter.label(
            text=f"Substance/Painter data {len(painter_objects)}",
            icon="INFO",
        )
        for obj in painter_objects[:8]:
            draw_wrapped_label(painter, obj.name, icon="OUTLINER_OB_MESH", width=34)
            painter.label(text="  handled by the Substance low workflow")
        if len(painter_objects) > 8:
            painter.label(text=f"+{len(painter_objects) - 8} more", icon="INFO")


class UEUN_OT_prepare_names(bpy.types.Operator):
    bl_idname = "ue_unique_names.prepare"
    bl_label = "Prepare External Textures"
    bl_description = "Normalize external material and texture names, write files, and create Unreal JSON"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ue_unique_names
        prefix = asset_prefix(context, props.prefix_mode, props.custom_prefix)
        objects, skipped_objects, protected = mutation_safe_mesh_objects(
            context, props.scope
        )
        if not objects:
            if props.scope == "EXPORT_COLLECTION" and export_collection(context) is None:
                self.report(
                    {"WARNING"},
                    "'Export' 콜렉션을 찾지 못했습니다 — Scope가 Export Collection입니다 "
                    "(no 'Export' collection found; Scope = Export Collection).",
                )
            else:
                self.report({"WARNING"}, "대상 메쉬가 없습니다 (no mesh objects found).")
            return {"CANCELLED"}

        materials, skipped_materials = external_materials_from_objects(
            context, objects, protected
        )
        if not materials:
            self.report(
                {"WARNING"},
                "변경 가능한 머티리얼이 없습니다. Painter Low와 공유하는 "
                "머티리얼/이미지는 보호됩니다 (no safe external materials).",
            )
            return {"CANCELLED"}
        texture_map = material_texture_map(materials)
        images = ordered_unique_images(texture_map)
        reset_previous_prepare(materials, images)

        texture_map = material_texture_map(materials)
        images = ordered_unique_images(texture_map)
        image_context = image_material_role_lookup(texture_map)
        export_dir = resolve_export_dir(props.texture_export_dir)
        protected_paths = {
            path
            for image in protected["images"]
            for path in [image_disk_path(image)]
            if path is not None
        }

        # Pre-flight: when writing files, confirm every image CAN be written before
        # renaming anything. Otherwise an empty image (no pixels, no file on disk)
        # aborts mid-run and leaves materials/images half-renamed.
        if props.texture_handling == "WRITE_FILES":
            blockers = []
            for image in images:
                if image_is_writable(image):
                    continue
                material, _role = image_context.get(image, (None, None))
                origin = f"머티리얼 '{material.name}'" if material else "소속 머티리얼 불명"
                blockers.append(f"{image.name} ({origin})")
            if blockers:
                print("[UE Unique Names] 저장 불가 이미지 (no pixel data / no file):", blockers)
                self.report(
                    {"ERROR"},
                    "픽셀 데이터도 없고 디스크에 파일도 없는 이미지가 있어 아무것도 바꾸지 않고 "
                    "중단했습니다. Painter에서 텍스처를 먼저 export하거나 해당 빈 텍스처 노드를 "
                    "삭제한 뒤 다시 실행하세요 "
                    "(image has no pixel data and no file on disk): "
                    + ", ".join(blockers),
                )
                return {"CANCELLED"}
            planned_material_names = {
                material: material_name_for(
                    prefix, index, len(materials)
                )
                for index, material in enumerate(materials)
            }
            protected_collisions = []
            for image in images:
                material, role = image_context.get(
                    image, (materials[0], "Texture")
                )
                planned_name = texture_name_for_material_name(
                    planned_material_names[material],
                    role,
                )
                planned_path = (export_dir / f"{planned_name}.png").resolve()
                if planned_path in protected_paths:
                    protected_collisions.append(planned_path.name)
            if protected_collisions:
                self.report(
                    {"ERROR"},
                    "생성할 텍스처가 Painter Low의 보호 파일과 충돌합니다. "
                    "Prefix 또는 Texture Folder를 바꾸세요: "
                    + ", ".join(sorted(set(protected_collisions))),
                )
                return {"CANCELLED"}
            cleanup_export_files(
                export_dir,
                prefix,
                preserve_paths=protected_paths,
            )

        for index, mat in enumerate(materials):
            remember_name(mat)
            mat.name = unique_name(bpy.data.materials, material_name_for(prefix, index, len(materials)), mat)

        texture_file_count = 0
        for image in images:
            remember_name(image)
            remember_image_path(image)
            material, role = image_context.get(image, (materials[0], "Texture"))
            new_name = unique_name(
                bpy.data.images,
                texture_name_for(material, role, 0, 1),
                image,
            )
            image.name = new_name
            if props.texture_handling == "WRITE_FILES":
                try:
                    write_or_copy_image_file(image, new_name, export_dir)
                except RuntimeError as error:
                    print(f"[UE Unique Names] 텍스처 저장 실패 (write failed): {new_name} -> {error}")
                    self.report(
                        {"ERROR"},
                        f"텍스처 '{new_name}' 저장에 실패했습니다 (failed to write texture): "
                        f"{error}. 이미 바뀐 이름은 'Restore Original Names'로 되돌릴 수 있습니다.",
                    )
                    return {"CANCELLED"}
                texture_file_count += 1
            elif props.rename_image_paths:
                suffix = image_suffix(image)
                image.filepath = f"//textures/{new_name}{suffix}"
                image.filepath_raw = image.filepath

        manifest_path = None
        pipeline_json_paths = []
        if props.write_manifest and props.texture_handling == "WRITE_FILES":
            manifest_path = write_manifest(context, prefix, objects, materials, texture_map, export_dir)
            pipeline_json_paths = write_unreal_pipeline_json(context, prefix, objects, materials, texture_map, export_dir)

        self.report(
            {"INFO"},
            f"완료 (done): 머티리얼 {len(materials)}개 · 텍스처 {len(images)}개 · "
            f"파일 {texture_file_count}개 작성"
            + (
                f" · 보호 데이터 공유 메쉬 {len(skipped_objects)}개 제외"
                if skipped_objects else ""
            )
            + (
                f" · 보호된 공유 머티리얼 {len(skipped_materials)}개 제외"
                if skipped_materials else ""
            )
            + (f" · manifest {manifest_path.name}" if manifest_path else "")
            + (f" · JSON {len(pipeline_json_paths)}개" if pipeline_json_paths else "")
            + ". (.blend는 저장되지 않았습니다 / file not saved)",
        )
        return {"FINISHED"}


class UEUN_OT_refresh_unreal_json(bpy.types.Operator):
    bl_idname = "ue_unique_names.refresh_unreal_json"
    bl_label = "Check Unreal Handoff"
    bl_description = (
        "Validate the current Send to Unreal handoff data and rewrite the material JSON "
        "without renaming Blender data"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ue_unique_names
        prefix = asset_prefix(context, props.prefix_mode, props.custom_prefix)
        objects = validation_scope_objects(context, props.scope)
        materials = unreal_handoff_materials_from_objects(objects)
        texture_map = material_texture_map(materials)

        errors = _json_refresh_validation_errors(
            context,
            props,
            objects,
            materials,
            texture_map,
        )
        if errors:
            props.last_handoff_status = f"Blocked: {len(errors)} issue(s), JSON not updated"
            visible_errors = [
                "JSON not updated. Fix the validation table, then run again.",
                f"Blocking issues: {len(errors)}",
                f"First: {errors[0]}",
            ]
            props.last_handoff_log = "\n".join(visible_errors)
            _report_validation_errors(self, errors)
            return {"CANCELLED"}

        export_dir = resolve_export_dir(props.texture_export_dir)
        try:
            json_paths = write_unreal_pipeline_json(
                context,
                prefix,
                objects,
                materials,
                texture_map,
                export_dir,
            )
        except OSError as error:
            props.last_handoff_status = "Failed"
            props.last_handoff_log = f"JSON refresh failed: {error}"
            self.report({"ERROR"}, f"JSON refresh failed: {error}")
            return {"CANCELLED"}

        if not json_paths:
            props.last_handoff_status = "Failed"
            props.last_handoff_log = "JSON refresh produced no files."
            self.report({"ERROR"}, "JSON refresh produced no files.")
            return {"CANCELLED"}

        refreshed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = export_validation_rows(
            context,
            props=props,
            objects=objects,
            materials=materials,
            texture_map=texture_map,
        )
        pipeline_counts = validation_pipeline_summary(rows)
        props.last_handoff_status = f"Ready: JSON refreshed {refreshed_at}"
        props.last_handoff_log = "\n".join(
            [
                f"Updated: {refreshed_at}",
                f"JSON files: {len(json_paths)}",
                f"Targets: {len(rows)} / Low {pipeline_counts['low']} / Hair bake {pipeline_counts['hair']} / Transfer {pipeline_counts['transfer']}",
                f"Folder: {export_dir}",
            ]
        )
        self.report(
            {"INFO"},
            f"Unreal handoff ready: {len(json_paths)} JSON file(s).",
        )
        return {"FINISHED"}


class UEUN_OT_reimport_unreal_textures(bpy.types.Operator):
    bl_idname = "ue_unique_names.reimport_unreal_textures"
    bl_label = "Reimport Unreal Textures"
    bl_description = (
        "Refresh the Unreal handoff JSON, then force-reimport only the referenced "
        "texture assets in Unreal"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        refresh_result = bpy.ops.ue_unique_names.refresh_unreal_json()
        if "FINISHED" not in refresh_result:
            return {"CANCELLED"}

        props = context.scene.ue_unique_names
        json_path = Path(props.last_pipeline_json_path)
        if not json_path.is_file():
            self.report({"ERROR"}, "No Unreal handoff JSON found for texture reimport.")
            return {"CANCELLED"}

        pipeline_dir = (Path.home() / "Documents" / "UE_Blender_Pipeline").resolve()
        try:
            from send2ue.dependencies.unreal import run_commands
        except Exception as exc:
            self.report({"ERROR"}, f"Send to Unreal remote execution unavailable: {exc}")
            return {"CANCELLED"}

        pipeline_arg = str(pipeline_dir).replace("\\", "/")
        json_arg = str(json_path).replace("\\", "/")
        commands = [
            "import sys",
            f'_d = r"{pipeline_arg}"',
            "sys.path.append(_d) if _d not in sys.path else None",
            "import importlib",
            "import ue_material_setup as _p",
            "importlib.reload(_p)",
            f'_p.reimport_textures_from_json(r"{json_arg}")',
        ]
        try:
            run_commands(commands)
        except Exception as exc:
            self.report({"ERROR"}, f"Unreal texture reimport failed: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Unreal texture reimport requested: {json_path.name}")
        return {"FINISHED"}


class UEUN_OT_toggle_validation_detail(bpy.types.Operator):
    bl_idname = "ue_unique_names.toggle_validation_detail"
    bl_label = "Toggle Validation Details"
    bl_description = "Show or hide detailed validation notes for one Export mesh"
    bl_options = {"REGISTER"}

    object_name: StringProperty(default="")

    def execute(self, context):
        props = context.scene.ue_unique_names
        expanded = validation_expanded_names(props)
        if self.object_name in expanded:
            set_validation_expanded_names(props, set())
        else:
            set_validation_expanded_names(props, {self.object_name})
        return {"FINISHED"}


class UEUN_OT_open_validation_sheet(bpy.types.Operator):
    bl_idname = "ue_unique_names.open_validation_sheet"
    bl_label = "Export Validation Window"
    bl_description = "Open Export validation in a separate Blender window"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return open_validation_spreadsheet_window(context, self)

    def execute(self, context):
        return open_validation_spreadsheet_window(context, self)

    def draw(self, context):
        layout = self.layout
        props = context.scene.ue_unique_names
        objects = validation_scope_objects(context, props.scope)
        materials = unreal_handoff_materials_from_objects(objects)
        texture_map = material_texture_map(materials)
        rows = export_validation_rows(
            context,
            props=props,
            objects=objects,
            materials=materials,
            texture_map=texture_map,
        )
        counts = validation_summary(rows)
        pipeline_counts = validation_pipeline_summary(rows)

        layout.label(text="Export Validation Table", icon="SPREADSHEET")
        summary = layout.row(align=True)
        summary.label(text=f"Targets {len(rows)}", icon="OUTLINER_OB_MESH")
        summary.label(text=f"OK {counts['OK']}", icon="CHECKMARK")
        summary.label(text=f"Warn {counts['WARN']}", icon="ERROR")
        summary.label(text=f"Err {counts['ERROR']}", icon="CANCEL")
        pipeline = layout.row(align=True)
        pipeline.label(text=f"Low {pipeline_counts['low']}", icon="LINKED")
        pipeline.label(text=f"Hair bake {pipeline_counts['hair']}", icon="INFO")
        pipeline.label(text=f"Transfer {pipeline_counts['transfer']}", icon="MOD_DATA_TRANSFER")

        if not rows:
            layout.label(text="No export targets to validate.", icon="ERROR")
            return

        expanded_names = validation_expanded_names(props)
        table = layout.box()
        draw_validation_wide_header(table)

        for group in grouped_validation_rows(rows):
            has_group_header = validation_group_needs_header(group)
            if has_group_header:
                table.separator()
                group_row = table.row(align=True)
                group_row.label(
                    text=f"Empty/Group: {group['unit']} ({len(group['rows'])} meshes)",
                    icon="EMPTY_AXIS",
                )

            for row_data in group["rows"]:
                draw_validation_wide_row(
                    table,
                    row_data,
                    has_group_header,
                    row_data["object_name"] in expanded_names,
                )


class UEUN_OT_restore_names(bpy.types.Operator):
    bl_idname = "ue_unique_names.restore"
    bl_label = "Restore Original Names"
    bl_description = "Restore names and paths changed by Prepare Unique Names"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        protected = protected_painter_data(context)
        restored = 0
        for mat in bpy.data.materials:
            if mat in protected["materials"]:
                continue
            if restore_name(mat, bpy.data.materials):
                restored += 1
        for image in bpy.data.images:
            if image in protected["images"]:
                continue
            path_restored = restore_image_path(image)
            name_restored = restore_name(image, bpy.data.images)
            if path_restored or name_restored:
                restored += 1
        for obj in bpy.data.objects:
            if obj in protected["objects"]:
                continue
            if restore_name(obj, bpy.data.objects):
                restored += 1
        for mesh in bpy.data.meshes:
            if mesh in protected["meshes"]:
                continue
            if restore_name(mesh, bpy.data.meshes):
                restored += 1

        # Remove Empties that Prepare Painter Asset created, but only the ones that
        # are now empty (no children left), so a group the user still relies on is
        # never deleted.
        removed_empties = 0
        for obj in list(bpy.data.objects):
            if obj.type == "EMPTY" and obj.get(CREATED_EMPTY_PROP) and not obj.children:
                bpy.data.objects.remove(obj, do_unlink=True)
                removed_empties += 1

        self.report(
            {"INFO"},
            f"완료 (done): 이름/경로 {restored}개 복원"
            + (f" · 빈 Empty {removed_empties}개 삭제" if removed_empties else "")
            + " (restored).",
        )
        return {"FINISHED"}


class UEUN_OT_prepare_mesh_names(bpy.types.Operator):
    bl_idname = "ue_unique_names.prepare_mesh_names"
    bl_label = "Prepare Mesh Names"
    bl_description = "Rename mesh objects and mesh data from the configured prefix"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ue_unique_names
        prefix = asset_prefix(context, props.prefix_mode, props.custom_prefix)
        objects, skipped_objects, protected = mutation_safe_mesh_objects(
            context, props.scope
        )
        if not objects:
            if props.scope == "EXPORT_COLLECTION" and export_collection(context) is None:
                self.report(
                    {"WARNING"},
                    "'Export' 콜렉션을 찾지 못했습니다 — Scope가 Export Collection입니다 "
                    "(no 'Export' collection found; Scope = Export Collection).",
                )
            else:
                self.report({"WARNING"}, "대상 메쉬가 없습니다 (no mesh objects found).")
            return {"CANCELLED"}

        units = export_naming_units(context, props.scope, objects)
        protected_meshes = protected["meshes"]
        roots = [
            unit["root"]
            for unit in units
            if unit["root"].type == "EMPTY"
        ]
        for root in roots:
            restore_name(root, bpy.data.objects)
        for obj in objects:
            restore_name(obj, bpy.data.objects)
            if (
                obj.data
                and not obj.data.library
                and obj.data not in protected_meshes
            ):
                restore_name(obj.data, bpy.data.meshes)

        # Park every target name first so units do not collide with one another
        # while A_01/A_02 and child suffixes are assigned.
        for index, root in enumerate(roots):
            remember_name(root)
            root.name = f"__ueun_root_{index:04d}"
        for index, obj in enumerate(objects):
            remember_name(obj)
            obj.name = f"__ueun_mesh_{index:04d}"
            if (
                obj.data
                and not obj.data.library
                and obj.data not in protected_meshes
            ):
                remember_name(obj.data)
                obj.data.name = f"__ueun_data_{index:04d}"

        renamed_meshes = 0
        for unit_index, unit in enumerate(units):
            unit_name = mesh_name_for(prefix, unit_index, len(units))
            root = unit["root"]
            meshes = unit["meshes"]
            if root.type == "EMPTY":
                root.name = unique_name(bpy.data.objects, unit_name, root)
                for child_index, mesh in enumerate(meshes, 1):
                    child_name = f"{root.name}_{child_index:02d}"
                    mesh.name = unique_name(bpy.data.objects, child_name, mesh)
                    if (
                        mesh.data
                        and not mesh.data.library
                        and mesh.data not in protected_meshes
                    ):
                        mesh.data.name = unique_name(
                            bpy.data.meshes, mesh.name, mesh.data
                        )
                    renamed_meshes += 1
            else:
                root.name = unique_name(bpy.data.objects, unit_name, root)
                if (
                    root.data
                    and not root.data.library
                    and root.data not in protected_meshes
                ):
                    root.data.name = unique_name(
                        bpy.data.meshes, root.name, root.data
                    )
                renamed_meshes += 1

        self.report(
            {"INFO"},
            f"완료 (done): Export 단위 {len(units)}개 · 메쉬 이름 {renamed_meshes}개 정리"
            + (
                f" · 보호 데이터 공유 메쉬 {len(skipped_objects)}개 제외"
                if skipped_objects else ""
            )
            + ". (.blend 저장 안 됨 / file not saved)",
        )
        return {"FINISHED"}


class UEUN_OT_prepare_external_asset(bpy.types.Operator):
    bl_idname = "ue_unique_names.prepare_external_asset"
    bl_label = "Prepare External Asset"
    bl_description = (
        "Rename mesh objects/data first, then normalize external material and "
        "texture names and write Unreal files"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        mesh_result = bpy.ops.ue_unique_names.prepare_mesh_names()
        if "FINISHED" not in mesh_result:
            self.report(
                {"ERROR"},
                "메쉬 이름 준비에 실패해 External 작업을 중단했습니다 "
                "(mesh naming failed; external workflow stopped).",
            )
            return {"CANCELLED"}

        texture_result = bpy.ops.ue_unique_names.prepare()
        if "FINISHED" not in texture_result:
            self.report(
                {"ERROR"},
                "메쉬 이름은 변경됐지만 텍스처 준비에 실패했습니다. "
                "필요하면 Restore Original Names로 되돌리세요 "
                "(mesh names changed, texture preparation failed).",
            )
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            "External 작업 완료: 메쉬 이름 정리 후 텍스처를 정리했습니다 "
            "(mesh names first, textures second).",
        )
        return {"FINISHED"}


class UEUN_PG_settings(bpy.types.PropertyGroup):
    scope: EnumProperty(
        name="Scope",
        items=[
            ("EXPORT_COLLECTION", "Export Collection",
             "Mesh objects inside the 'Export' collection (what Send to Unreal exports)"),
            ("SELECTED", "Selected Objects", "Only selected mesh objects"),
            ("SCENE", "Whole Scene", "All mesh objects in the current scene"),
        ],
        default="EXPORT_COLLECTION",
    )
    prefix_mode: EnumProperty(
        name="Prefix",
        items=[
            ("BLEND", "Blend File Name", "Use the .blend file name"),
            ("SCENE", "Scene Name", "Use the scene name"),
            ("CUSTOM", "Custom", "Use the custom prefix below"),
        ],
        default="BLEND",
    )
    custom_prefix: StringProperty(name="Custom Prefix", default="")
    texture_handling: EnumProperty(
        name="Texture Handling",
        items=[
            ("WRITE_FILES", "Write Texture Files", "Write texture files beside the .blend file"),
            ("RENAME_PATHS", "Rename Paths Only", "Only rewrite image path strings"),
        ],
        default="WRITE_FILES",
    )
    texture_export_dir: StringProperty(
        name="Texture Folder",
        description="Empty means the shared texture folder beside the .blend file",
        subtype="DIR_PATH",
        default="",
    )
    rename_image_paths: BoolProperty(name="Rename Texture Path Strings", default=True)
    write_manifest: BoolProperty(name="Write Unreal Manifest", default=True)
    last_manifest_path: StringProperty(name="Last Manifest", default="")
    last_pipeline_json_path: StringProperty(name="Last Pipeline JSON", default="")
    last_handoff_status: StringProperty(name="Last Handoff Status", default="")
    last_handoff_log: StringProperty(name="Last Handoff Log", default="")
    validation_expanded_rows: StringProperty(name="Expanded Validation Rows", default="")


def handoff_log_display_lines(log_text):
    lines = []
    for line in str(log_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.casefold()
        if lower.endswith(".json"):
            continue
        if lower.startswith("... +") and "more" in lower:
            continue
        lines.append(stripped)
    return lines


class UEUN_PT_panel(bpy.types.Panel):
    bl_label = "UE Unique Names"
    bl_idname = "UEUN_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UE Names"

    def draw(self, context):
        layout = self.layout
        props = context.scene.ue_unique_names
        handoff_box = layout.box()
        handoff_box.label(text="Unreal Handoff", icon="EXPORT")
        handoff_box.prop(props, "scope")
        handoff_box.prop(props, "texture_export_dir")
        json_objects = validation_scope_objects(context, props.scope)
        handoff_box.label(
            text=f"Export targets {len(json_objects)}",
            icon="INFO" if json_objects else "ERROR",
        )
        json_materials = unreal_handoff_materials_from_objects(json_objects)
        json_texture_map = material_texture_map(json_materials)
        draw_export_validation_table(
            handoff_box,
            context,
            props,
            json_objects,
            json_materials,
            json_texture_map,
        )
        handoff_box.operator(
            "ue_unique_names.refresh_unreal_json",
            text="Check Unreal Handoff",
            icon="CHECKMARK",
        )
        handoff_box.operator(
            "ue_unique_names.reimport_unreal_textures",
            text="Reimport Unreal Textures",
            icon="FILE_IMAGE",
        )
        if props.last_handoff_status or props.last_handoff_log:
            log_box = handoff_box.box()
            log_box.label(
                text=props.last_handoff_status or "Last handoff check",
                icon="CHECKMARK" if props.last_handoff_status.startswith("Ready") else "ERROR",
            )
            for line in handoff_log_display_lines(props.last_handoff_log):
                log_box.label(text=line)

        draw_export_transfer_source(layout, context)

        external_box = layout.box()
        external_box.label(text="External Texture Workflow", icon="FILE_IMAGE")
        external_box.prop(props, "prefix_mode")
        if props.prefix_mode == "CUSTOM":
            external_box.prop(props, "custom_prefix")
        external_box.prop(props, "texture_handling")
        if props.texture_handling == "WRITE_FILES":
            external_box.prop(props, "write_manifest")
        else:
            external_box.prop(props, "rename_image_paths")
        draw_external_workflow_preview(external_box, context, props)
        external_box.operator(
            "ue_unique_names.prepare_external_asset",
            text="Prepare External Asset",
            icon="CHECKMARK",
        )
        if props.last_manifest_path:
            layout.label(text=Path(props.last_manifest_path).name)
        if props.last_pipeline_json_path:
            layout.label(text=Path(props.last_pipeline_json_path).name)
        layout.operator("ue_unique_names.restore", icon="LOOP_BACK")


classes = (
    UEUN_PG_settings,
    UEUN_OT_prepare_mesh_names,
    UEUN_OT_prepare_names,
    UEUN_OT_refresh_unreal_json,
    UEUN_OT_reimport_unreal_textures,
    UEUN_OT_toggle_validation_detail,
    UEUN_OT_open_validation_sheet,
    UEUN_OT_prepare_external_asset,
    UEUN_OT_restore_names,
    UEUN_PT_panel,
)


def schedule_n_panel_sub_tabs_refresh():
    def refresh_view3d_sub_tabs():
        try:
            bpy.ops.n_panel_sub_tabs.update(etype_names_str="VIEW_3D")
        except Exception as error:
            print(f"[UE Unique Names] N Panel Sub Tabs refresh skipped: {error}")
        return None

    bpy.app.timers.register(refresh_view3d_sub_tabs, first_interval=0.2)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ue_unique_names = bpy.props.PointerProperty(type=UEUN_PG_settings)
    bpy.types.Object.ue_unique_transfer_shape_keys = BoolProperty(
        name="Shape Keys",
        description="Request Shape Key transfer during Unreal postprocess",
        default=False,
    )
    bpy.types.Object.ue_unique_transfer_weights = BoolProperty(
        name="Weights",
        description="Request weight transfer during Unreal postprocess",
        default=False,
    )
    if sync_painter_export_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(sync_painter_export_on_load)
    if sync_painter_export_on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(
            sync_painter_export_on_depsgraph
        )
    if not bpy.app.timers.is_registered(sync_painter_export_deferred):
        bpy.app.timers.register(sync_painter_export_deferred, first_interval=0.1)
    schedule_n_panel_sub_tabs_refresh()


def unregister():
    if bpy.app.timers.is_registered(sync_painter_export_deferred):
        bpy.app.timers.unregister(sync_painter_export_deferred)
    if sync_painter_export_on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(
            sync_painter_export_on_depsgraph
        )
    if sync_painter_export_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(sync_painter_export_on_load)
    if hasattr(bpy.types.Scene, "ue_unique_names"):
        del bpy.types.Scene.ue_unique_names
    if hasattr(bpy.types.Object, "ue_unique_transfer_weights"):
        del bpy.types.Object.ue_unique_transfer_weights
    if hasattr(bpy.types.Object, "ue_unique_transfer_shape_keys"):
        del bpy.types.Object.ue_unique_transfer_shape_keys
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


if __name__ == "__main__":
    register()
