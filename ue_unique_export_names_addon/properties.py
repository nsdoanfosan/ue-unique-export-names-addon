import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

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
