# Fusion Backup Manager 1.0.2

Autodesk Fusion（Windows）の保存済みクラウドデータをローカルへ半自動バックアップする Python アドインです。追加サービスへの登録、API キー、pip パッケージは不要です。

実機ログとユーザー報告では、一部の F3D 出力・読み込みを確認しています。**1.0.2 の進捗画面の実機表示と、バックアップ全体の完全復元は未検証です。** 自動テストの結果は [TEST_RESULTS.md](TEST_RESULTS.md)、実機確認手順は [TESTING.md](TESTING.md) を参照してください。`verified` はサイズ・SHA-256 の検証を意味し、Fusion で開ける保証ではありません。

## 処理内容が分かる進捗表示（1.0.2）

列挙中は、現在のプロジェクト・フォルダ・ファイル・発見済み件数と、選択したプロジェクトの走査番号を表示します。この段階では総ファイル数が未確定のため「総数を確認中」と表示します。選択範囲外の参照先を調査する際は、実際の参照先プロジェクト名に切り替わります。

保存中は「差分・ハッシュ確認」「外部参照調査」「保存済み設計を開く」「F3D／STEP出力」「SHA-256検証」「正式な保存先へ配置」「履歴記録」「スキップ」「完了／失敗」を表示します。完了／総数、今回保存、スキップ、エラーの件数も表示します。最後は「参照先の保存状態を確認・結果を記録中」です。

Fusion の単一の開く・出力・ダウンロード API の内部進捗率は取得していません。その間は直前の処理内容を表示して完了を待ちます。バックアップ用文書を開いている間に進捗表示のためだけの `doEvents` は追加しません。

更新時は新しい `progress.py` を含む内側のフォルダ全体を使用し、Fusion を再起動してください。実行中のバックアップには途中反映しません。

## 1.0.0 からの更新

2026-10-21 の実行で多発した長い保存パスを自動短縮するよう改善しました。詳細は [IMPROVEMENTS_1.0.1.md](IMPROVEMENTS_1.0.1.md) に記載しています。

Fusion のバックアップを終了して本アドインを停止し、内側のフォルダのコードと manifest を 1.0.1 に置き換えてください。読み込み済みモジュールの再利用を避けるため Fusion を再起動します。**同じバックアップ先で Skip Unchanged Files を有効にして再実行**すれば、検証済みの旧出力はスキップし、失敗した分を再試行します。既存のバックアップや manifest を消す必要はありません。

## Windows へのインストール

1. 配布 ZIP を `C:\FusionBackupManager` などに展開します。ソースから使う場合は、この README と同じ場所にある内側の `FusionBackupManager` フォルダを使用します。
2. インストールするフォルダは、直下に `FusionBackupManager.py`、`FusionBackupManager.manifest`、`core.py`、`fusion_adapter.py`、`__init__.py` があるものです。フォルダ名は `FusionBackupManager` のままにします。README がある外側のフォルダを登録しないでください。
3. Fusion を起動し、デザイン ワークスペース → ユーティリティ → スクリプトとアドインを開きます。
4. `+` → デバイスからスクリプトまたはアドインを追加／リンクする項目で、上記の内側のフォルダを選びます。従来のダイアログでファイル選択になる場合は、その中の `FusionBackupManager.py` を選択します。
5. アドイン一覧の `FusionBackupManager` を実行します。ユーティリティの Backup パネルに **Fusion Backup** が現れます。
6. 初回の実機確認が済んだ後、必要なら「起動時に実行」を有効にします。起動時に行うのはボタンの追加だけで、自動バックアップは始まりません。

標準フォルダへ置く方法もあります。公式資料にはバージョンによって `%APPDATA%\Autodesk\Autodesk Fusion\API\AddIns` と `%APPDATA%\Autodesk\FusionAddins` の案内があるため、上記の明示的なリンク登録を推奨します。[公式インストール手順](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/UsingSamplesFromGitHub_UM.htm)、[現在の管理ダイアログの説明](https://help.autodesk.com/view/fusion360/ENU/?guid=GUID-9701BBA7-EC0E-4016-A9C8-964AA4838954)。

Fusion 内では同梱 Python を使います。外部 Python のインストールは、単体テストや検証ツールを実行する場合だけ必要です。

## バックアップの実行

1. バックアップ対象の設計と、その参照先を Fusion 上で手動で閉じます。未保存の編集をアドインが保存することはありません。編集を残したい場合は開いたままでも構いませんが、その設計と依存するアセンブリの新規出力は失敗として報告されます。
2. データパネルで対象 Hub を選び、Fusion Backup を押します。
3. All Projects は現在の Hub 内のアクセス可能な全プロジェクト、Selected Projects は複数選択、Current Project はデータパネルで選択中のプロジェクトです。Hub の切替やプロジェクトの作成は行いません。
4. Output Directory に絶対パスを指定するか Browse で選択します。`C:\FusionBackups` のような短いパスを推奨します。OneDrive 等のローカル同期フォルダも指定できますが、クラウドへの同期完了は本アドインの検証対象外です。保存先は既存バックアップ専用のフォルダにします。
5. 通常は Fusion Archive、STEP for Assemblies、Skip Unchanged Files を有効にして Start Backup を押します。STEP for All Designs は任意です。旧版の保持は常に有効です。
6. Stop Backup で処理の区切りに中断できます。Fusion の単一 API 呼び出し中は完了まで待つことがあります。完了済みファイルは残ります。
7. 結果とログを確認します。手動 F3Z が必要な設計はファイル名を含む Fusion 上の場所を表示します。画面では件数を制限しており、全件は `last-result.json` とログにあります。結果表示後、バックアップフォルダとログをそれぞれ開けます。

バックアップ先の変更は次回起動時に Output Directory を変更します。設定は `%APPDATA%\FusionBackupManager\settings.json` に保存されます。プロジェクトの選択は ID で記録します。Hub を変えた後は選択を見直してください。

## 出力と差分の仕様

* プロジェクトとフォルダの階層を維持し、名前には ID の SHA-256 の先頭 16 桁を付けます。Windows 禁止文字・予約名・末尾の空白等を処理し、元の名前と ID はマニフェストに保存します。
* ファイル名にはバージョン番号とバージョン ID のハッシュが入ります。同じ保存先が既に存在する場合も上書きせず、別名を付けます。長いパスはまず表示名を短縮し、それでも深すぎる場合はプロジェクト配下の `_deep__<フォルダ ID 列のハッシュ>` へまとめます。元の全階層・名前は manifest の folders に保持します。保存先自体が極端に長く短縮でも収まらない場合だけ失敗します。
* 同一ファイル ID、バージョン ID、出力形式の記録があり、実ファイルのサイズと SHA-256 が一致する場合のみスキップします。STEP を後から追加した場合は不足する形式だけを出力します。
* 初回は列挙時点の最新版のみを保存します。以降は実行時に見つかった新バージョンを蓄積します。外部参照は参照 API が返した版を個別に処理します。過去の全バージョンを遡る機能はありません。
* クラウド上の削除や名前変更に合わせてローカルデータを削除しません。破損したローカルファイルも上書きせず残し、修復出力を別名で追加します。そのため検証ツールは過去の破損記録を引き続き報告する場合があります。
* 一時ディレクトリへの出力 → 存在・非ゼロサイズ・SHA-256 → 上書きしない公開 → マニフェスト更新の順です。マニフェストは一時ファイルと `os.replace` で更新し、前の版を `manifest.previous.json` に残します。
* 保存先ごとに `.backup.lock` を使い、同時実行を防ぎます。異常終了時の解除方法は TESTING.md に記載しています。ネットワーク共有や異なる PC からの同期経由の排他は保証しません。

出力は、プロジェクト別フォルダ、`manifest.json`（履歴・参照情報・実行結果）、`manifest.previous.json`、`last-result.json`、実行ごとの `logs/*.log` です。

## 外部参照と API の制約

外部参照なしの設計は `createFusionArchiveExportOptions` で F3D を出力します。外部参照あり／判定不明の設計は、参照先を再帰列挙して個別にバックアップし、親は既定で STEP を出力します。参照先が選択範囲外のプロジェクトにあっても検出します。アクセス失敗・参照判定失敗は警告します。

公式の [Archive API](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_ExportManager_createFusionArchiveExportOptions.htm) と [Archive オプション](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_FusionArchiveExportOptions.htm) では、この用途の完全な F3Z の包含・復元保証を確認できませんでした。拡張子だけを F3Z に変更することはしません。**外部参照あり／不明は常に手動対応が必要です。STEP と個別 F3D だけで元の参照構造を完全復元できるとは扱いません。**

参照 JSON の `backed_up` は「API から取得した同一 ID・版の F3D がローカルにありハッシュが一致する」ことを表します。子がさらに外部参照を持つ場合は false になります。アセンブリ内の参照版の一致、設計履歴、ジョイント等の復元は実機検証が必要です。

[DataFile.download](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_DataFile_download.htm) は非 Fusion データ専用です。TXT 等は元形式でダウンロードします。図面・電子設計・構成設計はこの初期版では対応済みとせず、失敗と手動対応として扱います。権限・ライセンス・クラウド処理待ちによる失敗も記録します。

実行結果は SUCCESS（要求された出力とバイト検証が完了）、PARTIAL（一部失敗・警告・手動対応）、FAILED（全ファイル失敗／基盤処理失敗）、CANCELLED（中断）です。SUCCESS でも実機復元の成功を意味しません。ネイティブ保存を無効にした STEP のみの設計バックアップは PARTIAL とします。Exported は今回出力が増えたファイル数、Skipped は要求形式が全て検証済みのファイル数です。一部形式だけ成功したファイルは Exported と Failed の両方に含まれる場合があります。

## 元データの保護

クラウド上の保存・削除・移動・参照更新・版変更を呼びません。バックアップ自身が開いた文書だけを保存せずに閉じます。既存の文書や参照先が開いている場合は出力しません。元のアクティブ文書へ戻します。開いた版が要求版と一致しない場合、変更状態が検出された場合も出力を拒否します。

Fusion API はメインスレッド上で使用し、文書の開閉を伴う処理はコマンド destroy 後の CustomEvent に送ります。[Document.close のコマンドイベント制約](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_Document_close.htm) に対応しています。

## 検証と復元

バイト検証は、README のあるフォルダで次を実行します（Python 3.10 以降）。バックアップを書き換えません。

```powershell
python .\verify_backup.py 'C:\FusionBackups'
```

終了コード 0 は全記録のバイト検証合格、1 は不一致または空の記録、2 はマニフェスト等の読み取り失敗です。これは復元テストではありません。

F3D はバックアップのコピーを使い、Fusion のファイル → 開く → コンピュータから開くで開きます。クラウドへのアップロードが必要な場合は、**元と別の復元試験用プロジェクト**にアップロードします。元データへ上書きしません。形状、寸法、部品数、タイムライン等を照合します。

アセンブリは Fusion UI のエクスポートで完全な F3Z を手動保存し、別プロジェクトで復元して参照版・ジョイントまで照合してください。F3Z の手動ファイルはアドインの検証済み記録へ自動登録しません。STEP は形状救済用で、履歴やリンクを保持しません。具体的な安全な検証手順・記録表は TESTING.md にあります。

## アンインストール

バックアップを停止し、スクリプトとアドインで本アドインを停止し、「起動時に実行」を解除します。リンク登録なら Unlink を選択し、その後インストールした `FusionBackupManager` フォルダを削除します。必要なら `%APPDATA%\FusionBackupManager` の設定も削除します。バックアップ先は別のデータなので保持してください。

## 開発・ライセンス

```powershell
python -m unittest discover -s tests -v
python -m compileall -q FusionBackupManager tests verify_backup.py build_release.py
python .\build_release.py
```

標準ライブラリのみを使用します。MIT。指定 OSS の調査と再利用範囲は [RESEARCH.md](RESEARCH.md)、設計は [DESIGN.md](DESIGN.md) にあります。FusionProjectExporter の通知は内側の `LICENSE.FusionProjectExporter` に保持しています。
