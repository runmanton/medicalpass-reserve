#!/opt/alt/python311/bin/python3
"""
MedicalPass 受付 Web 版: コマンド入口

  run.py --cron              ジョブスケジューラーから毎分実行。開始時刻が近い受付予定があれば受付処理
  run.py --now [--dry-run]   すぐ受付処理（Web 画面の「今すぐ実行」「テスト実行」から起動）
  run.py --init              初期化（キー作成・管理パスワード発行）
  run.py --reset-admin       管理パスワードを再発行
"""
import argparse
import secrets
import sys

import mp_server as ms


def new_admin_password(cfg):
    pw = secrets.token_urlsafe(9)
    cfg["admin_hash"] = ms.hash_password(pw)
    ms.save_config(cfg)
    print(f"管理画面パスワード: {pw}")
    print("（ログイン後、設定画面で変更できます）")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--cron", action="store_true")
    g.add_argument("--now", action="store_true")
    g.add_argument("--init", action="store_true")
    g.add_argument("--reset-admin", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--slot-keyword", default="", help="--now 時の診察時間（午前診察 / 午後診察、空欄=初期選択）")
    a = ap.parse_args()

    if a.init or a.reset_admin:
        ms.secret_key()
        cfg = ms.load_config()
        if a.init and cfg["admin_hash"]:
            print("初期化済みです（管理パスワードの再発行は --reset-admin）")
        else:
            new_admin_password(cfg)
        return

    if a.now:
        ms.run_job(booking=None, dry_run=a.dry_run, slot_keyword=a.slot_keyword)
        return

    # --cron: 開始時刻が近い受付予定がなければ即終了（毎分起動されるため軽く保つ）
    cfg = ms.load_config()
    if not cfg["bookings"]:
        return
    b = ms.due_booking(cfg, ms.now())
    if b:
        ms.update_booking(b["id"], status="running")   # 次の分の cron が同じ予定を拾わないように
        ms.run_job(booking=b, dry_run=False)


if __name__ == "__main__":
    sys.exit(main())
