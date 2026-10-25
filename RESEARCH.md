# OSS・公式 API 調査（2026-10-21）

## 指定 OSS

[tomerbek/FusionProjectExporter](https://github.com/tomerbek/FusionProjectExporter)、調査コミット `ea3651a40d6e00d67dd19dd55879fca03bb2fb07`。取得したコードは作業領域の `research/FusionProjectExporter` に保持。README だけでなく本体・ツールバー・manifest・LICENSE を読んだ。

既存コードはフォルダ再帰、複数スコープ、形式選択、版 ID の manifest 差分、CustomEvent でコマンド終了後に実行する仕組みを備える。単純なフォルダエクスポートなら再利用候補となる。

一方、調査版の本体は既存ファイルのハッシュを照合しない。壊れた manifest は空として扱い、JSON は直接書き込む。開いている設計を再利用して出力し、F3D が見つからない場合に F3Z を探すが、外部参照の完全性の証拠にはならない。本要件の安全性・復元性には追加設計が必要と判断した。

再利用範囲：ツールバーの command execute → destroy → queued CustomEvent の起動パターンを、本アドインの設定と起動処理へ改修して使用。API 使用方法と階層列挙も参考にした。出力エンジン、SHA-256、atomic manifest、版保持、参照グラフ、テストは分離実装。元コードの MIT 通知を `FusionBackupManager/LICENSE.FusionProjectExporter` に原文のまま保存し、インストール用フォルダと配布物にも含める。

## 公式 API の確認

|一次資料|確認内容・採用判断|
|---|---|
|[Data](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_Data.htm)|dataProjects は active Hub のプロジェクト。Current は activeProject。Hub の自動変更はしない|
|[DataFile](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_DataFile.htm)|id、versionId、versionNumber、dateModified、parentProject、parentFolder、isComplete、hasChildReferences、childReferences を使用。元データの変更メソッドは使用しない|
|[Documents.open](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_Documents_open.htm)|保存済み DataFile と visible=False で開く。戻り値と実際に開いた版を検査する|
|[Document.close](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_Document_close.htm)|コマンド関連イベント内の close は非対応。CustomEvent で実行し、アドイン所有文書だけ close(False)|
|[Document](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_Document.htm)|isModified、dataFile を照合。既存ユーザー文書は使わない|
|[Archive 作成](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_ExportManager_createFusionArchiveExportOptions.htm)、[オプション](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_FusionArchiveExportOptions.htm)|ファイル名と省略可能なコンポーネントを指定し execute する。外部参照入りの完全な F3Z を保証する設定を確認できず、安全なフォールバックを採用|
|[ExportManager](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_ExportManager.htm)|createSTEPExportOptions と execute。STEP は形状用で履歴・リンクの復元保証を付けない|
|[download](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_DataFile_download.htm)|非 Fusion データのみ。同期ダウンロードは handler=None、真偽値を検査|
|[CustomEvent](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_CustomEvent.htm)|メインスレッドへ実行を送るイベント。API をワーカースレッドから呼ばない|
|[ProgressDialog](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_ProgressDialog.htm)|maximumValue、progressValue、message、wasCancelled を使用|
|[ValidateInputsEventArgs](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_ValidateInputsEventArgs.htm)、[InputChangedEventArgs](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_InputChangedEventArgs.htm)、[CommandEventArgs](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_CommandEventArgs.htm)|各イベントの inputs、command、入力検証・実行失敗のプロパティを確認|

Autodesk の [2026 年 9 月の更新情報](https://help.autodesk.com/view/fusion360/ENU/?contextId=APIWhatsNew) には Documents.open の visible 引数の修正が記載されている。非表示で開く動作は版により差があり得るため実機確認項目とした。

インストール済み Fusion の SDK も読み取りのみで確認した。production `736d2b4c5e6b95e77c11721da222cf21c6a1fc1f` の `Python/AddInSamples/CommandSample/config.py` は `tools_tab_id = "ToolsTab"` を使用し、CommandSample.py が Utilities タブと説明している。表示言語依存のタブ名検索を避け、この ID を採用。

調査は API の存在・引数の確認であり、出力の完全性や復元成功の証明ではない。Fusion での実機確認は TESTING.md に分けた。

## 左寄せ進捗表示（2026-10-26）

[ProgressDialog](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_ProgressDialog.htm) にはテキスト配置のプロパティがない。[Palettes.add](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_Palettes_add.htm) と [HTML パレットの公式説明](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/Palettes_UM.htm) に従い、ローカル HTML を表示し CSS で左寄せを指定する。`sendInfoToHTML`、`incomingFromHTML`、`closed` を使う。HTML の受信準備が整うまで最新表示を保持し、ready 通知で送信する。閉じる操作も停止要求として扱う。追加の doEvents は行わず、既存の安全な区切りでのみイベント処理する。


## 標準進捗画面への復帰（1.0.5、2026-10-26）

ユーザーの方針により、HTML パレットの進捗表示を撤回し、1.0.2 の標準 ProgressDialog へ戻した。上記の HTML に関する記載は 1.0.3〜1.0.4 の調査履歴。現行版は HTML 通信を行わない。詳細な進捗テキストは progress.py のまま維持する。
