"""
MedicalPass 受付 Web 版: 設定・認証・予約日管理・実行管理（CGI と cron で共用）

画面で「受付したい日＋枠（午前/午後）」を登録しておくと、その日の開始時刻に自動で受付する。
ジョブスケジューラー（毎分 run.py --cron）は「登録日の開始時刻が近いか」を見るだけで、
登録がなければ何もせず終了する。

ディレクトリ構成（public_html の外に置く）:
  ~/medicalpass/
    mp_core.py / mp_server.py / run.py
    lib/                 pip --target でインストールしたライブラリ
    data/config.json     設定・受付予定（MEDICALPASS のパスワードは暗号化して保存）
    data/secret.key      暗号化キー・セッション署名キー（600）
    data/results.jsonl   実行結果の履歴
    logs/YYYY-MM-DD.log  日別ログ（HTML は logs/YYYY-MM-DD/ に保存）
"""
import base64
import copy
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR / "lib"))

DATA = APP_DIR / "data"
LOGS = APP_DIR / "logs"
CONFIG_FILE = DATA / "config.json"
KEY_FILE = DATA / "secret.key"
RESULTS_FILE = DATA / "results.jsonl"
LOCK_FILE = DATA / "run.lock"
STOP_FILE = DATA / "stop.flag"
PYTHON = "/opt/alt/python311/bin/python3"

JST = timezone(timedelta(hours=9))
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]   # datetime.weekday() の順
CRON_WINDOW_SEC = 70      # cron（毎分）起動時、開始時刻まで何秒以内なら担当するか
SESSION_TTL = 30 * 24 * 3600

STATUS_LABEL = {"pending": "⏳ 予定", "running": "▶ 実行中", "success": "✅ 成功", "fail": "❌ 失敗",
                "unknown": "❓ 判定不可", "stopped": "■ 停止", "skipped": "⏭ スキップ",
                "expired": "⌛ 期限切れ", "cancelled": "🚫 キャンセル済"}

DEFAULT_CONFIG = {
    "email": "",
    "password_enc": "",
    "dept_id": "3199",
    "admin_hash": "",
    "reservation": {
        "num_patients": 1,
        "cards": ["", "", "", ""],      # 診察券番号（空欄 = Web の初期選択）
        "inform": "メール",
        "inform_minutes": "30",
    },
    "send": {"interval": 0.5, "max_tries": 50},
    "slots": {                           # 枠ごとの既定の開始時刻（Web受付開始の少し前）
        "am": {"label": "午前", "start": "07:59:58", "slot_keyword": "午前診察"},
        "pm": {"label": "午後", "start": "13:59:58", "slot_keyword": "午後診察"},
    },
    "closed_weekdays": [3, 6],           # 休診日（木・日）
    "skip_holidays": True,
    "bookings": [],                      # 受付予定 [{id, date, slot, start, status, no, message}]
    "login_check": {"ok": None, "at": "", "msg": ""},   # 直近の MEDICALPASS ログイン確認結果
    "mp_reservations": {"at": "", "items": [], "error": ""},  # MEDICALPASS の受付一覧（最終取得分）
}


def now():
    return datetime.now(JST)


# ---------------------------------------------------------------- ファイル

def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def load_config():
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.pop("schedule", None)           # 旧バージョン（曜日スケジュール）の設定は使わない
    return _merge(DEFAULT_CONFIG, data)


def save_config(cfg):
    _write_private(CONFIG_FILE, json.dumps(cfg, ensure_ascii=False, indent=2))


def secret_key():
    if not KEY_FILE.exists():
        _write_private(KEY_FILE, base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    return KEY_FILE.read_text().strip().encode()


# ---------------------------------------------------------------- 暗号化・認証

def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(secret_key())


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode() if text else ""


def decrypt(token):
    return _fernet().decrypt(token.encode()).decode() if token else ""


def hash_password(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${digest}"


def check_password(pw, stored):
    try:
        _, salt, _ = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(pw, salt), stored)


def make_session():
    exp = str(int(time.time()) + SESSION_TTL)
    sig = hmac.new(secret_key(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def check_session(value):
    try:
        exp, sig = value.split(".")
    except (AttributeError, ValueError):
        return False
    good = hmac.new(secret_key(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and int(exp) > time.time()


def csrf_token(session):
    return hmac.new(secret_key(), b"csrf:" + session.encode(), hashlib.sha256).hexdigest()[:32]


# ---------------------------------------------------------------- MEDICALPASS ログイン確認

def has_credentials(cfg):
    return bool(cfg["email"] and cfg["password_enc"])


def verify_login():
    """保存済みの MEDICALPASS アカウントで実際にログインできるか確認し、結果を記録する。
    戻り値: (ok, メッセージ)"""
    from mp_core import Reserver
    cfg = load_config()
    if not has_credentials(cfg):
        ok, msg = False, "MEDICALPASS のログイン情報が未設定です"
    else:
        try:
            Reserver(cfg["dept_id"], log=lambda m: None, log_dir=LOGS).login(
                cfg["email"], decrypt(cfg["password_enc"]))
            ok, msg = True, "MEDICALPASS へのログインに成功しました"
        except Exception as e:
            ok, msg = False, f"MEDICALPASS にログインできません（{e}）"
    cfg = load_config()                  # 確認中に他の保存があっても上書きしないよう読み直す
    cfg["login_check"] = {"ok": ok, "at": now().strftime("%Y-%m-%d %H:%M"), "msg": msg}
    save_config(cfg)
    return ok, msg


# ---------------------------------------------------------------- MEDICALPASS の受付一覧・キャンセル

def _logged_in_reserver(log=lambda m: None):
    from mp_core import Reserver
    cfg = load_config()
    if not has_credentials(cfg):
        raise RuntimeError("MEDICALPASS のログイン情報が未設定です")
    rv = Reserver(cfg["dept_id"], log=log, log_dir=LOGS)
    rv.login(cfg["email"], decrypt(cfg["password_enc"]))
    return rv


def _daily_logger():
    from mp_core import DailyLog
    daily = DailyLog(LOGS)
    return lambda msg: daily.write(f"[{now():%H:%M:%S.%f}"[:-3] + f"] {msg}")


RESERVATIONS_TTL = 60     # 受付一覧のキャッシュ有効秒数（ホーム表示ごとに MEDICALPASS へ問い合わせないため）


def store_mp_reservations(items, error=""):
    cfg = load_config()
    cfg["mp_reservations"] = {"at": now().strftime("%Y-%m-%d %H:%M"), "ts": time.time(),
                              "items": items, "error": error}
    save_config(cfg)


def reservations_stale(cfg):
    """受付一覧を取り直すべきか（未取得・TTL 超過）。"""
    return time.time() - cfg["mp_reservations"].get("ts", 0) > RESERVATIONS_TTL


def refresh_mp_reservations_if_stale():
    """必要なときだけ受付一覧を取り直す。受付処理の実行中・ログイン情報未設定なら何もしない。"""
    cfg = load_config()
    if running_pid() or not has_credentials(cfg) or not reservations_stale(cfg):
        return
    fetch_mp_reservations()


def fetch_mp_reservations():
    """MEDICALPASS の受付一覧を取得して保存。戻り値: (ok, メッセージ)"""
    try:
        items = _logged_in_reserver().list_reservations()
    except Exception as e:
        store_mp_reservations(load_config()["mp_reservations"]["items"], error=str(e))
        return False, f"受付状況を取得できません（{e}）"
    store_mp_reservations(items)
    return True, f"受付状況を取得しました（{len(items)} 件）"


def match_reservation(cfg, booking, items):
    """受付予定（成功済み）に対応する MEDICALPASS の受付を一覧から探す。"""
    if booking.get("reservation_id"):
        hit = next((x for x in items if x["id"] == booking["reservation_id"]), None)
        if hit:
            return hit
    mmdd = booking["date"][5:7] + "/" + booking["date"][8:10]
    label = cfg["slots"][booking["slot"]]["label"]
    return next((x for x in items if x["date"] == mmdd and x["slot"].startswith(label)
                 and (not booking.get("no") or x["no"] == booking["no"])), None)


def cancel_mp_reservation(rid=None, booking_id=None):
    """MEDICALPASS の受付をキャンセル。rid（受付 ID）か booking_id（受付予定）で指定。
    戻り値: (ok, メッセージ)"""
    log = _daily_logger()
    if running_pid():
        return False, "受付処理の実行中はキャンセルできません"
    try:
        rv = _logged_in_reserver(log)
        cfg = load_config()
        current = rv.list_reservations()
        if booking_id:
            booking = next((b for b in cfg["bookings"] if b["id"] == booking_id), None)
            if not booking:
                return False, "受付予定が見つかりません"
            target = match_reservation(cfg, booking, current)
        else:
            target = next((x for x in current if x["id"] == str(rid)), None)
        if not target:
            store_mp_reservations(current)
            return False, "MEDICALPASS に該当する受付が見つかりません（既にキャンセル済みの可能性があります）"
        rid = target["id"]
        ok, msg, items = rv.cancel_reservation(rid)
    except Exception as e:
        log(f"受付キャンセル エラー: {e}")
        return False, f"キャンセルできませんでした（{e}）"
    store_mp_reservations(items)
    if ok:
        # 対応する受付予定を「キャンセル済」にする。受付 ID 未記録の予定（キャンセル機能の追加前に
        # 受付したもの・一覧から直接キャンセルしたもの）も、日付・枠・受付番号で照合する
        cfg = load_config()
        for b in cfg["bookings"]:
            if b["status"] == "success" and (
                    b["id"] == booking_id or b.get("reservation_id") == str(rid)
                    or match_reservation(cfg, b, [target]) is target):
                b["status"] = "cancelled"
        save_config(cfg)
        append_result({"date": now().strftime("%Y-%m-%d"), "time": now().strftime("%H:%M:%S"),
                       "slot": "キャンセル", "dry_run": False, "result": "cancelled", "no": "",
                       "message": msg[:200]})
        log(f"受付キャンセル完了: {msg}")
    else:
        log(f"受付キャンセル失敗: {msg}")
    return ok, msg


# ---------------------------------------------------------------- 受付予定（予約日）

def closed_reason(cfg, d):
    """その日が休診日なら理由、そうでなければ ""。"""
    if d.weekday() in cfg["closed_weekdays"]:
        return f"{WEEKDAYS[d.weekday()]}曜日は休診日"
    if cfg["skip_holidays"] and is_holiday(d):
        return "祝日"
    return ""


def is_holiday(d):
    try:
        import jpholiday
        return jpholiday.is_holiday(d)
    except ImportError:
        return False


def booking_start(cfg, b):
    """受付予定の開始日時。"""
    from mp_core import parse_hms
    hms = parse_hms(b.get("start") or cfg["slots"][b["slot"]]["start"])
    return datetime.combine(date.fromisoformat(b["date"]),
                            datetime.strptime(hms, "%H:%M:%S").time(), JST)


def add_booking(cfg, day, slot, start=""):
    """受付予定を追加。エラーなら理由の文字列を返す。"""
    from mp_core import parse_hms
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return "日付を選択してください"
    if slot not in cfg["slots"]:
        return "枠を選択してください"
    try:
        start = parse_hms(start)
    except ValueError:
        return "開始時刻の形式が正しくありません（例 07:59:58 / 075958）"
    reason = closed_reason(cfg, d)
    if reason:
        return f"{d:%m/%d} は{reason}のため登録できません"
    b = {"id": secrets.token_hex(4), "date": d.isoformat(), "slot": slot, "start": start,
         "status": "pending", "no": "", "message": ""}
    if booking_start(cfg, b) <= now():
        return "開始時刻を過ぎています（すぐ受付する場合はホームの「今すぐ受付」を使ってください）"
    if any(x["date"] == b["date"] and x["slot"] == slot and x["status"] == "pending"
           for x in cfg["bookings"]):
        return "同じ日・同じ枠の予定が既にあります"
    cfg["bookings"].append(b)
    cfg["bookings"].sort(key=lambda x: (x["date"], x["slot"]))
    return ""


def delete_booking(cfg, bid):
    cfg["bookings"] = [b for b in cfg["bookings"] if b["id"] != bid]


def update_booking(bid, **fields):
    """設定ファイルを読み直してから 1 件だけ更新（画面での同時編集を上書きしないため）。"""
    cfg = load_config()
    for b in cfg["bookings"]:
        if b["id"] == bid:
            b.update(fields)
    save_config(cfg)


def bookings_view(cfg):
    """画面表示用: 過ぎた pending は expired 扱い。新しい順ではなく日付順。"""
    t = now()
    out = []
    for b in cfg["bookings"]:
        b = dict(b)
        if b["status"] == "pending" and booking_start(cfg, b) + timedelta(minutes=10) < t:
            b["status"] = "expired"
        b["at"] = booking_start(cfg, b)
        out.append(b)
    return out


def due_booking(cfg, t):
    """t から CRON_WINDOW_SEC 秒以内に開始時刻が来る pending の予定。"""
    for b in cfg["bookings"]:
        if b["status"] == "pending" and 0 < (booking_start(cfg, b) - t).total_seconds() <= CRON_WINDOW_SEC:
            return b
    return None


# ---------------------------------------------------------------- 実行状態

def running_pid():
    """実行中のジョブの PID（なければ None）。"""
    try:
        pid = int(LOCK_FILE.read_text().strip())
        if os.name != "nt":          # Windows の os.kill(pid, 0) はプロセスを終了させるため使わない
            os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def acquire_lock():
    if running_pid():
        return False
    _write_private(LOCK_FILE, str(os.getpid()))
    return True


def release_lock():
    try:
        if LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass


def request_stop():
    _write_private(STOP_FILE, now().isoformat())


def append_result(rec):
    DATA.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_results(limit=30):
    try:
        lines = RESULTS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [json.loads(x) for x in lines[-limit:]][::-1]


def succeeded_on(day):
    """その日に有効な受付があるか（成功数 > キャンセル数）。キャンセル後は再受付できる。"""
    recs = [r for r in load_results(100) if r.get("date") == day and not r.get("dry_run")]
    return (sum(r.get("result") == "success" for r in recs)
            > sum(r.get("result") == "cancelled" for r in recs))


def log_dates():
    return sorted((p.stem for p in LOGS.glob("*.log")), reverse=True)


def read_log(day, tail=None):
    if len(day) != 10 or not day.replace("-", "").isdigit():
        return ""
    try:
        lines = (LOGS / f"{day}.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-tail:] if tail else lines)


def _valid_day(day):
    return len(day) == 10 and day[4] == "-" and day[7] == "-" and day.replace("-", "").isdigit()


def delete_log(day):
    """指定日のログ（.log と HTML フォルダ）を削除。"""
    import shutil
    if not _valid_day(day):
        return False
    (LOGS / f"{day}.log").unlink(missing_ok=True)
    shutil.rmtree(LOGS / day, ignore_errors=True)
    return True


def delete_all_logs():
    """すべてのログ・HTML・実行履歴を削除。"""
    import shutil
    for p in LOGS.glob("*"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    RESULTS_FILE.unlink(missing_ok=True)


def spawn_job(dry_run=False, slot_keyword=""):
    """Web 画面から手動実行（バックグラウンドで run.py --now）。"""
    args = [PYTHON, str(APP_DIR / "run.py"), "--now"] + (["--dry-run"] if dry_run else [])
    if slot_keyword:
        args += ["--slot-keyword", slot_keyword]
    subprocess.Popen(args, cwd=str(APP_DIR), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)


# ---------------------------------------------------------------- ジョブ本体

def run_job(booking=None, dry_run=False, slot_keyword=""):
    """1 回分の受付処理。booking=None は手動実行（すぐ開始、診察時間は slot_keyword、空なら Web の初期選択）。"""
    from mp_core import DailyLog, Options, Reserver, reception_no

    daily = DailyLog(LOGS)

    def log(msg):
        daily.write(f"[{now():%H:%M:%S.%f}"[:-3] + f"] {msg}")

    def finish(status, no="", message=""):
        if booking:
            update_booking(booking["id"], status=status, no=no, message=message[:200])

    if not acquire_lock():
        log("別のジョブが実行中のため起動しません")
        finish("skipped", message="別のジョブが実行中")
        return
    try:
        STOP_FILE.unlink(missing_ok=True)
        cfg = load_config()
        slot = cfg["slots"][booking["slot"]] if booking else None
        label = f"{booking['date']} {slot['label']}" if booking else f"手動 {slot_keyword or '初期選択'}"
        mode = "テスト実行" if dry_run else "本番送信"
        today = now().strftime("%Y-%m-%d")
        if not dry_run and succeeded_on(today):
            log(f"[{label}] 本日は受付成功済みのためスキップします")
            finish("skipped", message="本日は受付成功済み")
            return
        if not cfg["email"] or not cfg["password_enc"]:
            log("MEDICALPASS のログイン情報が未設定です")
            finish("fail", message="ログイン情報が未設定")
            return

        res = cfg["reservation"]
        opt = Options(
            num_patients=int(res["num_patients"]),
            slot_keyword=slot["slot_keyword"] if slot else slot_keyword,
            card_keywords=res["cards"][:int(res["num_patients"])],
            inform=res["inform"],
            inform_minutes=res["inform_minutes"],
            interval=float(cfg["send"]["interval"]),
            max_tries=int(cfg["send"]["max_tries"]),
            start_at=(booking.get("start") or slot["start"]) if booking else "",
            dry_run=dry_run,
        )
        log(f"===== 開始 [{label}]（{mode}）人数={opt.num_patients} "
            f"診察時間={opt.slot_keyword or '初期選択'} 間隔={opt.interval}秒 最大={opt.max_tries}回 "
            f"開始={opt.start_at or 'すぐ'} =====")

        rv = Reserver(cfg["dept_id"], log=log, log_dir=LOGS)
        result = False
        try:
            rv.login(cfg["email"], decrypt(cfg["password_enc"]))
            result = rv.run(opt, stop=STOP_FILE.exists)
        except Exception as e:
            log(f"エラー: {e}")
        verdict = {True: "success", False: "fail", None: "unknown"}[result]
        if STOP_FILE.exists():
            verdict = "stopped"
        msg = getattr(rv, "result_message", "")
        no = reception_no(msg)
        append_result({"date": today, "time": now().strftime("%H:%M:%S"), "slot": label,
                       "dry_run": dry_run, "result": verdict, "no": no, "message": msg[:200]})
        finish(verdict, no, msg)
        if verdict == "success" and not dry_run:
            # 受付一覧を取り直して保存（ホームからキャンセルできるように受付 ID も記録）
            try:
                items = rv.list_reservations()
                store_mp_reservations(items)
                if booking:
                    hit = match_reservation(cfg, dict(booking, no=no), items)
                    if hit:
                        update_booking(booking["id"], reservation_id=hit["id"])
            except Exception as e:
                log(f"受付一覧の取得に失敗: {e}")
        log(f"===== 終了 [{label}] 結果={verdict} =====")
    finally:
        STOP_FILE.unlink(missing_ok=True)
        release_lock()
