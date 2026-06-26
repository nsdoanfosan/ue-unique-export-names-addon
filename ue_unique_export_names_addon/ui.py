from pathlib import Path

import bpy

from .gpro import unreal_handoff_materials_from_objects
from .naming import material_texture_map
from .utils import validation_scope_objects
from .validation_ui import (
    draw_export_transfer_source,
    draw_export_validation_table,
    draw_external_workflow_preview,
)

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
    bl_label = "Unreal Handoff Validator"
    bl_idname = "UEUN_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Unreal Handoff"

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
