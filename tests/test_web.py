"""ConoHa 版のローカル結合テスト（CGI を環境変数で模擬）"""
import json, os, re, shutil, subprocess, sys, tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, unquote

SRC = Path(sys.argv[1])
APP = Path(tempfile.mkdtemp()) / "medicalpass"
shutil.copytree(SRC / "medicalpass", APP)
# テスト用コピーだけ: MEDICALPASS への実ログインを行わず、MP_FAKE_LOGIN と一致するパスワードのみ成功させる
with open(APP / "mp_core.py", "a", encoding="utf-8") as f:
    f.write('''
import os as _os
if _os.environ.get("MP_FAKE_LOGIN"):
    def _fake_login(self, email, password):
        if password != _os.environ["MP_FAKE_LOGIN"]:
            raise RuntimeError("ログイン失敗: fake")
        self.logged_in = True
        self._creds = (email, password)
    Reserver.login = _fake_login
if _os.environ.get("MP_FAKE_RESV"):
    import json as _json
    def _fake_list(self):
        return _json.load(open(_os.environ["MP_FAKE_RESV"], encoding="utf-8"))
    def _fake_cancel(self, rid):
        items = _fake_list(self)
        if not any(x["id"] == str(rid) for x in items):
            return False, "該当する受付が見つかりません", items
        items = [x for x in items if x["id"] != str(rid)]
        _json.dump(items, open(_os.environ["MP_FAKE_RESV"], "w", encoding="utf-8"))
        return True, "受付をキャンセルしました。", items
    Reserver.list_reservations = _fake_list
    Reserver.cancel_reservation = _fake_cancel
''')
os.environ["MP_FAKE_LOGIN"] = "secret-pw"
RESV_FILE = Path(tempfile.mkdtemp()) / "resv.json"
RESV_FILE.write_text("[]", encoding="utf-8")
os.environ["MP_FAKE_RESV"] = str(RESV_FILE)
CGI = SRC / "public" / "mp" / "index.py"
PY = sys.executable
fails = []
def ok(cond, name):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

out = subprocess.run([PY, "run.py", "--init"], cwd=APP, capture_output=True, text=True, encoding="utf-8").stdout
admin = re.search(r"パスワード: (\S+)", out).group(1)


def cgi(method="GET", query="", data=None, cookie="", origin="https://example.test"):
    body = urlencode(data or {}, doseq=True).encode()
    env = dict(os.environ, MP_APP_DIR=str(APP), REQUEST_METHOD=method, QUERY_STRING=query,
               SCRIPT_NAME="/mp/index.py", HTTP_HOST="example.test", HTTP_COOKIE=cookie,
               CONTENT_LENGTH=str(len(body)), HTTP_ORIGIN=origin, PYTHONIOENCODING="utf-8")
    r = subprocess.run([PY, str(CGI)], input=body, env=env, capture_output=True)
    return unquote(r.stdout.decode("utf-8")), r.stderr.decode("utf-8", "replace")


o, _ = cgi("POST", data={"action": "login", "password": admin})
sess = re.search(r"Set-Cookie: mp_session=([^;]+)", o).group(1)
ck = f"mp_session={sess}"
ok("X-Accel-Expires: 0" in o and "t=" in o, "リダイレクトにキャッシュ無効ヘッダ・一意 URL")
o, e = cgi(cookie=ck)
ok("受付予定" in o and "登録された受付予定はありません" in o, "ホーム表示（予定なし）" + e[-300:])
ok('location.protocol==="http:"' in o, "http→https 切替スクリプト")
csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', o).group(1)
o, e = cgi(query="p=settings", cookie=ck)
ok("受付予定の既定値" in o and "スケジュール" not in o, "設定画面（曜日スケジュール削除済み）" + e[-300:])

# --- ログイン情報の未設定・ログイン失敗時はホームの操作を実行しない
o, _ = cgi(cookie=ck)
ok("ログイン情報が未設定です" in o, "未設定の警告をホームに表示")
o, _ = cgi("POST", data={"action": "add_booking", "csrf": csrf, "date": "2030-01-07", "slot": "am"}, cookie=ck)
ok("p=settings" in o and "パスワードを入力" in o, "未設定: 受付予定を追加 → 設定画面へ")
o, _ = cgi("POST", data={"action": "run_now", "csrf": csrf, "confirm": "1", "slot_keyword": ""}, cookie=ck)
ok("p=settings" in o and "開始しました" not in o, "未設定: 今すぐ受付 → 設定画面へ")
wrong = {"action": "save_settings", "csrf": csrf, "email": "a@example.com", "mp_password": "WRONG",
         "dept_id": "3199", "num_patients": "1", "inform": "メール", "inform_minutes": "30",
         "interval": "0.5", "max_tries": "50", "slot_am_start": "075958", "slot_am_kw": "午前診察",
         "slot_pm_start": "135958", "slot_pm_kw": "午後診察", "closed3": "1", "closed6": "1", "skip_holidays": "1"}
o, _ = cgi("POST", data=wrong, cookie=ck)
ok("保存しました" in o and "ログインできません" in o, "保存時に自動でログイン確認（失敗）")
o, _ = cgi(cookie=ck)
ok("❌ ログイン失敗" in o and "ログインできませんでした" in o, "ホームに ❌ ログイン失敗")
o, _ = cgi("POST", data={"action": "add_booking", "csrf": csrf, "date": "2030-01-07", "slot": "am"}, cookie=ck)
ok("p=settings" in o and "ログインできません" in o and not json.loads(
    (APP / "data/config.json").read_text(encoding="utf-8"))["bookings"], "ログイン失敗: 受付予定を追加しない")
o, _ = cgi("POST", data={"action": "run_now", "csrf": csrf, "confirm": "1", "slot_keyword": ""}, cookie=ck)
ok("p=settings" in o and "開始しました" not in o, "ログイン失敗: 今すぐ受付しない")

form = {"action": "save_settings", "csrf": csrf, "email": "a@example.com", "mp_password": "secret-pw",
        "dept_id": "3199", "num_patients": "1", "card0": "12345", "inform": "メール",
        "inform_minutes": "30", "interval": "0.5", "max_tries": "100",
        "slot_am_start": "075958", "slot_am_kw": "午前診察", "slot_pm_start": "1359", "slot_pm_kw": "午後診察",
        "closed3": "1", "closed6": "1", "skip_holidays": "1"}
o, _ = cgi("POST", data=form, cookie=ck)
ok("保存しました" in o and "ログインに成功" in o, "設定保存（正しいパスワード → ログイン成功）")
o, _ = cgi(cookie=ck)
ok("✅ ログイン確認済み" in o and "ログインできませんでした" not in o, "ホームに ✅ ログイン確認済み")
cfg = json.loads((APP / "data/config.json").read_text(encoding="utf-8"))
ok(cfg["slots"]["pm"]["start"] == "13:59:00" and cfg["closed_weekdays"] == [3, 6], "既定時刻・休診日の保存")

# --- 受付予定の追加・検証
sys.path[:0] = [str(APP)]
os.chdir(APP)
import mp_server as ms
import mp_core
today = ms.now().date()
def next_wd(wd):  # 今日より後の指定曜日（祝日を避ける）
    d = today + timedelta(days=1)
    while d.weekday() != wd or ms.is_holiday(d): d += timedelta(days=1)
    return d
mon, thu = next_wd(0), next_wd(3)
add = lambda d, slot, start="": cgi("POST", data={"action": "add_booking", "csrf": csrf, "date": str(d),
                                                  "slot": slot, "start": start}, cookie=ck)[0]
ok("受付予定を追加しました" in add(mon, "am"), f"{mon} 午前を追加")
ok("受付予定を追加しました" in add(mon, "pm", "135958"), f"{mon} 午後（開始 13:59:58 指定）を追加")
ok("既にあります" in add(mon, "am"), "同じ日・枠の重複は拒否")
ok("休診日" in add(thu, "am"), "木曜は休診日で拒否")
ok("過ぎています" in add(today - timedelta(days=1), "am") or "休診日" in add(today - timedelta(days=1), "am"),
   "過去日は拒否")
ok("日付を選択" in add("", "am"), "日付未入力は拒否")
ok("形式" in add(mon + timedelta(days=1), "am", "25:00"), "不正な開始時刻は拒否")
o, _ = cgi(cookie=ck)
ok(o.count("⏳ 予定") == 2 and "13:59:58" in o, "ホームに 2 件の予定表示")

cfg = ms.load_config()
am = next(b for b in cfg["bookings"] if b["slot"] == "am")
pm = next(b for b in cfg["bookings"] if b["slot"] == "pm")
JST = ms.JST
at = lambda d, hh, mm, ss: datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=JST)
ok(ms.due_booking(cfg, at(mon, 7, 59, 0))["id"] == am["id"], "07:59:00 の cron で午前の予定を担当")
ok(ms.due_booking(cfg, at(mon, 7, 58, 0)) is None, "07:58:00 では担当しない")
ok(ms.due_booking(cfg, at(mon, 13, 59, 0))["id"] == pm["id"], "13:59:00 の cron で午後（13:59:58）")
ok(ms.due_booking(cfg, at(mon - timedelta(days=1), 7, 59, 0)) is None, "前日の同時刻では担当しない")


class FakeReserver:
    calls = 0
    def __init__(self, dept, log, log_dir): self.log = log
    def login(self, e, p): self.log("login")
    def run(self, opt, stop):
        FakeReserver.calls += 1
        self.result_message = "受付しました。 受付No：5"
        self.log(f"run slot={opt.slot_keyword} start={opt.start_at}")
        return True
mp_core.Reserver = FakeReserver

# 開始時刻を過去にして即実行させる（cron 経由を模擬）
ms.update_booking(am["id"], status="running", start="00:00:01")
ms.run_job(booking=dict(am, start="00:00:01"))
cfg = ms.load_config()
b_am = next(b for b in cfg["bookings"] if b["id"] == am["id"])
ok(b_am["status"] == "success" and b_am["no"] == "5", "予定の状態が success・受付No 5 に更新")
ms.run_job(booking=dict(pm, start="00:00:01"))
b_pm = next(b for b in ms.load_config()["bookings"] if b["id"] == pm["id"])
ok(FakeReserver.calls == 1 and b_pm["status"] == "skipped", "同じ日に成功済みなら午後はスキップ")
ok(ms.due_booking(ms.load_config(), at(mon, 7, 59, 0)) is None, "成功済みの予定は再度拾わない")
log = ms.read_log(ms.now().strftime("%Y-%m-%d"))
ok("run slot=午前診察 start=00:00:01" in log, "枠の診察時間・開始時刻が渡る")

o, _ = cgi(cookie=ck)
ok("✅ 成功（No.5）" in o and "過去の予定" in o and "<th>受付No</th><th></th>" not in o, "受付予定: 受付No は状態の後ろに表示（専用列なし）")
o, _ = cgi("POST", data={"action": "delete_booking", "csrf": csrf, "id": am["id"]}, cookie=ck)
ok("確認画面で" in o and any(b["id"] == am["id"] for b in ms.load_config()["bookings"]), "確認なしでは予定を削除しない")
o, _ = cgi("POST", data={"action": "delete_booking", "csrf": csrf, "id": am["id"], "confirm": "1"}, cookie=ck)
ok("削除しました" in o and all(b["id"] != am["id"] for b in ms.load_config()["bookings"]), "予定の削除")

# --- 今すぐ受付: 診察時間の指定
o, _ = cgi(cookie=ck)
ok('name="slot_keyword"' in o and "テスト実行" not in o, "今すぐ受付に診察時間選択・テスト実行ボタンなし")
o, _ = cgi("POST", data={"action": "run_now", "csrf": csrf, "confirm": "1", "slot_keyword": "夜間"}, cookie=ck)
ok("診察時間の指定が正しくありません" in o, "不正な診察時間は拒否")
o, _ = cgi("POST", data={"action": "run_now", "csrf": csrf, "slot_keyword": "午後診察"}, cookie=ck)
ok("確認画面で" in o and "開始しました" not in o, "確認なしは実行しない")
calls = FakeReserver.calls
ms.run_job(booking=None, slot_keyword="午後診察")
ok(FakeReserver.calls == calls and "本日は受付成功済み" in ms.read_log(ms.now().strftime("%Y-%m-%d")),
   "本日成功済みなら今すぐ受付もスキップ（二重受付防止）")
ms.RESULTS_FILE.unlink()
ms.run_job(booking=None, slot_keyword="午後診察")
ok("run slot=午後診察 start=" in ms.read_log(ms.now().strftime("%Y-%m-%d")), "手動実行に診察時間が渡る")

# --- ログ削除
day = ms.now().strftime("%Y-%m-%d")
(ms.LOGS / "2026-01-01.log").write_text("old", encoding="utf-8")
(ms.LOGS / "2026-01-01").mkdir(exist_ok=True); (ms.LOGS / "2026-01-01" / "x.html").write_text("x")
o, _ = cgi(query="p=logs&d=2026-01-01", cookie=ck)
ok("この日のログを削除" in o and "すべてのログを削除" in o, "ログ画面に削除ボタン")
o, _ = cgi("POST", data={"action": "delete_log", "csrf": csrf, "d": "2026-01-01"}, cookie=ck)
ok("確認画面で" in o and (ms.LOGS / "2026-01-01.log").exists(), "確認なしでは削除しない")
o, _ = cgi("POST", data={"action": "delete_log", "csrf": csrf, "d": "2026-01-01", "confirm": "1"}, cookie=ck)
ok(not (ms.LOGS / "2026-01-01.log").exists() and not (ms.LOGS / "2026-01-01").exists()
   and (ms.LOGS / f"{day}.log").exists(), "指定日のログ・HTML だけ削除")
o, _ = cgi("POST", data={"action": "delete_log", "csrf": csrf, "d": "../data", "confirm": "1"}, cookie=ck)
ok((APP / "data/config.json").exists(), "不正な日付（パス）は無視")
o, _ = cgi("POST", data={"action": "delete_all_logs", "csrf": csrf, "confirm": "1"}, cookie=ck)
ok(not list(ms.LOGS.glob("*")) and not ms.RESULTS_FILE.exists() and ms.load_config()["bookings"],
   "すべて削除（ログ・履歴のみ、受付予定は残る）")

# --- MEDICALPASS の受付一覧・キャンセル
import importlib
mp_core.Reserver = importlib.reload(mp_core).Reserver   # FakeReserver を外し、テスト用 fake 版に戻す
tdy = ms.now()
mmdd = tdy.strftime("%m/%d")
RESV_FILE.write_text(json.dumps([
    {"id": "777", "hospital": "わかたび皮ふ科", "dept_path": "/departments/3199", "date": mmdd,
     "slot": "午後診察", "no": "7", "text": ""},
    {"id": "888", "hospital": "別の病院", "dept_path": "/departments/1", "date": mmdd,
     "slot": "午前診察", "no": "3", "text": ""}], ensure_ascii=False), encoding="utf-8")
o, _ = cgi(cookie=ck)
ok("MEDICALPASS の受付状況" in o and "更新中" in o and 'data-src="/mp/?p=mp_reservations' in o
   and "最新の状態を取得" not in o, "ホーム表示時は保存済みで表示し、裏で自動更新（ボタンなし）")
o, _ = cgi(query="p=mp_reservations")
ok("forbidden" in o and "<b class" not in o, "未ログインの自動更新リクエストは 403")
o, _ = cgi(query="p=mp_reservations", cookie=ck)
ok(o.count("受付をキャンセル</button>") == 2 and 'id="mp-resv"' in o and "data-src" not in o and "時点" in o,
   "自動更新: 最新の受付一覧（2 件）を返す")
RESV_FILE.write_text("[]", encoding="utf-8")
o, _ = cgi(query="p=mp_reservations", cookie=ck)
ok(o.count("受付をキャンセル</button>") == 2, "60 秒以内は MEDICALPASS に再問い合わせしない（キャッシュ）")
o, _ = cgi(cookie=ck)
ok("data-src" not in o and "更新中" not in o, "キャッシュが新しい間はホームで自動更新しない")
RESV_FILE.write_text(json.dumps([
    {"id": "777", "hospital": "わかたび皮ふ科", "dept_path": "/departments/3199", "date": mmdd,
     "slot": "午後診察", "no": "7", "text": ""},
    {"id": "888", "hospital": "別の病院", "dept_path": "/departments/1", "date": mmdd,
     "slot": "午前診察", "no": "3", "text": ""}], ensure_ascii=False), encoding="utf-8")
o, _ = cgi("POST", data={"action": "fetch_reservations", "csrf": csrf}, cookie=ck)
ok("受付状況を取得しました（2 件）" in o, "最新の状態を取得")
o, _ = cgi(cookie=ck)
ok(o.count("受付をキャンセル</button>") == 2 and "<b class=\"no\">7</b>" in o, "受付一覧に 2 件・キャンセルボタン")
o, _ = cgi("POST", data={"action": "cancel_reservation", "csrf": csrf, "rid": "888"}, cookie=ck)
ok("確認画面で" in o and len(json.loads(RESV_FILE.read_text(encoding="utf-8"))) == 2, "確認なしではキャンセルしない")
o, _ = cgi("POST", data={"action": "cancel_reservation", "csrf": csrf, "rid": "888", "confirm": "1"}, cookie=ck)
ok("受付をキャンセルしました" in o and [x["id"] for x in json.loads(RESV_FILE.read_text(encoding="utf-8"))] == ["777"],
   "一覧からキャンセル（888 のみ削除）")
o, _ = cgi("POST", data={"action": "cancel_reservation", "csrf": csrf, "rid": "888", "confirm": "1"}, cookie=ck)
ok("見つかりません" in o, "キャンセル済みの受付を再度キャンセル → 見つからない")

# 本日成功済みの受付予定 → 「受付キャンセル」ボタン → 該当受付をキャンセル・状態を キャンセル済 に
ms.RESULTS_FILE.unlink(missing_ok=True)
ms.append_result({"date": tdy.strftime("%Y-%m-%d"), "time": "14:00:01", "slot": "x", "dry_run": False,
                  "result": "success", "no": "7", "message": ""})
c = ms.load_config()
c["bookings"].append({"id": "bk1", "date": tdy.strftime("%Y-%m-%d"), "slot": "pm", "start": "13:59:58",
                      "status": "success", "no": "7", "message": ""})
ms.save_config(c)
ok(ms.succeeded_on(tdy.strftime("%Y-%m-%d")), "キャンセル前: 本日受付済み")
o, _ = cgi(cookie=ck)
ok('value="cancel_booking"' in o, "本日成功の予定に「受付キャンセル」ボタン")
o, _ = cgi("POST", data={"action": "cancel_booking", "csrf": csrf, "id": "bk1", "confirm": "1"}, cookie=ck)
bk = next(b for b in ms.load_config()["bookings"] if b["id"] == "bk1")
ok("受付をキャンセルしました" in o and bk["status"] == "cancelled" and json.loads(RESV_FILE.read_text(encoding="utf-8")) == [],
   "受付予定からキャンセル → 🚫 キャンセル済")
ok(not ms.succeeded_on(tdy.strftime("%Y-%m-%d")), "キャンセル後は本日再受付できる")
o, _ = cgi(cookie=ck)
ok("🚫 キャンセル済" in o and "受付はありません" in o, "ホームにキャンセル済・受付なし表示")

# 旧形式の設定ファイル（schedule キー）でも読める
c = json.loads((APP / "data/config.json").read_text(encoding="utf-8")); c["schedule"] = {"enabled": True}
(APP / "data/config.json").write_text(json.dumps(c), encoding="utf-8")
ok("schedule" not in ms.load_config(), "旧設定（schedule）は無視")
r = subprocess.run([PY, "run.py", "--cron"], cwd=APP, capture_output=True, text=True)
ok(r.returncode == 0 and not r.stderr, "run.py --cron が正常終了" + r.stderr[-300:])
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAIL"))

# --- 確認モーダル
o, _ = cgi(cookie=ck)
ok('<dialog id="cf">' in o and 'name="confirm" value=""' in o and 'type="checkbox" name="confirm"' not in o,
   "確認モーダルあり・チェックボックスなし")
ok('data-confirm="今すぐ受付を開始しますか？' in o and 'data-ok-label="受付する"' in o, "今すぐ受付に確認メッセージ")
o, _ = cgi(query="p=logs", cookie=ck)
ok("data-confirm=" in o and "すべて削除する" in o, "ログ削除に確認メッセージ")
o, _ = cgi()
ok('<dialog id="cf">' not in o, "ログイン画面にはモーダルなし")
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAIL"))

# --- 不具合再現: 受付 ID 未記録の成功予定を「受付状況」一覧からキャンセル → 予定も キャンセル済 に
c = ms.load_config()
c["bookings"].append({"id": "bk2", "date": tdy.strftime("%Y-%m-%d"), "slot": "am", "start": "07:59:58",
                      "status": "success", "no": "1", "message": ""})          # reservation_id なし
ms.save_config(c)
RESV_FILE.write_text(json.dumps([{"id": "64720120", "hospital": "わかたび皮ふ科", "dept_path": "/departments/3199",
                                  "date": mmdd, "slot": "午前診察", "no": "1", "text": ""}], ensure_ascii=False),
                     encoding="utf-8")
cgi("POST", data={"action": "fetch_reservations", "csrf": csrf}, cookie=ck)
o, _ = cgi(cookie=ck)
ok(o.count('value="cancel_booking"') == 1, "一覧に残っている本日の成功予定には「受付キャンセル」")
o, _ = cgi("POST", data={"action": "cancel_reservation", "csrf": csrf, "rid": "64720120", "confirm": "1"}, cookie=ck)
bk2 = next(b for b in ms.load_config()["bookings"] if b["id"] == "bk2")
ok("受付をキャンセルしました" in o and bk2["status"] == "cancelled", "一覧からのキャンセルで、ID 未記録の予定も キャンセル済 に")
o, _ = cgi(cookie=ck)
ok('value="cancel_booking"' not in o, "キャンセル後は「受付キャンセル」ボタンを表示しない")

# 一覧に存在しない（MEDICALPASS 側で直接キャンセル等）本日の成功予定 → 「受付キャンセル」ではなく「削除」
c = ms.load_config()
c["bookings"].append({"id": "bk3", "date": tdy.strftime("%Y-%m-%d"), "slot": "pm", "start": "13:59:58",
                      "status": "success", "no": "9", "message": ""})
ms.save_config(c)
o, _ = cgi(cookie=ck)
ok('value="cancel_booking"' not in o and '<input type="hidden" name="id" value="bk3"><button>削除' in o,
   "一覧にない成功予定は「削除」のみ表示")
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAIL"))

# --- 受付人数 2 人以上の診察券番号チェック
base = {"action": "save_settings", "csrf": csrf, "email": "a@example.com", "dept_id": "3199", "inform": "メール",
        "inform_minutes": "30", "interval": "0.5", "max_tries": "50", "slot_am_start": "075958",
        "slot_am_kw": "午前診察", "slot_pm_start": "135958", "slot_pm_kw": "午後診察", "closed3": "1", "closed6": "1"}
o, _ = cgi("POST", data=dict(base, num_patients="2", card0="036222"), cookie=ck)
ok("2 人分の診察券番号を入力" in o and ms.load_config()["reservation"]["num_patients"] != 2, "2人で診察券1人分だけ → 保存しない")
o, _ = cgi("POST", data=dict(base, num_patients="2", card0="036222", card1="36222"), cookie=ck)
ok("重複" in o, "2人で同じ番号（先頭0違い）→ 重複エラー")
o, _ = cgi("POST", data=dict(base, num_patients="2", card0="036222", card1="36223"), cookie=ck)
ok("保存しました" in o and ms.load_config()["reservation"]["cards"][:2] == ["036222", "36223"], "2人分入力 → 保存")
o, _ = cgi(query="p=settings", cookie=ck)
ok("<details open>" in o and "全員分の診察券番号の入力が必要" in o, "2人以上なら詳細設定を開いて表示")
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAIL"))
