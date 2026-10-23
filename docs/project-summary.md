# MedicalPass 受付ツール — Tổng kết project

> Cập nhật: 2026-10-23
> Mục đích: tự động **当日順番受付 (lấy số thứ tự khám trong ngày)** trên MEDICALPASS cho phòng khám わかたび皮ふ科 (`https://medicalpass.jp/departments/3199`), gửi request trực tiếp đến endpoint thay vì thao tác trên màn hình web.

---

## 1. Bối cảnh và kết quả

| Hạng mục | Nội dung |
|---|---|
| Vấn đề | Web受付 hết suất rất nhanh (buổi sáng ngày 26/9: hết trong ~15 giây, 81人待ち) |
| Giải pháp | Gọi trực tiếp các endpoint của MEDICALPASS: login → form → confirm → 受付 |
| Kết quả thực tế | 26/9 14:00: form mở lúc 14:00:00.089, **受付 xong trong ~1 giây, lấy được 受付No：1** |
| Sản phẩm | ① App desktop (Windows exe) ② **Web app trên ConoHa WING** (đang dùng chính) |

---

## 2. Đặc tả MEDICALPASS (kết quả điều tra)

Site là **Ruby on Rails + Devise**. Mọi thao tác là form HTML thường (không có API JSON). Mọi POST đều cần `authenticity_token` (CSRF) lấy từ trang trước đó.

### 2.1 Luồng request

| # | Method / URL | Ghi chú |
|---|---|---|
| 1 | `GET /users/login` | Lấy `authenticity_token` + cookie |
| 2 | `POST /users/login` | `utf8=✓`, `authenticity_token`, `user[email]`, `user[password]`, `user[remember_me]=0`, `commit=ログイン` |
| 3 | `GET /departments/3199/reservations/new` | Form `#new_reservation`. Ngoài giờ / hết suất → redirect sang `/reservations/finished` |
| 4 | `POST /departments/3199/reservations/confirm` | Màn hình xác nhận (`commit=確認`) |
| 5 | `POST /departments/3199/reservations` | **受付 thật** (`commit=登録`). Thành công → redirect `/departments/3199` |
| 6 | `GET /reservations` | 受付確認・キャンセル (danh sách lịch đang có) |
| 7 | `POST /reservations/{id}` | **Huỷ lịch**: `_method=delete`, `authenticity_token` (lấy từ `<meta name="csrf-token">`) |

### 2.2 Field của form 受付 (bước 3 → 4)

| Field | Ý nghĩa / giá trị |
|---|---|
| `custom[num_of_patients]` | 受付人数 1–4 |
| `reservation[reservation_date]` | Ngày (hidden, `YYYY-MM-DD`) |
| `reservation[slot_id]` | Khung giờ (午前診察 / 午後診察). **ID đổi mỗi ngày** → chọn theo tên |
| `reservation[inform_kbn]` | お知らせ: `1`=なし, `2`=メール |
| `reservation[inform_minutes]` | お知らせ時間: 30 / 45 / 60 / 75 / 90 |
| `custom[patient_card_kbn][n]` | `1` (tái khám) |
| `custom[patient_nos_by_user][n]` | ID của thẻ 診察券 đã đăng ký (option label: `氏名 （診察券番号：12345）`) |
| `patient[...]` | Thông tin người khám lần đầu (để trống) |

- Màn hình xác nhận chứa lại toàn bộ thông tin dưới dạng hidden field (kể cả họ tên, ngày sinh, giới tính) + `back_mode_flg`.
- Tool **serialize form giống hệt trình duyệt** (lấy mọi field + giá trị mặc định), chỉ ghi đè field cần thiết → không phụ thuộc vào việc site thêm/bớt hidden field.

### 2.3 Nhận biết kết quả

- **Thành công**: flash `#notice.alert-info` chứa 「受付しました。…受付No：1」.
- **Thất bại**: redirect `/reservations/finished`, `.alert-danger`, hoặc flash chứa 「上限」「受付しておりません」….
- ⚠️ **Chỉ đọc vùng flash**, không đọc cả trang: thông báo của phòng khám luôn có chữ 「受付番号1〜5の方は…」 → đọc cả trang sẽ nhận nhầm là thành công.
- Không phân loại được → **dừng** (tránh 受付 hai lần).

### 2.4 Số 診察券

- MEDICALPASS hiển thị số thẻ **không có số 0 ở đầu** (VD `No. 12345`, `36222`).
- Tool so khớp **phần số sau 「診察券番号：」, bỏ qua số 0 ở đầu**, so khớp chính xác (không so một phần).

---

## 3. Kiến trúc

```
                     ┌──────────────────────────── ConoHa WING ─────────────────────────────┐
[Điện thoại / PC] ── HTTPS ─▶ /mp/index.py (CGI, màn hình quản lý)                          │
                     │          ├ 設定 / 受付予定 / 受付状況 / ログ                            │
                     │          └ đọc/ghi ~/medicalpass/data/config.json                      │
                     │                                                                       │
                     │  ジョブスケジューラー（分 50-59, 時 7,13）                              │
                     │   └▶ run.py --cron ─▶ mp_server.run_job ─▶ mp_core.Reserver ─▶ medicalpass.jp
                     └───────────────────────────────────────────────────────────────────────┘
```

### 3.1 Danh sách file

| File | Vai trò |
|---|---|
| `mp_core.py` | **Core**: login, serialize form, chọn khung giờ/thẻ, confirm, 受付, nhận biết kết quả, danh sách/huỷ lịch, log theo ngày. Dùng chung cho desktop & web |
| `medicalpass_app.py` | App desktop (Tkinter, giao diện tiếng Nhật), build thành `MedicalPassReserve.exe` bằng PyInstaller |
| `medicalpass_reserve.py` | Bản CLI |
| `conoha/medicalpass/mp_server.py` | Web: cài đặt, mã hoá mật khẩu, session, 受付予定, chạy job, huỷ lịch, cache 受付状況 |
| `conoha/medicalpass/run.py` | Lệnh cho cron (`--cron`), chạy ngay (`--now`), khởi tạo (`--init`, `--reset-admin`) |
| `conoha/medicalpass/setup.sh` | Cài đặt lần đầu (thư viện, quyền, khởi tạo) |
| `conoha/public/mp/index.py` | Màn hình web (CGI) |
| `conoha/public/mp/.htaccess` | `ExecCGI`, `AddHandler cgi-script .py`, `DirectoryIndex index.py` |

### 3.2 Bố trí trên server (user `c9687250`)

| Đường dẫn | Nội dung | Quyền |
|---|---|---|
| `~/medicalpass/` | Chương trình + `lib/` (thư viện pip) | 700 |
| `~/medicalpass/data/config.json` | Cài đặt, 受付予定, mật khẩu (đã mã hoá) | 600 |
| `~/medicalpass/data/secret.key` | Khoá mã hoá / ký session | 600 |
| `~/medicalpass/data/results.jsonl` | 実行履歴 | 600 |
| `~/medicalpass/logs/YYYY-MM-DD.log` | Log theo ngày | 700 |
| `~/medicalpass/logs/YYYY-MM-DD/HHMMSS_*.html` | HTML response (confirm / final / cancel) | 700 |
| `~/public_html/runmanton.conohawing.com/mp/` | `index.py`, `.htaccess` | 755 |

- Python: `/opt/alt/python311/bin/python3` (3.11.9)
- Thư viện: `requests`, `beautifulsoup4`, `cryptography`, `jpholiday`

### 3.3 Nhiều người dùng (cách A: tách bản)

| | Của mình | Bạn Yamaguchi |
|---|---|---|
| URL | `https://runmanton.conohawing.com/mp/` | `https://runmanton.conohawing.com/yamaguchi/` |
| Thư mục app | `~/medicalpass/` | `~/yamaguchi/` |
| Cron | `.../medicalpass/run.py --cron` | `.../yamaguchi/run.py --cron` (**job riêng**) |

- `index.py` tự xác định thư mục app theo tên thư mục web (`/mp/` → `~/medicalpass`, còn lại → `~/<tên>`) → **dùng chung 1 file `index.py`**.
- Cron phải là **job riêng** để hai bản chạy song song (nối hai lệnh trong một job thì chạy lần lượt → người sau bị chậm).

---

## 4. Chức năng web app

### 4.1 ホーム
- **状態**: 待機中 / 実行中 (lúc đang chạy tự tải lại mỗi 3 giây, có nút ■ 停止). Trạng thái đăng nhập MEDICALPASS (✅ / ❌ / 未確認).
- **今すぐ受付**: chọn 診察時間 (初期選択 / 午前 / 午後) → modal xác nhận → chạy ngay.
- **MEDICALPASS の受付状況**: danh sách lịch thật trên MEDICALPASS, **tự cập nhật khi mở trang** (chạy nền, cache 60 giây, không gọi khi đang chạy job). Mỗi lịch có nút 「受付をキャンセル」.
- **受付予定**: đăng ký **ngày + buổi** tái khám → đúng ngày đó tự chạy. Lịch thành công hôm nay (và vẫn còn trên MEDICALPASS) có nút 「受付キャンセル」.
- **実行履歴**, **本日のログ** (khung đóng/mở).

### 4.2 設定
- Tài khoản MEDICALPASS (email, mật khẩu, 診療科 ID) + nút kiểm tra đăng nhập.
- 受付内容: 受付人数 (mặc định 1), 診察券番号 (trong 「詳細設定」, **bắt buộc khi từ 2 người trở lên**), お知らせ (mặc định メール), お知らせ時間 (mặc định 30分前).
- 送信設定: 送信間隔 (mặc định **0.5 giây**, tối thiểu 0.3, tính từ lúc bắt đầu request này đến lúc bắt đầu request sau), 最大試行回数 (mặc định **50**).
- 受付予定の既定値: giờ bắt đầu buổi sáng **07:59:58** / buổi chiều **13:59:58**, 休診日 (**木・日**), ngày lễ.
- Đổi mật khẩu quản lý.

### 4.3 ログ
- Xem log theo ngày (khung cuộn, tự cuộn đến dòng cuối), xoá log theo ngày / xoá toàn bộ.

### 4.4 Quy tắc an toàn
- Mọi thao tác có rủi ro (今すぐ受付, キャンセル, 削除) đều phải qua **modal xác nhận**. Server cũng kiểm tra `confirm=1`.
- Trước 今すぐ受付 / thêm 受付予定: **đăng nhập thử MEDICALPASS**. Chưa cài hoặc đăng nhập thất bại → không thực hiện, chuyển sang 設定.
- **1 ngày 1 lần**: hôm đó đã 受付 thành công thì các lần sau tự bỏ qua (huỷ xong thì đặt lại được).
- Lỗi cài đặt (thẻ không tồn tại, 2 người trùng thẻ) → **dừng ngay**, log ghi danh sách thẻ đang đăng ký.
- Không nhận biết được kết quả → dừng (tránh 受付 hai lần).

---

## 5. Vận hành

### 5.1 Job scheduler (ConoHa コントロールパネル > サーバー管理 > ジョブスケジューラー)

| 月 | 日 | 曜日 | 時間 | 分 | コマンド |
|---|---|---|---|---|---|
| `*` | `*` | `*` | `7,13` | `50-59` | `/opt/alt/python311/bin/python3 /home/c9687250/medicalpass/run.py --cron` |

- Cron chạy mỗi phút trong khung 7:50–7:59 / 13:50–13:59. Nếu còn ≤70 giây đến giờ bắt đầu của một 受付予定 thì nhận lịch đó → login → chờ đến giây :58 → gửi.
- Giờ bắt đầu trong app phải nằm trong khung cron (07:51–08:00 / 13:51–14:00).

### 5.2 Cách cập nhật sau khi sửa code

1. Upload bằng WinSCP: `mp_core.py`, `mp_server.py`, `run.py` → `~/medicalpass/` (và `~/yamaguchi/`); `index.py` → `/mp/` (và `/yamaguchi/`).
2. Sửa ký tự xuống dòng và quyền file:

```bash
for a in medicalpass yamaguchi; do [ -d ~/$a ] && sed -i 's/\r$//' ~/$a/*.py && chmod 600 ~/$a/mp_core.py ~/$a/mp_server.py && chmod 700 ~/$a/run.py && /opt/alt/python311/bin/python3 ~/$a/run.py --cron && echo "$a OK"; done; cd ~/public_html/runmanton.conohawing.com && for d in mp yamaguchi; do [ -f $d/index.py ] && sed -i 's/\r$//' $d/index.py && chmod 755 $d/index.py && echo "web $d OK"; done
```

### 5.3 Lệnh hay dùng

| Mục đích | Lệnh (chạy trong thư mục app) |
|---|---|
| Tạo lại mật khẩu quản lý | `/opt/alt/python311/bin/python3 -c "import mp_server as ms, secrets; c=ms.load_config(); p=secrets.token_urlsafe(9); c['admin_hash']=ms.hash_password(p); ms.save_config(c); print('NEW PASSWORD =', p)"` |
| Xem mật khẩu MEDICALPASS đã lưu | `/opt/alt/python311/bin/python3 -c "import mp_server as ms; c=ms.load_config(); print(c['email'], ms.decrypt(c['password_enc']))"` |
| Chạy thử cron | `/opt/alt/python311/bin/python3 run.py --cron` (không có lịch hẹn thì không in gì là đúng) |

> Chỉ giải mã mật khẩu của người khác (bản yamaguchi) khi chính người đó đồng ý. Không dán mật khẩu vào chat.

### 5.4 Thêm người dùng mới
Copy chương trình + `lib/` sang `~/<tên>/` (**không copy `data/`, `logs/`**), copy `index.py` và `.htaccess` sang `/<tên>/`, đặt quyền, tạo mật khẩu quản lý, thêm **job cron riêng**. (Xem lệnh tạo bản `yamaguchi` trong lịch sử làm việc.)

---

## 6. Những lỗi đã gặp và bài học

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `ERR_TOO_MANY_REDIRECTS` | Viết redirect HTTPS bằng `%{HTTPS}` trong `.htaccess`, nhưng ConoHa có proxy phía trước nên giá trị này luôn là off | Bỏ redirect khỏi `.htaccess`, chuyển sang HTTPS bằng JS (hoặc dùng cài đặt của コンパネ) |
| Đúng mật khẩu nhưng không đăng nhập được | ① **Cache WEXAL** trả lại trang đăng nhập cũ (không phân biệt cookie) ② Mở bằng `http://` thì cookie `Secure` không được lưu | Header `X-Accel-Expires: 0` / `Cache-Control: private, no-store`, URL redirect mỗi lần một khác (`?t=`), tự chuyển http→https |
| Mật khẩu hiện ra bị thiếu ký tự | Console của WinSCP hiển thị UTF-8 thành Shift_JIS (lỗi font) | In mật khẩu bằng ký tự ASCII, không đặt ngay sau chữ tiếng Nhật |
| Nhận nhầm 受付 thành công | Thông báo của phòng khám có chữ 「受付番号」 | Chỉ nhận biết thành công / thất bại qua vùng flash `.alert` |
| Redirect sang 設定 nhưng vẫn thêm lịch hẹn | `redirect()` chỉ in ra rồi trả về `None` | Hàm kiểm tra trả về `True/False`, phía gọi dừng xử lý (đã thêm test) |
| Đặt cho 2 người, báo 「診察券が見つかりません」 | Nhập `036222`, MEDICALPASS hiển thị `36222` | So khớp phần số, bỏ qua số 0 ở đầu; lỗi cài đặt thì dừng ngay |
| Đã huỷ mà vẫn hiện nút キャンセル | Lịch chưa lưu ID của MEDICALPASS | So khớp theo ngày/buổi/受付番号; chỉ hiện nút khi lịch còn trên MEDICALPASS |
| iPhone nhận chuỗi số là số điện thoại | Safari tự nhận dạng số | `<meta name="format-detection" content="telephone=no,…">` |
| CGI không chạy được | Xuống dòng CRLF / thiếu quyền chạy | Sau khi upload chạy `sed -i 's/\r$//'` + `chmod 755` |

---

## 7. Test

- Bộ test giả lập CGI chạy trên máy (đăng nhập, CSRF, cài đặt, 受付予定, cron, huỷ lịch, modal, xoá log, kiểm tra 診察券…): **72 mục pass**.
- Trong test, **không gửi request thật nào đến MEDICALPASS** (đăng nhập, danh sách lịch và huỷ lịch đều là bản giả).
- Đã xác nhận trên môi trường thật: 受付 thành công (26/9, 10/7), khoảng cách gửi 0.5 giây trên server (sai lệch vài ms), huỷ lịch.

---

## 8. Việc còn lại / lưu ý

- [ ] App desktop (`MedicalPassReserve.exe`) **chưa được build lại** với `mp_core.py` mới nhất (sửa so khớp 診察券, dừng ngay khi lỗi cài đặt, danh sách/huỷ lịch).
- [ ] Bản yamaguchi: xác nhận tài khoản của họ đã đăng ký **đủ 2 thẻ** (No. 36222 / 36223) và lần đặt 2 người tiếp theo thành công.
- [ ] Source và exe đang nằm trong **thư mục tạm của session**. Cần chuyển sang thư mục cố định (hoặc quản lý bằng Git).
- ⚠️ Log và HTML response có **thông tin cá nhân** (họ tên, ngày sinh, số thẻ). Không chia sẻ ra ngoài.
- ⚠️ Nên xem điều khoản sử dụng của MEDICALPASS. Đừng hạ khoảng cách gửi xuống quá thấp (tối thiểu 0.3 giây) và đừng gửi quá nhiều request.
