# UE Unique Export Names

Blender add-on for preparing assets for the **Send to Unreal** workflow. It can:

- link an existing Substance Painter low-poly hierarchy into `Export` without changing it;
- normalize standalone mesh, material, and texture names;
- write PNG textures and Unreal manifest/JSON sidecars;
- restore names and image paths changed by the add-on.

The panel is in **3D Viewport > Sidebar (`N`) > UE Names**.

## Installation

1. Download or clone this repository.
2. Zip the `ue_unique_export_names_addon` folder.
3. In Blender, open **Edit > Preferences > Add-ons > Install from Disk**.
4. Select the zip and enable **UE Unique Export Names**.

Blender 3.6 or newer is required.

## Before You Start

- Save the `.blend` file first. The blend filename is the default naming prefix.
- Create an `Export` collection for objects handled by Send to Unreal.
- For the Painter workflow, use a collection hierarchy named `Baking/low`.
- The add-on changes Blender data but **does not save the `.blend` file automatically**.
- Keep **Send to Unreal > Combine > Child meshes** enabled when an Empty and its
  child meshes should become one Unreal asset.

## Choosing the Asset Prefix

At the top of the panel, choose:

- **Blend File Name**: use the current `.blend` filename;
- **Scene Name**: use the current Blender scene name;
- **Custom**: enter a prefix manually.

Unsupported characters are converted to underscores.

`Texture Folder` controls where generated PNG and metadata files are written.
When it is empty, the add-on uses a shared `texture` folder beside the `.blend`
file.

## Workflow 1: Painter Low to Send to Unreal

Use this when the low-poly asset already has its final names, materials,
textures, parenting, and transforms.

Required structure:

```text
Baking
`-- low
    `-- Asset_Empty
        |-- Mesh_A
        `-- Mesh_B
```

Steps:

1. Put the low-poly meshes in `Baking/low`. Existing Empty parents may also be
   directly contained there.
2. Open **UE Names > Painter Workflow**.
3. Press **Link Painter Low to Export**.
4. The add-on creates `Export` if necessary and links the existing low hierarchy
   into it.
5. Export with Send to Unreal.

This operation does **not** create Empties, rename data, alter parenting, or
change transforms. It adds collection links only.

Meshes present in both `Baking/low` and `Export` are treated as protected
Painter assets. The external-texture and standalone-mesh workflows skip their
objects, mesh data, materials, images, and texture files. Objects sharing that
protected data are skipped as a unit.

The hierarchy must use Empty parents only. The operation stops if it finds a
non-Empty parent or an outside mesh sibling that could be exported by mistake.

## Workflow 2: External Textures

Use this for images brought in from Photoshop, generated sources, or other
tools whose filenames do not already follow the project convention.

Steps:

1. Put the target mesh objects in `Export`, select them, or choose **Whole
   Scene** with the `Scope` setting.
2. Choose **Write Texture Files** to produce PNG files, or **Rename Paths Only**
   to change Blender image path strings without writing files.
3. Enable **Write Unreal Manifest** when the Unreal postprocess metadata is
   needed.
4. Press **Prepare External Textures**.
5. Review the generated files, then save the `.blend` file manually.

Naming rules:

```text
Material:          M_<asset>
Texture set:       <asset>
Texture:           T_<asset>_<role>.png
Material instance: MI_<asset>
```

When several materials exist, the add-on adds stable numeric suffixes. Texture
roles are inferred from material nodes and converted to Painter-style names
such as `Color`, `Extra`, `Normal`, `Emissive`, and `Height`.

With **Write Texture Files**, every image is checked before names are changed.
An empty image with neither pixel data nor a readable disk file cancels the
operation. Existing files used by protected Painter assets are not overwritten.

The add-on writes a manifest and per-mesh JSON sidecars beside the exported
textures. Unreal postprocess scripts can use them to update textures under
`/Game/Textures` and material instances based on
`/Game/Material/Mesh/MI_Prop_Master`.

## Workflow 3: Standalone Mesh Names

Use **Prepare Standalone Mesh Names** for meshes that are not protected Painter
low objects.

Each top-level export item is treated as one asset unit. A standalone mesh is
one unit. An Empty and its child meshes are one unit:

```text
A_01
|-- A_01_01
`-- A_01_02
A_02
```

Mesh data names are matched to object names. Parenting and transforms are
preserved.

## Restore

**Restore Original Names** restores object, mesh-data, material, image, and
image-path values previously backed up by this add-on. Protected Painter data
is left alone.

This restores Blender data names and paths; it does not delete generated PNG,
manifest, or JSON files from disk.

## Scope Reference

- **Export Collection**: meshes recursively contained in `Export`;
- **Selected Objects**: selected mesh objects only;
- **Whole Scene**: all mesh objects in the current scene.

Protected Painter-linked units are excluded from every scope.

## Developer Map

The add-on is intentionally contained in one module:

```text
ue_unique_export_names_addon/
`-- __init__.py
```

Important code areas:

- collection and protection rules: `export_collection`,
  `baking_low_collection`, `linked_painter_low_objects`,
  `protected_painter_data`;
- Painter linking: `UEUN_OT_prepare_painter_asset`;
- external material/texture processing: `UEUN_OT_prepare_names`;
- standalone object and mesh-data naming: `UEUN_OT_prepare_mesh_names`;
- restoration: `UEUN_OT_restore_names`;
- settings and panel layout: `UEUN_PG_settings`, `UEUN_PT_panel`;
- naming constants: `EXPORT_COLLECTION_NAME`, `BAKING_LOW_COLLECTION_NAME`,
  `PAINTER_ROLE_NAMES`.

When changing a workflow, preserve the protected-Painter-data checks. They are
the guardrail that prevents a shared material, image, mesh datablock, or disk
texture from being renamed or overwritten indirectly.
