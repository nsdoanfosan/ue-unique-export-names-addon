# UE Unique Export Names Addon

Blender addon for preparing Send to Unreal exports.

The panel now has two separate workflows.

## Painter workflow

`Link Painter Low to Export`:

- links every mesh in `Baking/low` into `Export`;
- links every object actually contained in `Baking/low`, including Empty
  objects and their child meshes;
- never creates an Empty or changes parenting/transforms;
- never renames meshes, materials, textures, or Empty objects;
- recognizes meshes present in both `Baking/low` and `Export`, so the External
  Texture and Standalone Mesh naming workflows ignore them without stored tags;
- protects shared mesh data, materials, images, and texture files used by the
  linked Low meshes. Objects sharing that protected data are skipped as a unit;
- accepts only Empty parent chains. It stops if the hierarchy contains a
  non-Empty parent or an outside mesh sibling that could be exported by mistake.

Protection is calculated only when an operator runs. The panel redraw uses only
the cheap `Baking/low` and `Export` object intersection.

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

`Prepare Standalone Mesh Names` treats each top-level Export item as one unit.
With prefix `A`, an Empty with two child meshes plus one standalone mesh becomes:

```text
A_01
|- A_01_01
`- A_01_02
A_02
```

Mesh data names follow their object names. Parent relationships are preserved,
and protected Painter-linked units are excluded.
