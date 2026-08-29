from pathlib import Path
import sys

import bpy


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from ue_unique_export_names_addon import utils


source_mesh = bpy.data.meshes.new("UEUN_SourceMesh")
source = bpy.data.objects.new("UEUN_Source", source_mesh)
bpy.context.scene.collection.objects.link(source)

hair_mesh = bpy.data.meshes.new("UEUN_HairMesh")
hair = bpy.data.objects.new("UEUN_Hair", hair_mesh)
bpy.context.scene.collection.objects.link(hair)

setup_group = bpy.data.node_groups.new("Hair_System_Setup_API52", "GeometryNodeTree")
source_socket = setup_group.interface.new_socket(
    name="Source Surface", in_out="INPUT", socket_type="NodeSocketObject"
)
setup_modifier = hair.modifiers.new("Hair_System_Setup", "NODES")
setup_modifier.node_group = setup_group
setup_modifier.properties.inputs[source_socket.identifier]["value"] = source

profile_group = bpy.data.node_groups.new("Hair_System_Profile_API52", "GeometryNodeTree")
material_socket = profile_group.interface.new_socket(
    name="Strands Material", in_out="INPUT", socket_type="NodeSocketMaterial"
)
profile_modifier = hair.modifiers.new("Hair_System_Profile", "NODES")
profile_modifier.node_group = profile_group
material = bpy.data.materials.new("M_HT_API52")
profile_modifier.properties.inputs[material_socket.identifier]["value"] = material

assert utils.is_hair_tool_object(hair)
assert utils.hair_tool_input_object(hair) == source
assert utils.hair_tool_profile_materials(hair) == [material]

print("UE_UNIQUE_HAIR_TOOL_MODIFIER_API52_SMOKE_OK")
