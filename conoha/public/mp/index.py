#!/opt/alt/python311/bin/python3
"""MedicalPass 受付 Web 版 - 管理画面 (CGI)"""
import html
import io
import os
import sys
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, urlparse

# Web フォルダ名から本体フォルダを決める: /mp/ -> ~/medicalpass、/yamaguchi/ -> ~/yamaguchi
# （同じ index.py を複数のインスタンスで共用できるように）
_WEB_NAME = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.environ.get("MP_APP_DIR") or "/home/c9687250/" + (
    "medicalpass" if _WEB_NAME == "mp" else _WEB_NAME)
sys.path.insert(0, APP_DIR)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import mp_server as ms  # noqa: E402

h = html.escape
SELF = os.environ.get("SCRIPT_NAME", "/mp/index.py")
BASE_PATH = SELF.rsplit("/", 1)[0] + "/"
SLOT_CHOICES = [("", "初期選択（Web と同じ）"), ("午前診察", "午前診察"), ("午後診察", "午後診察")]
RESULT_LABEL = {"success": "✅ 成功", "fail": "❌ 失敗", "unknown": "❓ 判定不可", "stopped": "■ 停止",
                "cancelled": "🚫 キャンセル"}
NEED_CONFIRM = "確認画面で操作を確定してください（画面を再読み込みしてもう一度お試しください）"


# ---------------------------------------------------------------- リクエスト

def read_request():
    method = os.environ.get("REQUEST_METHOD", "GET")
    query = {k: v[0] for k, v in parse_qs(os.environ.get("QUERY_STRING", "")).items()}
    form = {}
    if method == "POST":
        n = int(os.environ.get("CONTENT_LENGTH") or 0)
        body = sys.stdin.buffer.read(min(n, 100_000)).decode("utf-8", "replace")
        form = parse_qs(body, keep_blank_values=True)
    cookie = SimpleCookie(os.environ.get("HTTP_COOKIE", ""))
    session = cookie["mp_session"].value if "mp_session" in cookie else ""
    return method, query, form, session


def f1(form, key, default=""):
    return form.get(key, [default])[0]


def same_origin():
    host = os.environ.get("HTTP_HOST", "")
    src = os.environ.get("HTTP_ORIGIN") or os.environ.get("HTTP_REFERER") or ""
    return bool(host) and urlparse(src).netloc == host


def no_cache_headers():
    """ConoHa WING の WEXAL（nginx）ページキャッシュを無効化する。
    キャッシュは Cookie を区別しないため、ログイン後の画面が他人に表示されたり、
    ログインしてもログイン画面のキャッシュが返ったりするのを防ぐ。"""
    print("Cache-Control: private, no-cache, no-store, must-revalidate, max-age=0")
    print("X-Accel-Expires: 0")
    print("Pragma: no-cache")
    print("Vary: Cookie")


def redirect(page="", msg="", cookie=None):
    # 念のため URL を毎回変えて、キャッシュ済みの画面が返らないようにする
    loc = BASE_PATH + "?" + (f"p={page}&" if page else "") + f"t={int(time.time() * 1000)}"
    if msg:
        loc += "&msg=" + quote(msg)
    print("Status: 303 See Other")
    no_cache_headers()
    if cookie is not None:
        print(f"Set-Cookie: {cookie}")
    print(f"Location: {loc}\n")


def session_cookie(value, max_age):
    return (f"mp_session={value}; Path={BASE_PATH}; Max-Age={max_age}; "
            "HttpOnly; Secure; SameSite=Strict")


# ---------------------------------------------------------------- 画面共通

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--line:#dde1e6;--text:#1f2328;--muted:#6b7280;--accent:#1f6feb;
--ok:#1a7f37;--ng:#cf222e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.6 -apple-system,"Hiragino Sans","Yu Gothic UI","Meiryo",sans-serif}
header{background:#24292f;color:#fff;padding:10px 16px;display:flex;flex-wrap:wrap;gap:8px 16px;
align-items:center}header b{font-size:16px;margin-right:auto;white-space:nowrap}
header nav{display:flex;gap:6px;white-space:nowrap}
header nav a{color:#c9d1d9;text-decoration:none;padding:3px 10px;border-radius:14px;
border-bottom:2px solid transparent;transition:background .15s,color .15s}
header nav a:hover{color:#fff;background:rgba(255,255,255,.08)}
header nav a.active{color:#fff;font-weight:700;background:rgba(255,255,255,.16);
border-bottom-color:#58a6ff}
@media (max-width:480px){header{padding:10px 12px;gap:6px 8px}header b{font-size:15px}
header nav{gap:2px;font-size:14px}header nav a{padding:3px 7px}}
main{max-width:760px;margin:0 auto;padding:12px 16px 40px}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;
margin:12px 0}h2{font-size:16px;margin:0 0 8px}
.row{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;margin:8px 0}
.row>label:first-child{min-width:130px;color:var(--muted)}
input[type=text],input[type=password],input[type=number],input[type=email],input[type=date],select{font:inherit;
padding:6px 8px;border:1px solid var(--line);border-radius:6px;min-width:0;max-width:100%}
button{font:inherit;padding:8px 14px;border-radius:6px;border:1px solid var(--line);background:#fff;
cursor:pointer}button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.danger{background:var(--ng);border-color:var(--ng);color:#fff}
.msg{background:#fff8c5;border:1px solid #d4a72c;border-radius:6px;padding:8px 12px}
.muted{color:var(--muted);font-size:13px}.ok{color:var(--ok)}.ng{color:var(--ng)}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:13px;border:1px solid}
pre{background:#0d1117;color:#e6edf3;padding:10px;border-radius:6px;overflow-x:auto;
font:12px/1.5 Consolas,"MS Gothic",monospace;white-space:pre-wrap;word-break:break-all}
.tbl{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
td,th{border-bottom:1px solid var(--line);padding:6px;text-align:left;white-space:nowrap}
.inline{display:inline}[hidden]{display:none!important}
.row.run-now{margin-top:12px;gap:12px 16px}.row.run-now>label.check{min-width:0;color:var(--text)}
@media (max-width:480px){.row.run-now button{flex:1 1 100%;padding:12px}
.row>label:first-child{min-width:100%}}
details{margin:8px 0}summary{cursor:pointer}
details.log>summary{padding:4px 0;font-size:16px}
pre.logbox{max-height:45vh;overflow:auto;margin:8px 0 0}
footer{text-align:center;color:var(--muted);font-size:12px;padding:0 16px 24px}
dialog#cf{border:none;border-radius:12px;padding:0;width:min(92vw,420px);
box-shadow:0 10px 40px rgba(0,0,0,.3)}dialog#cf::backdrop{background:rgba(0,0,0,.45)}
.resv{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;justify-content:space-between;
padding:10px 0;border-bottom:1px solid var(--line)}.resv b.no{font-size:18px}
@media (max-width:480px){.resv>div:last-child{width:100%}.resv button{width:100%;padding:10px}}
.cf-body{padding:20px}#cf-msg{white-space:pre-line;margin:0 0 16px;line-height:1.7}
.cf-btns{display:flex;gap:10px;justify-content:flex-end}.cf-btns button{min-width:96px;padding:10px 14px}
@media (max-width:480px){.cf-btns button{flex:1}}
"""


def page(title, body, refresh=False, nav=True):
    print("Content-Type: text/html; charset=utf-8")
    no_cache_headers()
    print("X-Frame-Options: DENY\n")
    t = int(time.time() * 1000)
    items = [("ホーム", f"?t={t}"), ("設定", f"?p=settings&t={t}"), ("ログ", f"?p=logs&t={t}"),
             ("ログアウト", f"?p=logout&t={t}")]
    # 表示中の画面のメニューを強調（title が画面名と一致する項目）
    menu = "".join(
        f'<a href="{BASE_PATH}{q}"' + (' class="active" aria-current="page"' if label == title else "") + f">{label}</a>"
        for label, q in items) if nav else ""
    # http で開かれた場合は https に切り替える（Secure Cookie は http では保存されないため）。
    # サーバー側では前段プロキシのため http/https を判定できないので、ブラウザ側で行う。
    print(f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<script>if(location.protocol==="http:")location.replace("https:"+location.href.slice(5))</script>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="format-detection" content="telephone=no,date=no,address=no,email=no">
{'<meta http-equiv="refresh" content="3">' if refresh else ''}
<title>{h(title)} | MedicalPass 受付</title><style>{CSS}</style></head><body>
<header><b>MedicalPass 受付</b><nav>{menu}</nav></header><main>{body}</main>
<footer>&copy; 2026 TuanLM. All rights reserved.</footer>
{CONFIRM_DIALOG if nav else ""}
<script>document.querySelectorAll("details.log").forEach(d=>{{const p=d.querySelector("pre.logbox"),
b=()=>{{p.scrollTop=p.scrollHeight}};b();d.addEventListener("toggle",b)}})</script></body></html>""")


def flash(query):
    return f'<p class="msg">{h(query["msg"])}</p>' if query.get("msg") else ""


def log_box(text, title, open_=True):
    """開閉できるログ表示。高さを制限して枠内スクロール（最新行が見えるよう下端に移動）。"""
    lines = len(text.splitlines()) if text else 0
    body = h(text) if text else "（ログはありません）"
    return (f'<details class="log"{" open" if open_ else ""}><summary><b>{h(title)}</b>'
            f' <span class="muted">（{lines} 行・タップで開閉）</span></summary>'
            f'<pre class="logbox">{body}</pre></details>')


def post_form(action, csrf, inner, cls="inline", confirm=None, ok_label="はい", danger=True):
    """POST フォーム。confirm を指定すると送信前に確認モーダルを表示する。
    「はい」を押したときだけ JS が confirm=1 を入れて送信し、サーバー側もそれを必須にする。
    confirm 内の {slot} はフォーム内 select の選択中ラベルに置き換えて表示する。"""
    attrs = ""
    if confirm:
        attrs = (f' data-confirm="{h(confirm)}" data-ok-label="{h(ok_label)}"'
                 f' data-ok-class="{"danger" if danger else "primary"}"')
        inner = '<input type="hidden" name="confirm" value="">' + inner
    return (f'<form method="post" action="{BASE_PATH}" class="{cls}"{attrs}>'
            f'<input type="hidden" name="action" value="{action}">'
            f'<input type="hidden" name="csrf" value="{csrf}">{inner}</form>')


# 確認モーダル（<dialog>）。data-confirm 付きフォームの送信を横取りして表示する
CONFIRM_DIALOG = """<dialog id="cf"><div class="cf-body"><p id="cf-msg"></p><div class="cf-btns">
<button type="button" id="cf-no">いいえ</button><button type="button" id="cf-ok">はい</button></div></div></dialog>
<script>(()=>{const d=document.getElementById("cf"),m=document.getElementById("cf-msg"),
ok=document.getElementById("cf-ok");let form=null;
document.addEventListener("submit",e=>{const f=e.target;if(!f.dataset.confirm)return;e.preventDefault();
form=f;const s=f.querySelector("select");
m.textContent=f.dataset.confirm.replace("{slot}",s?s.selectedOptions[0].text:"");
ok.textContent=f.dataset.okLabel||"はい";ok.className=f.dataset.okClass||"danger";d.showModal();ok.focus()});
ok.onclick=()=>{if(!form)return;form.querySelector("input[name=confirm]").value="1";d.close();
const b=form.querySelector("button");if(b){b.disabled=true;b.textContent="処理中..."}form.submit()};
document.getElementById("cf-no").onclick=()=>d.close();
d.addEventListener("click",e=>{if(e.target===d)d.close()});
d.addEventListener("close",()=>{if(form&&form.querySelector("input[name=confirm]").value!=="1")form=null})})()</script>"""


# ---------------------------------------------------------------- 各画面

def mp_section_html(cfg, csrf, loading=False):
    """ホームの「MEDICALPASS の受付状況」。loading=True なら表示後に JS で最新を取得して差し替える。"""
    mr = cfg["mp_reservations"]

    def mp_row(x):
        cancel = post_form("cancel_reservation", csrf,
                           f'<input type="hidden" name="rid" value="{h(x["id"])}">'
                           '<button class="danger">受付をキャンセル</button>',
                           confirm=f"{x['date']} {x['slot']}（受付番号 {x['no']}）\n{x['hospital']}\n\n"
                                   "この受付をキャンセルしますか？\nキャンセルすると受付番号は取り消され、元に戻せません。",
                           ok_label="キャンセルする")
        # スマホでもキャンセルボタンが画面内に収まるよう、表ではなくカード形式で表示
        return (f'<div class="resv"><div><b>{h(x["date"])} {h(x["slot"])}</b>　受付番号 '
                f'<b class="no">{h(x["no"])}</b><br><span class="muted">{h(x["hospital"])}</span></div>'
                f'<div>{cancel}</div></div>')

    rows = "".join(mp_row(x) for x in mr["items"]) or '<p class="muted">受付はありません</p>'
    if loading:
        note = '<span class="muted">（更新中…）</span>'
    elif mr["at"]:
        note = f'<span class="muted">（{h(mr["at"])} 時点）</span>'
    elif not ms.has_credentials(cfg):
        note = '<span class="muted">（ログイン情報が未設定）</span>'
    else:
        note = '<span class="muted">（未取得）</span>'
    err = f'<p class="ng">取得エラー: {h(mr["error"])}</p>' if mr.get("error") and not loading else ""
    src = f' data-src="{BASE_PATH}?p=mp_reservations&t={int(time.time() * 1000)}"' if loading else ""
    return f"""<section id="mp-resv"{src}><h2>MEDICALPASS の受付状況 {note}</h2>{err}
<div class="resv-list">{rows}</div>
<p class="muted">MEDICALPASS の「受付確認・キャンセル」と同じ内容です（ホームを開くと自動で更新します）。
キャンセルすると受付番号は取り消され、元に戻せません。</p></section>"""


# 受付状況の自動更新: data-src 付きのセクションを、表示後に最新の内容で差し替える
MP_AUTOLOAD_JS = """<script>(()=>{const s=document.getElementById("mp-resv");if(!s||!s.dataset.src)return;
fetch(s.dataset.src,{credentials:"same-origin",cache:"no-store"}).then(r=>r.ok?r.text():Promise.reject())
.then(t=>{s.outerHTML=t}).catch(()=>{const n=s.querySelector("h2 .muted");if(n)n.textContent="（更新できませんでした）"})})()</script>"""

def view_login(query):
    page("ログイン", f"""{flash(query)}<section><h2>管理画面ログイン</h2>
<form method="post" action="{BASE_PATH}"><input type="hidden" name="action" value="login">
<div class="row"><label>パスワード</label><input type="password" name="password" autofocus></div>
<button class="primary">ログイン</button></form></section>""", nav=False)


def view_home(query, csrf):
    cfg = ms.load_config()
    pid = ms.running_pid()

    if pid:
        status = '<span class="badge ok">実行中</span>'
        actions = post_form("stop", csrf, '<button class="danger">■ 停止</button>')
    else:
        status = '<span class="badge">待機中</span>'
        slot_sel = "".join(f'<option value="{h(v)}">{h(l)}</option>' for v, l in SLOT_CHOICES)
        actions = post_form("run_now", csrf,
                            f'<div class="row"><label>診察時間</label><select name="slot_keyword">{slot_sel}</select></div>'
                            '<div class="row run-now"><button class="primary">今すぐ受付</button></div>', cls="",
                            confirm="今すぐ受付を開始しますか？\n診察時間: {slot}\n\n実際に MEDICALPASS へ受付を送信します。",
                            ok_label="受付する", danger=False)

    # --- 受付予定
    t0 = ms.now()
    items = ms.bookings_view(cfg)
    upcoming = [b for b in items if b["status"] in ("pending", "running")]
    past = [b for b in items if b["status"] not in ("pending", "running")][-10:][::-1]

    today_iso = t0.strftime("%Y-%m-%d")

    def booking_row(b, deletable):
        at = b["at"]
        slot = cfg["slots"][b["slot"]]["label"]
        hid = f'<input type="hidden" name="id" value="{h(b["id"])}">'
        desc = f"{at:%m/%d}（{ms.WEEKDAYS[at.weekday()]}）{slot}" + (f"（受付番号 {b['no']}）" if b.get("no") else "")
        # 「受付キャンセル」は、MEDICALPASS の最新の受付一覧にまだ残っている場合だけ表示
        # （一覧未取得なら表示し、押したときにサーバー側で存在を確認する）
        mr_ = cfg["mp_reservations"]
        still_there = not mr_["at"] or ms.match_reservation(cfg, b, mr_["items"]) is not None
        if b["status"] == "success" and b["date"] == today_iso and still_there:
            # 本日受付済みの予定は MEDICALPASS 側の受付をキャンセルできる
            delete = post_form("cancel_booking", csrf, f'{hid}<button class="danger">受付キャンセル</button>',
                               confirm=f"{desc} の受付を MEDICALPASS でキャンセルしますか？\n\nキャンセルすると受付番号は取り消され、元に戻せません。",
                               ok_label="キャンセルする")
        elif deletable:
            note = "（MEDICALPASS の受付はキャンセルされません）" if b["status"] == "success" else ""
            delete = post_form("delete_booking", csrf, f'{hid}<button>削除</button>',
                               confirm=f"{desc} の受付予定を一覧から削除しますか？{note}", ok_label="削除する")
        else:
            delete = ""
        # 受付No は成功時だけ意味があるので、専用列にせず状態の後ろに表示する
        status = ms.STATUS_LABEL.get(b["status"], h(b["status"])) + (
            f"（No.{h(b['no'])}）" if b.get("no") and b["status"] in ("success", "cancelled") else "")
        return (f"<tr><td>{at:%Y/%m/%d}（{ms.WEEKDAYS[at.weekday()]}）</td><td>{h(slot)}</td>"
                f"<td>{at:%H:%M:%S}</td><td>{status}</td><td>{delete}</td></tr>")

    head = "<tr><th>受付日</th><th>枠</th><th>開始</th><th>状態</th><th></th></tr>"
    up_rows = "".join(booking_row(b, b["status"] == "pending") for b in upcoming) or \
        "<tr><td colspan=5 class='muted'>登録された受付予定はありません</td></tr>"
    past_rows = "".join(booking_row(b, True) for b in past)
    # --- MEDICALPASS の受付状況: まず保存済みの内容で表示し、古ければ表示後に裏で取り直す
    autoload = ms.has_credentials(cfg) and ms.reservations_stale(cfg) and not pid
    mp_section = mp_section_html(cfg, csrf, loading=autoload)

    slot_opts = "".join(f'<option value="{k}">{h(s["label"])}（{h(s["start"])} 開始）</option>'
                        for k, s in cfg["slots"].items())
    closed = "・".join(ms.WEEKDAYS[i] for i in cfg["closed_weekdays"]) or "なし"
    add_form = post_form("add_booking", csrf, f"""
<div class="row"><label>受付日</label><input type="date" name="date" min="{t0:%Y-%m-%d}" required></div>
<div class="row"><label>枠</label><select name="slot">{slot_opts}</select></div>
<div class="row"><label>開始時刻</label><input type="text" name="start" size="9" placeholder="空欄=既定">
<span class="muted">空欄なら枠の既定時刻（設定画面で変更可）</span></div>
<button class="primary">＋ 受付予定を追加</button>""", cls="")

    rows = "".join(
        f"<tr><td>{h(r['date'])} {h(r['time'])}</td><td>{h(r['slot'])}{'（テスト）' if r.get('dry_run') else ''}</td>"
        f"<td>{RESULT_LABEL.get(r['result'], h(r['result']))}</td><td>{h(r.get('no') or '')}</td></tr>"
        for r in ms.load_results(10)) or "<tr><td colspan=4 class='muted'>まだありません</td></tr>"

    today = ms.now().strftime("%Y-%m-%d")
    log = ms.read_log(today, tail=40)
    lc = cfg["login_check"]
    settings_link = f'<a href="{BASE_PATH}?p=settings">設定画面</a>'
    if not ms.has_credentials(cfg):
        account = '<span class="ng">未設定</span>'
        warn = (f'<p class="msg">MEDICALPASS のログイン情報が未設定です。{settings_link}で'
                'メールアドレス・パスワードを入力してください（未設定の間は受付できません）。</p>')
    else:
        badge = {True: '<span class="badge ok">✅ ログイン確認済み</span>',
                 False: '<span class="badge ng">❌ ログイン失敗</span>'}.get(
                     lc["ok"], '<span class="badge">未確認</span>')
        when = f' <span class="muted">{h(lc["at"])}</span>' if lc["at"] else ""
        account = f'{h(cfg["email"])} {badge}{when}'
        warn = (f'<p class="msg">MEDICALPASS にログインできませんでした。{settings_link}で'
                'ログイン情報を確認してください。</p>') if lc["ok"] is False else ""

    page("ホーム", f"""{flash(query)}{warn}
<section><h2>状態 {status}</h2>
<div class="row"><label>MEDICALPASS</label>{account}</div>
{actions}
<p class="muted">「今すぐ受付」は確認画面で「受付する」を押した時点ですぐ開始し、実際に受付を送信します。
送信間隔・最大試行回数・受付内容は設定画面の値を使います。
「今すぐ受付」「受付予定を追加」は、押すたびに MEDICALPASS へのログインを確認し、ログインできない場合は実行しません。</p></section>
{mp_section}{MP_AUTOLOAD_JS if autoload else ""}
<section><h2>受付予定</h2>
<div class="tbl"><table>{head}{up_rows}</table></div>
<p class="muted">登録した日の開始時刻に自動で受付します（休診日: {closed}・祝日は登録不可）。
同じ日に午前・午後の両方を登録した場合、先に成功した時点で残りはスキップします。</p>
{add_form}
{f'<h2 style="margin-top:16px">過去の予定</h2><div class="tbl"><table>{head}{past_rows}</table></div>' if past else ''}
</section>
<section><h2>実行履歴</h2><div class="tbl"><table>
<tr><th>日時</th><th>枠</th><th>結果</th><th>受付No</th></tr>{rows}</table></div></section>
<section>{log_box(log, "本日のログ（最新 40 行）", open_=bool(pid))}
<a href="{BASE_PATH}?p=logs&d={today}">全文を見る</a></section>""", refresh=bool(pid))


def view_settings(query, csrf):
    cfg = ms.load_config()
    res, send = cfg["reservation"], cfg["send"]

    def sel(name, choices, cur):
        opts = "".join(f'<option value="{h(str(v))}"{" selected" if str(v) == str(cur) else ""}>{h(str(l))}</option>'
                       for v, l in choices)
        return f'<select name="{name}">{opts}</select>'

    def chk(name, on, label=""):
        return f'<label><input type="checkbox" name="{name}" value="1"{" checked" if on else ""}> {label}</label>'

    n = int(res["num_patients"])
    card_rows = "".join(
        f'<div class="row card-row" data-i="{i}"{"" if i < n else " hidden"}><label>診察券番号（{i + 1}人目）</label>'
        f'<input type="text" name="card{i}" value="{h(res["cards"][i])}" size="10" placeholder="空欄=初期選択"></div>'
        for i in range(4))
    # 診察券は、1 アカウントに複数枚登録している場合だけ指定が必要なので折りたたむ
    cards = (f'<details{" open" if any(res["cards"]) or n > 1 else ""}><summary class="muted">詳細設定: 診察券番号'
             '（2人以上で受付する場合・複数の診察券を登録している場合）</summary>'
             '<p class="muted">1人で診察券が 1 枚だけなら入力不要です（空欄なら MEDICALPASS で初期選択される診察券）。'
             '<b>2人以上で受付する場合は、全員分の診察券番号の入力が必要です。</b>'
             '番号は MEDICALPASS の「診察券情報」に表示される番号です（先頭の 0 は省略可）。</p>'
             f'{card_rows}</details>'
             '<script>document.querySelector("select[name=num_patients]").addEventListener("change",e=>'
             '{document.querySelectorAll(".card-row").forEach(r=>r.hidden=+r.dataset.i>=+e.target.value);'
             'if(+e.target.value>1)document.querySelector(".card-row").closest("details").open=true})</script>')
    informs = " ".join(f'<label><input type="radio" name="inform" value="{v}"'
                       f'{" checked" if res["inform"] == v else ""}> {v}</label>' for v in ("なし", "メール"))
    closed = " ".join(chk(f"closed{i}", i in cfg["closed_weekdays"], d) for i, d in enumerate(ms.WEEKDAYS))

    def slot_row(key, s):
        k = "slot_" + key
        return (f'<div class="row"><label>{h(s["label"])}の枠</label>'
                f'既定の開始 <input type="text" name="{k}_start" value="{h(s["start"])}" size="9">'
                f' 診察時間 {sel(k + "_kw", SLOT_CHOICES, s["slot_keyword"])}</div>')
    slots = "".join(slot_row(k, s) for k, s in cfg["slots"].items())

    body = f"""{flash(query)}
<form method="post" action="{BASE_PATH}"><input type="hidden" name="action" value="save_settings">
<input type="hidden" name="csrf" value="{csrf}">
<section><h2>MEDICALPASS ログイン情報</h2>
<div class="row"><label>メールアドレス</label><input type="email" name="email" value="{h(cfg["email"])}" size="30"></div>
<div class="row"><label>パスワード</label><input type="password" name="mp_password" size="20"
 placeholder="{'変更する場合のみ入力' if cfg['password_enc'] else '未設定'}" autocomplete="new-password"></div>
<div class="row"><label>診療科 ID</label><input type="text" name="dept_id" value="{h(cfg["dept_id"])}" size="8"></div>
<p class="muted">パスワードはサーバー上で暗号化して保存されます。</p></section>

<section><h2>受付内容</h2>
<div class="row"><label>受付人数</label>{sel("num_patients", [(i, i) for i in range(1, 5)], res["num_patients"])}</div>
{cards}
<div class="row"><label>お知らせ</label>{informs}</div>
<div class="row"><label>お知らせ時間</label>{sel("inform_minutes", [(m, m) for m in ("30", "45", "60", "75", "90")], res["inform_minutes"])} 分前</div>
</section>

<section><h2>送信設定</h2>
<div class="row"><label>送信間隔（秒）</label><input type="number" name="interval" value="{send["interval"]}"
 step="0.1" min="0.3" max="10" style="width:6em"><span class="muted">0.1 秒単位（最小 0.3 秒）</span></div>
<div class="row"><label>最大試行回数</label><input type="number" name="max_tries" value="{send["max_tries"]}"
 min="1" max="1000" style="width:6em"><span class="muted">受付成功した時点で自動停止</span></div>
</section>

<section><h2>受付予定の既定値</h2>
{slots}
<p class="muted">Web受付の開始（08:00 / 14:00）の少し前を指定します。07:59:58 / 075958 / 0759 などの形式で入力できます。</p>
<div class="row"><label>休診日</label>{closed}</div>
<div class="row"><label>祝日</label>{chk("skip_holidays", cfg["skip_holidays"], "祝日は登録できないようにする")}</div>
</section>

<section><h2>管理画面パスワード</h2>
<div class="row"><label>新しいパスワード</label><input type="password" name="new_admin" size="20"
 placeholder="変更する場合のみ（8文字以上）" autocomplete="new-password"></div></section>

<button class="primary">保存</button></form>
<section><h2>接続確認</h2>{post_form("check_login", csrf, '<button>MEDICALPASS にログインできるか確認</button>')}
</section>"""
    page("設定", body)


def view_logs(query, csrf):
    dates = ms.log_dates()
    d = query.get("d") or (dates[0] if dates else "")
    links = " ".join(f'<a href="{BASE_PATH}?p=logs&d={x}">{"<b>" + x + "</b>" if x == d else x}</a>'
                     for x in dates[:31]) or "<span class='muted'>ログはまだありません</span>"
    text = ms.read_log(d) if d else ""
    del_day = post_form("delete_log", csrf, f'<input type="hidden" name="d" value="{h(d)}">'
                        '<button>この日のログを削除</button>',
                        confirm=f"{d} のログ（HTML を含む）を削除しますか？\n元に戻せません。",
                        ok_label="削除する") if d in dates else ""
    del_all = post_form("delete_all_logs", csrf, '<button class="danger">すべてのログを削除</button>',
                        confirm="すべての日付のログ・HTML・実行履歴を削除しますか？\n（受付予定は残ります）\n\n元に戻せません。",
                        ok_label="すべて削除する")
    page("ログ", f"""{flash(query)}<section><h2>日付</h2><p>{links}</p></section>
<section>{log_box(text, d or "ログ")}
<p class="muted">HTML レスポンスはサーバーの {h(str(ms.LOGS))}/{h(d)}/ に保存されています。</p>
<div class="row">{del_day}</div></section>
<section><h2>ログの削除</h2>
<p class="muted">「すべてのログを削除」は全日付のログ・HTML・実行履歴を削除します（受付予定は残ります）。元に戻せません。</p>
<div class="row">{del_all}</div></section>""")


def view_mp_reservations(session):
    """受付状況セクションだけを返す（ホームの自動更新用）。必要なときだけ MEDICALPASS から取り直す。"""
    if not ms.check_session(session):
        print("Status: 403 Forbidden\nContent-Type: text/plain; charset=utf-8")
        no_cache_headers()
        print("\nforbidden")
        return
    ms.refresh_mp_reservations_if_stale()
    print("Content-Type: text/html; charset=utf-8")
    no_cache_headers()
    print()
    print(mp_section_html(ms.load_config(), ms.csrf_token(session)))


# ---------------------------------------------------------------- POST 処理

def save_settings(form):
    from mp_core import parse_hms
    cfg = ms.load_config()
    errors = []

    new_email = f1(form, "email").strip()
    account_changed = new_email != cfg["email"] or bool(f1(form, "mp_password"))
    cfg["email"] = new_email
    if f1(form, "mp_password"):
        cfg["password_enc"] = ms.encrypt(f1(form, "mp_password"))
    if account_changed:
        cfg["login_check"] = {"ok": None, "at": "", "msg": ""}
    dept = f1(form, "dept_id").strip()
    if dept.isdigit():
        cfg["dept_id"] = dept
    else:
        errors.append("診療科 ID は数字で入力してください")

    res = cfg["reservation"]
    res["num_patients"] = int(f1(form, "num_patients", "1"))
    res["cards"] = [f1(form, f"card{i}").strip() for i in range(4)]
    n = res["num_patients"]
    if n > 1:
        # 2人以上は「初期選択」だと全員が同じ診察券になり受付できないため、全員分を必須にする
        given = res["cards"][:n]
        if not all(given):
            errors.append(f"受付人数が {n} 人の場合は、詳細設定で {n} 人分の診察券番号を入力してください")
        elif len({c.lstrip("0") for c in given}) < n:
            errors.append("同じ診察券番号が重複しています")
    res["inform"] = f1(form, "inform", "メール")
    res["inform_minutes"] = f1(form, "inform_minutes", "30")

    try:
        interval = float(f1(form, "interval"))
        tries = int(f1(form, "max_tries"))
        if interval < 0.3 or not 1 <= tries <= 1000:
            raise ValueError
        cfg["send"] = {"interval": round(interval, 1), "max_tries": tries}
    except ValueError:
        errors.append("送信間隔は 0.3 秒以上、最大試行回数は 1〜1000 で入力してください")

    for key, s in cfg["slots"].items():
        s["slot_keyword"] = f1(form, f"slot_{key}_kw")
        try:
            s["start"] = parse_hms(f1(form, f"slot_{key}_start")) or s["start"]
        except ValueError:
            errors.append(f"{s['label']}の開始時刻の形式が正しくありません")
    cfg["closed_weekdays"] = [i for i in range(7) if f1(form, f"closed{i}") == "1"]
    cfg["skip_holidays"] = f1(form, "skip_holidays") == "1"

    new_admin = f1(form, "new_admin")
    if new_admin:
        if len(new_admin) >= 8:
            cfg["admin_hash"] = ms.hash_password(new_admin)
        else:
            errors.append("管理画面パスワードは 8 文字以上にしてください")

    if errors:
        return " / ".join(errors)
    ms.save_config(cfg)
    if account_changed and ms.has_credentials(cfg):
        return "保存しました。" + ms.verify_login()[1]
    return "保存しました"


def check_login():
    return ms.verify_login()[1]


def blocked_without_login():
    """ホームの操作前チェック。未設定・ログイン失敗なら設定画面へリダイレクトして True を返す。
    （redirect() は出力するだけで値を返さないため、呼び出し側はこの戻り値で処理を打ち切る）"""
    if not ms.has_credentials(ms.load_config()):
        redirect("settings", "MEDICALPASS のメールアドレス・パスワードを入力して保存してください")
        return True
    ok, msg = ms.verify_login()
    if not ok:
        redirect("settings", f"{msg}。ログイン情報を確認してください")
        return True
    return False


def handle_post(form, session):
    action = f1(form, "action")
    cfg = ms.load_config()

    if action == "login":
        if cfg["admin_hash"] and ms.check_password(f1(form, "password"), cfg["admin_hash"]):
            return redirect(cookie=session_cookie(ms.make_session(), ms.SESSION_TTL))
        time.sleep(2)
        return redirect(msg="パスワードが違います")

    if not ms.check_session(session):
        return redirect(msg="ログインしてください")
    if f1(form, "csrf") != ms.csrf_token(session) or not same_origin():
        return redirect(msg="不正なリクエストです。画面を再読み込みしてください")

    if action == "logout":
        return redirect(cookie=session_cookie("", 0))
    if action == "save_settings":
        return redirect("settings", save_settings(form))
    if action == "check_login":
        return redirect("settings", check_login())
    if action == "add_booking":
        if blocked_without_login():
            return
        cfg = ms.load_config()
        err = ms.add_booking(cfg, f1(form, "date"), f1(form, "slot"), f1(form, "start"))
        if err:
            return redirect(msg=err)
        ms.save_config(cfg)
        return redirect(msg="受付予定を追加しました")
    if action == "fetch_reservations":
        if not ms.has_credentials(ms.load_config()):
            return redirect("settings", "MEDICALPASS のメールアドレス・パスワードを入力して保存してください")
        return redirect(msg=ms.fetch_mp_reservations()[1])
    if action in ("cancel_reservation", "cancel_booking"):
        if f1(form, "confirm") != "1":
            return redirect(msg=NEED_CONFIRM)
        if not ms.has_credentials(ms.load_config()):
            return redirect("settings", "MEDICALPASS のメールアドレス・パスワードを入力して保存してください")
        if action == "cancel_reservation":
            ok, msg = ms.cancel_mp_reservation(rid=f1(form, "rid"))
        else:
            ok, msg = ms.cancel_mp_reservation(booking_id=f1(form, "id"))
        return redirect(msg=("受付をキャンセルしました: " if ok else "") + msg)
    if action == "delete_booking":
        if f1(form, "confirm") != "1":
            return redirect(msg=NEED_CONFIRM)
        ms.delete_booking(cfg, f1(form, "id"))
        ms.save_config(cfg)
        return redirect(msg="受付予定を削除しました")
    if action == "run_now":
        if ms.running_pid():
            return redirect(msg="実行中のジョブがあります")
        if f1(form, "confirm") != "1":
            return redirect(msg=NEED_CONFIRM)
        kw = f1(form, "slot_keyword")
        if kw not in dict(SLOT_CHOICES):
            return redirect(msg="診察時間の指定が正しくありません")
        if blocked_without_login():
            return
        ms.spawn_job(dry_run=False, slot_keyword=kw)
        time.sleep(1)
        return redirect(msg=f"受付処理を開始しました（診察時間: {dict(SLOT_CHOICES)[kw]}）")
    if action in ("delete_log", "delete_all_logs"):
        page_ = "logs"
        if ms.running_pid():
            return redirect(page_, "実行中は削除できません")
        if f1(form, "confirm") != "1":
            return redirect(page_, NEED_CONFIRM)
        if action == "delete_log":
            ms.delete_log(f1(form, "d"))
            return redirect(page_, f"{f1(form, 'd')} のログを削除しました")
        ms.delete_all_logs()
        return redirect(page_, "すべてのログ・実行履歴を削除しました")
    if action == "stop":
        ms.request_stop()
        return redirect(msg="停止を要求しました")
    return redirect(msg="不明な操作です")


# ---------------------------------------------------------------- main

def main():
    method, query, form, session = read_request()
    if method == "POST":
        return handle_post(form, session)
    p = query.get("p", "")
    if p == "mp_reservations":
        return view_mp_reservations(session)
    if not ms.check_session(session):
        return view_login(query)
    csrf = ms.csrf_token(session)
    if p == "settings":
        return view_settings(query, csrf)
    if p == "logs":
        return view_logs(query, csrf)
    if p == "logout":
        return redirect(cookie=session_cookie("", 0))
    return view_home(query, csrf)


try:
    main()
except Exception as e:  # 画面にスタックトレースを出さない
    print("Status: 500 Internal Server Error\nContent-Type: text/plain; charset=utf-8\n")
    print(f"エラーが発生しました: {type(e).__name__}")
