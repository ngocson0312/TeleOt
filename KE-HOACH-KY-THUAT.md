# Kế hoạch kỹ thuật: Bot báo cáo KiotViet → Telegram

Bản cho người làm. Bản dễ đọc gửi anh rể nằm ở `tai-lieu/Tro-ly-bao-cao-KiotViet-Telegram.pdf`, ảnh từng trang để gửi Zalo nằm ở `tai-lieu/anh-gui-zalo/`. Sửa nội dung trong `tai-lieu/nguon/gioi-thieu.html` rồi chạy `python tai-lieu/nguon/build.py` để xuất lại.

## 1. Phạm vi

1. **Báo cáo tồn kho:** gửi theo lịch và cảnh báo gấp, gồm sắp hết hàng, sắp hết hạn (theo lô), tồn nhiều / bán chậm.
2. **Thống kê nhân viên:** doanh thu, số đơn, số cửa hàng, số lượng theo mặt hàng, % chỉ tiêu, xếp hạng cuối ngày. Tra cứu bằng tin nhắn Telegram.
3. **Nhiều nhà phân phối (NPP):** mỗi NPP một tài khoản KiotViet và một nhóm Telegram riêng. Chủ có nhóm tổng xem tất cả.
4. **Chỉ đọc KiotViet:** code không gọi bất kỳ API ghi nào.

## 2. Kiến trúc

```
KiotViet Public API  (mỗi NPP: Retailer + ClientId + ClientSecret)
        │  GET theo lịch (giai đoạn 3: thêm webhook stock.update)
        ▼
Đồng bộ (scheduler) ──► CSDL (SQLite cho MVP, Postgres khi nhiều NPP)
        │
        ▼
Tính báo cáo ──► Telegram Bot (gửi theo lịch + trả lời tin nhắn)
```

**Stack đề xuất:** Python 3.12+ (máy đã có 3.13).

| Việc | Thư viện |
|---|---|
| Gọi API | `httpx` (async) |
| Lịch chạy | `APScheduler`, múi giờ `Asia/Ho_Chi_Minh` |
| Bot | `python-telegram-bot` v21 (async) |
| CSDL | `SQLAlchemy` + SQLite, chuyển sang Postgres khi cần |
| Excel (xuất báo cáo, đọc file chỉ tiêu) | `openpyxl` |
| Cấu hình | `pydantic-settings` + file `.env` |
| Mã hóa client secret | `cryptography` (Fernet) |

MVP chạy một tiến trình duy nhất: bot và scheduler dùng chung một event loop.

## 3. KiotViet Public API: phần sẽ dùng

Đã đối chiếu với tài liệu chính thức: <https://www.kiotviet.vn/huong-dan-su-dung-public-api-retail/>

- Base URL: `https://public.kiotapi.com`
- Mọi request đều gửi header `Retailer: <tên gian hàng>` và `Authorization: Bearer <token>`.
- Phân trang: `pageSize` tối đa 100, `currentItem` là offset.
- Giới hạn: **5.000 request GET mỗi giờ**.

| Mục đích | Endpoint | Trường cần dùng |
|---|---|---|
| Lấy token | `POST https://id.kiotviet.vn/connect/token` | `grant_type=client_credentials`, scope `PublicApi.Access`. Token sống 24h: cache lại, làm mới trước khi hết hạn và khi gặp lỗi 401. |
| Chi nhánh | `GET /branches` | `id`, `branchName` |
| Nhân viên | `GET /users` | `id`, `userName`, `givenName` (không trả Super Admin) |
| Hàng, tồn, định mức, lô/HSD | `GET /products?includeInventory=true&IncludeBatchExpires=true` | `inventories[]`: `branchId`, `onHand`, `minQuality`, `maxQuality` (tài liệu viết "Quality")<br>`productBatchExpires[]`: `batchName`, `expireDate`, `onHand`, `branchId`<br>cờ `isBatchExpireControl` |
| Tồn nhanh, đồng bộ tăng dần | `GET /productOnHands?lastModifiedFrom=` | `inventories[]`: `onhand`, `reserved` (không có định mức) |
| Hóa đơn | `GET /invoices?fromPurchaseDate=&toPurchaseDate=` hoặc `lastModifiedFrom=` | `soldById`, `soldByName`, `customerId`, `purchaseDate`, `status` (1 hoàn thành, 2 hủy, 3 đang xử lý, 5 không giao được)<br>`invoiceDetails`: `productId`, `quantity`, `price`, `discount` |
| Trả hàng | `GET /returns` | `invoiceId` (trỏ về hóa đơn gốc), `returnDetails[]` |
| Webhook (giai đoạn 3) | `POST /webhooks` | sự kiện `stock.update`, `invoice.update`, `product.update`. Ký bằng `X-Hub-Signature` (HMAC-SHA256). Phải trả lời trong 5 giây; nếu trả 4xx thì KiotViet ngừng gửi. |

Chủ gian hàng lấy ClientId/Secret ở **Thiết lập cửa hàng → Thiết lập kết nối API**.

### Cần kiểm tra trên dữ liệu thật ở giai đoạn 0

Tài liệu chưa rõ hoặc tự mâu thuẫn ở các điểm sau:

- [ ] Body lấy token dùng `scopes` hay `scope`.
- [ ] `invoiceDetails` là mảng hay object (tài liệu ghi cả hai kiểu).
- [ ] `inventories` có bắt buộc `includeInventory=true` mới trả về không.
- [ ] Trả hàng có `soldById` không. Nếu không có, gán theo người bán của hóa đơn gốc.
- [ ] Gói KiotViet nào có mục "Thiết lập kết nối API". Tài liệu không nói, cần kiểm tra trên tài khoản từng NPP hoặc hỏi KiotViet.
- [ ] Giới hạn 5.000/giờ tính theo gian hàng hay theo client.
- [ ] `discount` trên dòng hóa đơn là giảm giá mỗi đơn vị hay cả dòng, có `subTotal` không. `probe` cộng thử từng công thức và so với tổng hóa đơn.
- [ ] Hàng nhiều đơn vị tính: đơn vị lớn (thùng) có `masterUnitId` trỏ về đơn vị gốc và `conversionValue` không. Code đang giả định có.
- [ ] Doanh thu nhân viên theo tổng hóa đơn hay cộng dòng hàng mới khớp báo cáo nhân viên của KiotViet. Đối soát bằng `python -m app.cli staff-check`.

## 4. Đồng bộ dữ liệu

- **Lần đầu (full sync):**
  - branches, users
  - products kèm tồn, định mức, lô
  - hóa đơn và trả hàng 90 ngày gần nhất, để tính tốc độ bán
- **Định kỳ, mỗi 15–60 phút:**
  - products / productOnHands, invoices, returns, lọc theo `lastModifiedFrom` (để bắt cả hóa đơn bị sửa hoặc hủy)
  - users và branches thì mỗi ngày một lần
- **Mốc đồng bộ:** lưu `last_modified` riêng cho từng NPP và từng loại dữ liệu (bảng `sync_state`).
- **Ước lượng số request:** NPP có 500 mặt hàng tốn khoảng 5 trang products, cộng 1–3 trang hóa đơn, tức khoảng 10 request mỗi lần. Chạy 4 lần mỗi giờ thì khoảng 40 request/giờ/NPP, rất xa mức 5.000.
- **Khi lỗi:** retry có backoff với 429/5xx, làm mới token khi gặp 401. Đồng bộ lỗi liên tục (ví dụ NPP đổi secret) thì báo cho người vận hành.

## 5. Logic báo cáo

### 5.1 Sắp hết hàng
- Điều kiện: `onHand < minQuality` nếu NPP đã đặt định mức trên KiotViet. Nếu chưa đặt thì dùng ngưỡng cấu hình (theo nhóm hàng hoặc một mức chung).
- Thêm chỉ số **số ngày còn bán được** = `onHand` ÷ lượng bán trung bình/ngày (30 ngày gần nhất). Cảnh báo khi dưới X ngày. Chỉ số này sát thực tế hơn ngưỡng cứng.

### 5.2 Sắp hết hạn
- Chỉ áp dụng cho hàng có `isBatchExpireControl = true`.
- Điều kiện: lô có `onHand > 0` và `expireDate − hôm nay ≤ N` ngày (mặc định 30, cấu hình được).
- Chia mức: ≤ 7 ngày (đỏ), ≤ N ngày (cam). Lô đã hết hạn mà còn tồn thì đưa vào mục riêng.

### 5.3 Tồn nhiều / bán chậm
- Gặp một trong các điều kiện sau:
  - `onHand > maxQuality` (nếu có đặt)
  - số ngày tồn (`onHand` ÷ bán trung bình/ngày) lớn hơn Y ngày
  - không bán được trong Z ngày mà vẫn còn tồn

### 5.4 Chống spam
- **Báo cáo theo lịch** (ví dụ 8h, 12h, 17h): bản tóm tắt đầy đủ.
- **Cảnh báo gấp:** chỉ gửi khi mặt hàng *mới* vượt ngưỡng kể từ lần trước (lưu trong bảng `alert_state`), không nhắc lại mỗi giờ.
  - Quét sau mỗi lần đồng bộ (15 phút).
  - Gồm: hết hàng, sắp hết hàng, lô đã hết hạn còn tồn, lô còn ≤ `expiry_red_days` ngày. Tồn nhiều không gấp.
  - Lần quét đầu chỉ ghi nhớ, không gửi.
  - Giờ im lặng (mặc định 21:00–07:00): cảnh báo chờ đến sáng.
  - Hết vấn đề rồi lại có vấn đề trong 24 giờ: không gửi lại.
  - Báo cáo theo lịch vừa gửi thì coi như đã báo.
  - Webhook `stock.update` chưa làm: cần domain + HTTPS, chỉ đáng làm nếu 15 phút là chậm.
- **Tin nhắn dài:** Telegram giới hạn 4.096 ký tự mỗi tin, nên chỉ hiện top N, phần còn lại đưa vào file Excel đính kèm.

### 5.5 Doanh thu nhân viên
- Lấy hóa đơn `status = 1`, lọc theo `purchaseDate`, nhóm theo `soldById`.
- Có hai cách tính doanh thu: tổng các dòng (`quantity × price − discount`), hoặc tổng tiền hóa đơn. Chọn cách nào **sau khi đối chiếu với báo cáo nhân viên có sẵn trên KiotViet** để số khớp, rồi trừ hàng trả lại trong kỳ.
- Số cửa hàng = số `customerId` khác nhau. Số lượng theo mặt hàng = tổng `quantity` theo `productId`.

### 5.6 Chỉ tiêu
- Chủ gửi file Excel theo mẫu vào chat với bot. Các cột: `Mã NV | Tên NV | Tháng | Loại (doanh thu / mặt hàng) | Mã hàng | Chỉ tiêu`. Bot đọc, kiểm tra lỗi và trả lời xác nhận.
- Các chỉ số:
  - % đạt = thực tế ÷ chỉ tiêu
  - tiến độ = số ngày đã qua ÷ số ngày trong tháng (có thể bỏ Chủ nhật nếu chủ muốn)
  - "chậm tiến độ" khi % đạt thấp hơn tiến độ quá 10 điểm

### 5.7 Xếp hạng cuối ngày
- Gửi lúc 18:00 (cấu hình được): doanh thu ngày theo nhân viên, so với hôm qua, kèm danh sách người đang chậm tiến độ tháng.

## 6. Telegram bot

- Tạo bot qua @BotFather rồi thêm vào nhóm. Muốn bot đọc được tin nhắn tự do trong nhóm (không có dấu `/`) thì phải **tắt privacy mode** của bot.
- Lệnh:

| Lệnh | Ví dụ | Kết quả |
|---|---|---|
| `/ton <tên hàng>` | `/ton banh quy` | tồn theo từng kho, kèm lô và hạn |
| `/nv <tên> [kỳ]` | `/nv tuan thang 11` | thống kê một nhân viên |
| `/top [kỳ]` | `/top hom nay` | xếp hạng nhân viên |
| `/hethan [số ngày]` | `/hethan 60` | các lô hết hạn trong 60 ngày |
| `/baocao` | | gửi ngay báo cáo tồn kho |
| `/chitieu` | gửi kèm file Excel | nạp chỉ tiêu |
| `/help` | | hướng dẫn |

- Tin nhắn tự do như "tồn bánh quy" hay "Tuấn tháng 11" được hiểu bằng từ khóa. So khớp tên sau khi bỏ dấu tiếng Việt; nếu khớp nhiều kết quả thì hỏi lại kèm nút chọn.
- Định dạng tin nhắn bằng HTML parse mode. File Excel gửi bằng `sendDocument`.

## 7. Nhiều NPP và phân quyền

- Bảng `tenants`: mỗi NPP có `retailer`, `client_id`, `client_secret` (mã hóa) và cấu hình riêng (ngưỡng, giờ gửi).
- Bảng `chats`: mỗi `chat_id` gắn với **một NPP** (chỉ thấy NPP đó) hoặc vai trò **owner** (thấy tất cả, có thêm lệnh so sánh NPP).
- Bot bỏ qua mọi chat không có trong danh sách.
- Mỗi chat có cấu hình nhận gì (`chats.settings_json`, lệnh `chat-set`). Nhóm tổng mặc định nhận:
  - tóm tắt tồn kho mọi NPP lúc 08:00;
  - so sánh NPP lúc 18:30.

  Nhóm tổng mặc định không nhận báo cáo đầy đủ, cảnh báo gấp hay xếp hạng của từng NPP, để tránh spam khi có nhiều NPP.
- Lịch chạy tự đối chiếu với CSDL mỗi 5 phút: thêm / tạm dừng NPP, đổi giờ gửi không cần khởi động lại.
- Đồng bộ tối đa 3 NPP cùng lúc (`MAX_PARALLEL_SYNCS`), lần đồng bộ đầu của các NPP cách nhau 20 giây.

## 8. Mô hình dữ liệu (rút gọn)

```
tenants(id, name, retailer, client_id, client_secret_enc, settings_json)
chats(chat_id, tenant_id | NULL, role, settings_json)   -- role: tenant | owner
branches(id, tenant_id, name)
staff(id, tenant_id, user_name, given_name)
products(id, tenant_id, code, name, unit, category, is_batch_expire)
inventory(tenant_id, product_id, branch_id, on_hand, min_qty, max_qty, updated_at)
batches(tenant_id, product_id, branch_id, batch_name, expire_date, on_hand)
invoices(id, tenant_id, branch_id, sold_by_id, customer_id, purchase_date, status, total)
invoice_lines(invoice_id, product_id, quantity, price, discount)
returns(id, tenant_id, invoice_id, return_date, total) + return_lines
targets(tenant_id, staff_id, month, kind, product_id, value)
alert_state(tenant_id, key, kind, active, first_seen, last_seen, last_sent)  -- key = kind:hàng:kho[:lô]
sync_state(tenant_id, resource, last_modified)
```

## 9. Bảo mật

- Client KiotViet chỉ có hàm GET.
- Client secret được mã hóa bằng khóa để trong biến môi trường. `.env` (token bot, khóa mã hóa) nằm trong `.gitignore`, chỉ commit `.env.example`.
- Log không in secret hay token.
- Về kỹ thuật, mã API cho quyền truy cập toàn bộ dữ liệu gian hàng của NPP, kể cả quyền ghi. Cần NPP đồng ý rõ ràng trước khi lấy mã.

## 10. Triển khai và vận hành

- **Máy chủ:** VPS Linux nhỏ (1 vCPU, 1–2 GB RAM), chạy bằng Docker hoặc systemd, tự khởi động lại khi lỗi.
- **Sao lưu:** CSDL mỗi ngày lúc 03:00, giữ 14 bản trong `data/backup/` (`BACKUP_DIR`, `BACKUP_KEEP`). Nên chép thêm ra ngoài VPS.
- **Báo lỗi:** gửi tin vào nhóm của người vận hành khi đồng bộ lỗi nhiều lần liên tiếp. Xem nhanh bằng `/trangthai` hoặc `python -m app.cli status`.
- **Webhook (giai đoạn 3):** cần domain và HTTPS (Caddy hoặc Nginx + Let's Encrypt).

## 11. Lộ trình

| Giai đoạn | Nội dung | Xong khi |
|---|---|---|
| 0. Chuẩn bị | Tạo bot Telegram, lấy ClientId/Secret của 1 NPP thử, gọi thử API, đánh dấu xong các mục ở phần 3 | Lấy được danh sách hàng và hóa đơn 1 ngày thật |
| 1. Tồn kho (1 NPP) | Đồng bộ hàng, tồn, lô; báo cáo 3 loại theo lịch; `/ton`, `/hethan`; file Excel | Anh rể nhận báo cáo đúng giờ, số khớp KiotViet |
| 2. Nhân viên | Đồng bộ hóa đơn và trả hàng; `/nv`, `/top`; xếp hạng 18h; nạp chỉ tiêu từ Excel | Doanh thu từng nhân viên khớp báo cáo nhân viên trên KiotViet |
| 3. Mở rộng | Nhiều NPP, nhóm tổng, phân quyền; cảnh báo gấp (webhook hoặc quét 15 phút); so sánh NPP | Tất cả NPP chạy ổn trong 1 tuần |
| 4. Làm thêm | Cửa hàng lâu không lấy hàng; báo cáo tháng; hàng chạy và hàng chậm toàn hệ thống | Theo yêu cầu |

## 12. Câu hỏi cho anh rể

Đã có ở trang 10 của tài liệu gửi Zalo:

1. Có bao nhiêu NPP, mỗi NPP mấy kho / chi nhánh?
2. Các NPP đã bật quản lý lô / hạn sử dụng và có nhập hạn khi nhập hàng chưa?
3. Ngưỡng "sắp hết" và "tồn nhiều" đặt theo từng mặt hàng hay một mức chung? Đã đặt định mức tồn trên KiotViet chưa?
4. Báo trước hạn sử dụng bao nhiêu ngày?
5. Chỉ tiêu tính theo doanh thu hay số lượng? Có chỉ tiêu riêng theo mặt hàng không?
6. Gửi báo cáo lúc mấy giờ? Nhân viên có được xem bảng xếp hạng không?

## 13. Cấu trúc thư mục

```
app/
  main.py              khởi động bot + scheduler (python -m app.main)
  cli.py               lệnh quản trị: probe, tenant-*, chat-* (chat-set), sync, report, send-report,
                       staff-check, top, targets-*, status, compare, backup
  probe.py             giai đoạn 0: kiểm tra các điểm chưa rõ ở mục 3 trên dữ liệu thật
  config.py            cấu hình từ .env + cấu hình riêng từng NPP (ngưỡng, giờ gửi)
  services.py          đồng bộ, gửi báo cáo, báo lỗi cho người vận hành
  scheduler.py         lịch chạy (JobQueue của python-telegram-bot, dựa trên APScheduler), tự đối chiếu CSDL mỗi 5 phút
  crypto.py            mã hóa client secret (Fernet)
  kiotviet/client.py   token, phân trang, retry, chỉ GET
  sync/                products.py (chi nhánh, hàng, tồn, lô), invoices.py, returns.py, staff.py,
                       runner.py, parse.py
  reports/             inventory.py (3 loại báo cáo tồn kho + tra cứu), excel.py,
                       staff.py (doanh thu, chỉ tiêu, xếp hạng), periods.py (kỳ: hôm nay, tháng 11...),
                       targets.py (đọc / tạo file Excel chỉ tiêu), alerts.py (cảnh báo gấp),
                       compare.py (so sánh NPP), status.py (tình trạng đồng bộ)
  bot/                 handlers.py, text_parse.py, formatting.py (tồn kho, cảnh báo gấp),
                       staff_format.py (nhân viên), owner_format.py (nhóm tổng)
  db/                  models.py, session.py (tự thêm cột mới vào CSDL cũ, sao lưu SQLite)
tests/                 test trên gian hàng KiotViet giả (tests/fakes.py), đối tượng Telegram giả (tests/tg.py)
tai-lieu/              tài liệu gửi anh rể (PDF + ảnh + nguồn)
.env.example
requirements.txt, requirements-dev.txt
```
