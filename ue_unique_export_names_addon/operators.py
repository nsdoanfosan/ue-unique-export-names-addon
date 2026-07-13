from datetime import datetime
from pathlib import Path

import bpy
from bpy.props import BoolProperty, StringProperty

from . import api as handoff_api
from .constants import CREATED_EMPTY_PROP
from .armature_repair import prepare_scope_armatures
from .gpro import is_unreal_handoff_material, unreal_handoff_materials_from_objects
from .materials import (
    external_materials_from_objects,
    mutation_safe_mesh_objects,
    protected_painter_data,
)
from .naming import (
    cleanup_export_files,
    external_workflow_preview_rows,
    export_naming_units,
    image_disk_path,
    image_is_writable,
    image_material_role_lookup,
    image_suffix,
    material_texture_map,
    material_name_for,
    mesh_name_for,
    ordered_unique_images,
    remember_image_path,
    remember_name,
    reset_previous_prepare,
    resolve_export_dir,
    restore_image_path,
    restore_name,
    texture_name_for,
    texture_name_for_material_name,
    unique_name,
    write_manifest,
    write_or_copy_image_file,
)
from .pipeline_json import (
    _json_refresh_validation_errors,
    _report_validation_errors,
    write_unreal_pipeline_json,
)
from .spreadsheet import open_validation_spreadsheet_window
from .utils import asset_prefix, export_collection, hair_tool_asset_groups, validation_scope_objects
from .validation import (
    export_validation_rows,
    grouped_validation_rows,
    set_validation_expanded_names,
    validation_expanded_names,
    validation_group_needs_header,
    validation_pipeline_summary,
    validation_summary,
)
from .validation_ui import draw_validation_wide_header, draw_validation_wide_row


def _handoff_materials_with_hair(context, props, objects):
    hair_assets = hair_tool_asset_groups(context, props.scope)
    materials = unreal_handoff_materials_from_objects(objects)
    seen_materials = {material.name for material in materials}
    for asset in hair_assets:
        for material in asset["materials"]:
            if (
                is_unreal_handoff_material(material)
                and material.name not in seen_materials
            ):
                materials.append(material)
                seen_materials.add(material.name)
    return materials, hair_assets


class UEUN_OT_prepare_armatures(bpy.types.Operator):
    bl_idname = "ue_unique_names.prepare_armatures"
    bl_label = "Prepare Armatures"
    bl_description = (
        "Add or repair Armature modifiers for weighted export meshes, then move "
        "the Armature modifier to the bottom of the stack"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ue_unique_names
        results, skipped = prepare_scope_armatures(context, props.scope)
        changed = [result for result in results if result.get("changed")]
        ready = [
            result
            for result in results
            if result.get("operation") in {"already_ready", "moved_existing"}
        ]
        skipped_other = [
            result
            for result in results
            if result.get("operation") == "skipped_other_armature"
        ]

        if not results and not skipped:
            self.report(
                {"INFO"},
                "No weighted export meshes needed Armature preparation.",
            )
            return {"FINISHED"}

        message = (
            f"Armature prep: changed {len(changed)}, ready {len(ready)}, "
            f"skipped {len(skipped) + len(skipped_other)}."
        )
        if skipped_other:
            self.report({"WARNING"}, message + " Some meshes use another armature.")
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}


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
                print("[Unreal Handoff Validator] 저장 불가 이미지 (no pixel data / no file):", blockers)
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
                    print(f"[Unreal Handoff Validator] 텍스처 저장 실패 (write failed): {new_name} -> {error}")
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
            hair_assets = hair_tool_asset_groups(context, props.scope)
            pipeline_json_paths = write_unreal_pipeline_json(
                context,
                prefix,
                objects,
                materials,
                texture_map,
                export_dir,
                hair_assets=hair_assets,
            )

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
        data = handoff_api.refresh_handoff_json(context)
        errors = data.get("errors", [])
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

        json_paths = data.get("json_paths", [])
        if not json_paths:
            props.last_handoff_status = "Failed"
            props.last_handoff_log = "JSON refresh produced no files."
            self.report({"ERROR"}, "JSON refresh produced no files.")
            return {"CANCELLED"}

        export_dir = data["export_dir"]
        refreshed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = export_validation_rows(
            context,
            props=props,
            objects=data["objects"],
            materials=data["materials"],
            texture_map=data["texture_map"],
            hair_assets=data["hair_assets"],
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
    bl_label = "Rename Selected Meshes"
    bl_description = (
        "Preview and rename only the selected mesh objects and their mesh data "
        "from the configured prefix"
    )
    bl_options = {"REGISTER", "UNDO"}

    confirmed: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        self.confirmed = True
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        layout = self.layout
        props = context.scene.ue_unique_names
        objects, skipped_objects, _protected = mutation_safe_mesh_objects(
            context, "SELECTED"
        )
        rows = external_workflow_preview_rows(context, props, objects, scope="SELECTED")

        layout.label(text="Selected mesh rename preview", icon="VIEWZOOM")
        layout.label(
            text=f"Targets {len(objects)}",
            icon="INFO" if objects else "ERROR",
        )
        if skipped_objects:
            layout.label(
                text=f"Protected Painter meshes skipped {len(skipped_objects)}",
                icon="INFO",
            )
        if not rows:
            layout.label(text="Select mesh objects before running this.", icon="ERROR")
            return

        preview = layout.column(align=True)
        for row_data in rows[:12]:
            preview.label(text=row_data["object"], icon="OUTLINER_OB_MESH")
            preview.label(text=f"  -> {row_data['planned']}", icon="CHECKMARK")
            if row_data.get("group"):
                preview.label(text=f"  in {row_data['group']}", icon="EMPTY_AXIS")
        if len(rows) > 12:
            preview.label(text=f"+{len(rows) - 12} more", icon="INFO")

    def execute(self, context):
        if not self.confirmed:
            self.report(
                {"WARNING"},
                "Use the Rename Selected Meshes button and confirm the preview first.",
            )
            return {"CANCELLED"}

        props = context.scene.ue_unique_names
        prefix = asset_prefix(context, props.prefix_mode, props.custom_prefix)
        objects, skipped_objects, protected = mutation_safe_mesh_objects(
            context, "SELECTED"
        )
        if not objects:
            self.report({"WARNING"}, "Select one or more mesh objects to rename.")
            return {"CANCELLED"}
            if props.scope == "EXPORT_COLLECTION" and export_collection(context) is None:
                self.report(
                    {"WARNING"},
                    "'Export' 콜렉션을 찾지 못했습니다 — Scope가 Export Collection입니다 "
                    "(no 'Export' collection found; Scope = Export Collection).",
                )
            else:
                self.report({"WARNING"}, "대상 메쉬가 없습니다 (no mesh objects found).")
            return {"CANCELLED"}

        units = export_naming_units(context, "SELECTED", objects)
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

        message = (
            f"Done: selected units {len(units)}; mesh names {renamed_meshes} renamed"
        )
        if skipped_objects:
            message += f"; protected Painter meshes skipped {len(skipped_objects)}"
        self.report({"INFO"}, message + ". (.blend file not saved)")
        return {"FINISHED"}

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
    bl_label = "Prepare External Textures"
    bl_description = (
        "Prepare armatures, external material and texture names, and Unreal files. "
        "Mesh renaming is a separate preview-confirmed selected-object step"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armature_result = bpy.ops.ue_unique_names.prepare_armatures()
        if "FINISHED" not in armature_result:
            self.report(
                {"ERROR"},
                "Armature preparation failed; external workflow stopped.",
            )
            return {"CANCELLED"}

        mesh_result = {"FINISHED"}
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
                "External texture preparation failed. Mesh names were not changed "
                "by this operator.",
            )
            return {"CANCELLED"}
            self.report(
                {"ERROR"},
                "메쉬 이름은 변경됐지만 텍스처 준비에 실패했습니다. "
                "필요하면 Restore Original Names로 되돌리세요 "
                "(mesh names changed, texture preparation failed).",
            )
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            "External texture workflow complete. Mesh renaming remains a separate "
            "preview-confirmed step.",
        )
        return {"FINISHED"}

        self.report(
            {"INFO"},
            "External 작업 완료: 메쉬 이름 정리 후 텍스처를 정리했습니다 "
            "(mesh names first, textures second).",
        )
        return {"FINISHED"}
