"""Palette bridge lifecycle tests with no Fusion process or backup data."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch


class Event:
    def __init__(self):
        self.handlers = []
    def add(self, handler):
        self.handlers.append(handler)


class DisplayTests(unittest.TestCase):
    def setUp(self):
        adsk = ModuleType('adsk')
        adsk.core = ModuleType('adsk.core')
        adsk.core.HTMLEventHandler = type('HTMLEventHandler', (), {})
        adsk.core.UserInterfaceGeneralEventHandler = type('UserInterfaceGeneralEventHandler', (), {})
        adsk.core.PaletteDockingStates = NS(PaletteDockStateFloating=0)
        self.patch = patch.dict(sys.modules, {'adsk': adsk, 'adsk.core': adsk.core})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        path = Path(__file__).parents[1] / 'FusionBackupManager' / 'progress_display.py'
        spec = importlib.util.spec_from_file_location('display_under_test', path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.messages = []
        self.palette = NS(incomingFromHTML=Event(), closed=Event(), isValid=True, isVisible=False,
                          sendInfoToHTML=self.send, deleteMe=lambda: setattr(self.palette, 'isValid', False))
        self.native = NS(wasCancelled=False, show=lambda *args: True, hide=lambda: None)
        self.ui = NS(palettes=NS(itemById=lambda _: None, add=lambda *args: self.palette),
                     createProgressDialog=lambda: self.native)
        self.display = self.module.ProgressDisplay(self.ui)

    def send(self, action, data):
        self.messages.append((action, json.loads(data)))
        return 'OK'

    def test_browser_ready_gets_latest_buffered_text_and_counts(self):
        self.display.show('Title', 'Preparing', 0, 10)
        self.display.message = 'プロジェクト: Robot <part>'
        self.display.progressValue = 3
        self.display.message = 'ファイル: part.f3d'
        self.assertEqual(self.messages, [])
        args = NS(action='ready')
        self.palette.incomingFromHTML.handlers[0].notify(args)
        self.assertEqual(args.returnData, 'OK')
        self.assertEqual(self.messages, [])  # No reentrant send inside the ready event.
        self.display.poll()
        self.assertEqual(self.messages[-1][1]['message'], 'ファイル: part.f3d')
        self.assertEqual(self.messages[-1][1]['value'], 3)
        self.assertEqual(self.messages[-1][1]['maximum'], 10)
        self.assertTrue(self.palette.isVisible)

    def test_button_or_palette_close_requests_cancellation(self):
        self.display.show('Title', 'Preparing', 0, 1)
        self.palette.incomingFromHTML.handlers[0].notify(NS(action='cancel'))
        self.assertTrue(self.display.wasCancelled)
        self.display.wasCancelled = False
        self.palette.closed.handlers[0].notify(NS())
        self.assertTrue(self.display.wasCancelled)

    def test_hide_is_idempotent_and_deletes_only_own_palette(self):
        self.display.show('Title', 'Preparing', 0, 1)
        self.display.hide()
        self.display.hide()
        self.assertFalse(self.palette.isValid)
        self.assertEqual(self.display._handlers, [])

    def test_create_failure_uses_cancellable_native_display(self):
        self.ui.palettes.add = lambda *args: None
        self.assertTrue(self.display.show('Title', 'Preparing', 0, 1))
        self.assertIs(self.display.native, self.native)
        self.assertTrue(self.native.isCancelButtonShown)
        self.native.wasCancelled = True
        self.assertTrue(self.display.wasCancelled)
        self.display.hide()

    def test_text_uses_safe_dom_and_explicit_left_alignment(self):
        html = (Path(self.module.__file__).with_name('progress.html')).read_text(encoding='utf-8')
        self.assertIn('text-align: left', html)
        self.assertIn('white-space: pre-wrap', html)
        self.assertIn('overflow-wrap: anywhere', html)
        self.assertIn('.textContent = state.message', html)
        self.assertNotIn('innerHTML', html)

    def test_native_bridge_return_is_not_treated_as_synchronous_ack(self):
        self.display.show('Title', 'Preparing', 0, 10)
        self.display.ready = True
        for index, response in enumerate((None, '', True, False, '{}')):
            with self.subTest(response=response):
                self.palette.sendInfoToHTML = lambda *args: response
                self.display.message = 'File ' + str(index)
                self.assertIsNone(self.display.native)
                self.assertFalse(self.display.wasCancelled)

    def test_send_exception_falls_back_and_updates_existing_counts(self):
        self.display.show('Title', 'Preparing', 0, 10)
        self.display.ready = True
        self.display.progressValue = 4
        def fail(*args):
            raise RuntimeError('bridge unavailable')
        self.palette.sendInfoToHTML = fail
        self.display.message = 'Checking Robot'
        self.assertIs(self.display.native, self.native)
        self.assertFalse(self.display.wasCancelled)
        self.assertEqual(self.native.progressValue, 4)
        self.assertFalse(self.palette.isVisible)
        self.assertTrue(self.display.warnings)
        self.display.progressValue = 5
        self.display.message = 'Exporting Robot'
        self.assertEqual(self.native.message, 'Exporting Robot')
        self.assertEqual(self.native.progressValue, 5)
        self.native.wasCancelled = True
        self.assertTrue(self.display.wasCancelled)

    def test_both_display_failures_request_cancel_instead_of_raising(self):
        self.ui.palettes.add = lambda *args: None
        self.ui.createProgressDialog = lambda: None
        self.assertFalse(self.display.show('Title', 'Preparing', 0, 1))
        self.assertTrue(self.display.wasCancelled)
        self.display.message = 'Still safe'
        self.display.hide()

    def test_cleanup_error_does_not_mask_backup_result(self):
        self.display.show('Title', 'Preparing', 0, 1)
        def fail():
            raise RuntimeError('already closing')
        self.palette.deleteMe = fail
        self.display.hide()
        self.assertTrue(self.display.warnings)

    def test_backup_completes_when_bridge_returns_no_ack(self):
        import os
        import tempfile
        from test_core import Adapter, item
        from FusionBackupManager.core import Backup
        self.display.show('Title', 'Preparing', 0, 1)
        self.display.ready = True
        self.palette.sendInfoToHTML = lambda *args: None
        def progress(result, name):
            self.display.maximumValue = max(1, result['total'])
            self.display.progressValue = result['completed']
            self.display.message = name
            self.display.poll()
        with tempfile.TemporaryDirectory(dir=os.environ.get('FBM_TEST_TMP')) as root:
            result = Backup(root, Adapter([item()]), progress=progress,
                            activity=lambda event: setattr(self.display, 'message', event['phase']),
                            cancelled=lambda: self.display.wasCancelled).run([])
            self.assertEqual(result['status'], 'SUCCESS')
            self.assertEqual(result['exported'], 1)
            saved = json.loads((Path(root) / 'last-result.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['status'], 'SUCCESS')
            self.assertEqual(saved['errors'], [])
        self.display.hide()
