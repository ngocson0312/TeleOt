# TeleOt: Bot báo cáo KiotViet → Telegram

Bot dành cho hệ thống nhà phân phối bánh kẹo dùng KiotViet. Bot tự đọc số liệu từ KiotViet rồi gửi báo cáo về Telegram:

1. **Tồn kho**, gửi theo giờ: hàng sắp hết, lô sắp hết hạn, hàng tồn nhiều hoặc bán chậm.
2. **Nhân viên**: doanh thu, số đơn, số lượng theo mặt hàng, % chỉ tiêu, xếp hạng cuối ngày. Tra cứu bằng tin nhắn, ví dụ "Tuấn tháng 11".

## Trạng thái (23/11/2026)

- [x] Xác nhận làm được. Đã đối chiếu tài liệu KiotViet Public API: có tồn kho theo kho, định mức tồn, lô/hạn sử dụng, và người bán trên mỗi hóa đơn.
- [x] Tài liệu giới thiệu dễ đọc cho chủ hệ thống (PDF khổ điện thoại + ảnh từng trang để gửi Zalo).
- [x] Kế hoạch kỹ thuật chi tiết.
- [x] **Code giai đoạn 0:** lệnh `probe` gọi thử API thật và tự trả lời các câu hỏi ở mục 3 của kế hoạch.
- [x] **Code giai đoạn 1 (tồn kho, 1 NPP):** đồng bộ hàng, tồn, lô, hóa đơn; 3 loại báo cáo theo lịch; `/ton`, `/hethan`, `/baocao`; file Excel. 45 test chạy trên dữ liệu giả.
- [ ] **Đang chờ:** chủ hệ thống trả lời 6 câu hỏi ở trang 10 của tài liệu (ngưỡng, số ngày báo trước hạn, giờ gửi). Hiện bot dùng giá trị mặc định.
- [ ] **Đang chờ:** Client ID/Secret KiotViet của 1 NPP thử và token bot Telegram, để chạy `probe` và đối chiếu số với KiotViet.
- [ ] Giai đoạn 2: thống kê nhân viên, chỉ tiêu, xếp hạng 18h.

## Trong repo có gì

| Đường dẫn | Nội dung |
|---|---|
| [KE-HOACH-KY-THUAT.md](KE-HOACH-KY-THUAT.md) | Kế hoạch kỹ thuật: kiến trúc, API KiotViet sẽ dùng, logic báo cáo, lệnh bot, phân quyền, lộ trình |
| [app/](app/) | Code bot: `kiotviet/` (client chỉ đọc), `sync/`, `reports/`, `bot/`, `db/`, `cli.py`, `probe.py`, `main.py` |
| [tests/](tests/) | Test trên một gian hàng KiotViet giả (`tests/fakes.py`) |
| [tai-lieu/Tro-ly-bao-cao-KiotViet-Telegram.pdf](tai-lieu/Tro-ly-bao-cao-KiotViet-Telegram.pdf) | Tài liệu giới thiệu 10 trang khổ điện thoại |
| [tai-lieu/anh-gui-zalo/](tai-lieu/anh-gui-zalo/) | 10 ảnh PNG, mỗi trang một ảnh, để gửi Zalo |
| [tai-lieu/nguon/](tai-lieu/nguon/) | Nguồn HTML của tài liệu và script xuất PDF/ảnh |

## Chạy bot

Cần Python 3.12+.

```bash
python -m venv .venv
.venv\Scripts\activate           # Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                           # chạy test trên dữ liệu giả
copy .env.example .env           # Linux: cp .env.example .env
python -m app.cli gen-key        # dán kết quả vào SECRET_KEY trong .env
```

### Giai đoạn 0: gọi thử API thật

1. Điền `KIOTVIET_RETAILER`, `KIOTVIET_CLIENT_ID`, `KIOTVIET_CLIENT_SECRET` vào `.env` (KiotViet → Thiết lập cửa hàng → Thiết lập kết nối API).
2. Chạy `python -m app.cli probe`. Lệnh in ra từng mục ở phần 3 của kế hoạch (✔ ổn, ✘ cần xử lý, ? cần xem thêm), kèm số mặt hàng và số hóa đơn hôm qua để so với KiotViet.
3. Mẫu phản hồi thật nằm trong `data/probe/<gian hàng>/`. Thư mục `data/` không lên git. File `ket-qua.txt` chỉ chứa tên trường và số lượng, gửi được.

### Giai đoạn 1: chạy bot

```bash
python -m app.cli tenant-add --name "NPP Hà Nội" --from-env   # lưu mã KiotViet (đã mã hóa)
python -m app.cli sync 1                                      # đồng bộ lần đầu: hàng, tồn, lô, 90 ngày hóa đơn
python -m app.cli report 1 --excel                            # xem báo cáo ngay trên màn hình
```

1. Tạo bot qua @BotFather, dán token vào `TELEGRAM_BOT_TOKEN`. Muốn bot hiểu tin nhắn không có dấu `/` trong nhóm thì tắt privacy mode (`/setprivacy` → Disable).
2. Thêm bot vào nhóm, gõ `/id` để lấy mã chat, rồi cấp quyền:
   - `python -m app.cli chat-add <mã chat> --tenant 1`: nhóm của NPP 1
   - `python -m app.cli chat-add <mã chat> --owner`: nhóm tổng, xem tất cả NPP
3. Đặt `ADMIN_CHAT_ID` (chat của người vận hành) để nhận tin khi đồng bộ lỗi 3 lần liên tiếp.
4. Chạy bot: `python -m app.main`. Bot đồng bộ mỗi 30 phút và gửi báo cáo lúc 08:00, 12:00, 17:00.

Đổi cấu hình từng NPP (khởi động lại bot nếu đổi giờ gửi):

```bash
python -m app.cli tenant-set 1 report_times 7:30,17:00
python -m app.cli tenant-set 1 expiry_warn_days 45
python -m app.cli tenant-set 1 low_stock_qty 10                          # ngưỡng chung khi hàng chưa đặt định mức trên KiotViet
python -m app.cli tenant-set 1 low_stock_qty_by_category '{"Kẹo": 50}'
python -m app.cli tenant-list                                            # xem toàn bộ cấu hình
```

Các lệnh khác: `python -m app.cli -h`.

## Sửa và xuất lại tài liệu

Cần Python 3, PyMuPDF (`pip install pymupdf`) và Google Chrome (hoặc Edge).

1. Sửa nội dung trong `tai-lieu/nguon/gioi-thieu.html`.
2. Chạy:

```bash
python tai-lieu/nguon/build.py
```

Script xuất lại PDF và 10 ảnh, đồng thời cảnh báo nếu có trang bị tràn chữ.

## Bước tiếp theo

1. Lấy Client ID/Secret của 1 NPP thử và token bot Telegram, điền vào `.env` (**không commit**, `.gitignore` đã chặn).
2. Chạy `python -m app.cli probe`, sửa code theo kết quả nếu tên trường thật khác tài liệu.
3. Đồng bộ, so báo cáo với màn hình KiotViet, rồi chạy bot thật cho anh rể dùng thử.
4. Cài lên VPS (mục 10 của kế hoạch), sau đó làm giai đoạn 2.
