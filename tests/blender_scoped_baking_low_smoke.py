"""Read-only Low discovery for independent baking scopes in one blend."""
import pathlib
import sys
import types
import bpy

root = pathlib.Path(__file__).resolve().parents[1] / 'ue_unique_export_names_addon'
package = types.ModuleType('scope_ue_test')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
from scope_ue_test import utils

first = bpy.context.scene
legacy = bpy.data.collections.new('Baking')
low = bpy.data.collections.new('low')
legacy.children.link(low)
first.collection.children.link(legacy)
assert utils.baking_low_collection(first) is low
second = bpy.data.scenes.new('Second')
scoped = bpy.data.collections.new('Baking__Second')
scoped_low = bpy.data.collections.new('low')
scoped_low['substance_tools_role'] = 'low'
scoped.children.link(scoped_low)
second.collection.children.link(scoped)
second['st_baking_root_name'] = scoped.name
assert utils.baking_low_collection(second) is scoped_low
assert utils.baking_low_collection(first) is low
third = bpy.data.scenes.new('Third')
third['st_baking_root_name'] = scoped.name
assert utils.baking_low_collection(third) is None
assert scoped_low.name != low.name
print('SCOPED_BAKING_LOW_SMOKE_PASS')
