bl_info = {
    "name": "UE Unique Export Names",
    "author": "Codex",
    "version": (2, 1, 0),
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
UNREAL_PIPELINE_EXPORT_DIR = Path(r"C:/Users/PARK/Documents/UE_Blender_Pipeline/exports")

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


def selected_or_all_mesh_objects(context, scope):
    objects = context.selected_objects if scope == "SELECTED" else context.scene.objects
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
        return Path(bpy.data.filepath).resolve().parent / "textures_ue_unique"
    return Path(bpy.app.tempdir).resolve() / "textures_ue_unique"


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


def write_or_copy_image_file(image, new_name, export_dir):
    export_dir.mkdir(parents=True, exist_ok=True)
    target = export_dir / f"{new_name}{image_suffix(image)}"

    if image.packed_file:
        data = getattr(image.packed_file, "data", None)
        if data:
            target.write_bytes(bytes(data))
        else:
            old_path = image.filepath
            image.filepath = str(target)
            image.save()
            image.filepath = old_path
    else:
        source = Path(bpy.path.abspath(image.filepath_raw or image.filepath))
        if source.exists() and source.resolve() != target.resolve():
            shutil.copy2(source, target)
        elif not source.exists():
            old_path = image.filepath
            image.filepath = str(target)
            image.save()
            image.filepath = old_path

    image.filepath = str(target)
    image.filepath_raw = str(target)
    return target


def cleanup_export_files(export_dir, prefix):
    if not export_dir.exists():
        return
    for path in export_dir.glob(f"T_{prefix}_*"):
        if path.is_file():
            path.unlink()


def material_name_for(prefix, index, material_count):
    return f"M_{prefix}" if material_count == 1 else f"M_{prefix}_{index + 1:02d}"


def material_instance_name(material_name):
    clean_name = clean_token(material_name)
    if clean_name.startswith("M_"):
        return f"MI_{clean_name[2:]}"
    if clean_name.startswith("MI_"):
        return clean_name
    return f"MI_{clean_name}"


def texture_name_for(prefix, role, index, count):
    name = f"T_{prefix}_{role}"
    if count > 1:
        name = f"{name}_{index + 1:02d}"
    return name


def write_manifest(context, prefix, objects, materials, texture_map, export_dir):
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


def write_unreal_pipeline_json(context, prefix, objects, materials, texture_map):
    data = {
        "mesh_name": prefix,
        "materials": [],
    }

    for mat in materials:
        textures = []
        for role, image in texture_map.get(mat, {}).items():
            textures.append(
                {
                    "param": role,
                    "asset_name": image.name,
                    "file": bpy.path.abspath(image.filepath_raw or image.filepath).replace("\\", "/"),
                }
            )

        data["materials"].append(
            {
                "name": mat.name,
                "slot_index": first_slot_index_for_material(objects, mat),
                "textures": textures,
            }
        )

    UNREAL_PIPELINE_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = UNREAL_PIPELINE_EXPORT_DIR / f"{prefix}.json"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    context.scene.ue_unique_names.last_pipeline_json_path = str(json_path)
    return json_path


class UEUN_OT_prepare_names(bpy.types.Operator):
    bl_idname = "ue_unique_names.prepare"
    bl_label = "Prepare Unique Names"
    bl_description = "Rename materials/textures for Send to Unreal and write a manifest"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ue_unique_names
        prefix = asset_prefix(context, props.prefix_mode, props.custom_prefix)
        objects = selected_or_all_mesh_objects(context, props.scope)
        if not objects:
            self.report({"WARNING"}, "No mesh objects found.")
            return {"CANCELLED"}

        materials = materials_from_objects(objects)
        texture_map = material_texture_map(materials)
        images = ordered_unique_images(texture_map)
        reset_previous_prepare(materials, images)

        texture_map = material_texture_map(materials)
        images = ordered_unique_images(texture_map)
        image_roles = image_role_lookup(texture_map)
        counts = role_counts(texture_map)
        seen_roles = {}
        export_dir = resolve_export_dir(props.texture_export_dir)
        if props.texture_handling == "WRITE_FILES":
            cleanup_export_files(export_dir, prefix)

        for index, mat in enumerate(materials):
            remember_name(mat)
            mat.name = unique_name(bpy.data.materials, material_name_for(prefix, index, len(materials)), mat)

        texture_file_count = 0
        for image in images:
            remember_name(image)
            remember_image_path(image)
            role = image_roles.get(image, "Texture")
            seen_roles[role] = seen_roles.get(role, 0) + 1
            new_name = unique_name(
                bpy.data.images,
                texture_name_for(prefix, role, seen_roles[role] - 1, counts.get(role, 1)),
                image,
            )
            image.name = new_name
            if props.texture_handling == "WRITE_FILES":
                write_or_copy_image_file(image, new_name, export_dir)
                texture_file_count += 1
            elif props.rename_image_paths:
                suffix = image_suffix(image)
                image.filepath = f"//textures/{new_name}{suffix}"
                image.filepath_raw = image.filepath

        manifest_path = None
        pipeline_json_path = None
        if props.write_manifest and props.texture_handling == "WRITE_FILES":
            manifest_path = write_manifest(context, prefix, objects, materials, texture_map, export_dir)
            pipeline_json_path = write_unreal_pipeline_json(context, prefix, objects, materials, texture_map)

        self.report(
            {"INFO"},
            f"Prepared {len(materials)} materials, {len(images)} textures, wrote {texture_file_count} files"
            + (f", manifest: {manifest_path.name}" if manifest_path else "")
            + (f", JSON: {pipeline_json_path.name}" if pipeline_json_path else "")
            + ". File was not saved.",
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
        self.report({"INFO"}, f"Restored {restored} names/paths.")
        return {"FINISHED"}


class UEUN_PG_settings(bpy.types.PropertyGroup):
    scope: EnumProperty(
        name="Scope",
        items=[
            ("SELECTED", "Selected Objects", "Only selected mesh objects"),
            ("SCENE", "Whole Scene", "All mesh objects in the current scene"),
        ],
        default="SELECTED",
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
        description="Empty means a textures_ue_unique folder beside the .blend file",
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
        layout.prop(props, "scope")
        layout.prop(props, "prefix_mode")
        if props.prefix_mode == "CUSTOM":
            layout.prop(props, "custom_prefix")
        layout.prop(props, "texture_handling")
        if props.texture_handling == "WRITE_FILES":
            layout.prop(props, "texture_export_dir")
            layout.prop(props, "write_manifest")
        else:
            layout.prop(props, "rename_image_paths")
        if props.last_manifest_path:
            layout.label(text=Path(props.last_manifest_path).name)
        if props.last_pipeline_json_path:
            layout.label(text=Path(props.last_pipeline_json_path).name)
        layout.operator("ue_unique_names.prepare", icon="CHECKMARK")
        layout.operator("ue_unique_names.restore", icon="LOOP_BACK")


classes = (
    UEUN_PG_settings,
    UEUN_OT_prepare_names,
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
