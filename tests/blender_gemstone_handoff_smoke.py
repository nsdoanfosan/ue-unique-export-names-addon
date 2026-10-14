"""Name-only crystal handoff must not carry Painter or Blender appearance."""
import bpy
from ue_unique_export_names_addon.unreal_material_json import _material_json_entry, master_preset_for_material

for name in ('M_Accessory_cane_inset_Gemstone_01', 'M_Cane_Ornament_Gemstone', 'M_GEMSTONE_Test.001'):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    principled = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    principled.inputs['Transmission Weight'].default_value = 1.0
    image = bpy.data.images.new('PainterCrystalColor', 8, 8)
    entry = _material_json_entry(material, 2, {material: {'BaseColor': image}})
    assert entry['master_preset'] == 'gemstone', entry
    assert entry['translucent'] is False, entry
    assert entry['textures'] == entry['layers'] == [], entry
    assert entry['material_layer']['parent'].endswith('/ML_Gemstone_Crystal'), entry
    assert entry['material_layer']['texture_remap'] == {}, entry
    assert entry['manage_existing_material_instance'] is False, entry
    assert entry['create_if_missing'] is True, entry
    print(entry)
for name in ('M_Accessory_cane_connectors_01', 'M_Ornament_GemstoneDust', 'M_StoneFloor'):
    assert master_preset_for_material(bpy.data.materials.new(name)) != 'gemstone', name
print('GEMSTONE_HANDOFF_SMOKE_OK')
