import ast
import unittest
from pathlib import Path


API_PATH = (
    Path(__file__).resolve().parents[1]
    / "ue_unique_export_names_addon"
    / "api.py"
)


class PublicApiSourceContractTests(unittest.TestCase):
    def test_painter_low_export_sync_is_versioned_and_exported(self):
        tree = ast.parse(API_PATH.read_text(encoding="utf-8"))
        functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("sync_painter_low_export", functions)
        self.assertIn("get_painter_low_export_api", functions)

        version = None
        service_id = None
        exported = ()
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = {
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            if "PAINTER_LOW_EXPORT_API_VERSION" in names:
                version = ast.literal_eval(node.value)
            if "PAINTER_LOW_EXPORT_SERVICE_ID" in names:
                service_id = ast.literal_eval(node.value)
            if "__all__" in names:
                exported = ast.literal_eval(node.value)

        self.assertEqual(version, 2)
        self.assertEqual(service_id, "unreal-handoff.painter-low-export")
        self.assertIn("get_painter_low_export_api", exported)
        self.assertIn("sync_painter_low_export", exported)
        self.assertIn("ensure_painter_low_export_unit", functions)
        self.assertIn("ensure_painter_low_export_unit", exported)


if __name__ == "__main__":
    unittest.main()
