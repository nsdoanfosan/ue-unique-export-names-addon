# Gemstone slot handoff

A delimited `Gemstone` token in a Blender material name selects the crystal
material layer, case-insensitively. Examples: `M_Accessory_cane_inset_Gemstone_01`
and `M_Cane_Ornament_Gemstone_02`. `GemstoneDust` and ordinary stone names do not
select it. Mesh/Empty/rig names remain independent of this material-slot rule.

The sidecar writes `master_preset: gemstone`, an opaque material intent, and a
unique MYI path under `/Game/Materials/Gemstone/MYI`. The background layer is
`/Game/Materials/Gemstone/Layers/ML_Gemstone_Crystal`. Shared paths are configurable
through `substance-tools/pipeline_contract.json` → `gemstone_material_layer`.

Blender/Painter appearance textures and transmission settings are omitted for
these slots. Color, interior textures, normals, roughness and switches are edited
in Unreal. Existing MI/MYI values are preserved on reexport. Explicit references
to an existing artist-owned material still take precedence over this name rule.

Verify with Blender's factory-startup background QA and
`tests/blender_gemstone_handoff_smoke.py`; do not save preferences.
