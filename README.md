# UE Unique Export Names Addon

Blender addon for preparing Send to Unreal exports.

The panel now has two separate workflows.

## Painter workflow

`Prepare Painter Asset`:

- keeps Painter texture image names and files unchanged;
- infers each Texture Set from `T_<texture_set>_<role>.png`;
- renames only the material to `M_<texture_set>`;
- groups the `Baking/low` meshes into **one Empty per asset**:
  - a mesh already parented to an Empty keeps that Empty (and its own name) — only
    the Empty is renamed;
  - a loose mesh (no Empty parent) gets a **new Empty** created for it, added to
    both `Baking/low` and `Export`;
- renames the Empties sequentially from the asset prefix (`Asset`, or `Asset_01`,
  `Asset_02`, … when there are several);
- links every Empty and its child meshes into `Export`;
- preserves every child mesh name used for Painter bake matching;
- writes the manifest and one combined Empty JSON sidecar per Empty.

Grouping is therefore driven by how you parent meshes inside `low`: meshes under
the same Empty export as one combined asset; loose meshes each become their own
asset.

Enable Send to Unreal's `Combine > Child meshes` option. Each Empty name becomes
the exported Unreal mesh asset name.

## External texture workflow

`Prepare External Textures` is for arbitrarily named images from other sources.
It normalizes material and texture names, optionally writes PNG files, and
creates the Unreal manifest/JSON.

Naming rule:

- Material: `M_<blend_file_name>`
- Texture Set: material name without the leading `M_`
- Texture: `T_<texture_set>_<Painter role>.png`
- Unreal material instance target in manifest: `MI_<blend_file_name>`

Painter-compatible role names include `Color`, `Extra`, `Normal`, `Emissive`,
and `Height`. The default output is the shared `texture` folder next to the
`.blend` file. Existing Blender images are encoded as PNG there, so a later
manual export from Painter with the `Unreal_V2` preset replaces the same files.

The addon writes manifests and per-mesh JSON sidecars next to exported texture
files. Unreal postprocess scripts can use them to move/update textures under
`/Game/Textures` and create/update material instances under
`/Game/Material/Mesh/MI_Prop_Master`.

The `.blend` file is not saved automatically.
