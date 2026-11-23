# TeleOt: Bot báo cáo KiotViet → Telegram

Bot dành cho hệ thống nhà phân phối bánh kẹo dùng KiotViet. Bot tự đọc số liệu từ KiotViet rồi gửi báo cáo về Telegram:

1. **Tồn kho**, gửi theo giờ: hàng sắp hết, lô sắp hết hạn, hàng tồn nhiều hoặc bán chậm.
2. **Nhân viên**: doanh thu, số đơn, số lượng theo mặt hàng, % chỉ tiêu, xếp hạng cuối ngày. Tra cứu bằng tin nhắn, ví dụ "Tuấn tháng 11".
3. **Nhiều NPP**: mỗi NPP một nhóm riêng. Nhóm tổng của chủ nhận bản tóm tắt và so sánh các NPP. Có cảnh báo gấp khi hàng mới hết hoặc lô sắp hết hạn.

## Trạng thái (23/11/2026)

- [x] Xác nhận làm được. Đã đối chiếu tài liệu KiotViet Public API: có tồn kho theo kho, định mức tồn, lô/hạn sử dụng, và người bán trên mỗi hóa đơn.
- [x] Tài liệu giới thiệu dễ đọc cho chủ hệ thống (PDF khổ điện thoại + ảnh từng trang để gửi Zalo).
- [x] Kế hoạch kỹ thuật chi tiết.
- [x] **Code giai đoạn 0:** lệnh `probe` gọi thử API thật và tự trả lời các câu hỏi ở mục 3 của kế hoạch.
- [x] **Code giai đoạn 1 (tồn kho, 1 NPP):** đồng bộ hàng, tồn, lô, hóa đơn; 3 loại báo cáo theo lịch; `/ton`, `/hethan`, `/baocao`; file Excel.
- [x] **Code giai đoạn 2 (nhân viên):** đồng bộ nhân viên và phiếu trả hàng; doanh thu, số đơn, số cửa hàng, số lượng theo mặt hàng; `/nv`, `/top`; nạp chỉ tiêu từ Excel bằng `/chitieu`; xếp hạng cuối ngày 18:00; lệnh `staff-check` để đối soát với KiotViet.
- [x] **Code giai đoạn 3 (mở rộng):**
  - nhiều NPP;
  - nhóm tổng nhận tóm tắt thay vì báo cáo của từng NPP;
  - cấu hình riêng từng nhóm chat;
  - cảnh báo gấp quét sau mỗi lần đồng bộ (15 phút);
  - `/sosanh`, `/trangthai`;
  - lịch chạy tự cập nhật khi thêm NPP hoặc đổi giờ;
  - sao lưu CSDL mỗi ngày.
- [x] **Cài đặt chạy 24/7** ([CAI-DAT.md](CAI-DAT.md)): VPS Linux (systemd) hoặc máy Windows ở nhà (Task Scheduler); tự chạy lại khi lỗi; chặn chạy 2 bản cùng lúc.
- [x] **NPP mẫu** (`demo`): dữ liệu giả để thử bot trên Telegram khi chưa có mã KiotViet.
- [x] 102 test chạy trên dữ liệu giả.
- [ ] **Đang chờ:** chủ hệ thống trả lời 6 câu hỏi ở trang 10 của tài liệu (ngưỡng, số ngày báo trước hạn, giờ gửi, nhân viên có được xem xếp hạng không). Hiện bot dùng giá trị mặc định.
- [ ] **Đang chờ:** Client ID/Secret KiotViet của 1 NPP thử và token bot Telegram, để chạy `probe` và đối chiếu số với KiotViet.
- [ ] Chạy thật tất cả NPP 1 tuần (điều kiện xong giai đoạn 3 trong kế hoạch).
- [ ] Webhook `stock.update` của KiotViet: chưa làm. Cảnh báo gấp hiện quét 15 phút một lần. Webhook cần domain + HTTPS, chỉ đáng làm nếu 15 phút là quá chậm.

## Trong repo có gì

| Đường dẫn | Nội dung |
|---|---|
| [KE-HOACH-KY-THUAT.md](KE-HOACH-KY-THUAT.md) | Kế hoạch kỹ thuật: kiến trúc, API KiotViet sẽ dùng, logic báo cáo, lệnh bot, phân quyền, lộ trình |
| [app/](app/) | Code bot: `kiotviet/` (client chỉ đọc), `sync/`, `reports/`, `bot/`, `db/`, `cli.py`, `probe.py`, `main.py` |
| [tests/](tests/) | Test trên gian hàng KiotViet giả `app/demo.py` (cố định ngày trong `tests/fakes.py`) |
| [CAI-DAT.md](CAI-DAT.md) | Cài bot chạy 24/7 trên VPS hoặc máy Windows, sao lưu, xử lý sự cố |
| [deploy/](deploy/) | Script cài đặt: `cai-dat-linux.sh` + `teleot.service` (VPS), `cai-dat-windows.ps1`, `chay-bot.ps1`, `dung-bot.ps1` (Windows) |
| [tai-lieu/Tro-ly-bao-cao-KiotViet-Telegram.pdf](tai-lieu/Tro-ly-bao-cao-KiotViet-Telegram.pdf) | Tài liệu giới thiệu 10 trang khổ điện thoại |
| [tai-lieu/anh-gui-zalo/](tai-lieu/anh-gui-zalo/) | 10 ảnh PNG, mỗi trang một ảnh, để gửi Zalo |
| [tai-lieu/nguon/](tai-lieu/nguon/) | Nguồn HTML của tài liệu và script xuất PDF/ảnh |

## Chạy bot

Cần Python 3.12+. Phần dưới là chạy tay để thử. Muốn bot chạy 24/7 thì làm theo [CAI-DAT.md](CAI-DAT.md).

```bash
python -m venv .venv
.venv\Scripts\activate           # Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                           # chạy test trên dữ liệu giả
copy .env.example .env           # Linux: cp .env.example .env
python -m app.cli gen-key        # dán kết quả vào SECRET_KEY trong .env
```

### Thử bot khi chưa có mã KiotViet

```bash
python -m app.cli demo --chat <mã chat>    # NPP mẫu: hàng, lô, 30 ngày hóa đơn, 3 nhân viên, chỉ tiêu (đều giả)
python -m app.main                         # rồi nhắn bot /ton banh, /baocao, /hethan, /nv, /top, /chitieu
python -m app.cli demo                     # làm mới số liệu giả theo ngày hôm nay
python -m app.cli demo --remove            # xóa NPP mẫu khi đã có NPP thật
```

Mã chat lấy bằng cách nhắn `/start` hoặc `/id` cho bot. NPP mẫu không gọi KiotViet, kể cả khi bot đồng bộ theo lịch.

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
4. Chạy bot: `python -m app.main`. Bot đồng bộ mỗi 15 phút và gửi báo cáo lúc 08:00, 12:00, 17:00.

Đổi cấu hình từng NPP. Bot đang chạy tự cập nhật lịch trong 5 phút, không cần khởi động lại:

```bash
python -m app.cli tenant-set 1 report_times 7:30,17:00
python -m app.cli tenant-set 1 expiry_warn_days 45
python -m app.cli tenant-set 1 low_stock_qty 10                          # ngưỡng chung khi hàng chưa đặt định mức trên KiotViet
python -m app.cli tenant-set 1 low_stock_qty_by_category '{"Kẹo": 50}'
python -m app.cli tenant-list                                            # xem toàn bộ cấu hình
python -m app.cli tenant-key 1                                           # NPP đổi Client Secret: nhập mã mới
```

### Giai đoạn 2: thống kê nhân viên

Lệnh trong nhóm Telegram:

| Lệnh | Ví dụ | Kết quả |
|---|---|---|
| `/nv tên [kỳ]` | `/nv Tuấn tháng 11` | doanh thu (đã trừ hàng trả), số đơn, số cửa hàng, mặt hàng bán nhiều nhất, % chỉ tiêu |
| `/top [kỳ]` | `/top hôm nay` | xếp hạng nhân viên; xem một ngày thì so với hôm trước, xem một tháng thì kèm % chỉ tiêu |
| `/chitieu [tháng]` | `/chitieu 12/2026` | bot gửi file mẫu điền sẵn nhân viên; điền rồi gửi lại trong 30 phút (hoặc gửi kèm ghi chú `/chitieu`). Chỉ quản trị viên nhóm dùng được |

Kỳ: hôm nay, hôm qua, tuần này, tuần trước, tháng này, tháng trước, tháng 11, 11/2026, 22/11, 1/11-15/11. Nhắn tự do cũng được: "Tuấn tháng 11", "top hôm qua", "doanh thu Lan tuần này".

Lúc 18:00 bot gửi xếp hạng cuối ngày, so với hôm qua, kèm danh sách người chậm tiến độ tháng (% đạt thấp hơn tiến độ tháng quá 10 điểm).

**Trước khi dùng thật, đối soát doanh thu với KiotViet.** Tài liệu KiotViet chưa rõ nên có hai cách tính (mục 5.5 của kế hoạch):

```bash
python -m app.cli staff-check 1 "hôm qua"     # in 2 cột: theo tổng hóa đơn, theo cộng dòng hàng
```

So với KiotViet: Báo cáo → Nhân viên → Bán hàng, cùng ngày. Mặc định bot tính theo tổng hóa đơn; nếu cột "cộng dòng hàng" mới khớp thì chạy `tenant-set 1 revenue_mode lines`.

Cấu hình thêm cho phần nhân viên:

```bash
python -m app.cli tenant-set 1 ranking_time 17:30              # giờ gửi xếp hạng; "off" để tắt
python -m app.cli tenant-set 1 ranking_to_npp_groups false      # chỉ nhóm tổng của chủ nhận xếp hạng
python -m app.cli tenant-set 1 skip_sundays true                # tiến độ tháng không tính Chủ nhật
python -m app.cli tenant-set 1 behind_margin 15                 # "chậm tiến độ" khi thấp hơn tiến độ quá 15 điểm
python -m app.cli targets-template 1 --month 12/2026            # file mẫu chỉ tiêu, không qua Telegram
python -m app.cli targets-import 1 chi-tieu.xlsx                # nạp chỉ tiêu từ file
python -m app.cli top 1 "tháng này"                             # in xếp hạng ra màn hình
```

### Giai đoạn 3: nhiều NPP, nhóm tổng, cảnh báo gấp

Thêm NPP nào thì làm lại các bước của giai đoạn 1 cho NPP đó: `tenant-add`, `sync`, `chat-add <mã nhóm> --tenant <id>`. Bot đang chạy tự nhận NPP mới trong 5 phút.

**Nhóm tổng** (`chat-add <mã> --owner`) mặc định nhận:

| Lúc | Nội dung |
|---|---|
| 08:00 | Tóm tắt tồn kho tất cả NPP: số mặt hàng sắp hết / lô sắp hết hạn / tồn nhiều, kèm 3 mục gấp nhất mỗi NPP |
| 18:30 | So sánh NPP trong ngày: doanh thu (so với hôm qua), số đơn, số cửa hàng, người bán nhiều nhất, % chỉ tiêu tháng, số NV chậm tiến độ, tồn kho |

Nhóm tổng không nhận báo cáo đầy đủ và cảnh báo gấp của từng NPP, để tránh bị spam khi có nhiều NPP. Xếp hạng nhân viên chỉ gửi vào nhóm tổng khi NPP đó đặt `ranking_to_npp_groups false`.

Đổi những gì một nhóm nhận (nhóm NPP cũng dùng được):

```bash
python -m app.cli chat-set -100777 inventory full            # full: báo cáo đầy đủ từng NPP · summary: tóm tắt · off: không nhận
python -m app.cli chat-set -100777 summary_times 7:30,17:00  # giờ gửi tóm tắt
python -m app.cli chat-set -100777 compare_time off          # tắt so sánh NPP hằng ngày (hoặc 19:00)
python -m app.cli chat-set -100777 urgent true               # nhận cả cảnh báo gấp
python -m app.cli chat-set -100123 ranking false             # nhóm nhân viên của NPP: không nhận xếp hạng
python -m app.cli chat-list                                  # xem từng nhóm nhận gì
```

Lệnh trong nhóm tổng:

| Lệnh | Ví dụ | Kết quả |
|---|---|---|
| `/sosanh [kỳ]` | `/sosanh tháng này` | so sánh các NPP, so với kỳ trước (hôm trước, cùng kỳ tháng trước...) |
| `/baocao [tên NPP]` | `/baocao Hà Nội` | không ghi tên thì xem tóm tắt tất cả NPP, kèm nút xem báo cáo đầy đủ từng NPP |
| `/top [tên NPP] [kỳ]` | `/top Hà Nội hôm qua` | xếp hạng nhân viên một NPP |
| `/hethan [tên NPP] [số ngày]` | `/hethan Hà Nội 60` | lô sắp hết hạn của một NPP |
| `/trangthai` | | lần đồng bộ cuối và lỗi của từng NPP. Chat của người vận hành (`ADMIN_CHAT_ID`) thấy cả NPP tạm dừng |

**Cảnh báo gấp** (gửi vào nhóm NPP): sau mỗi lần đồng bộ, bot báo các mặt hàng *mới* rơi vào một trong các trường hợp sau:
- hết hàng;
- sắp hết hàng;
- lô đã hết hạn mà còn tồn;
- lô còn ≤ 7 ngày là hết hạn.

Cách bot tránh làm phiền:
- Không nhắc lại mặt hàng đã báo.
- Lần đầu chạy, bot chỉ ghi nhớ, không báo.
- Từ 21:00 đến 07:00, cảnh báo được giữ lại, sáng mới gửi.
- Mặt hàng vừa báo trong 24 giờ, đã hết vấn đề rồi lại có vấn đề, thì không báo lại.
- Báo cáo theo lịch vừa gửi thì tính là đã báo.

```bash
python -m app.cli tenant-set 1 quiet_hours 22:00-06:30      # giờ im lặng; "off" để tắt
python -m app.cli tenant-set 1 urgent_alerts false          # tắt cảnh báo gấp của NPP này
python -m app.cli tenant-set 1 sync_interval_minutes 30     # quét thưa hơn (mặc định 15 phút)
```

Vận hành:

```bash
python -m app.cli status              # NPP nào đồng bộ lỗi, lần cuối lúc nào
python -m app.cli compare "tháng này" # so sánh NPP ngay trên màn hình
python -m app.cli backup              # sao lưu ngay; bot tự sao lưu lúc 03:00, giữ 14 bản trong data/backup/
python -m app.cli tenant-set 2 active false   # tạm dừng một NPP (bot bỏ lịch của NPP đó trong 5 phút)
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
3. Đồng bộ, so báo cáo tồn kho với KiotViet, chạy `staff-check` để chọn cách tính doanh thu, rồi chạy bot thật cho anh rể dùng thử.
4. Cài cho bot chạy 24/7 theo [CAI-DAT.md](CAI-DAT.md) (chạy thử trên máy nhà trước, ổn thì lên VPS), thêm dần các NPP, theo dõi `/trangthai` trong 1 tuần.
5. Giai đoạn 4 (theo yêu cầu): cửa hàng lâu không lấy hàng, báo cáo tháng, hàng chạy / hàng chậm toàn hệ thống.
