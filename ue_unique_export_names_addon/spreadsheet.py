import bpy

from .gpro import unreal_handoff_materials_from_objects
from .naming import material_texture_map
from .transfer import transfer_check_label
from .utils import validation_scope_objects
from .validation import (
    compact_list_label,
    export_validation_rows,
    grouped_validation_rows,
)

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
