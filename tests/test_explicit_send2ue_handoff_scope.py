"""Exercise the real public collector against relinked, excluded source objects."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


PACKAGE_DIR = Path(__file__).resolve().parents[1] / 'ue_unique_export_names_addon'
PACKAGE_NAME = '_handoff_scope_review'
SELECTION_MODULE = 'send2ue.core.export_selection'


class Material:
    def __init__(self, name):
        self.name = name


class Modifier(dict):
    def __init__(self, group_name, **inputs):
        super().__init__(inputs)
        self.name = group_name
        self.type = 'NODES'
        self.node_group = types.SimpleNamespace(name=group_name)


class Object:
    def __init__(self, name, object_type='MESH', material=None, admitted=True):
        self.name = name
        self.type = object_type
        self.parent = None
        self.users_collection = []
        self.modifiers = []
        self.materials = [material] if material else []
        self.admitted = admitted
        self.visible = True

    def visible_get(self):
        return self.visible


def material_list(objects):
    materials = []
    for obj in objects:
        for material in obj.materials:
            if material not in materials:
                materials.append(material)
    return materials


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


class ExplicitHandoffScopeTests(unittest.TestCase):
    def setUp(self):
        self.latest = Object('LatestMeshyLow', material=Material('M_LatestBaked'))
        self.legacy = Object('RelinkedPainterLow', material=Material('M_OldPainter'), admitted=False)
        self.current_fur = Object('CurrentFur', object_type='CURVES')
        self.current_fur.modifiers = [Modifier('Hair_System_Setup', Input_3=None),
                                     Modifier('Hair_System_Profile', Input_3=Material('M_CurrentFur'))]
        self.excluded_fur = Object('ExcludedFur', object_type='CURVES', admitted=False)
        self.excluded_fur.modifiers = [Modifier('Hair_System_Setup', Input_3=None),
                                      Modifier('Hair_System_Profile', Input_3=Material('M_ExcludedFur'))]
        self.root = Object('Ornament', object_type='EMPTY')
        self.objects = [self.root, self.latest, self.legacy, self.current_fur, self.excluded_fur]
        self.collection = types.SimpleNamespace(all_objects=self.objects)
        for obj in self.objects[1:]:
            obj.parent = self.root
            obj.users_collection = [self.collection]
        props = types.SimpleNamespace(scope='EXPORT_COLLECTION', texture_export_dir='unused',
                                      prefix_mode='CUSTOM', custom_prefix='Review')
        self.context = types.SimpleNamespace(
            scene=types.SimpleNamespace(ue_unique_names=props, objects=self.objects),
            selected_objects=[self.latest, self.legacy, self.current_fur, self.excluded_fur])
        self.writer_calls = []
        self.validator_calls = []
        self.bpy = module('bpy', context=self.context,
                          data=types.SimpleNamespace(collections={'Export': self.collection}, filepath=''),
                          types=types.SimpleNamespace(Object=Object, Material=Material))
        package = module(PACKAGE_NAME)
        package.__path__ = [str(PACKAGE_DIR)]
        dependencies = {
            'bpy': self.bpy,
            PACKAGE_NAME: package,
            PACKAGE_NAME+'.constants': module(PACKAGE_NAME+'.constants',
                BAKING_LOW_COLLECTION_NAME='low', BAKING_ROOT_COLLECTION_NAME='Baking',
                EXPORT_COLLECTION_NAME='Export'),
            PACKAGE_NAME+'.gpro': module(PACKAGE_NAME+'.gpro',
                is_unreal_handoff_material=lambda value: True,
                unreal_handoff_materials_from_objects=material_list),
            PACKAGE_NAME+'.naming': module(PACKAGE_NAME+'.naming',
                material_texture_map=lambda values: {}, resolve_export_dir=Path,
                top_empty_parent=lambda value, scope: value.parent),
            PACKAGE_NAME+'.nested_pivots': module(PACKAGE_NAME+'.nested_pivots',
                get_asset_pivot=lambda value, collection: value.parent),
            PACKAGE_NAME+'.pipeline_json': module(PACKAGE_NAME+'.pipeline_json',
                _json_refresh_validation_errors=self.validate,
                write_unreal_pipeline_json=self.write),
            PACKAGE_NAME+'.painter_sync': module(PACKAGE_NAME+'.painter_sync',
                ensure_painter_low_export_unit=lambda *args: None,
                sync_painter_export=lambda *args: None),
        }
        self.modules_patch = patch.dict(sys.modules, dependencies)
        self.modules_patch.start()
        self.addCleanup(self.modules_patch.stop)
        sys.modules.pop(SELECTION_MODULE, None)
        self.utils = self.load('utils')
        self.api = self.load('api')

    def load(self, suffix):
        name = PACKAGE_NAME+'.'+suffix
        spec = importlib.util.spec_from_file_location(name, PACKAGE_DIR/(suffix+'.py'))
        result = importlib.util.module_from_spec(spec)
        sys.modules[name] = result
        spec.loader.exec_module(result)
        return result

    def validate(self, context, props, objects, materials, textures, hair_assets=None):
        self.validator_calls.append((objects, materials, hair_assets))
        return []

    def write(self, context, prefix, objects, materials, textures, directory, hair_assets=None):
        self.writer_calls.append((objects, materials, hair_assets))
        return []

    def active_selection(self, predicate=None):
        sys.modules[SELECTION_MODULE] = module(SELECTION_MODULE,
            includes=predicate or (lambda obj: obj.admitted))

    def test_relinked_low_mesh_cannot_contaminate_material_collector(self):
        self.active_selection()
        data = self.api.collect_handoff_data(self.context, scope='EXPORT_COLLECTION')
        self.assertEqual(data['objects'], [self.latest])
        self.assertEqual([value.name for value in data['materials']], ['M_LatestBaked', 'M_CurrentFur'])

    def test_hair_sources_and_profile_materials_obey_the_same_domain(self):
        self.active_selection()
        data = self.api.collect_handoff_data(self.context, scope='EXPORT_COLLECTION')
        self.assertEqual(len(data['hair_assets']), 1)
        self.assertEqual(data['hair_assets'][0]['sources'], [self.current_fur])
        self.assertEqual([mat.name for mat in data['hair_assets'][0]['materials']], ['M_CurrentFur'])

    def test_validator_and_json_writer_receive_only_the_filtered_domain(self):
        self.active_selection()
        self.api.refresh_handoff_json(self.context, scope='EXPORT_COLLECTION')
        for calls in (self.validator_calls, self.writer_calls):
            self.assertEqual(len(calls), 1)
            objects, materials, assets = calls[0]
            self.assertEqual(objects, [self.latest])
            self.assertEqual([mat.name for mat in materials], ['M_LatestBaked', 'M_CurrentFur'])
            self.assertEqual(assets[0]['sources'], [self.current_fur])

    def test_generated_hair_mesh_is_admitted_by_the_native_predicate(self):
        generated = Object('DisposableHairMesh', material=Material('M_CurrentFur'))
        generated.parent = self.root
        generated.users_collection = [self.collection]
        self.objects.append(generated)
        self.active_selection()
        data = self.api.collect_handoff_data(self.context, scope='EXPORT_COLLECTION')
        self.assertIn(generated, data['objects'])

    def test_ordinary_handoff_without_send2ue_keeps_all_visible_sources(self):
        data = self.api.collect_handoff_data(self.context, scope='EXPORT_COLLECTION')
        self.assertEqual(data['objects'], [self.latest, self.legacy])
        self.assertEqual(data['hair_assets'][0]['sources'], [self.current_fur, self.excluded_fur])
        self.assertIn('M_OldPainter', [mat.name for mat in data['materials']])

    def test_loaded_but_inactive_native_selection_keeps_existing_handoff_behavior(self):
        self.active_selection(lambda obj: True)
        data = self.api.collect_handoff_data(self.context, scope='EXPORT_COLLECTION')
        self.assertEqual(data['objects'], [self.latest, self.legacy])
        self.assertIn('M_ExcludedFur', [mat.name for mat in data['materials']])

    def test_scene_and_selected_scopes_keep_their_existing_semantics(self):
        self.active_selection()
        for scope in ('SCENE', 'SELECTED'):
            with self.subTest(scope=scope):
                self.assertIn(self.legacy, self.utils.scope_objects_for_validation(self.context, scope))

    def test_predicate_failure_aborts_instead_of_writing_an_unfiltered_sidecar(self):
        def failed(obj):
            raise ValueError('Invalid explicit source selection')
        self.active_selection(failed)
        with self.assertRaisesRegex(ValueError, 'Invalid explicit source selection'):
            self.api.refresh_handoff_json(self.context, scope='EXPORT_COLLECTION')
        self.assertEqual(self.writer_calls, [])


if __name__ == '__main__':
    unittest.main()
