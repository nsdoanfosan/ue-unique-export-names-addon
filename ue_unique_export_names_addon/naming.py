import json
import shutil
from pathlib import Path

import bpy

from .constants import (
    BACKUP_FILEPATH_PROP,
    BACKUP_FILEPATH_RAW_PROP,
    BACKUP_PROP,
    MATERIAL_PREFIX,
    TEXTURE_PREFIX,
    ROLE_BY_BSDF_INPUT,
    ROLE_PRIORITY,
)
from .gpro import effective_material_names
from .utils import asset_prefix, clean_token, export_collection, parent_chain

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


def _image_handoff_token(image):
    raw_path = (getattr(image, "filepath_raw", "") or getattr(image, "filepath", "") or "")
    if raw_path:
        return clean_token(Path(raw_path.replace("\\", "/")).stem).lower()
    return clean_token(getattr(image, "name", "")).lower()


def image_is_excluded_handoff_texture(image):
    token = _image_handoff_token(image)
    return token.endswith(("_color_alpha", "_color_baking"))


def image_matches_handoff_role(image, role):
    token = _image_handoff_token(image)
    if image_is_excluded_handoff_texture(image):
        return False
    if token.startswith(TEXTURE_PREFIX.lower()):
        token = token[len(TEXTURE_PREFIX):]
    suffixes_by_role = {
        "BaseColor": ("_color",),
        "MetallicRoughness": ("_extra",),
        "Normal": ("_normal",),
        "Emissive": ("_emissive",),
        "Height": ("_height",),
        "SheenColor": ("_sheencolor",),
        "SheenOpacity": ("_sheenopacity",),
        "SheenRoughness": ("_sheenroughness",),
    }
    suffixes = suffixes_by_role.get(role)
    return bool(suffixes and token.endswith(suffixes))


def find_canonical_handoff_image_for_role(mat, role):
    if not mat or not mat.node_tree:
        return None
    for node in mat.node_tree.nodes:
        if node.type != "TEX_IMAGE" or not node.image or node.image.library:
            continue
        if image_matches_handoff_role(node.image, role):
            return node.image
    return None


def ensure_required_handoff_roles(mat, textures):
    for role in ("Height",):
        if textures.get(role):
            continue
        image = find_canonical_handoff_image_for_role(mat, role)
        if image is not None and not image.library and not image_is_excluded_handoff_texture(image):
            textures[role] = image


def fallback_role_from_node(node):
    image = getattr(node, "image", None)
    image_name = getattr(image, "name", "") if image is not None else ""
    text = clean_token(f"{node.label}_{node.name}_{image_name}").lower()
    if "sheen" in text and "color" in text:
        return "SheenColor"
    if "sheen" in text and ("opacity" in text or "weight" in text):
        return "SheenOpacity"
    if "sheen" in text and "rough" in text:
        return "SheenRoughness"
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


def image_node_has_output_links(node):
    return any(output.links for output in getattr(node, "outputs", []))


def image_node_is_canonical_handoff_texture(node):
    image = getattr(node, "image", None)
    if image is None:
        return False
    lowered = _image_handoff_token(image)
    if lowered.startswith(TEXTURE_PREFIX.lower()):
        lowered = lowered[len(TEXTURE_PREFIX):]
    if image_is_excluded_handoff_texture(image):
        return False
    return lowered.endswith((
        "_color",
        "_extra",
        "_normal",
        "_emissive",
        "_height",
        "_sheencolor",
        "_sheenopacity",
        "_sheenroughness",
    ))


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
                        image = image_node.image
                        if image_is_excluded_handoff_texture(image):
                            image = find_canonical_handoff_image_for_role(mat, role)
                        if image:
                            textures[role] = image

        for node in mat.node_tree.nodes:
            if (
                node.type == "TEX_IMAGE"
                and node.image
                and not node.image.library
                and not image_is_excluded_handoff_texture(node.image)
                and (
                    image_node_has_output_links(node)
                    or image_node_is_canonical_handoff_texture(node)
                )
            ):
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

        ensure_required_handoff_roles(mat, textures)

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
    for path in export_dir.glob(f"{TEXTURE_PREFIX}{prefix}_*"):
        if path.is_file() and path.resolve() not in preserve_paths:
            path.unlink()


def material_name_for(prefix, index, material_count):
    return (
        f"{MATERIAL_PREFIX}{prefix}"
        if material_count == 1
        else f"{MATERIAL_PREFIX}{prefix}_{index + 1:02d}"
    )


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
    if clean_name.startswith(MATERIAL_PREFIX):
        return f"MI_{clean_name[len(MATERIAL_PREFIX):]}"
    if clean_name.startswith("MI_"):
        return clean_name
    return f"MI_{clean_name}"


def texture_set_name(material):
    name = clean_token(material.name)
    return name[len(MATERIAL_PREFIX):] if name.startswith(MATERIAL_PREFIX) else name


def texture_name_for(material, role, index, count):
    output_role = PAINTER_ROLE_NAMES.get(role, role)
    name = f"{TEXTURE_PREFIX}{texture_set_name(material)}_{output_role}"
    if count > 1:
        name = f"{name}_{index + 1:02d}"
    return name


def texture_name_for_material_name(material_name, role):
    output_role = PAINTER_ROLE_NAMES.get(role, role)
    clean_name = clean_token(material_name)
    texture_set = (
        clean_name[len(MATERIAL_PREFIX):]
        if clean_name.startswith(MATERIAL_PREFIX)
        else clean_name
    )
    return f"{TEXTURE_PREFIX}{texture_set}_{output_role}"


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
