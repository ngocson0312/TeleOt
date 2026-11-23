# Cài bot chạy 24/7

Bot phải chạy liên tục để đồng bộ KiotViet mỗi 15 phút và gửi báo cáo đúng giờ. Có hai cách:

| | VPS Linux (khuyên dùng) | Máy Windows ở nhà |
|---|---|---|
| Chi phí | thuê VPS hằng tháng | tiền điện |
| Ổn định | chạy liên tục, ít khi mất mạng | dừng khi mất điện, mất mạng, máy ngủ hoặc cập nhật Windows |
| Hợp với | chạy thật cho các NPP | chạy thử 1–2 tuần |

⚠️ **Chỉ chạy bot ở MỘT nơi.** Hai nơi cùng dùng một token thì Telegram báo lỗi `Conflict` và mỗi tin nhắn chỉ một bên nhận được. Trên cùng một máy, bot tự chặn bản thứ hai. Còn giữa máy nhà và VPS thì bot không tự biết, phải tự nhớ tắt một bên.

## Chuẩn bị (cách nào cũng cần)

1. **Token bot:** nhắn @BotFather trên Telegram, gõ `/newbot`, đặt tên, nhận token dạng `123456:ABC...`. Gõ tiếp `/setprivacy`, chọn bot, chọn **Disable** để bot hiểu được tin nhắn không có dấu `/` (ví dụ "tồn bánh quy").
2. **Nhóm Telegram:** mỗi NPP một nhóm, thêm một nhóm tổng cho chủ, rồi thêm bot vào từng nhóm.
3. **Mã KiotViet của từng NPP:** Client ID và Client Secret, lấy ở *Thiết lập cửa hàng → Thiết lập kết nối API*. Phải có sự đồng ý của NPP.

## Cách 1: VPS Linux

Thuê VPS **Ubuntu 24.04**, 1 CPU, 1 GB RAM là đủ. Không cần chỉnh giờ VPS, bot luôn dùng giờ Việt Nam. SSH vào VPS rồi chạy:

```bash
sudo apt-get install -y git
sudo git clone https://github.com/ngocson0312/TeleOt.git /opt/teleot
sudo bash /opt/teleot/deploy/cai-dat-linux.sh
```

Script sẽ cài các thứ sau:
- Python, thư viện, và tài khoản riêng `teleot` để chạy bot.
- File `.env` có sẵn khóa mã hóa.
- Dịch vụ `teleot`: tự chạy lại khi lỗi và khi VPS khởi động lại.
- Lệnh quản trị `sudo teleot ...`, thay cho `python -m app.cli ...`.

Sau đó:

```bash
sudo nano /opt/teleot/.env             # điền TELEGRAM_BOT_TOKEN
sudo teleot tenant-add --name "NPP Hà Nội"
sudo teleot sync 1                     # lần đầu mất vài phút: 90 ngày hóa đơn
sudo systemctl start teleot
```

Cấp quyền các nhóm:
1. Trong từng nhóm, gõ `/id` để lấy mã nhóm.
2. Chạy `sudo teleot chat-add <mã> --tenant 1` cho nhóm NPP, hoặc `--owner` cho nhóm tổng. Không cần khởi động lại bot.
3. Nhắn riêng `/id` cho bot, điền mã đó vào `ADMIN_CHAT_ID` trong `.env`, rồi chạy `sudo systemctl restart teleot`. Đồng bộ lỗi nhiều lần là bot báo về chat này.

**Hằng ngày**

```bash
sudo teleot status                 # NPP nào đồng bộ lỗi, lần cuối lúc nào
journalctl -u teleot -f            # xem log trực tiếp (Ctrl+C để thoát)
journalctl -u teleot --since today
sudo systemctl restart teleot      # khởi động lại
sudo systemctl stop teleot         # dừng
```

**Cập nhật code mới**

```bash
sudo -u teleot git -C /opt/teleot pull
sudo bash /opt/teleot/deploy/cai-dat-linux.sh   # cài thư viện mới nếu có, khởi động lại bot
```

Luôn chạy lệnh quản trị bằng `sudo teleot ...`, không chạy `python` bằng root. Chạy bằng root thì file CSDL đổi chủ và bot không ghi được nữa.

## Cách 2: Máy Windows ở nhà

Cần Python 3.12+ (python.org) và máy bật suốt ngày đêm.

1. Lấy code (bỏ qua nếu đã có thư mục):

   ```powershell
   git clone https://github.com/ngocson0312/TeleOt.git D:\TeleOT
   ```

2. Mở PowerShell bằng **Run as administrator**, rồi chạy:

   ```powershell
   cd D:\TeleOT
   powershell -ExecutionPolicy Bypass -File deploy\cai-dat-windows.ps1
   ```

   Script cài thư viện, tạo `.env` có sẵn khóa mã hóa, và tạo tác vụ **TeleOt** trong Task Scheduler. Tác vụ này bật bot khi máy khởi động, không cần đăng nhập, và bot dừng thì 30 giây sau tự chạy lại.
3. `notepad .env`: điền `TELEGRAM_BOT_TOKEN`.
4. Thêm NPP rồi bật bot:

   ```powershell
   .venv\Scripts\python -m app.cli tenant-add --name "NPP Hà Nội"
   .venv\Scripts\python -m app.cli sync 1
   Start-ScheduledTask -TaskName TeleOt
   ```

5. Cấp quyền các nhóm giống cách 1, dùng `.venv\Scripts\python -m app.cli chat-add ...`. Đổi `ADMIN_CHAT_ID` xong thì bật lại bot: dừng rồi `Start-ScheduledTask -TaskName TeleOt`.
6. **Tắt chế độ ngủ:** Settings → System → Power → Sleep: **Never**. Máy ngủ là bot dừng.

**Hằng ngày**

| Việc | Lệnh / file |
|---|---|
| Xem log | `Get-Content data\logs\teleot.log -Tail 50 -Wait -Encoding UTF8` |
| Bot bật / tắt lúc nào | `data\logs\chay-bot.log` |
| Bot không lên được (thiếu token, sai khóa...) | `data\logs\loi-khoi-dong.txt` |
| Tình trạng đồng bộ | `.venv\Scripts\python -m app.cli status` |
| Dừng (PowerShell admin) | `powershell -ExecutionPolicy Bypass -File deploy\dung-bot.ps1` |
| Bật lại | `Start-ScheduledTask -TaskName TeleOt` |
| Gỡ hẳn (dữ liệu vẫn giữ) | `powershell -ExecutionPolicy Bypass -File deploy\cai-dat-windows.ps1 -Uninstall` |

**Cập nhật code mới:** chạy `git pull`, rồi chạy lại `deploy\cai-dat-windows.ps1` bằng PowerShell admin. Script cài thư viện mới và khởi động lại bot.

Muốn chạy tay `python -m app.main` để thử thì phải dừng bot nền trước. Không dừng thì bản chạy tay báo "Bot đã đang chạy" và thoát.

## Sao lưu ra ngoài máy

Bot tự sao lưu CSDL lúc 03:00 vào `data/backup/`, giữ 14 bản. Hỏng ổ cứng là mất luôn cả bản sao lưu, nên chép ra ngoài:

- **Windows:**
  - Đặt `BACKUP_DIR` trong `.env` vào một thư mục OneDrive (hoặc Google Drive chế độ *Mirror files*), ví dụ `BACKUP_DIR=C:\Users\<tên>\OneDrive\TeleOt-sao-luu`.
  - Phải dùng thư mục thật trên ổ C. Ổ ảo `G:` của Google Drive không dùng được vì bot chạy bằng tài khoản SYSTEM, không thấy ổ đó.
  - Đổi xong thì bật lại bot. Hôm sau kiểm tra xem file đã lên OneDrive chưa.
- **VPS:**
  - Giữ `BACKUP_DIR` trong `data/`, vì dịch vụ chỉ được ghi vào đó.
  - Dùng [rclone](https://rclone.org) chép `/opt/teleot/data/backup` lên Google Drive mỗi ngày, hoặc thỉnh thoảng tải về máy bằng `scp`.

**Khôi phục từ bản sao lưu:**
1. Dừng bot.
2. Xóa `data/teleot.db-wal` và `data/teleot.db-shm` nếu có.
3. Chép file `teleot-YYYYMMDD.db` đè lên `data/teleot.db`.
4. Bật bot. Hóa đơn sau ngày sao lưu sẽ được đồng bộ lại ở lần chạy kế tiếp.

## Sự cố thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Bot im lặng trong nhóm | Nhóm chưa được cấp quyền (`chat-list`). Bot chưa tắt privacy mode, nên tin không có `/` không tới bot. Hoặc bot không chạy (`status`, xem log) |
| Log báo `Conflict: terminated by other getUpdates request` | Bot đang chạy ở nơi khác cùng token, ví dụ cả máy nhà lẫn VPS. Tắt một bên |
| "Bot đã đang chạy ở một tiến trình khác" | Bot nền đang chạy. Dừng nó trước khi chạy tay |
| `/trangthai` báo 🚨, lỗi `KiotVietAuthError` | NPP đã đổi Client Secret: chạy `tenant-key <id>` để nhập mã mới, không cần khởi động lại |
| `/trangthai` báo 🚨, lỗi 429 hoặc 5xx | KiotViet quá tải hoặc đang bảo trì. Bot tự thử lại và báo khi đồng bộ lại được |
| Windows: khởi động lại máy mà bot không chạy | Task Scheduler → TeleOt → tab History. Xem `data\logs\loi-khoi-dong.txt` |
