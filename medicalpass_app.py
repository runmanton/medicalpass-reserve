"""MedicalPass 受付 - デスクトップアプリ (Tkinter)"""
import json
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import keyring

import mp_core
from mp_core import (INFORM_MINUTES, MAX_PATIENTS, MIN_INTERVAL, DailyLog, Options, Reserver,
                     parse_hms)

APP_NAME = "MedicalPassReserve"
TITLE = "MedicalPass 受付"
DEFAULT_LABEL = "初期選択（Web と同じ）"
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
LOG_DIR = APP_DIR / "logs"
SETTINGS_FILE = Path(os.environ.get("APPDATA", APP_DIR)) / APP_NAME / "settings.json"


# ---------------------------------------------------------------- 設定

def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_password(email):
    try:
        return keyring.get_password(APP_NAME, email) if email else None
    except Exception:
        return None


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, settings, on_save):
        super().__init__(master)
        self.title("設定")
        self.resizable(False, False)
        self.transient(master)
        self.on_save = on_save

        email = settings.get("email", "")
        self.v_email = tk.StringVar(value=email)
        self.v_pass = tk.StringVar(value=get_password(email) or "")
        self.v_dept = tk.StringVar(value=settings.get("dept_id", "3199"))

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both")
        rows = [("メールアドレス", self.v_email, None),
                ("パスワード", self.v_pass, "*"),
                ("診療科 ID", self.v_dept, None)]
        for r, (label, var, show) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=4)
            ttk.Entry(f, textvariable=var, width=34, show=show or "").grid(row=r, column=1, pady=4)
        ttk.Label(f, text="パスワードは Windows 資格情報マネージャーに保存されます。",
                  foreground="gray").grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 8))
        b = ttk.Frame(f)
        b.grid(row=4, column=0, columnspan=2, sticky="e")
        ttk.Button(b, text="保存してログイン", command=self.save).pack(side="left", padx=4)
        ttk.Button(b, text="キャンセル", command=self.destroy).pack(side="left")
        self.grab_set()

    def save(self):
        email, pw, dept = self.v_email.get().strip(), self.v_pass.get(), self.v_dept.get().strip()
        if not email or not pw or not dept.isdigit():
            messagebox.showwarning("設定", "メールアドレス・パスワード・診療科 ID（数字）を入力してください。",
                                   parent=self)
            return
        keyring.set_password(APP_NAME, email, pw)
        self.on_save({"email": email, "dept_id": dept})
        self.destroy()


# ---------------------------------------------------------------- メイン画面

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITLE)
        self.minsize(560, 600)
        self.settings = load_settings()
        self.logq = queue.Queue()
        self.rv = None
        self.worker = None
        self.stop_flag = threading.Event()
        self.daily_log = DailyLog(LOG_DIR)

        self._build()
        self.log(f"起動しました（ログ保存先: {LOG_DIR}）")
        self.after(100, self._drain_log)
        self.after(200, self.startup)

    # ------------------------------------------------------------ レイアウト

    def _build(self):
        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        self.v_status = tk.StringVar(value="未ログイン")
        ttk.Label(top, textvariable=self.v_status).pack(side="left")
        ttk.Button(top, text="設定", command=self.open_settings).pack(side="right")
        ttk.Button(top, text="再ログイン", command=self.login_async).pack(side="right", padx=4)

        # --- 受付内容
        opt = ttk.LabelFrame(self, text="受付内容", padding=8)
        opt.pack(fill="x", padx=8, pady=8)

        ttk.Label(opt, text="受付人数").grid(row=0, column=0, sticky="w", **pad)
        self.v_num = tk.StringVar(value="1")
        cb = ttk.Combobox(opt, textvariable=self.v_num, state="readonly", width=6,
                          values=[str(i) for i in range(1, MAX_PATIENTS + 1)])
        cb.grid(row=0, column=1, sticky="w", **pad)
        cb.bind("<<ComboboxSelected>>", lambda e: self._show_card_rows())

        ttk.Label(opt, text="診察時間").grid(row=1, column=0, sticky="w", **pad)
        self.v_slot = tk.StringVar(value=DEFAULT_LABEL)
        self.cb_slot = ttk.Combobox(opt, textvariable=self.v_slot, state="readonly", width=28,
                                    values=[DEFAULT_LABEL, "午前診察", "午後診察"])
        self.cb_slot.grid(row=1, column=1, sticky="w", **pad)
        ttk.Button(opt, text="Web から選択肢を取得", command=self.load_form_async).grid(
            row=1, column=2, sticky="w", **pad)

        self.card_frame = ttk.Frame(opt)
        self.card_frame.grid(row=2, column=0, columnspan=3, sticky="w")
        self.v_cards, self.card_rows = [], []
        for i in range(MAX_PATIENTS):
            v = tk.StringVar(value=DEFAULT_LABEL)
            lbl = ttk.Label(self.card_frame, text=f"診察券（{i + 1}人目）")
            box = ttk.Combobox(self.card_frame, textvariable=v, state="readonly", width=40,
                               values=[DEFAULT_LABEL])
            self.v_cards.append(v)
            self.card_rows.append((lbl, box))
        self._show_card_rows()

        ttk.Label(opt, text="お知らせ").grid(row=3, column=0, sticky="w", **pad)
        self.v_inform = tk.StringVar(value="メール")
        rf = ttk.Frame(opt)
        rf.grid(row=3, column=1, columnspan=2, sticky="w", **pad)
        for text in ("なし", "メール"):
            ttk.Radiobutton(rf, text=text, value=text, variable=self.v_inform,
                            command=self._toggle_minutes).pack(side="left", padx=(0, 12))

        ttk.Label(opt, text="お知らせ時間").grid(row=4, column=0, sticky="w", **pad)
        self.v_minutes = tk.StringVar(value="30")
        mf = ttk.Frame(opt)
        mf.grid(row=4, column=1, sticky="w", **pad)
        self.cb_minutes = ttk.Combobox(mf, textvariable=self.v_minutes, state="readonly",
                                       width=6, values=INFORM_MINUTES)
        self.cb_minutes.pack(side="left")
        ttk.Label(mf, text="分前").pack(side="left", padx=4)

        # --- 送信設定
        run = ttk.LabelFrame(self, text="送信設定", padding=8)
        run.pack(fill="x", padx=8)

        self.v_interval = tk.StringVar(value="0.5")
        self.v_tries = tk.StringVar(value="100")
        self.v_start = tk.StringVar(value="")

        ttk.Label(run, text="送信間隔（秒）").grid(row=0, column=0, sticky="w", **pad)
        ttk.Spinbox(run, textvariable=self.v_interval, from_=MIN_INTERVAL, to=10,
                    increment=0.1, format="%.1f", width=8).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(run, text=f"0.1 秒単位で指定可（最小 {MIN_INTERVAL} 秒）",
                  foreground="gray").grid(row=0, column=2, sticky="w")

        ttk.Label(run, text="最大試行回数").grid(row=1, column=0, sticky="w", **pad)
        ttk.Spinbox(run, textvariable=self.v_tries, from_=1, to=10000, increment=10,
                    width=8).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(run, text="受付成功した時点で自動停止", foreground="gray").grid(
            row=1, column=2, sticky="w")

        ttk.Label(run, text="開始時刻（JST）").grid(row=2, column=0, sticky="w", **pad)
        e_start = ttk.Entry(run, textvariable=self.v_start, width=10)
        e_start.grid(row=2, column=1, sticky="w", **pad)
        e_start.bind("<FocusOut>", self._normalize_start)
        e_start.bind("<Return>", self._normalize_start)
        ttk.Label(run, text="例 14:00:00 / 140000 / 1400（空欄 = すぐ開始）",
                  foreground="gray").grid(row=2, column=2, sticky="w")

        self.v_dry = tk.BooleanVar(value=False)
        ttk.Checkbutton(run, text="テスト実行（確認画面で止め、受付は送信しない）",
                        variable=self.v_dry).grid(row=3, column=0, columnspan=3, sticky="w", **pad)

        bf = ttk.Frame(run)
        bf.grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        self.btn_start = ttk.Button(bf, text="▶ 開始", command=self.start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bf, text="■ 停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        ttk.Button(bf, text="ログフォルダを開く", command=self.open_logs).pack(side="left")

        # --- ログ
        self.txt = scrolledtext.ScrolledText(self, height=14, state="disabled",
                                             font=("MS Gothic", 9))
        self.txt.pack(fill="both", expand=True, padx=8, pady=8)

    def _show_card_rows(self):
        n = int(self.v_num.get())
        for i, (lbl, box) in enumerate(self.card_rows):
            if i < n:
                lbl.grid(row=i, column=0, sticky="w", padx=8, pady=4)
                box.grid(row=i, column=1, sticky="w", padx=8, pady=4)
            else:
                lbl.grid_remove()
                box.grid_remove()

    def _normalize_start(self, _event=None):
        """入力された開始時刻を HH:MM:SS 表示に整える（不正な値はそのまま）。"""
        try:
            self.v_start.set(parse_hms(self.v_start.get()))
        except ValueError:
            pass

    def _toggle_minutes(self):
        self.cb_minutes.configure(state="readonly" if self.v_inform.get() == "メール" else "disabled")

    # ------------------------------------------------------------ ログ

    def log(self, msg):
        line = f"[{mp_core.now():%H:%M:%S.%f}"[:-3] + f"] {msg}"
        self.daily_log.write(line)
        self.logq.put(line)

    def _drain_log(self):
        lines = []
        while not self.logq.empty():
            lines.append(self.logq.get())
        if lines:
            self.txt.configure(state="normal")
            self.txt.insert("end", "\n".join(lines) + "\n")
            self.txt.see("end")
            self.txt.configure(state="disabled")
        self.after(100, self._drain_log)

    # ------------------------------------------------------------ ログイン / 設定

    def startup(self):
        email = self.settings.get("email")
        if email and get_password(email):
            self.login_async()
        else:
            self.log("ログイン情報が未設定です。「設定」から入力してください。")
            self.open_settings()

    def open_settings(self):
        SettingsDialog(self, self.settings, self._on_settings_saved)

    def _on_settings_saved(self, data):
        self.settings.update(data)
        save_settings(self.settings)
        self.login_async()

    def _bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def login_async(self):
        email = self.settings.get("email")
        pw = get_password(email)
        if not email or not pw:
            self.open_settings()
            return
        self.v_status.set(f"ログイン中: {email} ...")

        def job():
            rv = Reserver(self.settings.get("dept_id", "3199"), log=self.log, log_dir=LOG_DIR)
            try:
                rv.login(email, pw)
            except Exception as e:
                self.log(f"ログインエラー: {e}")
                self.after(0, lambda: self.v_status.set("ログイン失敗 - 設定を確認してください"))
                return
            self.rv = rv
            self.after(0, lambda: self.v_status.set(f"ログイン中: {email}"))
            self._load_form()
        self._bg(job)

    # ------------------------------------------------------------ 選択肢の取得

    def load_form_async(self):
        if not self.rv:
            messagebox.showinfo(TITLE, "ログインしていません。")
            return
        self._bg(self._load_form)

    def _load_form(self):
        """Web の受付フォームから診察時間・診察券の選択肢を取得（公開中の場合のみ）。"""
        try:
            soup, form = self.rv.open_form()
        except Exception as e:
            self.log(f"フォームを取得できません: {e}")
            return
        if form is None:
            self.log("受付フォームは未公開です（受付時間外 / 上限到達）。送信時は「初期選択」を使います。")
            return
        slots = [o[1] for o in mp_core.select_options(form, "reservation[slot_id]") if o[0]]
        cards = [o[1] for o in mp_core.select_options(form, "custom[patient_nos_by_user][1]") if o[0]]
        self.log(f"フォーム取得: 診察時間={slots} / 診察券={cards}")

        def apply():
            values = [DEFAULT_LABEL] + slots + ["午前診察", "午後診察"]
            self.cb_slot.configure(values=list(dict.fromkeys(values)))
            for _, box in self.card_rows:
                box.configure(values=[DEFAULT_LABEL] + cards)
        self.after(0, apply)

    # ------------------------------------------------------------ 実行

    def _options(self):
        try:
            interval = float(self.v_interval.get())
            tries = int(self.v_tries.get())
        except ValueError:
            raise ValueError("送信間隔・最大試行回数は数値で入力してください。")
        if interval < MIN_INTERVAL:
            raise ValueError(f"送信間隔は {MIN_INTERVAL} 秒以上にしてください（サイトからのブロック防止）。")
        if tries < 1:
            raise ValueError("最大試行回数は 1 以上にしてください。")
        try:
            start = parse_hms(self.v_start.get())
        except ValueError:
            raise ValueError("開始時刻の形式が正しくありません。\n例: 14:00:00 / 140000 / 14:00 / 1400")
        self.v_start.set(start)
        pick = lambda v: "" if v.get() == DEFAULT_LABEL else v.get()
        n = int(self.v_num.get())
        return Options(
            num_patients=n,
            slot_keyword=pick(self.v_slot),
            card_keywords=[pick(v) for v in self.v_cards[:n]],
            inform=self.v_inform.get(),
            inform_minutes=self.v_minutes.get(),
            interval=interval,
            max_tries=tries,
            start_at=start,
            dry_run=self.v_dry.get(),
        )

    def start(self):
        if not self.rv:
            messagebox.showinfo(TITLE, "ログインしていません。")
            return
        try:
            opt = self._options()
        except ValueError as e:
            messagebox.showwarning(TITLE, str(e))
            return
        self.stop_flag.clear()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        mode = "テスト実行" if opt.dry_run else "本番送信"
        self.log(f"開始（{mode}）: 人数={opt.num_patients} 診察時間={opt.slot_keyword or '初期選択'} "
                 f"お知らせ={opt.inform}/{opt.inform_minutes}分前 間隔={opt.interval}秒 "
                 f"最大={opt.max_tries}回 開始={opt.start_at or 'すぐ'}")

        def job():
            result = False
            try:
                result = self.rv.run(opt, stop=self.stop_flag.is_set)
            except Exception as e:
                self.log(f"エラー: {e}")
            finally:
                self.after(0, lambda: self._run_done(result, opt.dry_run))
        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_flag.set()
        self.log("停止中...")

    def _run_done(self, result, dry_run):
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        if result is True and not dry_run:
            messagebox.showinfo(TITLE, "受付が完了しました。\n受付完了メール・Web の受付状況を確認してください。")
        elif result is True:
            messagebox.showinfo(TITLE, "テスト実行: 確認画面まで到達しました（受付は送信していません）。")
        elif result is None:
            messagebox.showwarning(TITLE, "受付結果を判定できませんでした。\nWeb で受付状況を確認してください。")

    def open_logs(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(LOG_DIR)


if __name__ == "__main__":
    App().mainloop()
