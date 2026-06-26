import bpy

from .materials import mutation_safe_mesh_objects
from .naming import external_workflow_preview_rows
from .validation import (
    compact_list_label,
    compact_name,
    draw_wrapped_label,
    export_validation_rows,
    fixed_table_column,
    grouped_validation_rows,
    mesh_summary_name,
    validation_expanded_names,
    validation_group_needs_header,
    validation_icon,
    validation_pipeline_summary,
    validation_summary,
)

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
