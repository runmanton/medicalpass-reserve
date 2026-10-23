# MedicalPass 受付ツール

MEDICALPASS の **当日順番受付** を、画面操作ではなくエンドポイントへ直接リクエストして自動化するツール。

- **Web 版（ConoHa WING, CGI + ジョブスケジューラー）**: 受付日・枠を登録しておくと、その日の Web受付開始時刻に自動で受付。受付状況の確認・キャンセルも可能。
- **デスクトップ版（Windows / Tkinter）**: 開始時刻を指定して手動実行。

詳細（MEDICALPASS のリクエスト仕様・構成・運用手順・トラブル事例）は [docs/project-summary.md](docs/project-summary.md) を参照。

## 構成

```
mp_core.py                リクエスト処理（共通）
medicalpass_app.py        デスクトップアプリ（Tkinter）
medicalpass_reserve.py    CLI 版
conoha/medicalpass/       Web 版の本体（サーバーでは ~/medicalpass/ に配置）
  mp_core.py              ※ ルートの mp_core.py と同じ内容を置く
  mp_server.py / run.py / setup.sh
conoha/public/mp/         Web 版の画面（サーバーでは ~/public_html/<domain>/mp/ に配置）
  index.py / .htaccess
tests/test_web.py         Web 版の結合テスト（MEDICALPASS へは実通信しない）
```

## 開発

```bash
pip install -r requirements.txt
cp mp_core.py conoha/medicalpass/mp_core.py   # 共通コードを Web 版へ反映
python tests/test_web.py "$(pwd)/conoha"       # 結合テスト
```

デスクトップ版のビルド:

```bash
python -m PyInstaller --noconfirm --onefile --windowed --name MedicalPassReserve --hidden-import keyring.backends.Windows medicalpass_app.py
```

## 注意

- ログ・HTML レスポンス・`data/` には個人情報やパスワード（暗号化）が含まれるため、リポジトリには含めない（`.gitignore` 済み）。
- 送信間隔は 0.3 秒以上。MEDICALPASS の利用規約に配慮して利用すること。

© 2026 TuanLM. All rights reserved.
