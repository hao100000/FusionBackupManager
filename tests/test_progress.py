import unittest
from FusionBackupManager.progress import progress_text


class ProgressTextTests(unittest.TestCase):
    def test_enumeration_shows_project_folder_and_discovered_not_fake_percent(self):
        text = progress_text({}, dict(phase='enumerating', project='ABU2027', folder='Robot/Parts',
                                     file='', discovered=42, project_index=2, project_total=5))
        self.assertIn('ABU2027', text)
        self.assertIn('Robot/Parts', text)
        self.assertIn('発見済み 42 件', text)
        self.assertIn('2 / 5', text)
        self.assertIn('完了 0 / 確認中', text)

    def test_export_shows_phase_file_format_and_counters(self):
        text = progress_text(dict(total_known=True, total=12, completed=3, exported=2, skipped=1, failed=0),
                             dict(phase='exporting', project='Project B', folder='Parts', file='Motor', format='f3d'))
        for expected in ('ローカルへ出力中', 'Project B', 'Motor', 'F3D', '完了 3 / 12', '今回保存 2', 'スキップ 1'):
            self.assertIn(expected, text)

    def test_cloud_names_cannot_be_interpreted_as_progress_macros(self):
        text = progress_text({}, dict(phase='opening', project='100%p', folder='test%m', file='part%v\nname'))
        for macro in ('%p', '%m', '%v'):
            self.assertNotIn(macro, text)
        self.assertIn('part％v name', text)
