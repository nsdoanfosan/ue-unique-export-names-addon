bl_info = {
    "name": "Unreal Handoff Validator",
    "author": "Codex",
    "version": (2, 8, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Unreal Handoff",
    "description": "Validate Blender-to-Unreal handoff data and write the Unreal postprocess manifest.",
    "category": "Import-Export",
}

import bpy
from bpy.props import BoolProperty

from . import api
from .gpro import unreal_handoff_materials_from_objects
from .naming import material_texture_map, resolve_export_dir
from .operators import (
    UEUN_OT_open_validation_sheet,
    UEUN_OT_prepare_external_asset,
    UEUN_OT_prepare_mesh_names,
    UEUN_OT_prepare_names,
    UEUN_OT_refresh_unreal_json,
    UEUN_OT_reimport_unreal_textures,
    UEUN_OT_restore_names,
    UEUN_OT_toggle_validation_detail,
)
from .pipeline_json import _json_refresh_validation_errors, write_unreal_pipeline_json
from .painter_sync import (
    sync_painter_export_deferred,
    sync_painter_export_on_depsgraph,
    sync_painter_export_on_load,
)
from .properties import UEUN_PG_settings
from .ui import UEUN_PT_panel
from .utils import asset_prefix, validation_scope_objects

__all__ = (
    "api",
    "register",
    "unregister",
)

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
            print(f"[Unreal Handoff Validator] N Panel Sub Tabs refresh skipped: {error}")
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
