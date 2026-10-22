"""Exercise native temporary naming through actual public consumer functions."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


PACKAGE = Path(__file__).parents[1] / 'ue_unique_export_names_addon'


def load_function(filename, name, namespace):
    tree = ast.parse((PACKAGE / filename).read_text(encoding='utf8'))
    node = next(item for item in tree.body
                if isinstance(item, ast.FunctionDef) and item.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
    return namespace[name]


class Object(dict):
    __hash__ = object.__hash__

    def __init__(self, name, **values):
        super().__init__(values)
        self.name, self.parent = name, None


class NativeHairNameTests(unittest.TestCase):
    def setUp(self):
        self.namespace = {'re': re}
        self.clean = load_function('utils.py', 'clean_token', self.namespace)
        self.name = load_function('utils.py', 'handoff_object_name', self.namespace)

    def temporary(self):
        return Object('Short_GuideSim.001', _send2ue_hair_tool_temp=True,
                      _send2ue_hair_tool_source_name='Short_GuideSim')

    def test_only_native_temporary_outputs_use_the_export_identity(self):
        self.assertEqual(self.name(self.temporary()), 'Short_GuideSim')
        ordinary = Object('Artist.001', _send2ue_hair_tool_source_name='Other')
        self.assertEqual(self.name(ordinary), 'Artist.001')
        for invalid in ('', '  ', None, 12):
            temp = self.temporary()
            temp['_send2ue_hair_tool_source_name'] = invalid
            self.assertEqual(self.name(temp), 'Short_GuideSim.001')

    def test_public_asset_name_matches_native_export_without_renaming(self):
        obj = self.temporary()
        self.namespace.update(_context=lambda value: value,
                              export_collection=lambda context: None,
                              get_asset_pivot=lambda *args: None,
                              top_empty_parent=lambda *args: None)
        resolve = load_function('api.py', 'resolve_asset_unit_name', self.namespace)
        self.assertEqual(resolve(obj), 'Short_GuideSim')
        self.assertEqual(obj.name, 'Short_GuideSim.001')
        self.assertEqual(resolve(Object('Artist.001')), 'Artist_001')

    def targets(self, standalone, units=()):
        self.namespace['_asset_unit_groups'] = lambda *args: (standalone, units)
        return load_function('pipeline_json.py', '_json_target_names', self.namespace)

    def test_sidecar_names_and_duplicate_unit_check_share_the_alias(self):
        obj, owner = self.temporary(), Object('Short_GuideSim')
        targets = self.targets([obj], [(owner, [obj])])
        self.assertEqual(targets([obj]), ['Short_GuideSim'])
        self.assertEqual(obj.name, 'Short_GuideSim.001')

    def test_ordinary_names_and_explicit_hair_units_keep_their_ownership(self):
        obj = Object('Ordinary.001')
        owner = Object('OtherUnit')
        targets = self.targets([obj], [(owner, [obj])])
        self.assertEqual(targets([obj], hair_assets=[{'asset_name': 'Short'}]),
                         ['Ordinary.001', 'OtherUnit', 'Short'])
        self.assertEqual(targets([obj], combined_only=True), ['OtherUnit'])


if __name__ == '__main__':
    unittest.main()
