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
        self.ui = NS(palettes=NS(itemById=lambda _: None, add=lambda *args: self.palette))
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

    def test_create_failure_is_explicit(self):
        self.ui.palettes.add = lambda *args: None
        with self.assertRaisesRegex(RuntimeError, 'Could not create'):
            self.display.show('Title', 'Preparing', 0, 1)
        self.display.hide()

    def test_text_uses_safe_dom_and_explicit_left_alignment(self):
        html = (Path(self.module.__file__).with_name('progress.html')).read_text(encoding='utf-8')
        self.assertIn('text-align: left', html)
        self.assertIn('white-space: pre-wrap', html)
        self.assertIn('overflow-wrap: anywhere', html)
        self.assertIn('.textContent = state.message', html)
        self.assertNotIn('innerHTML', html)
