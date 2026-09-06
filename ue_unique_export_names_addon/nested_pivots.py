"""Static asset boundaries for explicitly nested Export pivots.

Keep this small structural contract in sync with Send2UE's
``core.nested_pivots``.  Ordinary hierarchies continue to use their existing
highest-Empty naming rule; collection instances and skeletal assets never
opt in through this helper.
"""


_HELPER_PREFIXES = ("SOCKET_", "UBX_", "UCP_", "USP_", "UCX_")


def _directly_exported(obj, export_collection):
    return export_collection is not None and export_collection in getattr(
        obj, "users_collection", ()
    )


def _is_exported_static_mesh(obj, export_collection):
    if getattr(obj, "type", None) != "MESH":
        return False
    if not _directly_exported(obj, export_collection):
        return False
    if getattr(obj, "name", "").startswith(_HELPER_PREFIXES):
        return False
    if getattr(obj, "active_shape_key", None):
        return False
    return not any(
        getattr(modifier, "type", None) == "ARMATURE"
        and getattr(modifier, "object", None)
        for modifier in getattr(obj, "modifiers", ())
    )


def is_export_pivot(obj, export_collection):
    """Recognize a plain explicit Export Empty owning a static mesh."""
    if getattr(obj, "type", None) != "EMPTY":
        return False
    if getattr(obj, "instance_type", "NONE") == "COLLECTION":
        return False
    if getattr(obj, "instance_collection", None) is not None:
        return False
    if getattr(obj, "name", "").startswith(_HELPER_PREFIXES):
        return False
    if not _directly_exported(obj, export_collection):
        return False
    return any(
        _is_exported_static_mesh(child, export_collection)
        for child in getattr(obj, "children", ())
    )


def get_assembly_root(pivot, export_collection):
    """Return a root only for a direct chain of two or more export pivots."""
    if not is_export_pivot(pivot, export_collection):
        return None
    root = pivot
    while is_export_pivot(getattr(root, "parent", None), export_collection):
        root = root.parent
    ancestor = getattr(root, "parent", None)
    while ancestor is not None:
        if getattr(ancestor, "type", None) == "ARMATURE":
            return None
        ancestor = getattr(ancestor, "parent", None)
    if root is not pivot or any(
        is_export_pivot(child, export_collection)
        for child in getattr(root, "children", ())
    ):
        return root
    return None


def get_asset_pivot(obj, export_collection):
    """Return a nested-assembly owner, or None to preserve legacy grouping."""
    if not _is_exported_static_mesh(obj, export_collection):
        return None
    parent = getattr(obj, "parent", None)
    while parent is not None:
        if getattr(parent, "type", None) == "ARMATURE":
            return None
        if is_export_pivot(parent, export_collection):
            if get_assembly_root(parent, export_collection) is not None:
                return parent
            return None
        parent = getattr(parent, "parent", None)
    return None
