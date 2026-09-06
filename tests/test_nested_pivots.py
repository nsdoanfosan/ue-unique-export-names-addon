"""Dependency-free structural regression tests for nested export ownership."""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "ue_unique_export_names_addon"
    / "nested_pivots.py"
)
SPEC = importlib.util.spec_from_file_location("nested_pivots_under_test", MODULE_PATH)
nested_pivots = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nested_pivots)


class Object:
    def __init__(self, name, kind, collection=None, parent=None):
        self.name = name
        self.type = kind
        self.users_collection = [collection] if collection is not None else []
        self.parent = parent
        self.children = []
        self.instance_type = "NONE"
        self.instance_collection = None
        self.active_shape_key = None
        self.modifiers = []
        if parent is not None:
            parent.children.append(self)


class NestedPivotTests(unittest.TestCase):
    def setUp(self):
        self.export = object()
        self.root = Object("window", "EMPTY", self.export)
        self.wood = Object("frame", "MESH", self.export, self.root)
        self.glass_pivot = Object("window_glass", "EMPTY", self.export, self.root)
        self.glass = Object("pane", "MESH", self.export, self.glass_pivot)

    def test_explicit_nested_pivots_own_separate_meshes(self):
        self.assertIs(nested_pivots.get_asset_pivot(self.wood, self.export), self.root)
        self.assertIs(
            nested_pivots.get_asset_pivot(self.glass, self.export), self.glass_pivot
        )
        self.assertIs(
            nested_pivots.get_assembly_root(self.glass_pivot, self.export), self.root
        )

    def test_deeper_pivot_chain_keeps_each_owner(self):
        latch = Object("latch", "EMPTY", self.export, self.glass_pivot)
        mesh = Object("latch_mesh", "MESH", self.export, latch)
        self.assertIs(nested_pivots.get_asset_pivot(mesh, self.export), latch)
        self.assertIs(nested_pivots.get_assembly_root(latch, self.export), self.root)

    def test_ordinary_single_pivot_retains_legacy_grouping(self):
        self.glass_pivot.users_collection = []
        self.assertIsNone(nested_pivots.get_asset_pivot(self.wood, self.export))
        self.assertIsNone(nested_pivots.get_asset_pivot(self.glass, self.export))

    def test_subcollection_membership_does_not_enable_a_boundary(self):
        self.glass_pivot.users_collection = [object()]
        self.assertIsNone(nested_pivots.get_asset_pivot(self.glass, self.export))

    def test_collection_instances_do_not_enable_a_boundary(self):
        for property_name, value in (
            ("instance_type", "COLLECTION"),
            ("instance_collection", object()),
        ):
            with self.subTest(property_name=property_name):
                setattr(self.glass_pivot, property_name, value)
                self.assertIsNone(nested_pivots.get_asset_pivot(self.glass, self.export))
                setattr(self.glass_pivot, property_name, "NONE" if property_name == "instance_type" else None)

    def test_non_export_mesh_does_not_create_or_use_a_boundary(self):
        self.glass.users_collection = []
        self.assertIsNone(nested_pivots.get_asset_pivot(self.glass, self.export))
        self.assertIsNone(nested_pivots.get_assembly_root(self.root, self.export))

    def test_helper_meshes_and_pivots_do_not_enable_a_boundary(self):
        for prefix in ("SOCKET_", "UBX_", "UCP_", "USP_", "UCX_"):
            with self.subTest(prefix=prefix):
                self.glass.name = prefix + "glass"
                self.assertIsNone(nested_pivots.get_asset_pivot(self.glass, self.export))
                self.assertIsNone(nested_pivots.get_assembly_root(self.root, self.export))
        self.glass.name = "glass"
        self.glass_pivot.name = "SOCKET_glass"
        self.assertIsNone(nested_pivots.get_assembly_root(self.root, self.export))

    def test_shape_key_or_rig_mesh_preserves_legacy_grouping(self):
        self.glass.active_shape_key = object()
        self.assertIsNone(nested_pivots.get_asset_pivot(self.glass, self.export))
        self.glass.active_shape_key = None
        self.glass.modifiers = [SimpleNamespace(type="ARMATURE", object=object())]
        self.assertIsNone(nested_pivots.get_asset_pivot(self.glass, self.export))
        self.assertIsNone(nested_pivots.get_assembly_root(self.root, self.export))

    def test_armature_ancestor_never_uses_static_assembly_owner(self):
        rig = Object("rig", "ARMATURE", self.export, self.root)
        skinned = Object("skinned", "MESH", self.export, rig)
        self.assertIsNone(nested_pivots.get_asset_pivot(skinned, self.export))

    def test_nested_static_pivots_below_armature_preserve_legacy_grouping(self):
        rig = Object("rig", "ARMATURE", self.export)
        intermediary = Object("rig_helper", "EMPTY", self.export, rig)
        self.root.parent = intermediary
        intermediary.children.append(self.root)
        self.assertIsNone(nested_pivots.get_assembly_root(self.root, self.export))
        self.assertIsNone(nested_pivots.get_assembly_root(self.glass_pivot, self.export))
        self.assertIsNone(nested_pivots.get_asset_pivot(self.wood, self.export))
        self.assertIsNone(nested_pivots.get_asset_pivot(self.glass, self.export))

    def test_non_pivot_parent_breaks_assembly_chain(self):
        self.root.children.remove(self.glass_pivot)
        helper = Object("helper", "EMPTY", self.export, self.root)
        self.glass_pivot.parent = helper
        helper.children.append(self.glass_pivot)
        self.assertIsNone(nested_pivots.get_asset_pivot(self.glass, self.export))


if __name__ == "__main__":
    unittest.main()
