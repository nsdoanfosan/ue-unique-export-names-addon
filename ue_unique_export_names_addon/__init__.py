bl_info = {
    "name": "UE Unique Export Names",
    "author": "Codex",
    "version": (2, 5, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > UE Names",
    "description": "Rename Blender materials/textures for Send to Unreal and write an Unreal postprocess manifest.",
    "category": "Import-Export",
}

import json
import re
import shutil
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty


BACKUP_PROP = "_ue_unique_export_original_name"
BACKUP_FILEPATH_PROP = "_ue_unique_export_original_filepath"
BACKUP_FILEPATH_RAW_PROP = "_ue_unique_export_original_filepath_raw"
# Marks an Empty that Prepare Painter Asset created (for a standalone mesh) so
# Restore can clean it up when it's no longer holding any children.
CREATED_EMPTY_PROP = "_ue_unique_export_created_empty"

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

PAINTER_TEXTURE_PATTERN = re.compile(
    r"^T_(?P<texture_set>.+)_(?:Color|Extra|Normal|Emissive|Height)$",
    re.IGNORECASE,
)


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
    """The collection Send to Unreal exports from. Prefer one literally named "Export";
    fall back to a scene child collection whose name starts with "Export"."""
    coll = bpy.data.collections.get(EXPORT_COLLECTION_NAME)
    if coll is not None:
        return coll
    for child in context.scene.collection.children_recursive:
        if child.name == EXPORT_COLLECTION_NAME or child.name.startswith(EXPORT_COLLECTION_NAME):
            return child
    return None


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
    return bpy.data.collections.get(BAKING_LOW_COLLECTION_NAME)


def collect_export_units(low_collection):
    """Group the Baking/low meshes into export units, one Empty per unit.

    - A mesh already parented to an EMPTY joins that empty's unit (the existing
      empty is reused; child mesh names stay untouched, only the empty is renamed).
    - A mesh with no empty parent becomes its own unit; an empty is created for it
      later in the operator.

    Returns a list of {"empty": <Object|None>, "meshes": [<Object>, ...]} dicts in a
    stable, name-sorted order so the sequential Empty names are deterministic."""
    meshes = [obj for obj in low_collection.all_objects if obj.type == "MESH"]
    units = []
    empty_units = {}
    standalone = []
    for mesh in meshes:
        parent = mesh.parent
        if parent is not None and parent.type == "EMPTY":
            unit = empty_units.get(parent)
            if unit is None:
                unit = {"empty": parent, "meshes": []}
                empty_units[parent] = unit
                units.append(unit)
            unit["meshes"].append(mesh)
        else:
            standalone.append(mesh)
    for mesh in standalone:
        units.append({"empty": None, "meshes": [mesh]})

    def unit_key(unit):
        # Existing-empty units first (sorted by empty name), then standalone meshes.
        if unit["empty"] is not None:
            return (0, unit["empty"].name)
        return (1, unit["meshes"][0].name)

    units.sort(key=unit_key)
    return units


def selected_or_all_mesh_objects(context, scope):
    if scope == "SELECTED":
        objects = context.selected_objects
    elif scope == "EXPORT_COLLECTION":
        coll = export_collection(context)
        objects = coll.all_objects if coll else []
    else:  # "SCENE"
        objects = context.scene.objects
    return [obj for obj in objects if obj.type == "MESH"]


def materials_from_objects(objects):
    materials = []
    seen = set()
    for obj in objects:
        for slot in obj.material_slots:
            mat = slot.material
            if mat and not mat.library and mat.name not in seen:
                materials.append(mat)
                seen.add(mat.name)
    return materials


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


def cleanup_export_files(export_dir, prefix):
    if not export_dir.exists():
        return
    for path in export_dir.glob(f"T_{prefix}_*"):
        if path.is_file():
            path.unlink()


def material_name_for(prefix, index, material_count):
    return f"M_{prefix}" if material_count == 1 else f"M_{prefix}_{index + 1:02d}"


def mesh_name_for(prefix, index, object_count):
    return prefix if object_count == 1 else f"{prefix}_{index + 1:02d}"


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


def painter_texture_set_from_image(image):
    name = Path(image.name).stem
    match = PAINTER_TEXTURE_PATTERN.match(name)
    if match:
        return clean_token(match.group("texture_set"))
    path_name = Path(bpy.path.abspath(image.filepath_raw or image.filepath)).stem
    match = PAINTER_TEXTURE_PATTERN.match(path_name)
    return clean_token(match.group("texture_set")) if match else None


def painter_texture_set_for_material(material, texture_map):
    names = {
        painter_texture_set_from_image(image)
        for image in texture_map.get(material, {}).values()
    }
    names.discard(None)
    if len(names) == 1:
        return next(iter(names))
    return None


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
                "material_slots": [slot.material.name if slot.material else "" for slot in obj.material_slots],
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
        for slot_index, slot in enumerate(obj.material_slots):
            if slot.material == material:
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


def _material_json_entry(mat, slot_index, texture_map):
    textures = []
    for role, image in texture_map.get(mat, {}).items():
        textures.append(
            {
                "param": role,
                "asset_name": image.name,
                "file": bpy.path.abspath(image.filepath_raw or image.filepath).replace("\\", "/"),
            }
        )
    return {
        "name": mat.name,
        "slot_index": slot_index,
        "translucent": is_translucent_material(mat),
        "textures": textures,
    }


def _write_pipeline_sidecar(json_dir, mesh_name, prefix, material_entries):
    data = {
        "mesh_name": mesh_name,
        "asset_prefix": prefix,
        "materials": material_entries,
    }
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

    if not combined_only:
        # Per mesh-object sidecar for the normal non-combined export workflow.
        for obj in objects:
            mesh_name = clean_token(obj.name)
            entries = []
            seen_materials = set()
            for slot_index, slot in enumerate(obj.material_slots):
                mat = slot.material
                if not mat or mat in seen_materials:
                    continue
                seen_materials.add(mat)
                entries.append(_material_json_entry(mat, slot_index, texture_map))
            json_paths.append(_write_pipeline_sidecar(json_dir, mesh_name, prefix, entries))

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
            for slot in obj.material_slots:
                mat = slot.material
                if not mat or mat in seen_materials:
                    continue
                seen_materials.add(mat)
                entries.append(_material_json_entry(mat, slot_index, texture_map))
                slot_index += 1
        if entries:
            json_paths.append(_write_pipeline_sidecar(json_dir, empty_name, prefix, entries))

    if json_paths:
        context.scene.ue_unique_names.last_pipeline_json_path = str(json_paths[-1])
    return json_paths


class UEUN_OT_prepare_names(bpy.types.Operator):
    bl_idname = "ue_unique_names.prepare"
    bl_label = "Prepare External Textures"
    bl_description = "Normalize external material and texture names, write files, and create Unreal JSON"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ue_unique_names
        prefix = asset_prefix(context, props.prefix_mode, props.custom_prefix)
        objects = selected_or_all_mesh_objects(context, props.scope)
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

        materials = materials_from_objects(objects)
        texture_map = material_texture_map(materials)
        images = ordered_unique_images(texture_map)
        reset_previous_prepare(materials, images)

        texture_map = material_texture_map(materials)
        images = ordered_unique_images(texture_map)
        image_context = image_material_role_lookup(texture_map)
        export_dir = resolve_export_dir(props.texture_export_dir)

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
            cleanup_export_files(export_dir, prefix)

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
            + (f" · manifest {manifest_path.name}" if manifest_path else "")
            + (f" · JSON {len(pipeline_json_paths)}개" if pipeline_json_paths else "")
            + ". (.blend는 저장되지 않았습니다 / file not saved)",
        )
        return {"FINISHED"}


class UEUN_OT_prepare_painter_asset(bpy.types.Operator):
    bl_idname = "ue_unique_names.prepare_painter_asset"
    bl_label = "Prepare Painter Asset"
    bl_description = (
        "Group each Baking/low asset under its own Empty (an existing Empty parent is "
        "reused and merely renamed; a loose mesh gets a new Empty), rename the Empties "
        "sequentially from the asset prefix, rename materials from their Texture Set, "
        "link every Empty and its meshes into Export, and write the Unreal JSON. "
        "Child mesh and texture names are preserved"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ue_unique_names
        prefix = asset_prefix(context, props.prefix_mode, props.custom_prefix)
        low_collection = baking_low_collection()
        if low_collection is None:
            self.report(
                {"ERROR"},
                "'Baking/low' 콜렉션을 찾지 못했습니다 (Baking/low collection not found).",
            )
            return {"CANCELLED"}

        units = collect_export_units(low_collection)
        if not units:
            self.report(
                {"ERROR"},
                "'Baking/low'에 메쉬가 없습니다 (Baking/low has no mesh objects).",
            )
            return {"CANCELLED"}

        # Flat list of every Low mesh across all units, for material handling/manifest.
        objects = [mesh for unit in units for mesh in unit["meshes"]]

        materials = materials_from_objects(objects)
        texture_map = material_texture_map(materials)
        material_texture_sets = {
            material: painter_texture_set_for_material(material, texture_map)
            for material in materials
        }
        unresolved = [
            material.name
            for material, texture_set in material_texture_sets.items()
            if texture_set is None
        ]
        if unresolved:
            print("[UE Unique Names] Texture Set 추론 실패 (cannot infer):", unresolved)
            self.report(
                {"ERROR"},
                "다음 머티리얼의 Painter Texture Set을 추론하지 못했습니다. 텍스처 이름이 "
                "'T_<세트>_<역할>' (예: T_Rock_Color) 형식인지, 한 머티리얼이 서로 다른 세트의 "
                "텍스처를 섞어 쓰고 있지 않은지 확인하세요 "
                "(could not infer one Painter Texture Set for): "
                + ", ".join(unresolved),
            )
            return {"CANCELLED"}

        export_coll = ensure_export_collection(context)
        unit_count = len(units)

        # Pass 1: park existing Empties on a collision-free temporary name so the
        # sequential names assigned in pass 2 can't clash with an Empty that hasn't
        # been renamed yet.
        for index, unit in enumerate(units):
            empty = unit["empty"]
            if empty is not None:
                remember_name(empty)
                empty.name = f"__ueun_unit_{index:04d}"

        # Pass 2: name each unit's Empty sequentially (Asset, Asset_01, Asset_02, ...),
        # create an Empty for standalone meshes, parent the meshes (keeping their world
        # transform and names), and link the Empty + meshes into Export. New Empties
        # also go into Baking/low so the grouping is visible there too.
        empties = []
        created = 0
        for index, unit in enumerate(units):
            desired_name = mesh_name_for(prefix, index, unit_count)
            empty = unit["empty"]
            if empty is None:
                empty = bpy.data.objects.new(unique_name(bpy.data.objects, desired_name), None)
                empty[CREATED_EMPTY_PROP] = True
                low_collection.objects.link(empty)
                unit["empty"] = empty
                created += 1
            else:
                empty.name = unique_name(bpy.data.objects, desired_name, empty)
            if empty.name not in export_coll.objects:
                export_coll.objects.link(empty)
            for mesh in unit["meshes"]:
                if mesh.name not in export_coll.objects:
                    export_coll.objects.link(mesh)
                if mesh.parent is not empty:
                    world_matrix = mesh.matrix_world.copy()
                    mesh.parent = empty
                    mesh.matrix_world = world_matrix
            empties.append(empty)

        for material, texture_set in material_texture_sets.items():
            desired_name = f"M_{texture_set}"
            remember_name(material)
            material.name = unique_name(bpy.data.materials, desired_name, material)

        # Rebuild after material names change. Image names and paths remain untouched.
        texture_map = material_texture_map(materials)
        export_dir = resolve_export_dir(props.texture_export_dir)
        manifest_path = write_manifest(
            context,
            prefix,
            objects,
            materials,
            texture_map,
            export_dir,
        )
        json_paths = write_unreal_pipeline_json(
            context,
            prefix,
            objects,
            materials,
            texture_map,
            export_dir,
            combined_only=True,
        )
        if not json_paths:
            self.report(
                {"ERROR"},
                "Empty 기준 Unreal JSON을 만들지 못했습니다 "
                "(could not create the Empty-based Unreal JSON).",
            )
            return {"CANCELLED"}

        empty_names = ", ".join(empty.name for empty in empties)
        print(
            f"[UE Unique Names] Painter asset 준비 완료: 그룹 {len(empties)}개 "
            f"({empty_names}), 새 Empty {created}개, 메쉬 {len(objects)}개, "
            f"머티리얼 {len(materials)}개"
        )
        self.report(
            {"INFO"},
            (
                f"완료 (done): Empty 그룹 {len(empties)}개 [{empty_names}] · "
                f"새 Empty {created}개 · Low 메쉬 {len(objects)}개 · "
                f"머티리얼 {len(materials)}개 이름 변경 · JSON {len(json_paths)}개 · "
                f"manifest {manifest_path.name}. (자식 메쉬·텍스처 이름 유지 / "
                f"child mesh & texture names preserved)"
            ),
        )
        return {"FINISHED"}


class UEUN_OT_restore_names(bpy.types.Operator):
    bl_idname = "ue_unique_names.restore"
    bl_label = "Restore Original Names"
    bl_description = "Restore names and paths changed by Prepare Unique Names"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        restored = 0
        for mat in bpy.data.materials:
            if restore_name(mat, bpy.data.materials):
                restored += 1
        for image in bpy.data.images:
            path_restored = restore_image_path(image)
            name_restored = restore_name(image, bpy.data.images)
            if path_restored or name_restored:
                restored += 1
        for obj in bpy.data.objects:
            if restore_name(obj, bpy.data.objects):
                restored += 1
        for mesh in bpy.data.meshes:
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
        objects = selected_or_all_mesh_objects(context, props.scope)
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

        for obj in objects:
            restore_name(obj, bpy.data.objects)
            if obj.data and not obj.data.library:
                restore_name(obj.data, bpy.data.meshes)

        for index, obj in enumerate(objects):
            desired_name = mesh_name_for(prefix, index, len(objects))
            remember_name(obj)
            obj.name = unique_name(bpy.data.objects, desired_name, obj)
            if obj.data and not obj.data.library:
                remember_name(obj.data)
                obj.data.name = unique_name(bpy.data.meshes, desired_name, obj.data)

        self.report(
            {"INFO"},
            f"완료 (done): 메쉬 이름 {len(objects)}개 정리. (.blend 저장 안 됨 / file not saved)",
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


class UEUN_PT_panel(bpy.types.Panel):
    bl_label = "UE Unique Names"
    bl_idname = "UEUN_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UE Names"

    def draw(self, context):
        layout = self.layout
        props = context.scene.ue_unique_names
        layout.label(text="Asset Naming", icon="TAG")
        layout.prop(props, "prefix_mode")
        if props.prefix_mode == "CUSTOM":
            layout.prop(props, "custom_prefix")
        layout.prop(props, "texture_export_dir")

        painter_box = layout.box()
        painter_box.label(text="Painter Workflow", icon="TEXTURE")
        painter_box.label(text="자식 메쉬·텍스처 이름 유지 (keeps child names)")

        # Lightweight live status so problems are visible before pressing a button.
        status = painter_box.column(align=True)
        low_collection = baking_low_collection()
        if low_collection is None:
            status.label(text="Baking/low 없음 (no Baking/low)", icon="ERROR")
        else:
            units = collect_export_units(low_collection)
            mesh_count = sum(len(unit["meshes"]) for unit in units)
            if not units:
                status.label(text="low에 메쉬 없음 (no meshes)", icon="ERROR")
            else:
                reused = sum(1 for unit in units if unit["empty"] is not None)
                fresh = len(units) - reused
                status.label(
                    text=f"low: 메쉬 {mesh_count} → 그룹 {len(units)}개",
                    icon="OUTLINER_OB_MESH",
                )
                status.label(text=f"기존 Empty {reused} · 새 Empty 예정 {fresh}")
        export_exists = export_collection(context) is not None
        status.label(
            text="Export 콜렉션 있음 (exists)" if export_exists
            else "Export 콜렉션 생성 예정 (will be created)",
            icon="OUTLINER_COLLECTION" if export_exists else "ADD",
        )

        painter_box.operator(
            "ue_unique_names.prepare_painter_asset",
            text="Prepare Painter Asset",
            icon="LINKED",
        )

        external_box = layout.box()
        external_box.label(text="External Texture Workflow", icon="FILE_IMAGE")
        external_box.prop(props, "scope")
        external_box.prop(props, "texture_handling")
        if props.texture_handling == "WRITE_FILES":
            external_box.prop(props, "write_manifest")
        else:
            external_box.prop(props, "rename_image_paths")
        ext_objects = selected_or_all_mesh_objects(context, props.scope)
        external_box.label(
            text=f"대상 메쉬 {len(ext_objects)}개 (target meshes)",
            icon="INFO" if ext_objects else "ERROR",
        )
        external_box.operator(
            "ue_unique_names.prepare",
            text="Prepare External Textures",
            icon="CHECKMARK",
        )
        external_box.operator(
            "ue_unique_names.prepare_mesh_names",
            text="Prepare Standalone Mesh Names",
            icon="MESH_DATA",
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
    UEUN_OT_prepare_painter_asset,
    UEUN_OT_restore_names,
    UEUN_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ue_unique_names = bpy.props.PointerProperty(type=UEUN_PG_settings)


def unregister():
    if hasattr(bpy.types.Scene, "ue_unique_names"):
        del bpy.types.Scene.ue_unique_names
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
