from pathlib import Path
import os
from types import SimpleNamespace as NS
import tempfile
import unittest
from FusionBackupManager.core import Cancelled
from FusionBackupManager.fusion_adapter import FusionAdapter


class Collection:
    def __init__(self, items):
        self.items = items
    @property
    def count(self):
        return len(self.items)
    def item(self, index):
        return self.items[index]


def source(identity='file', version=1):
    project = NS(id='project', name='Project')
    root = NS(id='root', name='root', isRoot=True, dataFiles=Collection([]), dataFolders=Collection([]))
    project.rootFolder = root
    data = NS(id=identity, versionId=f'{identity}:v{version}', versionNumber=version, name='Part',
              parentProject=project, parentFolder=root, fileExtension='f3d', dateModified=123,
              hasChildReferences=False, childReferences=Collection([]), isComplete=True)
    root.dataFiles.items.append(data)
    return data


class Manager:
    def __init__(self):
        self.calls = []
        self.success = True
    def createFusionArchiveExportOptions(self, path):
        self.calls.append('f3d')
        return path
    def createSTEPExportOptions(self, path):
        self.calls.append('step')
        return path
    def execute(self, path):
        Path(path).write_bytes(b'export')
        return self.success


class Document:
    def __init__(self, data):
        self.dataFile = data
        self.isModified = False
        self.isValid = True
        self.closed = []
        self.design = NS(exportManager=Manager())
    def close(self, save):
        self.closed.append(save)
        return True
    def activate(self):
        return True


class Documents(Collection):
    def __init__(self, items=()):
        super().__init__(list(items))
        self.opened = []
    def open(self, data, visible):
        document = Document(data)
        self.opened.append((document, visible))
        return document


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.data = source()
        self.docs = Documents()
        self.app = NS(documents=self.docs, activeDocument=None)
        self.fusion = NS(FusionDocument=NS(cast=lambda doc: doc))
        self.adapter = FusionAdapter(self.app, self.fusion)
        self.item = self.adapter.enumerate([self.data.parentProject], lambda: False)[0][0]
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('FBM_TEST_TMP'))
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'output.f3d'

    def test_export_owns_document_and_closes_without_save(self):
        self.adapter.export(self.item, 'f3d', self.path)
        document, visible = self.docs.opened[0]
        self.assertFalse(visible)
        self.assertEqual(document.closed, [False])
        self.assertEqual(document.design.exportManager.calls, ['f3d'])
        self.assertTrue(self.path.exists())

    def test_activity_reports_scan_projects_folders_and_actual_export_steps(self):
        events = []
        adapter = FusionAdapter(self.app, self.fusion, activity=events.append)
        other = source('other')
        other.parentProject.name = 'Other Project'
        adapter.enumerate([self.data.parentProject, other.parentProject], lambda: False)
        scans = [event for event in events if event['phase'] == 'enumerating']
        self.assertTrue(any(event['project'] == 'Other Project' and event['project_index'] == 2
                            and event['project_total'] == 2 for event in scans))
        self.assertTrue(any(event['phase'] == 'references' and event['file'] == 'Part' for event in events))
        events.clear()
        adapter.export(self.item, 'f3d', self.path)
        self.assertEqual([event['phase'] for event in events], ['references', 'opening', 'exporting', 'closing'])

    def test_open_user_document_protected_even_clean_or_other_version(self):
        for dirty in (False, True):
            document = Document(source(version=2))
            document.isModified = dirty
            self.docs.items = [document]
            with self.assertRaisesRegex(RuntimeError, 'open in Fusion'):
                self.adapter.export(self.item, 'f3d', self.path)
            self.assertFalse(document.closed)
            self.assertFalse(self.docs.opened)

    def test_transitive_open_dependency_protected(self):
        child, grandchild = source('child'), source('grandchild')
        self.data.childReferences = Collection([child])
        child.childReferences = Collection([grandchild])
        self.docs.items = [Document(grandchild)]
        with self.assertRaisesRegex(RuntimeError, 'open in Fusion'):
            self.adapter.export(self.item, 'step', self.path)
        self.assertEqual(self.docs.opened, [])

    def test_version_mismatch_and_incomplete_cloud_refused(self):
        self.data.versionId = 'changed'
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            self.adapter.export(self.item, 'f3d', self.path)
        self.data.versionId = self.item.version_id
        self.data.isComplete = False
        with self.assertRaisesRegex(RuntimeError, 'processing'):
            self.adapter.export(self.item, 'f3d', self.path)

    def test_export_failure_closes_only_owned_document(self):
        original_open = self.docs.open
        def open_failed(data, visible):
            doc = original_open(data, visible)
            doc.design.exportManager.success = False
            return doc
        self.docs.open = open_failed
        with self.assertRaisesRegex(RuntimeError, 'failure'):
            self.adapter.export(self.item, 'f3d', self.path)
        self.assertEqual(self.docs.opened[0][0].closed, [False])

    def test_reused_document_never_closed(self):
        existing = Document(None)
        self.docs.items = [existing]
        self.docs.open = lambda data, visible: existing
        with self.assertRaisesRegex(RuntimeError, 'reused'):
            self.adapter.export(self.item, 'f3d', self.path)
        self.assertEqual(existing.closed, [])

    def test_opened_version_mismatch_refused_and_closed(self):
        wrong = Document(source('wrong'))
        self.docs.open = lambda data, visible: wrong
        with self.assertRaisesRegex(RuntimeError, 'match'):
            self.adapter.export(self.item, 'f3d', self.path)
        self.assertEqual(wrong.closed, [False])

    def test_outside_project_reference_and_cycle_deduplicated(self):
        child = source('child', version=3)
        child.parentProject.id = 'outside'
        self.data.hasChildReferences = child.hasChildReferences = True
        self.data.childReferences = Collection([child])
        child.childReferences = Collection([self.data])
        items, warnings = self.adapter.enumerate([self.data.parentProject], lambda: False)
        self.assertEqual(len(items), 2)
        self.assertEqual(warnings, [])
        self.assertEqual(items[0].references[0]['project_id'], 'outside')
        self.assertEqual(items[1].version, 3)
        self.assertEqual(items[0].reference_state, 'external')

    def test_inaccessible_references_mark_unknown(self):
        self.data.hasChildReferences = True
        self.data.childReferences = Collection([])
        items, _ = self.adapter.enumerate([self.data.parentProject], lambda: False)
        self.assertEqual(items[0].reference_state, 'unknown')
        self.assertTrue(items[0].warnings)

    def test_folder_error_retains_other_project(self):
        bad = NS(rootFolder=None)
        items, warnings = self.adapter.enumerate([bad, self.data.parentProject], lambda: False)
        self.assertEqual(len(items), 1)
        self.assertTrue(warnings)

    def test_cancel_during_enumeration(self):
        with self.assertRaises(Cancelled):
            self.adapter.enumerate([self.data.parentProject], lambda: True)

    def test_configurations_require_manual_validation(self):
        self.data.isConfiguredDesign = True
        with self.assertRaisesRegex(RuntimeError, 'Configured'):
            self.adapter.export(self.item, 'f3d', self.path)

    def test_download_accepts_unambiguous_original_filename(self):
        self.item.extension = 'dxf'
        self.path = self.path.with_suffix('.dxf')
        def download(path, handler):
            (Path(path).parent / 'original.dxf').write_bytes(b'dxf content')
            return True
        self.data.download = download
        self.adapter.export(self.item, 'dxf', self.path)
        self.assertEqual(self.path.read_bytes(), b'dxf content')

    def test_download_missing_or_ambiguous_output_is_not_success(self):
        self.item.extension = 'dxf'
        self.path = self.path.with_suffix('.dxf')
        self.data.download = lambda path, handler: True
        with self.assertRaisesRegex(RuntimeError, 'expected output is missing'):
            self.adapter.export(self.item, 'dxf', self.path)
        (self.path.parent / 'a.dxf').write_bytes(b'a')
        (self.path.parent / 'b.dxf').write_bytes(b'b')
        with self.assertRaisesRegex(RuntimeError, 'matching staged files=2'):
            self.adapter.export(self.item, 'dxf', self.path)

    def test_iterator_yields_before_reading_later_projects(self):
        accessed = []
        class LaterRoot:
            id = 'later-root'
            @property
            def dataFiles(self):
                accessed.append('later')
                raise ConnectionError('later project unavailable')
            dataFolders = Collection([])
        later = NS(id='later', name='Later', rootFolder=LaterRoot())
        warnings = []
        iterator = self.adapter.iter_items([self.data.parentProject, later], lambda: False, warnings)
        first = next(iterator)
        self.assertEqual(first.file_id, self.data.id)
        self.assertEqual(accessed, [])
        self.assertEqual(list(iterator), [])
        self.assertEqual(accessed, ['later'])
        self.assertTrue(any('Later' in warning for warning in warnings))

    def test_reference_collection_checks_cancel_between_items(self):
        stop = [False]
        calls = []
        class SlowReferences:
            count = 10000
            def item(self, index):
                calls.append(index)
                stop[0] = True
                return source('child-' + str(index))
        self.data.hasChildReferences = True
        self.data.childReferences = SlowReferences()
        with self.assertRaises(Cancelled):
            self.adapter.enumerate([self.data.parentProject], lambda: stop[0])
        self.assertEqual(calls, [0])

    def test_folder_cycles_are_detected_instead_of_looping(self):
        root = self.data.parentProject.rootFolder
        root.dataFolders = Collection([root])
        items, warnings = self.adapter.enumerate([self.data.parentProject], lambda: False)
        self.assertEqual(len(items), 1)
        self.assertTrue(any('cyclic folder' in warning for warning in warnings))

    def test_deep_reference_chain_does_not_recurse_on_python_stack(self):
        current = self.data
        for index in range(1100):
            child = source('deep-' + str(index))
            current.hasChildReferences = True
            current.childReferences = Collection([child])
            current = child
        items, warnings = self.adapter.enumerate([self.data.parentProject], lambda: False)
        self.assertEqual(len(items), 1101)
        self.assertEqual(warnings, [])

    def test_trace_records_operation_before_blocking_folder_getter(self):
        events = []
        adapter = FusionAdapter(self.app, self.fusion)
        adapter.set_trace(events.append)
        class BadRoot:
            id = 'bad-root'
            @property
            def dataFiles(self):
                self_test.assertEqual(events[-1]['phase'], 'listing_files')
                raise ConnectionError('cloud API unavailable')
            dataFolders = Collection([])
        self_test = self
        project = NS(id='bad', name='Bad Project', rootFolder=BadRoot())
        items, warnings = adapter.enumerate([project], lambda: False)
        self.assertEqual(items, [])
        self.assertTrue(warnings)
        self.assertTrue(any(event['project'] == 'Bad Project' for event in events))

    def test_real_adapter_streams_through_backup_engine(self):
        from FusionBackupManager.core import Backup, Store
        result = Backup(self.temp.name, self.adapter).run([self.data.parentProject])
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(result['exported'], 1)
        self.assertTrue(result['total_known'])
        self.assertEqual(len(Store(self.temp.name).data['files']), 1)
        self.assertEqual(self.docs.opened[0][0].closed, [False])
        self.assertTrue(any('ACTIVITY' in line for line in Path(result['log_path']).read_text(encoding='utf-8').splitlines()))


if __name__ == '__main__':
    unittest.main()
