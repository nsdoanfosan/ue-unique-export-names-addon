"""Regression: Alpha socket discovery must retain the distinct Fabric opacity role."""
from pathlib import Path
import sys
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ue_unique_export_names_addon.naming import material_texture_map
from ue_unique_export_names_addon.unreal_material_json import _material_json_entry

materials=[]
for suffix in ('','_back'):
    mat=bpy.data.materials.new('M_FabricTwoSided_Test'+suffix)
    mat.use_nodes=True
    shader=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
    for role,socket in [('Opacity','Alpha'),('SheenOpacity','Sheen Weight')]:
        image=bpy.data.images.new('T_FabricTwoSided_Test'+suffix+'_'+role,8,8)
        image.filepath='/tmp/'+image.name+'.png'
        node=mat.node_tree.nodes.new('ShaderNodeTexImage');node.image=image
        mat.node_tree.links.new(node.outputs['Color'],shader.inputs[socket])
    materials.append(mat)
maps=material_texture_map(materials)
assert all(set(maps[m])=={'Opacity','SheenOpacity'} for m in materials),maps
entry=_material_json_entry(materials[0],0,maps)
textures={t['param']:t for t in entry['layers'][0]['textures']}
assert {'Opacity Map','Backface Opacity Map','Sheen Opacity','Backface Sheen Opacity'}==set(textures),textures
assert all(textures[p]['virtual_texture_streaming'] is False for p in ('Opacity Map','Backface Opacity Map'))
assert all('_Opacity' in textures[p]['asset_name'] for p in ('Opacity Map','Backface Opacity Map'))
assert all('_SheenOpacity' in textures[p]['asset_name'] for p in ('Sheen Opacity','Backface Sheen Opacity'))
legacy=bpy.data.materials.new('M_Cloth_Legacy');legacy.use_nodes=True
image=bpy.data.images.new('LegacyTransparency',8,8);node=legacy.node_tree.nodes.new('ShaderNodeTexImage');node.image=image
shader=next(n for n in legacy.node_tree.nodes if n.type=='BSDF_PRINCIPLED');legacy.node_tree.links.new(node.outputs['Color'],shader.inputs['Alpha'])
assert 'Alpha' in material_texture_map([legacy])[legacy]
print('FABRIC_OPACITY_HANDOFF_SMOKE_OK')
