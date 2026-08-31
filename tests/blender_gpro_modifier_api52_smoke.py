import addon_utils
import bpy


MODULE = "ue_unique_export_names_addon"


addon_utils.enable(MODULE, default_set=False)
try:
    from ue_unique_export_names_addon.gpro import (
        effective_material_slot_entries,
        gpro_instance_collections,
    )
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

    source_material = bpy.data.materials.new("M_GPro_Source")
    source_mesh = bpy.data.meshes.new("UEUN_GPro_SourceMesh")
    source_mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    source_mesh.materials.append(source_material)
    source_object = bpy.data.objects.new("UEUN_GPro_Source", source_mesh)
    source_collection.objects.link(source_object)

    nested_collection = bpy.data.collections.new("UEUN_GPro_NestedCollection")
    nested_material = bpy.data.materials.new("M_GPro_Nested")
    nested_mesh = bpy.data.meshes.new("UEUN_GPro_NestedMesh")
    nested_mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    nested_mesh.materials.append(nested_material)
    nested_object = bpy.data.objects.new("UEUN_GPro_Nested", nested_mesh)
    nested_collection.objects.link(nested_object)
    nested_instance = bpy.data.objects.new("UEUN_GPro_NestedInstance", None)
    nested_instance.instance_type = "COLLECTION"
    nested_instance.instance_collection = nested_collection
    source_collection.objects.link(nested_instance)

    fallback_entries = effective_material_slot_entries(instance)
    assert [mat.name for _index, mat, _location in fallback_entries] == [
        source_material.name,
        nested_material.name,
    ]

    evaluated_collection = bpy.data.collections.new("UEUN_GPro_EvaluatedSource")
    bpy.context.scene.collection.children.link(evaluated_collection)
    source_only_material = bpy.data.materials.new("M_GPro_SourceOnly")
    source_only_mesh = bpy.data.meshes.new("UEUN_GPro_SourceOnlyMesh")
    source_only_mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    source_only_mesh.materials.append(source_only_material)
    source_only_object = bpy.data.objects.new("UEUN_GPro_SourceOnly", source_only_mesh)
    evaluated_collection.objects.link(source_only_object)

    evaluated_material = bpy.data.materials.new("M_GPro_Evaluated")
    evaluated_mesh = bpy.data.meshes.new("UEUN_GPro_EvaluatedMesh")
    evaluated_mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    evaluated_mesh.materials.append(evaluated_material)
    evaluated_object = bpy.data.objects.new("UEUN_GPro_Evaluated", evaluated_mesh)
    bpy.context.scene.collection.objects.link(evaluated_object)

    evaluated_group = bpy.data.node_groups.new("GPro_Instance_Evaluated", "GeometryNodeTree")
    evaluated_group.interface.new_socket(
        name="Geometry",
        in_out="INPUT",
        socket_type="NodeSocketGeometry",
    )
    evaluated_group.interface.new_socket(
        name="Geometry",
        in_out="OUTPUT",
        socket_type="NodeSocketGeometry",
    )
    evaluated_collection_socket = evaluated_group.interface.new_socket(
        name="Collection",
        in_out="INPUT",
        socket_type="NodeSocketCollection",
    )
    group_input = evaluated_group.nodes.new("NodeGroupInput")
    group_output = evaluated_group.nodes.new("NodeGroupOutput")
    evaluated_group.links.new(
        group_input.outputs["Geometry"],
        group_output.inputs["Geometry"],
    )
    evaluated_modifier = evaluated_object.modifiers.new("GPro_Instance", "NODES")
    evaluated_modifier.node_group = evaluated_group
    evaluated_modifier.properties.inputs[evaluated_collection_socket.identifier]["value"] = (
        evaluated_collection
    )

    evaluated_entries = effective_material_slot_entries(evaluated_object)
    assert [mat.name for _index, mat, _location in evaluated_entries] == [
        evaluated_material.name
    ]
    assert source_only_material.name not in {
        mat.name for _index, mat, _location in evaluated_entries if mat
    }

    empty_mesh = bpy.data.meshes.new("UEUN_GPro_EmptyUsedMesh")
    empty_mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    empty_mesh.materials.append(None)
    empty_object = bpy.data.objects.new("UEUN_GPro_EmptyUsed", empty_mesh)
    bpy.context.scene.collection.objects.link(empty_object)
    empty_modifier = empty_object.modifiers.new("GPro_Instance", "NODES")
    empty_modifier.node_group = evaluated_group
    empty_modifier.properties.inputs[evaluated_collection_socket.identifier]["value"] = (
        evaluated_collection
    )
    empty_entries = effective_material_slot_entries(empty_object)
    assert len(empty_entries) == 1 and empty_entries[0][1] is None

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
