# 外部参照付きアセンブリの自動バックアップ調査

調査日：2026-11-01。配布するアドインから利用でき、マウス・キーボード操作に依存しない方法を比較した。実装・F3Z生成・復元の実機検証は未実施。現行1.0.8の動作とインストール内容は変更しない。

## 結論

画面操作以外にも候補がある。公開された型付きExportManager APIの制約と、クラウドAPI・内部テキストコマンドの可否を区別する必要がある。「APIでは無理なので画面操作しかない」という結論は不適切。

|方法|配布時の追加要件|確認できた根拠|未確認・課題|
|---|---|---|---|
|APS Data Managementのダウンロード生成|配布者のAPSアプリ登録、利用者のOAuthログイン・同意|公式のF3Z生成例と現行OpenAPIにダウンロード生成・ジョブ確認の定義あり|対象HubでのF3Z可用性、権限、参照版の一致、復元、費用・配布条件|
|Fusion内部のdata.fileExportコマンド|Fusionにログインした状態。追加OAuthなしで使える可能性|Autodeskフォーラムの成功報告とOSSソースに呼び出し例あり|個別コマンドの仕様保証、現行版の動作、非同期完了、元版の特定、名前・保存先の制御|
|通常のExportManager|現在と同じ|F3D・STEP出力APIあり|外部参照をまとめた完全F3Zを生成できる根拠を確認できない|

配布版で公開仕様を重視するならAPS方式を本命とする。追加ログインなしを重視するなら、内部コマンド方式を隔離した試作で先に評価する価値がある。どちらも現段階では対応済みと表示しない。

## 候補A：公式クラウドAPI

[Autodesk公式「Download Fusion 360 archives」（2017-09-07）](https://aps.autodesk.com/blog/download-fusion-360-archives) は、特定版をソースとしてF3Z生成を要求し、参照設計と関係情報を含むアーカイブを取得する経路を説明している。記事が古いため、これだけで2026年の全Hubでの動作を保証しない。

[公式OpenAPI定義](https://github.com/autodesk-platform-services/aps-sdk-openapi/blob/d411c0851cfb078ff15ac442223b495b82e3a0b7/datamanagement/datamanagement.yaml) も読み、以下を確認した。取得したファイルのGit blob SHAは407c26768d054321ab8689541398052944c51674。

1. GET /data/v1/projects/{project_id}/versions/{version_id}/downloadFormats：対象版の対応形式を照会。
2. POST /data/v1/projects/{project_id}/downloads：保存済みの版を指定し、F3Zが利用可能なら生成ジョブを開始。必要スコープはdata:create。
3. GET /data/v1/projects/{project_id}/jobs/{job_id}：状態確認。完了時303とLocationによる遷移が定義されている。
4. ダウンロード詳細とストレージ情報からアーカイブを取得。GET側はdata:read。バイナリ取得は現行の署名付きダウンロード方式も確認する。

注意：版のdownloads一覧には未完全実装で空を返す旨の記載がある。これを「出力済みか」の判定に使わず、ジョブ単位で追跡する。F3Z生成自体の対象Hub上での応答は今回未検証。

配布アプリの認証は[公式のDesktop App向けPKCE例](https://aps.autodesk.com/blog/getting-token-pkce-desktop-app-0)を参考にできる。配布者が公開クライアントとしてアプリを登録し、利用者が初回に自分のAutodeskアカウントで同意する構成を検討する。client secretをアドインへ埋め込まない。Fusion内部のトークン・Cookieの抜き取りは採用しない。実際に必要なHub側設定は対象環境で確認する。

生成ジョブの作成は設計編集・版更新とは別だが、data:createという権限が必要になる。APIクライアントはダウンロード生成と読み取りに限定し、設計・フォルダ・参照の作成や更新を実装しない。料金・利用規約・公開アプリの運用要件は今回確定していない。

## 候補B：Fusion内部テキストコマンド

[Application.executeTextCommand](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_Application_executeTextCommand.htm)自体は公式API。[Autodeskフォーラム](https://forums.autodesk.com/t5/fusion-api-and-scripts-forum/is-it-possible-to-export-an-assembly-via-api/m-p/12924966)では2020年にF3Z書き出しの成功報告、2024年にフォルダ指定例がある。

```python
app.executeTextCommand('data.fileExport f3z "<専用の出力フォルダ>"')
```

これはクリックの自動化とは異なり、アドインからFusionの処理を直接呼び出す案。ただし、executeTextCommandが公開APIであることと、data.fileExportの引数・戻り値・完了通知が公開仕様で保証されることは別。現行Fusionで動くかは未検証。フォーラムには出力名変更の制限やメール通知の報告もあり、同期処理と決めつけない。

[MaestroFusion360/BackupProjectのソース](https://github.com/MaestroFusion360/BackupProject/blob/2732d1d4ac32a071c019ca8146e110e66e7c0f9e/commands/commandDialog/entry.py)も確認。対象blobは35fe1f0abebc8d483b0ad8e8e90251a4f48c9077。data.fileExportの利用例だが、書き出し前にDocument.RemoveLinksを呼び、末尾で固定時間待機とサイズ確認を行っている。参照リンクを外す処理は参照関係を保存する目的に合わず、移植しない。サイズが正数であることだけでも完全性は証明できない。ソースを読む調査のみで、実行・コピー・導入はしていない。

## 次の実証と採用条件

いきなり全件出力へ組み込まず、別の試験用データでアセンブリ1件を検証する。

- 外部参照のある親設計、子部品、各版IDを事前記録。ユーザーが開いている親や依存部品があれば実行を拒否する。
- 元データは保存・更新・リンク解除しない。アドインが開いた文書のみを保存せず閉じる。
- 出力を試験用の隔離フォルダに保存し、クラウドジョブまたは実際の完了を追跡。キャンセル後も遅れて生成される出力を成功として誤登録しない。
- 拡張子だけで成功にせず、ZIPとしての構造・CRC、含まれるF3Dと関連情報を検査。ただし内部構造検査だけで復元可能とは判定しない。
- 別の試験プロジェクトに取り込み、親子の関係・部品数・位置・履歴・参照版を比較。F3Z復元はクラウドへのアップロードを伴うため、この試験は元データを含まない専用の場所で行う。
- 日本語名・空白・同名ファイル、旧版参照、別プロジェクト参照、ネットワーク停止、中断、遅延完了、Fusion更新後を検証。
- スモーク試験が成功してから既存の差分・版保持・manifest・ハッシュ検証へ統合。未認証・非対応・出力失敗は手動確認として残す。

本調査でコード変更、認証情報作成、クラウドへの実行要求、元データの変更は行っていない。
