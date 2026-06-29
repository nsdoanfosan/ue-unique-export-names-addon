from .utils import parent_chain, validation_scope_objects


def handoff_rig_for_object(mesh_object):
    """
    Return the rig the current handoff hierarchy already assigns to this mesh.

    This intentionally does not guess from bone/weight names. The validator should
    repair missing modifier links for an already-known rig, not infer a new rig.
    """
    for modifier in mesh_object.modifiers:
        if (
            modifier.type == "ARMATURE"
            and modifier.object
            and modifier.object.type == "ARMATURE"
            and modifier.object.data
            and len(modifier.object.data.bones) > 0
        ):
            return modifier.object

    top_rig = None
    for parent in parent_chain(mesh_object):
        if (
            parent.type == "ARMATURE"
            and parent.data
            and len(parent.data.bones) > 0
        ):
            top_rig = parent
    return top_rig


def move_modifier_to_bottom(mesh_object, modifier):
    modifiers = mesh_object.modifiers
    current_index = list(modifiers).index(modifier)
    bottom_index = len(modifiers) - 1
    if current_index != bottom_index:
        modifiers.move(current_index, bottom_index)
        return True
    return False


def repair_armature_modifier(mesh_object, armature_object):
    armature_modifiers = [
        modifier for modifier in mesh_object.modifiers if modifier.type == "ARMATURE"
    ]
    valid_modifiers = [
        modifier for modifier in armature_modifiers if modifier.object == armature_object
    ]
    broken_modifiers = [
        modifier for modifier in armature_modifiers if modifier.object is None
    ]
    other_modifiers = [
        modifier
        for modifier in armature_modifiers
        if modifier.object and modifier.object != armature_object
    ]

    if other_modifiers and not valid_modifiers:
        return {
            "object": mesh_object.name,
            "operation": "skipped_other_armature",
            "armature": armature_object.name,
            "other_armatures": [modifier.object.name for modifier in other_modifiers],
            "changed": False,
        }

    if valid_modifiers:
        modifier = valid_modifiers[0]
        moved = move_modifier_to_bottom(mesh_object, modifier)
        return {
            "object": mesh_object.name,
            "operation": "moved_existing" if moved else "already_ready",
            "armature": armature_object.name,
            "modifier": modifier.name,
            "changed": moved,
        }

    if broken_modifiers:
        modifier = broken_modifiers[0]
        modifier.object = armature_object
        moved = move_modifier_to_bottom(mesh_object, modifier)
        return {
            "object": mesh_object.name,
            "operation": "repaired_empty_modifier",
            "armature": armature_object.name,
            "modifier": modifier.name,
            "changed": True,
            "moved": moved,
        }

    modifier = mesh_object.modifiers.new(name=armature_object.name, type="ARMATURE")
    modifier.object = armature_object
    modifier.show_viewport = True
    modifier.show_render = True
    move_modifier_to_bottom(mesh_object, modifier)
    return {
        "object": mesh_object.name,
        "operation": "added_modifier",
        "armature": armature_object.name,
        "modifier": modifier.name,
        "changed": True,
    }


def prepare_scope_armatures(context, scope):
    results = []
    skipped = []
    objects = [
        obj for obj in validation_scope_objects(context, scope) if obj.type == "MESH"
    ]

    for mesh_object in objects:
        armature_object = handoff_rig_for_object(mesh_object)
        if not armature_object:
            if mesh_object.vertex_groups:
                skipped.append(
                    {
                        "object": mesh_object.name,
                        "reason": "no_handoff_rig",
                        "vertex_groups": len(mesh_object.vertex_groups),
                    }
                )
            continue
        if not mesh_object.vertex_groups:
            continue

        result = repair_armature_modifier(mesh_object, armature_object)
        result["vertex_groups"] = len(mesh_object.vertex_groups)
        results.append(result)

    return results, skipped


def armature_validation_warnings(context, obj):
    if obj.type != "MESH" or not obj.vertex_groups:
        return []

    armature_object = handoff_rig_for_object(obj)
    if not armature_object:
        return []

    armature_modifiers = [
        modifier for modifier in obj.modifiers if modifier.type == "ARMATURE"
    ]
    valid_modifiers = [
        modifier for modifier in armature_modifiers if modifier.object == armature_object
    ]
    if not armature_modifiers:
        return [f"Weighted mesh has no Armature modifier for {armature_object.name}"]
    if not valid_modifiers:
        return [f"Armature modifier is not bound to {armature_object.name}"]
    if list(obj.modifiers).index(valid_modifiers[0]) != len(obj.modifiers) - 1:
        return [f"Armature modifier should be last: {armature_object.name}"]
    return []
