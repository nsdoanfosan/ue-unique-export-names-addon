# UE Unique Export Names Addon

Blender addon for preparing Send to Unreal exports.

Current fixed naming rule:

- Material: `M_<blend_file_name>`
- Texture: `T_<blend_file_name>_<role>`
- Unreal material instance target in manifest: `MI_<blend_file_name>`

The addon writes a manifest next to exported texture files. Unreal postprocess scripts can use that manifest to move/update textures under `/Game/Textures` and create/update material instances under `/Game/Material/Mesh/MI_Prop_Master`.

The `.blend` file is not saved automatically.
