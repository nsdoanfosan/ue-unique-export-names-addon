"""Texture-only optional phase contracts without importing live Blender."""
import ast
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


class TextureOnlyOperatorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.json_path = Path(self.directory.name) / 'Prop.json'
        self.json_path.write_text('{"materials":[{"textures":[{"asset_name":"T_Prop_Color","param":"Albedo"}]}]}', encoding='utf-8')
        self.bridge = mock.Mock()
        self.operation = mock.Mock()
        self.coordination = mock.Mock(RESOURCE='unreal:MyProject2')
        self.coordination.load_bridge.return_value = self.bridge
        self.coordination.begin_operation.return_value = self.operation
        self.namespace = {}
        self.bpy = types.SimpleNamespace(types=types.SimpleNamespace(Operator=object), app=types.SimpleNamespace(driver_namespace=self.namespace), ops=types.SimpleNamespace(ue_unique_names=types.SimpleNamespace(refresh_unreal_json=lambda: {'FINISHED'})))
        self.context = types.SimpleNamespace(scene=types.SimpleNamespace(ue_unique_names=types.SimpleNamespace(last_pipeline_json_path=str(self.json_path))))
        path = Path(__file__).resolve().parents[1] / 'ue_unique_export_names_addon/operators.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'UEUN_OT_reimport_unreal_textures')
        env = dict(bpy=self.bpy, StringProperty=lambda **kwargs: None, Path=Path)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), env)
        self.op = env['UEUN_OT_reimport_unreal_textures']()
        self.op.workstation_phase_id = 'phase'
        self.op.report = mock.Mock()
        self.runner = mock.Mock(side_effect=self.execute_texture_tail)
        self.native = types.SimpleNamespace(_master_preset=lambda data, entry: None, _entry_layers=lambda entry, preset: [entry], reimport_textures_from_json=lambda path: 1)
        send2ue = types.ModuleType('send2ue')
        send2ue.coordination = self.coordination
        unreal_stub = types.ModuleType('send2ue.dependencies.unreal')
        unreal_stub.run_commands = self.runner
        patch = mock.patch.dict(sys.modules, {'send2ue': send2ue, 'send2ue.dependencies.unreal': unreal_stub})
        patch.start()
        self.addCleanup(patch.stop)

    def execute_texture_tail(self, commands, strict=False):
        if 'import json as _wq_texture_json' in commands:
            tail = commands[commands.index('import json as _wq_texture_json'):]
            exec('\n'.join(tail), {'_p': self.native})

    def test_texture_complete_requires_exact_phase_and_full_native_count(self):
        self.assertEqual(self.op.execute(self.context), {'FINISHED'})
        self.bridge.require_active_phase.assert_called_once_with('phase', 'unreal:MyProject2')
        self.operation.require_scopes.assert_called_once_with(['editor'])
        self.operation.complete.assert_called_once()
        self.assertEqual(self.runner.call_args.kwargs, {'strict': True})
        self.coordination.begin_operation.assert_called_once_with('phase', self.namespace, str(self.json_path.resolve()), pipeline='send2ue-texture-reimport')

    def test_partial_texture_result_never_completes(self):
        self.native.reimport_textures_from_json = lambda path: 0
        self.assertEqual(self.op.execute(self.context), {'CANCELLED'})
        self.operation.complete.assert_not_called()
        self.operation.fail.assert_called_once()

    def test_scope_rejection_does_not_dispatch_remote_texture_command(self):
        self.operation.require_scopes.side_effect = RuntimeError('editor scope missing')
        self.assertEqual(self.op.execute(self.context), {'CANCELLED'})
        self.runner.assert_not_called()
        self.operation.complete.assert_not_called()

    def test_inactive_phase_stops_before_even_refreshing_local_sidecar(self):
        self.bridge.require_active_phase.side_effect = RuntimeError('waiting')
        self.bpy.ops.ue_unique_names.refresh_unreal_json = mock.Mock()
        self.assertEqual(self.op.execute(self.context), {'CANCELLED'})
        self.bpy.ops.ue_unique_names.refresh_unreal_json.assert_not_called()
        self.coordination.begin_operation.assert_not_called()

    def test_remote_timeout_retains_recovery_instead_of_completing(self):
        self.runner.side_effect = TimeoutError('editor response uncertain')
        self.assertEqual(self.op.execute(self.context), {'CANCELLED'})
        self.operation.complete.assert_not_called()
        self.operation.fail.assert_called_once()

    def test_manual_texture_flow_does_not_use_work_phase(self):
        self.op.workstation_phase_id = ''
        self.assertEqual(self.op.execute(self.context), {'FINISHED'})
        self.bridge.require_active_phase.assert_not_called()
        self.coordination.begin_operation.assert_not_called()
        self.assertNotIn('import json as _wq_texture_json', self.runner.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
