"""Human-readable progress without Fusion dependencies."""
PHASES = {
    'preparing': 'バックアップを準備中',
    'enumerating': 'クラウドのファイルを列挙中',
    'references': '外部参照を調査中',
    'checking': '保存済みバックアップと差分・ハッシュを確認中',
    'opening': '保存済みの設計を開いています',
    'exporting': 'ローカルへ出力中（Fusion の処理完了待ち）',
    'downloading': 'クラウドからダウンロード中',
    'verifying': '出力ファイルのサイズ・SHA-256 を検証中',
    'publishing': '検証したファイルを正式な保存先へ配置中',
    'recording': 'バックアップ履歴を記録中',
    'closing': 'バックアップ用に開いた設計を閉じています',
    'skipped': '保存済みファイルは正常です。この形式はスキップ',
    'file_done': 'このファイルの処理が完了',
    'file_error': 'このファイルは失敗。次のファイルへ進みます',
    'finalizing': '参照先の保存状態を確認・結果を記録中',
}


def progress_text(counts, event):
    def clean(value):
        # ProgressDialog interprets %p, %v and %m even inside cloud names.
        return str(value or '—').replace('\r', ' ').replace('\n', ' ').replace('%', '％')
    phase = event.get('phase', 'preparing')
    heading = PHASES.get(phase, phase)
    if phase in ('enumerating', 'references') and 'discovered' in event:
        heading += f" ／ 発見済み {event['discovered']} 件（総数を確認中）"
    lines = [heading, 'プロジェクト: ' + clean(event.get('project')),
             'フォルダ: ' + clean(event.get('folder')),
             'ファイル: ' + clean(event.get('file'))]
    if event.get('project_total'):
        lines.append(f"対象プロジェクトの走査: {event.get('project_index', 0)} / {event['project_total']}")
    else:
        lines.append('形式: ' + clean(event.get('format')).upper())
    total = str(counts['total']) if counts.get('total_known') else '確認中'
    lines.append(f"完了 {counts.get('completed', 0)} / {total} 件 ｜ 今回保存 {counts.get('exported', 0)} ｜ スキップ {counts.get('skipped', 0)} ｜ エラー {counts.get('failed', 0)}")
    return '\n'.join(lines)
