# 開発時の指示

- 開発の正本は、このリポジトリ（`Fusionバックアップ/FusionBackupManager`）です。旧 `fusion権限問題/FusionBackupManager` は移行元の参照用とし、今後の開発には使用しません。
- Git のコミットメッセージは日本語で記述します。
- Fusion へのインストール先は `%APPDATA%/Autodesk/Autodesk Fusion 360/API/Scripts/FusionBackupManager` のままです。開発リポジトリやバックアップデータの保存先と混同しないでください。
- バックアップ用の作業ディレクトリ・保存先設定を、以前の場所へ戻したり推測で変更したりしないでください。
- 元の Fusion クラウドデータと既存バックアップは保護します。実機で確認していない出力・復元・UI 動作は未検証と明示します。
- 標準の単体テストは、リポジトリ直下で `python -m unittest discover -s tests -q` を実行します。一時領域の指定が必要な場合は `FBM_TEST_TMP` を使用します。
