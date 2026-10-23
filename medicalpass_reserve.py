"""
MedicalPass 受付 CLI 版（リクエスト処理は mp_core.py、デスクトップアプリは medicalpass_app.py）

  set MP_EMAIL=... & set MP_PASSWORD=...
  python medicalpass_reserve.py --slot 午後 --dry-run
  python medicalpass_reserve.py --slot 午後 --start 14:00:00
"""
import argparse
import os
import sys

from pathlib import Path

from mp_core import INFORM_MINUTES, MIN_INTERVAL, DailyLog, Options, Reserver, now, parse_hms

sys.stdout.reconfigure(encoding="utf-8")
LOG_DIR = Path(__file__).with_name("logs")
daily_log = DailyLog(LOG_DIR)


def log(msg):
    line = f"[{now():%H:%M:%S.%f}"[:-3] + f"] {msg}"
    daily_log.write(line)
    print(line, flush=True)


def main():
    ap = argparse.ArgumentParser(description="MEDICALPASS 当日順番受付")
    ap.add_argument("--dept", default="3199", help="診療科 ID")
    ap.add_argument("--num", type=int, default=1, choices=[1, 2, 3, 4], help="受付人数")
    ap.add_argument("--slot", default="", help="午前 / 午後（空欄 = Web の初期選択）")
    ap.add_argument("--card-no", action="append", default=[], help="診察券番号（人数分繰り返し指定）")
    ap.add_argument("--inform", default="メール", choices=["なし", "メール"], help="お知らせ")
    ap.add_argument("--inform-minutes", default="30", choices=INFORM_MINUTES, help="お知らせ時間（分前）")
    ap.add_argument("--interval", type=float, default=0.5, help="送信間隔（秒）")
    ap.add_argument("--max-tries", type=int, default=100, help="最大試行回数（成功時は自動停止）")
    ap.add_argument("--start", default="", help="開始時刻 JST（例 14:00:00）")
    ap.add_argument("--dry-run", action="store_true", help="テスト実行（確認画面で停止）")
    a = ap.parse_args()

    if a.interval < MIN_INTERVAL:
        sys.exit(f"送信間隔は {MIN_INTERVAL} 秒以上にしてください")
    email, password = os.environ.get("MP_EMAIL"), os.environ.get("MP_PASSWORD")
    if not email or not password:
        sys.exit("環境変数 MP_EMAIL / MP_PASSWORD を設定してください")
    try:
        start = parse_hms(a.start)
    except ValueError:
        sys.exit("開始時刻の形式が正しくありません（例 14:00:00 / 140000 / 1400）")
    rv = Reserver(a.dept, log=log, log_dir=LOG_DIR)
    rv.login(email, password)
    rv.run(Options(num_patients=a.num, slot_keyword=a.slot, card_keywords=a.card_no,
                   inform=a.inform, inform_minutes=a.inform_minutes, interval=a.interval,
                   max_tries=a.max_tries, start_at=start, dry_run=a.dry_run))


if __name__ == "__main__":
    main()
