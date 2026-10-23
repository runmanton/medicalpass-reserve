#!/bin/bash
# MedicalPass 受付 Web 版 セットアップ（アップロード後に 1 回だけ実行）
#   bash ~/medicalpass/setup.sh
set -e
PY=/opt/alt/python311/bin/python3
APP="$HOME/medicalpass"
WEB="$HOME/public_html/runmanton.conohawing.com/mp"

echo "== 1. 改行コード（CRLF -> LF）を修正"
sed -i 's/\r$//' "$APP"/*.py "$APP"/*.sh "$WEB/index.py" "$WEB/.htaccess"

echo "== 2. ライブラリをインストール ($APP/lib)"
$PY -m pip --version >/dev/null 2>&1 || $PY -m ensurepip --user
$PY -m pip install --quiet --disable-pip-version-check --target "$APP/lib" --upgrade \
    requests beautifulsoup4 cryptography jpholiday

echo "== 3. パーミッション"
mkdir -p "$APP/data" "$APP/logs"
chmod 700 "$APP" "$APP/data" "$APP/logs"
chmod 600 "$APP"/*.py
chmod 700 "$APP/run.py" "$APP/setup.sh"
chmod 755 "$WEB" "$WEB/index.py"
chmod 644 "$WEB/.htaccess"

echo "== 4. 動作確認"
$PY -c "import sys; sys.path[:0]=['$APP','$APP/lib']; import requests, bs4, cryptography, jpholiday, mp_core, mp_server; print('OK: Python', sys.version.split()[0])"

echo "== 5. 初期化"
cd "$APP" && $PY run.py --init

echo
echo "セットアップ完了。"
echo "ジョブスケジューラーに登録するコマンド（cron: 毎分 * * * * *）:"
echo "  $PY $APP/run.py --cron"
echo "管理画面: https://runmanton.conohawing.com/mp/"
