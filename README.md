# TeleOt: Bot báo cáo KiotViet → Telegram

Bot dành cho hệ thống nhà phân phối bánh kẹo dùng KiotViet. Bot tự đọc số liệu từ KiotViet rồi gửi báo cáo về Telegram:

1. **Tồn kho**, gửi theo giờ: hàng sắp hết, lô sắp hết hạn, hàng tồn nhiều hoặc bán chậm.
2. **Nhân viên**: doanh thu, số đơn, số lượng theo mặt hàng, % chỉ tiêu, xếp hạng cuối ngày. Tra cứu bằng tin nhắn, ví dụ "Tuấn tháng 11".

## Trạng thái (22/11/2026)

- [x] Xác nhận làm được. Đã đối chiếu tài liệu KiotViet Public API: có tồn kho theo kho, định mức tồn, lô/hạn sử dụng, và người bán trên mỗi hóa đơn.
- [x] Tài liệu giới thiệu dễ đọc cho chủ hệ thống (PDF khổ điện thoại + ảnh từng trang để gửi Zalo).
- [x] Kế hoạch kỹ thuật chi tiết.
- [ ] **Đang chờ:** chủ hệ thống trả lời 6 câu hỏi ở trang 10 của tài liệu.
- [ ] Giai đoạn 0: lấy Client ID/Secret KiotViet của 1 nhà phân phối thử và token bot Telegram, rồi gọi thử API.
- [ ] Chưa có code bot.

## Trong repo có gì

| Đường dẫn | Nội dung |
|---|---|
| [KE-HOACH-KY-THUAT.md](KE-HOACH-KY-THUAT.md) | Kế hoạch kỹ thuật: kiến trúc, API KiotViet sẽ dùng, logic báo cáo, lệnh bot, phân quyền, lộ trình |
| [tai-lieu/Tro-ly-bao-cao-KiotViet-Telegram.pdf](tai-lieu/Tro-ly-bao-cao-KiotViet-Telegram.pdf) | Tài liệu giới thiệu 10 trang khổ điện thoại |
| [tai-lieu/anh-gui-zalo/](tai-lieu/anh-gui-zalo/) | 10 ảnh PNG, mỗi trang một ảnh, để gửi Zalo |
| [tai-lieu/nguon/](tai-lieu/nguon/) | Nguồn HTML của tài liệu và script xuất PDF/ảnh |

## Sửa và xuất lại tài liệu

Cần Python 3, PyMuPDF (`pip install pymupdf`) và Google Chrome (hoặc Edge).

1. Sửa nội dung trong `tai-lieu/nguon/gioi-thieu.html`.
2. Chạy:

```bash
python tai-lieu/nguon/build.py
```

Script xuất lại PDF và 10 ảnh, đồng thời cảnh báo nếu có trang bị tràn chữ.

## Bước tiếp theo

Xem mục **11. Lộ trình** trong [KE-HOACH-KY-THUAT.md](KE-HOACH-KY-THUAT.md). Giai đoạn 0 cần:

- Client ID và Client Secret KiotViet của 1 nhà phân phối thử (KiotViet → Thiết lập cửa hàng → Thiết lập kết nối API)
- Token bot Telegram (tạo qua @BotFather)

Các mã này để trong file `.env`, **không commit lên git** (`.gitignore` đã chặn sẵn).
