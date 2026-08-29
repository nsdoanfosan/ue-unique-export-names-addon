import addon_utils
import bpy


MODULE = "ue_unique_export_names_addon"


addon_utils.enable(MODULE, default_set=False)
try:
    from ue_unique_export_names_addon.gpro import gpro_instance_collections
    from ue_unique_export_names_addon.utils import (
        geometry_nodes_input_value,
        geometry_nodes_input_values,
    )

    source_collection = bpy.data.collections.new("UEUN_GPro_SourceCollection")
    bpy.context.scene.collection.children.link(source_collection)
    instance_mesh = bpy.data.meshes.new("UEUN_GPro_InstanceMesh")
    instance = bpy.data.objects.new("UEUN_GPro_Instance", instance_mesh)
    bpy.context.scene.collection.objects.link(instance)

    node_group = bpy.data.node_groups.new("gPro_Instance", "GeometryNodeTree")
    collection_socket = node_group.interface.new_socket(
        name="Collection",
        in_out="INPUT",
        socket_type="NodeSocketCollection",
    )
    modifier = instance.modifiers.new("gPro_Instance", "NODES")
    modifier.node_group = node_group
    modifier.properties.inputs[collection_socket.identifier]["value"] = source_collection

    assert gpro_instance_collections(instance) == [source_collection]

    class LegacyModifier:
        def keys(self):
            return ("Socket_2",)

        def get(self, key, fallback=None):
            return source_collection if key == "Socket_2" else fallback

    legacy_modifier = LegacyModifier()
    assert geometry_nodes_input_value(legacy_modifier, "Socket_2") == source_collection
    assert list(geometry_nodes_input_values(legacy_modifier)) == [source_collection]
    print("UE_UNIQUE_GPRO_MODIFIER_API52_SMOKE_OK")
finally:
    addon_utils.disable(MODULE, default_set=False)
