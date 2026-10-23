"""
MEDICALPASS 当日順番受付: リクエスト処理（デスクトップアプリと CLI で共用）

リクエストの流れ（実サイトで確認済み）:
  1. GET  /users/login                             -> authenticity_token + cookie
  2. POST /users/login                             -> ログイン
  3. GET  /departments/{id}/reservations/new       -> form#new_reservation
       * 受付時間外 / 上限到達 -> /reservations/finished にリダイレクト
  4. POST /departments/{id}/reservations/confirm   (commit=確認)
  5. POST 確認画面のフォーム                        -> 受付確定
"""
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://medicalpass.jp"
JST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
INFORM_KBN = {"なし": "1", "メール": "2"}
INFORM_MINUTES = ["30", "45", "60", "75", "90"]
MAX_PATIENTS = 4
MIN_INTERVAL = 0.3

# 受付確定レスポンスの判定用キーワード（ページ全体ではなくフラッシュメッセージ .alert のみで判定）
# 実績: 成功時は /departments/{id} にリダイレクトし #notice.alert-info に
#       「受付しました。…受付No：1」が表示される（2026-09-26 確認）
SUCCESS_WORDS = ("受付しました", "受付No")
FAIL_WORDS = ("上限", "受付しておりません", "受付できません", "受付できませんでした", "エラー")


def now():
    return datetime.now(JST)


def parse_hms(text):
    """開始時刻を "HH:MM:SS" に正規化。対応形式: 13:59:58 / 135958 / 13:59 / 1359。
    空欄なら ""、不正なら ValueError。"""
    s = text.strip().replace("：", ":")
    if not s:
        return ""
    if ":" in s:
        parts = s.split(":")
    elif s.isdigit() and len(s) in (3, 4, 5, 6):
        s = s.zfill(6 if len(s) > 4 else 4)
        parts = [s[i:i + 2] for i in range(0, len(s), 2)]
    else:
        raise ValueError(text)
    if len(parts) == 2:
        parts.append("0")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(text)
    h, m, sec = map(int, parts)
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= sec < 60):
        raise ValueError(text)
    return f"{h:02d}:{m:02d}:{sec:02d}"


class DailyLog:
    """ログを logs/YYYY-MM-DD.log に日別で追記する。"""

    def __init__(self, log_dir):
        self.dir = Path(log_dir)
        self.lock = threading.Lock()

    def write(self, line):
        with self.lock:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                with open(self.dir / f"{now():%Y-%m-%d}.log", "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass


def page_text(soup, limit=400):
    return " ".join(soup.get_text(" ", strip=True).split())[:limit]


NAV_TEXT = "受付確認・キャンセル 診察券情報 設定 ログアウト"


def main_text(soup, limit=400):
    """ページ本文（先頭のメニュー部分を除く）。"""
    text = page_text(soup, 5000)
    for marker in (NAV_TEXT, "MENU"):
        if marker in text:
            text = text.split(marker, 1)[1].strip()
            break
    return text[:limit]


def reception_no(text):
    """フラッシュメッセージから受付No を取り出す（なければ ""）。"""
    m = re.search(r"受付No[：:]\s*(\d+)", text or "")
    return m.group(1) if m else ""


def redact(pairs):
    return [(k, "***" if ("password" in k or "token" in k) else v) for k, v in pairs]


# ---------------------------------------------------------------- form helpers

def serialize_form(form, submitter=None):
    """ブラウザが送信するのと同じ (name, value) のリストを返す。"""
    pairs = []
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name or el.has_attr("disabled"):
            continue
        if el.name == "input":
            typ = (el.get("type") or "text").lower()
            if typ in ("submit", "button", "image", "reset", "file"):
                continue
            if typ in ("checkbox", "radio"):
                if el.has_attr("checked"):
                    pairs.append((name, el.get("value", "on")))
                continue
            pairs.append((name, el.get("value", "")))
        elif el.name == "select":
            opts = el.find_all("option")
            chosen = [o for o in opts if o.has_attr("selected")] or opts[:1]
            for o in chosen:
                pairs.append((name, o.get("value", o.get_text(strip=True))))
        else:
            pairs.append((name, el.get_text()))
    if submitter is not None and submitter.get("name"):
        pairs.append((submitter["name"], submitter.get("value", "")))
    return pairs


def set_field(pairs, name, value):
    for i, (k, _) in enumerate(pairs):
        if k == name:
            pairs[i] = (k, value)
            return
    pairs.append((name, value))


def select_options(form, name):
    """フォーム内 <select> の [(value, label, selected)]。"""
    sel = form.find("select", attrs={"name": name}) if form else None
    if sel is None:
        return []
    return [(o.get("value", ""), o.get_text(strip=True), o.has_attr("selected"))
            for o in sel.find_all("option")]


def pick_option(options, keyword):
    """keyword が空 -> Web 上の初期選択（なければ値のある先頭）。
    keyword あり -> 値があり、ラベルに keyword を含む option。"""
    valued = [o for o in options if o[0]]
    if not keyword:
        return next((o for o in valued if o[2]), valued[0] if valued else None)
    return next((o for o in valued if keyword in o[1]), None)


def card_number(label):
    """診察券ラベル「氏名 （診察券番号：12345）」から番号部分を取り出す（なければ ""）。"""
    m = re.search(r"診察券番号[：:]\s*(\d+)", label or "")
    return m.group(1) if m else ""


def pick_card(options, keyword):
    """診察券の選択。keyword が空 -> Web 上の初期選択。
    数字なら診察券番号と完全一致で比較（先頭の 0 は無視: 「036222」と「36222」は同じ）。
    数字以外（氏名など）はラベルの部分一致。"""
    keyword = (keyword or "").strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if not keyword:
        return pick_option(options, "")
    valued = [o for o in options if o[0]]
    if keyword.isdigit():
        want = keyword.lstrip("0") or "0"
        return next((o for o in valued if (card_number(o[1]).lstrip("0") or "0") == want), None)
    return next((o for o in valued if keyword in o[1]), None)


def card_choices(options):
    """ログ表示用: 登録済み診察券の一覧（「36222（山口 太郎）」形式）。"""
    out = []
    for value, label, _ in options:
        if value:
            name = re.sub(r"[（(]\s*診察券番号.*$", "", label).strip()
            out.append(f"{card_number(label) or '?'}（{name}）")
    return "、".join(out) or "なし"


def flash_text(soup):
    """フラッシュメッセージ（.alert / #notice / #alert）のテキスト。"""
    return " ".join(x.get_text(" ", strip=True) for x in soup.select(".alert, #notice, #alert"))


def classify_final(resp, soup):
    """受付確定レスポンスを 'success' / 'fail' / 'unknown' に分類。
    ページ本文（病院のお知らせ等）には「受付番号」等が常に含まれるため、判定はフラッシュのみで行う。"""
    if "/reservations/finished" in resp.url:
        return "fail"
    flash = flash_text(soup)
    if soup.select(".alert-danger, .alert-warning, .alert-error"):
        return "fail"
    if any(w in flash for w in SUCCESS_WORDS):
        return "success"
    if any(w in flash for w in FAIL_WORDS) or soup.find("form", id="new_reservation"):
        return "fail"
    return "unknown"


@dataclass
class Options:
    num_patients: int = 1
    slot_keyword: str = ""              # "" = Web の初期選択と同じ
    card_keywords: list = field(default_factory=list)  # 各人の診察券番号; "" = 初期選択
    inform: str = "メール"
    inform_minutes: str = "30"
    interval: float = 0.5
    max_tries: int = 100
    start_at: str = ""                  # "HH:MM:SS" (JST), "" = すぐ開始
    dry_run: bool = False


# ---------------------------------------------------------------- client

class Reserver:
    def __init__(self, dept_id="3199", log=print, log_dir=None):
        self.dept = str(dept_id)
        self.log = log
        self.log_dir = Path(log_dir) if log_dir else Path.cwd() / "logs"
        self.logged_in = False
        self.last_url = None
        self._creds = None
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})

    def url(self, path):
        return urljoin(BASE, path)

    def save_html(self, step, resp):
        """レスポンス HTML を logs/YYYY-MM-DD/HHMMSS_step.html に保存。"""
        t = now()
        day_dir = self.log_dir / f"{t:%Y-%m-%d}"
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{t:%H%M%S}_{step}.html"
        path.write_text(resp.text, encoding="utf-8")
        return path

    def login(self, email, password):
        r = self.s.get(self.url("/users/login"), timeout=15)
        form = BeautifulSoup(r.text, "html.parser").find("form", id="new_user")
        if form is None:
            raise RuntimeError("ログインフォームが見つかりません")
        pairs = serialize_form(form)
        set_field(pairs, "user[email]", email)
        set_field(pairs, "user[password]", password)
        pairs.append(("commit", "ログイン"))
        r = self.s.post(self.url(form["action"]), data=pairs, timeout=15,
                        headers={"Referer": r.url, "Origin": BASE})
        if "/users/login" in r.url:
            self.save_html("login_failed", r)
            raise RuntimeError("ログイン失敗: "
                               + page_text(BeautifulSoup(r.text, "html.parser"), 150))
        self.logged_in = True
        self._creds = (email, password)
        self.log("ログインしました")

    def _get_logged_in(self, path):
        """ログインが必要なページを GET。セッション切れの場合は 1 回だけ自動で再ログイン。"""
        r = self.s.get(self.url(path), timeout=15)
        if "/users/login" in r.url:
            self.logged_in = False
            if not self._creds:
                raise RuntimeError("ログインしていません")
            self.log("セッション切れのため再ログインします...")
            self.login(*self._creds)
            r = self.s.get(self.url(path), timeout=15)
        self.last_url = r.url
        return r

    def open_form(self):
        """GET /reservations/new -> (soup, form)。受付時間外・上限到達なら form=None。"""
        r = self._get_logged_in(f"/departments/{self.dept}/reservations/new")
        soup = BeautifulSoup(r.text, "html.parser")
        return soup, soup.find("form", id="new_reservation")

    # ------------------------------------------------------------ 受付確認・キャンセル

    def list_reservations(self):
        """「受付確認・キャンセル」(/reservations) の受付一覧。
        戻り値: [{id, hospital, dept_path, date("MM/DD"), slot("午後診察"), no, text}]
        画面構成（2026-10-07 確認）:
          <div class="pull-left">
            <a data-method="delete" href="/reservations/{id}">下記受診をキャンセル</a>
            <div class="hospital"><a href="/departments/3199">わかたび皮ふ科（…）</a></div>
            <div class="reservation_info">10/07 (水) 午後診察<br>受付番号：1<br>氏名（診察券番号：…）</div>
          </div>"""
        r = self._get_logged_in("/reservations")
        soup = BeautifulSoup(r.text, "html.parser")
        meta = soup.find("meta", attrs={"name": "csrf-token"})
        self._csrf = meta["content"] if meta else ""
        items = []
        for a in soup.select('a[data-method="delete"][href^="/reservations/"]'):
            box = a.find_parent("div", class_="pull-left") or a.parent.parent
            info = box.select_one(".reservation_info")
            text = " ".join(info.get_text(" ", strip=True).split()) if info else ""
            hosp = box.select_one(".hospital a")
            date_m = re.search(r"(\d{1,2})/(\d{1,2})", text)
            slot_m = re.search(r"(午前|午後)\S*", text)
            no_m = re.search(r"受付番号[：:]\s*(\d+)", text)
            items.append({
                "id": a["href"].rstrip("/").rsplit("/", 1)[-1],
                "hospital": hosp.get_text(strip=True) if hosp else "",
                "dept_path": hosp.get("href", "") if hosp else "",
                "date": f"{int(date_m.group(1)):02d}/{int(date_m.group(2)):02d}" if date_m else "",
                "slot": slot_m.group(0) if slot_m else "",
                "no": no_m.group(1) if no_m else "",
                "text": text,
            })
        return items

    def cancel_reservation(self, rid):
        """受付をキャンセル（rails-ujs の data-method="delete" と同じ POST _method=delete）。
        キャンセル後に一覧を取り直し、消えていれば成功。戻り値: (ok, メッセージ, 最新の一覧)"""
        rid = str(rid)
        before = self.list_reservations()
        target = next((x for x in before if x["id"] == rid), None)
        if target is None:
            return False, "該当する受付が見つかりません（既にキャンセル済みの可能性があります）", before
        self.log(f"受付キャンセル: {target['date']} {target['slot']} 受付番号 {target['no']} (id={rid})")
        r = self.s.post(self.url(f"/reservations/{rid}"),
                        data={"_method": "delete", "authenticity_token": self._csrf}, timeout=20,
                        headers={"Referer": self.url("/reservations"), "Origin": BASE})
        path = self.save_html("cancel", r)
        msg = flash_text(BeautifulSoup(r.text, "html.parser"))[:200]
        self.log(f"  -> {r.status_code} {r.url}  (ログ: {path.name}) {msg}")
        after = self.list_reservations()
        if any(x["id"] == rid for x in after):
            return False, f"キャンセルできませんでした（受付が残っています）{msg}", after
        return True, msg or "キャンセルしました", after

    def fill_form(self, form, opt):
        """フォームを埋める。戻り値: (pairs, エラー)。
        設定の誤り（診察券が見つからない・重複など、再試行しても直らないもの）は
        self.fatal = True にして、呼び出し側で即終了させる。"""
        self.fatal = False
        pairs = serialize_form(form)

        slot = pick_option(select_options(form, "reservation[slot_id]"), opt.slot_keyword)
        if not slot:
            return None, f"診察時間「{opt.slot_keyword or '初期選択'}」がまだありません"
        set_field(pairs, "reservation[slot_id]", slot[0])
        picked = [f"診察時間={slot[1]}({slot[0]})"]

        set_field(pairs, "custom[num_of_patients]", str(opt.num_patients))
        used = {}
        for i in range(1, opt.num_patients + 1):
            name = f"custom[patient_nos_by_user][{i}]"
            kw = opt.card_keywords[i - 1] if i - 1 < len(opt.card_keywords) else ""
            options = select_options(form, name)
            card = pick_card(options, kw)
            if not card:
                self.fatal = True
                return None, (f"{i}人目の診察券「{kw or '初期選択'}」が見つかりません。"
                              f"このアカウントに登録済みの診察券: {card_choices(options)}")
            if card[0] in used:
                self.fatal = True
                return None, (f"{used[card[0]]}人目と{i}人目が同じ診察券（{card[1]}）になっています。"
                              f"設定画面で人数分の診察券番号を入力してください。"
                              f"登録済みの診察券: {card_choices(options)}")
            used[card[0]] = i
            set_field(pairs, name, card[0])
            picked.append(f"{i}人目={card[1]}({card[0]})")

        set_field(pairs, "reservation[inform_kbn]", INFORM_KBN.get(opt.inform, opt.inform))
        set_field(pairs, "reservation[inform_minutes]", opt.inform_minutes)
        pairs.append(("commit", "確認"))
        self.log("選択内容: " + " / ".join(picked))
        return pairs, None

    def post(self, step, action, pairs, referer):
        self.log(f"POST {action}")
        r = self.s.post(self.url(action), data=pairs, timeout=20,
                        headers={"Referer": referer, "Origin": BASE})
        path = self.save_html(step, r)
        self.log(f"  -> {r.status_code} {r.url}  (ログ: {path.name})")
        return r, BeautifulSoup(r.text, "html.parser")

    @staticmethod
    def find_final_form(soup):
        """確認画面の受付フォームと送信ボタン（「戻る」は除外）。"""
        for form in soup.find_all("form"):
            if "login" in (form.get("action") or ""):
                continue
            for btn in form.find_all(["input", "button"]):
                typ = (btn.get("type") or ("submit" if btn.name == "button" else "")).lower()
                label = btn.get("value") or btn.get_text(strip=True)
                if typ == "submit" and btn.get("id") != "back_button" and "戻る" not in label:
                    return form, btn
        return None, None

    # ------------------------------------------------------------ full flow

    def run(self, opt, stop=lambda: False):
        """手順 3〜5 を実行。受付成功（テスト実行は確認画面到達）で即終了。
        戻り値: True=成功 / False=失敗・中止 / None=結果不明（二重受付防止のため停止）"""
        if opt.start_at:
            self._wait_until(opt.start_at, stop)

        self.result_message = ""
        for attempt in range(1, opt.max_tries + 1):
            if stop():
                self.log("停止しました")
                return False
            tag = f"[{attempt}/{opt.max_tries}]"
            started = time.monotonic()
            try:
                result = self._attempt(tag, opt)
            except requests.RequestException as e:
                self.log(f"{tag} 通信エラー: {e}")
                result = "retry"
            if result == "success":
                return True
            if result == "unknown":
                return None
            if result == "fatal":
                return False
            # 送信間隔は「送信開始〜次の送信開始」で計る
            self._sleep(opt.interval - (time.monotonic() - started), stop)
        self.log(f"最大試行回数（{opt.max_tries} 回）に達したため終了します")
        return False

    def _attempt(self, tag, opt):
        soup, form = self.open_form()
        if form is None:
            self.log(f"{tag} 受付フォーム未公開: {main_text(soup, 50)}")
            return "retry"
        pairs, err = self.fill_form(form, opt)
        if err:
            self.log(f"{tag} {err}")
            if self.fatal:
                self.log("設定の誤りのため、再試行せずに終了します（設定画面の診察券番号を確認してください）")
                self.result_message = err
                return "fatal"
            return "retry"

        r, csoup = self.post("confirm", form["action"], pairs, self.last_url)
        final_form, btn = self.find_final_form(csoup)
        if final_form is None:
            self.log(f"{tag} 確認画面になりません: {main_text(csoup, 150)}")
            return "retry"
        self.log(f"確認画面: {main_text(csoup, 150)}")
        final_pairs = serialize_form(final_form, submitter=btn)
        action = final_form.get("action") or r.url

        if opt.dry_run:
            self.log(f"テスト実行のため受付は送信しません。送信予定: POST {urljoin(BASE, action)}")
            for k, v in redact(final_pairs):
                self.log(f"    {k} = {v}")
            return "success"

        fr, fsoup = self.post("final", action, final_pairs, r.url)
        verdict = classify_final(fr, fsoup)
        summary = flash_text(fsoup)[:300] or main_text(fsoup, 300)
        self.result_message = summary
        if verdict == "success":
            self.log(f"★ 受付成功！処理を終了します: {summary}")
        elif verdict == "fail":
            self.log(f"{tag} 受付失敗、再試行します: {summary}")
            return "retry"
        else:
            self.log(f"？ 結果を判定できません。二重受付防止のため停止します。"
                     f"Web で受付状況を確認してください: {summary}")
        return verdict

    def _wait_until(self, hms, stop):
        hms = parse_hms(hms)
        target = datetime.combine(now().date(), datetime.strptime(hms, "%H:%M:%S").time(), JST)
        if target <= now():
            self.log(f"開始時刻 {hms} は既に過ぎているため、すぐ開始します")
            return
        last = None
        while (left := (target - now()).total_seconds()) > 0 and not stop():
            if last is None or last - left >= 30:
                self.log(f"{hms} まで待機中（残り {int(left)} 秒）")
                last = left
            time.sleep(min(left, 0.1))

    @staticmethod
    def _sleep(sec, stop):
        end = time.monotonic() + sec
        while (left := end - time.monotonic()) > 0 and not stop():
            time.sleep(min(0.05, left))
