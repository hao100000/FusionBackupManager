"""Command lifecycle tests using minimal Fusion UI doubles; no Fusion imports."""
import importlib.util
import sys
import tempfile
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
from test_adapter import Collection


class Inputs(dict):
    def itemById(self, key):
        return self[key]


class UITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('FBM_TEST_TMP'))
        self.addCleanup(self.temp.cleanup)
        adsk = ModuleType('adsk')
        adsk.core = ModuleType('adsk.core')
        adsk.fusion = ModuleType('adsk.fusion')
        for name in ('CommandCreatedEventHandler', 'CommandEventHandler', 'InputChangedEventHandler',
                     'ValidateInputsEventHandler', 'CustomEventHandler', 'HTMLEventHandler', 'UserInterfaceGeneralEventHandler'):
            setattr(adsk.core, name, type(name, (), {}))
        self.fired = []
        self.project = NS(id='project', name='Project')
        self.app = NS(data=NS(activeProject=self.project), fireCustomEvent=self.fired.append)
        adsk.core.Application = NS(get=lambda: self.app)
        self.modules = patch.dict(sys.modules, {'adsk': adsk, 'adsk.core': adsk.core, 'adsk.fusion': adsk.fusion})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        path = Path(__file__).parents[1] / 'FusionBackupManager' / 'FusionBackupManager.py'
        spec = importlib.util.spec_from_file_location('FusionBackupManager.ui_test', path)
        self.ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ui)
        self.ui._config = Path(self.temp.name) / 'settings.json'
        self.ui._projects = [self.project, NS(id='second', name='Second')]
        self.inputs = Inputs(scope=NS(selectedItem=NS(index=1)),
                             projects=NS(listItems=Collection([NS(isSelected=True), NS(isSelected=True)])),
                             directory=NS(value=self.temp.name))
        for key in ('export_fusion_archive', 'export_step_for_assemblies', 'incremental'):
            self.inputs[key] = NS(value=True)
        self.inputs['export_step'] = NS(value=False)

    def test_execute_only_queues_then_destroy_fires_custom_event(self):
        self.ui.Execute().notify(NS(command=NS(commandInputs=self.inputs)))
        self.assertIsNotNone(self.ui._pending)
        self.assertEqual(self.fired, [])
        self.assertEqual(self.ui.settings()['project_ids'], ['project', 'second'])
        self.ui.Destroy().notify(NS())
        self.assertEqual(self.fired, [self.ui.EVENT])
        self.assertIsNone(self.ui._pending)
        self.assertIsNotNone(self.ui._queued)

    def test_current_and_all_scope(self):
        for index, expected in [(2, ['project']), (0, ['project', 'second'])]:
            self.inputs['scope'].selectedItem.index = index
            self.ui.Execute().notify(NS(command=NS(commandInputs=self.inputs)))
            self.assertEqual([x.id for x in self.ui._pending[1]], expected)

    def test_cancel_dialog_does_not_start_backup(self):
        self.ui.Destroy().notify(NS())
        self.assertEqual(self.fired, [])
        self.assertIsNone(self.ui._queued)

    def test_invalid_path_fails_execute_and_does_not_queue(self):
        self.inputs['directory'].value = 'relative'
        args = NS(command=NS(commandInputs=self.inputs))
        self.ui.Execute().notify(args)
        self.assertTrue(args.executeFailed)
        self.assertIsNone(self.ui._pending)
        self.assertFalse(self.ui._config.exists())

    def test_no_current_project_fails_safely(self):
        self.app.data.activeProject = None
        self.inputs['scope'].selectedItem.index = 2
        args = NS(command=NS(commandInputs=self.inputs))
        self.ui.Execute().notify(args)
        self.assertTrue(args.executeFailed)
        self.assertIsNone(self.ui._pending)

    def test_selection_validation_and_formats(self):
        args = NS(inputs=self.inputs)
        self.ui.Validate().notify(args)
        self.assertTrue(args.areInputsValid)
        for option in self.inputs['projects'].listItems.items:
            option.isSelected = False
        self.ui.Validate().notify(args)
        self.assertFalse(args.areInputsValid)
        self.inputs['scope'].selectedItem.index = 0
        self.inputs['export_fusion_archive'].value = False
        self.ui.Validate().notify(args)
        self.assertFalse(args.areInputsValid)
        self.inputs['export_step'].value = True
        self.ui.Validate().notify(args)
        self.assertTrue(args.areInputsValid)

    def test_corrupt_settings_not_silently_replaced(self):
        self.ui._config.write_text('{broken')
        with self.assertRaises(ValueError):
            self.ui.settings()
        self.assertEqual(self.ui._config.read_text(), '{broken')

    def test_launch_connects_detailed_activity_to_progress_dialog(self):
        from test_core import Adapter, item
        messages = []
        class Progress:
            wasCancelled = False
            warnings = []
            def poll(self):
                pass
            def __setattr__(self, key, value):
                if key == 'message':
                    messages.append(value)
                object.__setattr__(self, key, value)
            def show(self, title, message, *args):
                self.message = message
            def hide(self):
                self.hidden = True
        progress = Progress()
        self.app.userInterface = NS(allToolbarPanels=NS(itemById=lambda _: None),
                                    createProgressDialog=lambda: progress, messageBox=lambda *args: 0)
        adsk = sys.modules['adsk']
        adsk.doEvents = lambda: None
        adsk.core.MessageBoxButtonTypes = NS(YesNoButtonType=1)
        adsk.core.DialogResults = NS(DialogYes=1)
        adapter = Adapter([item(folders=[('Parts', 'parts-id')])])
        original_enumerate = adapter.enumerate
        def factory(*args, **kwargs):
            def enumerate_files(projects, cancelled):
                kwargs['activity'](dict(phase='enumerating', project='Scanning Project', folder='Parts',
                                        discovered=1, project_index=1, project_total=1))
                return original_enumerate(projects, cancelled)
            adapter.enumerate = enumerate_files
            return adapter
        self.ui._queued = (dict(backup_directory=self.temp.name), [self.project])
        with patch.object(self.ui, 'FusionAdapter', side_effect=factory), patch.object(self.ui, 'ProgressDisplay', return_value=progress):
            self.ui.Launch().notify(NS())
        self.assertTrue(any('Scanning Project' in text and '発見済み 1 件' in text for text in messages))
        self.assertTrue(any('SHA-256 を検証中' in text and 'Part' in text and 'F3D' in text for text in messages))
        self.assertTrue(any('完了 1 / 1' in text for text in messages))
        self.assertTrue(progress.hidden)
        self.assertFalse(self.ui._running)
