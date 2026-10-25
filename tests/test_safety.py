import ast
from pathlib import Path
import unittest


class SafetyTests(unittest.TestCase):
    def test_no_cloud_mutating_api_calls_and_no_close_with_save(self):
        root = Path(__file__).parents[1] / 'FusionBackupManager'
        banned = {'save', 'saveAs', 'saveDataVersion', 'saveMilestone', 'updateAllReferences',
                  'advanceToLatest', 'promote', 'move', 'copy', 'copyWithInput', 'createDataVersion',
                  'createMilestone', 'uploadFile', 'upload', 'deleteMe'}
        # UI deleteMe is allowed for controls; adapter is the cloud-facing boundary.
        tree = ast.parse((root / 'fusion_adapter.py').read_text(encoding='utf-8'))
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            self.assertNotIn(call.func.attr, banned)
            if call.func.attr == 'close':
                self.assertEqual(len(call.args), 1)
                self.assertIsInstance(call.args[0], ast.Constant)
                self.assertIs(call.args[0].value, False)

    def test_supported_python_syntax(self):
        root = Path(__file__).parents[1]
        for source in root.rglob('*.py'):
            if 'dist' not in source.parts:
                ast.parse(source.read_text(encoding='utf-8'), filename=str(source), feature_version=(3, 10))
