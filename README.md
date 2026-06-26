# Unreal Handoff Validator

Blender add-on for the final Blender-to-Unreal stage of the workflow built
around PARK's [`substance-tools` fork](https://github.com/nsdoanfosan/substance-tools).
It also prepares unrelated external-texture assets.

Shared pipeline conventions (collection names, the `Export` collection, naming
prefixes, JSON IPC files, Unreal path anchors) are the contract documented in
the `substance-tools` repo:
[`docs/pipeline_contract.md`](https://github.com/nsdoanfosan/substance-tools/blob/main/docs/pipeline_contract.md)
and `pipeline_contract.json`. Treat that as the source of truth before changing
any pipeline-facing names.

The panel is in **3D Viewport > Sidebar (`N`) > Unreal Handoff**.

## Main Workflow: Substance Tools to Unreal

The two add-ons have separate responsibilities:

| Stage | Tool | Responsibility |
| --- | --- | --- |
| High/Low setup | `substance-tools` fork | Creates and manages `Baking/low` and `Baking/high` |
| Painter project and baking | `substance-tools` + Painter startup plugin | Exports temporary FBX files, creates or updates the SPP, and controls mesh-map baking |
| Painter texture return | `substance-tools` | Exports with `Unreal_V2`, reloads the maps, and connects them to Low materials |
| Unreal handoff | Unreal Handoff Validator | Automatically mirrors Low meshes and their parent chains into `Export` and protects Painter-managed data |
| Unreal mesh export | Send to Unreal | Exports the objects linked into `Export` |

Normal order:

1. Install the [`substance-tools` fork](https://github.com/nsdoanfosan/substance-tools)
   and its Painter startup plugin.
2. Put final game meshes in `Baking/low` and baking sources in `Baking/high`.
3. In **N > Substance > High to Low Baking**, use **Create in Painter**.
4. Work in Painter. Use **Update Painter** when Blender geometry changes.
5. Use **Export Painter Textures & Apply** to export with `Unreal_V2`, reload
   the shared `texture` folder, and connect the maps to Low materials.
6. Do not rename Low materials after the Painter round trip. Painter Texture Set
   names come from Blender material names with the leading `M_` removed.
7. Unreal Handoff Validator automatically links the Low meshes and their complete
   existing parent chains into `Export`. There is no Painter handoff button.
8. Export with Send to Unreal. Enable **Combine > Child meshes** when an Empty
   and its children should become one Unreal asset.

For Painter Low assets, do **not** run **Prepare External Asset**. The automatic
link marks the Low objects, mesh data, materials, images, and texture files as
protected, so the External workflow skips them.

The integration uses shared Blender structure and naming; neither add-on imports
the other. `substance-tools` owns Painter creation, baking, texture export, and
texture reconnection. Unreal Handoff Validator owns automatic `Export` collection
synchronization and the protection boundary.

## Installation

1. Download or clone this repository.
2. Zip the `ue_unique_export_names_addon` folder.
3. In Blender, open **Edit > Preferences > Add-ons > Install from Disk**.
4. Select the zip and enable **Unreal Handoff Validator**.

Blender 3.6 or newer is required.

## Automatic Painter Low Synchronization

The add-on watches `Baking/low` continuously.

- Every mesh inside `Baking/low` is linked directly into `Export`.
- Each mesh's complete existing parent chain is also linked, including
  Armatures and parents above an Armature.
- A sibling mesh under the same Armature is not linked unless that sibling is
  also inside `Baking/low`.
- Adding, removing, or reparenting a Low mesh updates the links automatically.
- `Export` is created automatically when the first Low mesh appears.
- Names, mesh data, materials, textures, parenting, and transforms are not
  changed.
- Only collection links managed by this automatic workflow are removed.
  Unrelated External objects already in `Export` are preserved.

Example:

```text
Character_Root
`-- Character_Armature
    |-- Body_Low       <- inside Baking/low, automatically linked
    `-- Preview_Mesh   <- outside Baking/low, not linked
```

The Low mesh, Armature, and `Character_Root` are linked into `Export`.
`Preview_Mesh` is not.

Meshes present in both `Baking/low` and `Export` are protected Painter assets.
Objects sharing their mesh data, materials, or images are also protected from
the External naming workflow.

## External Asset Workflow

Use this for assets and images that are not managed by the Painter Low workflow.

Before running:

- save the `.blend` file;
- place targets in `Export`, select them, or choose **Whole Scene**;
- choose a prefix from the blend filename, scene name, or a custom value;
- leave `Texture Folder` empty to use the shared `texture` folder beside the
  `.blend` file.

Steps:

1. Choose **Write Texture Files** to produce PNG files, or **Rename Paths Only**
   to change Blender image path strings.
2. Enable **Write Unreal Manifest** when Unreal postprocess metadata is needed.
3. Press **Prepare External Asset**.
4. Review the generated files and save the `.blend` file manually.

The single button runs in this order:

1. rename mesh objects and mesh data;
2. normalize material and texture names;
3. write textures and Unreal metadata.

This prevents texture and sidecar names from being generated from obsolete mesh
names.

Naming rules:

```text
Material:          M_<asset>
Texture set:       <asset>
Texture:           T_<asset>_<role>.png
Material instance: MI_<asset>
```

Painter-style roles include `Color`, `Extra`, `Normal`, `Emissive`, and
`Height`. With several materials, stable numeric suffixes are added.

Every image is checked before files or names are changed. An image with neither
pixel data nor a readable disk file cancels the operation. Files used by
protected Painter Low assets are not overwritten.

Each top-level export item is treated as one asset unit. An Empty and its child
meshes are one unit:

```text
A_01
|-- A_01_01
`-- A_01_02
A_02
```

Mesh data names follow object names. Parenting and transforms are preserved.

The add-on writes a manifest and per-mesh JSON sidecars beside generated
textures. Unreal postprocess scripts can use them to update textures under
`/Game/Textures` and material instances based on
`/Game/Material/Mesh/MI_Prop_Master`.

## Restore

**Restore Original Names** restores object, mesh-data, material, image, and
image-path values backed up by the External workflow. Protected Painter data is
left alone.

Generated PNG, manifest, and JSON files are not deleted from disk.

## Scope Reference

- **Export Collection**: meshes recursively contained in `Export`;
- **Selected Objects**: selected mesh objects only;
- **Whole Scene**: all mesh objects in the current scene.

Protected Painter-linked units are excluded from every scope.

## Developer Map

The add-on is contained in:

```text
ue_unique_export_names_addon/
`-- __init__.py
```

Important code areas:

- Low hierarchy discovery: `baking_low_collection`,
  `painter_export_hierarchy`;
- automatic synchronization: `sync_painter_export`,
  `sync_painter_export_on_depsgraph`, `sync_painter_export_on_load`;
- Painter-data protection: `linked_painter_low_objects`,
  `protected_painter_data`;
- External all-in-one operator: `UEUN_OT_prepare_external_asset`;
- mesh naming: `UEUN_OT_prepare_mesh_names`;
- material, texture, and metadata processing: `UEUN_OT_prepare_names`;
- restoration: `UEUN_OT_restore_names`;
- settings and UI: `UEUN_PG_settings`, `UEUN_PT_panel`.

Keep the automatic-link ownership marker and protected-Painter-data checks when
changing the workflow. They prevent stale Low links from accumulating while
ensuring that shared Painter materials, images, mesh datablocks, and disk
textures are not renamed or overwritten indirectly.
