import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from FusionBackupManager.core import (Backup, Item, Store, atomic_json, component,
                                      fingerprint, verify, formats, DEFAULTS, publish)


def item(version=1, identity='file', **kwargs):
    return Item(identity, f'{identity}:version:{version}', version, 'Part', 'project', 'Project', **kwargs)


class Adapter:
    def __init__(self, items, failures=(), warnings=()):
        self.items, self.failures, self.warnings = items, failures, list(warnings)
        self.calls = []

    def enumerate(self, projects, cancelled):
        return self.items, self.warnings

    def export(self, source, fmt, path):
        self.calls.append((source.key(), fmt))
        path.write_bytes((source.version_id + ':' + fmt).encode())
        if source.file_id in self.failures:
            raise ConnectionError('offline')


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('FBM_TEST_TMP'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_backup(self, items=None, **kwargs):
        adapter = Adapter(items or [item()])
        return Backup(self.root, adapter, **kwargs).run(['project']), adapter

    def test_initial_incremental_versions_and_cloud_deletion(self):
        first, adapter = self.run_backup()
        self.assertEqual(first['status'], 'SUCCESS')
        self.assertEqual(len(adapter.calls), 1)
        original = Store(self.root).data['files'][0]
        old_bytes = (self.root / original['local_path']).read_bytes()
        second, adapter = self.run_backup()
        self.assertEqual(second['skipped'], 1)
        self.assertEqual(adapter.calls, [])
        third, _ = self.run_backup([item(5)])
        self.assertEqual(third['exported'], 1)
        self.assertEqual(len(Store(self.root).data['files']), 2)
        self.assertEqual((self.root / original['local_path']).read_bytes(), old_bytes)
        Backup(self.root, Adapter([])).run([])
        self.assertEqual(len(Store(self.root).data['files']), 2)

    def test_missing_and_corrupted_files_reexport_without_overwrite(self):
        self.run_backup()
        entry = Store(self.root).data['files'][0]
        target = self.root / entry['local_path']
        target.unlink()
        result, adapter = self.run_backup()
        self.assertEqual(result['exported'], 1)
        target.write_bytes(b'damage')
        result, _ = self.run_backup()
        self.assertEqual(result['exported'], 1)
        self.assertEqual(target.read_bytes(), b'damage')
        self.assertEqual(len(list(self.root.rglob('*.f3d'))), 2)

    def test_failure_continues_and_does_not_publish_temporary_file(self):
        adapter = Adapter([item(identity='bad'), item(identity='good')], ['bad'])
        result = Backup(self.root, adapter).run([])
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual((result['failed'], result['exported']), (1, 1))
        self.assertEqual(len(Store(self.root).data['files']), 1)
        self.assertFalse(list(self.root.rglob('.export-*')))
        self.assertIn('offline', Path(result['log_path']).read_text(encoding='utf-8'))

    def test_all_failed_status(self):
        result = Backup(self.root, Adapter([item()], ['file'])).run([])
        self.assertEqual(result['status'], 'FAILED')

    def test_empty_export_rejected(self):
        adapter = Adapter([item()])
        adapter.export = lambda source, fmt, path: path.touch()
        result = Backup(self.root, adapter).run([])
        self.assertEqual(result['failed'], 1)
        self.assertEqual(Store(self.root).data['files'], [])

    def test_cancel_keeps_finished_file(self):
        stop = [False]
        def progress(result, name):
            if result['completed'] == 1:
                stop[0] = True
        result, adapter = self.run_backup([item(identity='a'), item(identity='b')],
                                           progress=progress, cancelled=lambda: stop[0])
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['completed'], 1)
        self.assertEqual(len(Store(self.root).data['files']), 1)
        self.assertFalse((self.root / '.backup.lock').exists())

    def test_external_reference_graph_and_manual_status(self):
        child = item(identity='child')
        parent = item(reference_state='external', references=[dict(file_id=child.file_id,
                      version_id=child.version_id, version=1, project_id='other')])
        result, adapter = self.run_backup([parent, child])
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(adapter.calls[0][1], 'step')
        self.assertTrue(result['references'][0]['backed_up'])
        self.assertEqual(len(result['manual']), 1)
        second, adapter = self.run_backup([parent, child])
        self.assertEqual(second['status'], 'PARTIAL')
        self.assertEqual(second['skipped'], 2)
        self.assertEqual(adapter.calls, [])

    def test_unknown_references_never_native_success(self):
        result, adapter = self.run_backup([item(reference_state='unknown')])
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(adapter.calls[0][1], 'step')

    def test_enumeration_warning_prevents_success(self):
        result = Backup(self.root, Adapter([], warnings=['permission denied'])).run([])
        self.assertEqual(result['status'], 'PARTIAL')

    def test_corrupt_manifest_is_preserved_and_no_export(self):
        path = self.root / 'manifest.json'
        path.write_text('{broken')
        result, adapter = self.run_backup()
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(adapter.calls, [])
        self.assertEqual(path.read_text(), '{broken')
        self.assertFalse((self.root / '.backup.lock').exists())

    def test_manifest_schema_rejected(self):
        for data in [{'schema_version': 99, 'files': [], 'runs': []},
                     {'schema_version': 1, 'files': [{}], 'runs': []}]:
            atomic_json(self.root / 'manifest.json', data)
            result, adapter = self.run_backup()
            self.assertEqual(result['status'], 'FAILED')
            self.assertEqual(adapter.calls, [])

    def test_previous_manifest_is_retained(self):
        self.run_backup()
        self.assertTrue((self.root / 'manifest.previous.json').is_file())
        self.assertEqual(json.loads((self.root / 'manifest.previous.json').read_text())['schema_version'], 1)

    def test_atomic_json_failure_preserves_existing_manifest(self):
        path = self.root / 'manifest.json'
        atomic_json(path, {'old': True})
        with patch('FusionBackupManager.core.os.replace', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                atomic_json(path, {'new': True})
        self.assertEqual(json.loads(path.read_text()), {'old': True})
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_disk_full_and_link_failure_never_claim_verified(self):
        with patch('FusionBackupManager.core.publish', side_effect=OSError('disk full')):
            result, _ = self.run_backup()
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(Store(self.root).data['files'], [])
        self.assertFalse(list(self.root.rglob('*.f3d')))

    def test_exclusive_lock_is_not_removed_by_second_run(self):
        path = self.root / '.backup.lock'
        path.write_text('other run')
        with self.assertRaises(FileExistsError):
            self.run_backup()
        self.assertEqual(path.read_text(), 'other run')

    def test_path_escape_is_refused(self):
        store = Store(self.root)
        for path in ('../escape', str(self.root.parent / 'escape'), '', '.'):
            with self.assertRaises(ValueError):
                store.safe_path(path)

    def test_windows_names_and_collisions(self):
        for name in ('CON', 'nul.txt', 'COM1', 'LPT9', '...', 'a<>:"/\\|?*\x00'):
            value = component(name, 'id')
            self.assertFalse(any(c in value for c in '<>:"/\\|?*\x00'))
            self.assertNotIn(value.split('.')[0].upper(), ['CON', 'COM1'])
        self.assertNotEqual(component('same', 'a'), component('same', 'b'))
        self.assertEqual(component('same', 'a'), component('same', 'a'))

    def test_incremental_false_keeps_previous_copy(self):
        self.run_backup()
        result, _ = self.run_backup(settings={'incremental': False})
        self.assertEqual(result['exported'], 1)
        self.assertEqual(len(list(self.root.rglob('*.f3d'))), 2)

    def test_verifier_reports_damage(self):
        self.run_backup()
        self.assertTrue(verify(self.root)[0]['valid'])
        entry = Store(self.root).data['files'][0]
        (self.root / entry['local_path']).write_bytes(b'damage')
        self.assertFalse(verify(self.root)[0]['valid'])

    def test_native_non_design_and_unsupported_types(self):
        result, adapter = self.run_backup([item(extension='txt')])
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(adapter.calls[0][1], 'txt')
        for extension in ('f2d', 'fsch', '../f3d', ''):
            with self.assertRaises(ValueError):
                formats(item(extension=extension), DEFAULTS)

    def test_step_option_and_format_change(self):
        self.run_backup()
        result, adapter = self.run_backup(settings={'export_step': True})
        self.assertEqual(result['exported'], 1)
        self.assertEqual([x[1] for x in adapter.calls], ['step'])

    def test_publish_never_overwrites_existing_file(self):
        source = self.root / 'temp'
        target = self.root / 'existing'
        source.write_bytes(b'new')
        target.write_bytes(b'old')
        with self.assertRaises(FileExistsError):
            publish(source, target)
        self.assertEqual(target.read_bytes(), b'old')

    def test_cancel_between_formats_retains_export_count(self):
        adapter = Adapter([item()])
        original = adapter.export
        stop = [False]
        def export(source, fmt, path):
            original(source, fmt, path)
            stop[0] = True
        adapter.export = export
        result = Backup(self.root, adapter, settings={'export_step': True}, cancelled=lambda: stop[0]).run([])
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['exported'], 1)
        self.assertEqual(len(Store(self.root).data['files']), 1)

    def test_step_only_is_explicitly_partial(self):
        result, _ = self.run_backup(settings={'export_fusion_archive': False, 'export_step': True})
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertTrue(result['warnings'])

    def test_final_report_write_failure_is_failed(self):
        original = atomic_json
        def write(path, data):
            if Path(path).name == 'last-result.json':
                raise OSError('disk full')
            return original(path, data)
        with patch('FusionBackupManager.core.atomic_json', side_effect=write):
            result, _ = self.run_backup()
        self.assertEqual(result['status'], 'FAILED')
        self.assertTrue(result['errors'])
        self.assertEqual(len(Store(self.root).data['files']), 1)

    def test_destination_path_limit_fails_before_export(self):
        if os.name != 'nt':
            self.skipTest('Windows path policy')
        deep = self.root / ('a' * max(1, 180 - len(str(self.root)) - 1))
        result = Backup(deep, Adapter([item()])).run([])
        self.assertEqual(result['status'], 'FAILED')
        self.assertTrue(any('too long' in value for value in result['errors']))

    def test_long_path_compacted_and_hierarchy_mapped_without_export_failure(self):
        deep = item(folders=[('Long folder ' + str(i) + 'x' * 30, 'folder-' + str(i)) for i in range(12)])
        result, adapter = self.run_backup([deep])
        self.assertEqual(result['status'], 'SUCCESS')
        entry = Store(self.root).data['files'][0]
        self.assertIn('_deep__', entry['local_path'])
        self.assertLessEqual(len(str(self.root / entry['local_path'])), 240)
        self.assertEqual(entry['folders'], [list(value) for value in deep.folders])
        result, adapter = self.run_backup([deep])
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(adapter.calls, [])

    def test_compact_repair_never_replaces_existing_file(self):
        deep = item(folders=[('Folder ' + str(i), str(i)) for i in range(12)])
        self.run_backup([deep])
        entry = Store(self.root).data['files'][0]
        original = self.root / entry['local_path']
        original.write_bytes(b'damage')
        result, _ = self.run_backup([deep])
        self.assertEqual(result['exported'], 1)
        self.assertEqual(original.read_bytes(), b'damage')
        self.assertNotEqual(Store(self.root).data['files'][-1]['local_path'], entry['local_path'])

    def test_cleanup_sharing_violation_preserves_success_and_manifest(self):
        import shutil
        real_remove = shutil.rmtree
        calls = []
        def remove(path, *args, **kwargs):
            if Path(path).name.startswith('.export-'):
                calls.append(str(path))
                raise PermissionError('sharing violation')
            return real_remove(path, *args, **kwargs)
        with patch('FusionBackupManager.core.shutil.rmtree', side_effect=remove), patch('FusionBackupManager.core.time.sleep'):
            result, _ = self.run_backup()
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['exported'], 1)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertTrue(result['warnings'])
        self.assertEqual(len(calls), 3)
        self.assertTrue(verify(self.root)[0]['valid'])

    def test_short_path_names_remain_compatible(self):
        store = Store(self.root)
        source = item()
        old_name = (component(source.project_name, source.project_id) + '/' + component(source.name, source.file_id)
                    + '_v1__' + __import__('hashlib').sha256(source.version_id.encode()).hexdigest()[:16] + '.f3d')
        self.assertEqual(store.relative_destination(source, 'f3d').as_posix(), old_name)

    def test_detailed_activity_tracks_real_export_steps_and_context(self):
        events = []
        source = item(folders=[('Parts', 'parts-id')])
        result, _ = self.run_backup([source], activity=lambda event: events.append(event))
        self.assertEqual(result['status'], 'SUCCESS')
        phases = [event['phase'] for event in events]
        for phase in ('preparing', 'checking', 'exporting', 'verifying', 'publishing', 'recording', 'file_done', 'finalizing'):
            self.assertIn(phase, phases)
        self.assertLess(phases.index('exporting'), phases.index('verifying'))
        self.assertLess(phases.index('verifying'), phases.index('publishing'))
        export = next(event for event in events if event['phase'] == 'exporting')
        self.assertEqual((export['project'], export['folder'], export['file'], export['format']), ('Project', 'Parts', 'Part', 'f3d'))
        self.assertEqual(events[-1]['counts']['completed'], 1)
        self.assertTrue(events[-1]['counts']['total_known'])
        events.clear()
        self.run_backup([source], activity=lambda event: events.append(event))
        self.assertIn('skipped', [event['phase'] for event in events])
        self.assertNotIn('exporting', [event['phase'] for event in events])

    def test_deep_folder_id_chains_remain_distinct(self):
        store = Store(self.root)
        a = item(folders=[('same', 'a' + str(i)) for i in range(12)])
        b = item(folders=[('same', 'b' + str(i)) for i in range(12)])
        self.assertNotEqual(store.relative_destination(a, 'f3d'), store.relative_destination(b, 'f3d'))


if __name__ == '__main__':
    unittest.main()
