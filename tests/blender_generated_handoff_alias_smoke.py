"""Run in Blender: generated asset aliases must not rename Painter slots."""
import bpy
from ue_unique_export_names_addon import unreal_material_json as exporter

material = bpy.data.materials.new('__alias_smoke_material')
image = bpy.data.images.new('__alias_smoke_image', width=1, height=1)
try:
    material.name = 'M_AliasSmokeSource'
    material.use_nodes = True
    image.filepath = 'D:/example/T_AliasSmokeSource_Color.png'
    maps = {material: {'BaseColor': image}}
    default = exporter._material_json_entry(material, 0, maps)
    assert default['name'] == default['slot_name'] == 'M_AliasSmokeSource'
    material['unreal_generated_material_name'] = 'M_Prop_AliasSmoke_01'
    image['unreal_texture_asset_name'] = 'T_Prop_AliasSmoke_01_Color'
    entry = exporter._material_json_entry(material, 0, maps)
    assert entry['name'] == 'M_Prop_AliasSmoke_01'
    assert entry['slot_name'] == material.name == 'M_AliasSmokeSource'
    assert entry['textures'][0]['asset_name'] == 'T_Prop_AliasSmoke_01_Color'
    assert entry['layers'][0]['textures'][0]['asset_name'] == 'T_Prop_AliasSmoke_01_Color'
    image['unreal_texture_asset_name'] = '../bad'
    try:
        exporter._material_json_entry(material, 0, maps)
        raise AssertionError('unsafe texture alias was accepted')
    except ValueError:
        pass
    del image['unreal_texture_asset_name']
    material['unreal_generated_material_name'] = '../bad'
    try:
        exporter._material_json_entry(material, 0, maps)
        raise AssertionError('unsafe material alias was accepted')
    except ValueError:
        pass
    print('generated handoff aliases: PASS')
finally:
    bpy.data.materials.remove(material)
    bpy.data.images.remove(image)
