#!/usr/bin/env bash
# Cài bot lên VPS Ubuntu 24.04 (hoặc Debian 13), chạy 24/7 bằng systemd.
#
#   sudo git clone https://github.com/ngocson0312/TeleOt.git /opt/teleot
#   sudo bash /opt/teleot/deploy/cai-dat-linux.sh
#
# Cập nhật code về sau: sudo -u teleot git -C /opt/teleot pull, rồi chạy lại script này
# (cài thư viện mới nếu có và khởi động lại bot). Chạy lại nhiều lần không sao.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${APP_USER:-teleot}"
SERVICE=teleot

if [ "$(id -u)" -ne 0 ]; then
    echo "Chạy bằng sudo: sudo bash $0" >&2
    exit 1
fi

echo "==> Cài Python và git"
apt-get update -qq
apt-get install -y -qq python3 python3-venv git >/dev/null
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    echo "Cần Python 3.12 trở lên (Ubuntu 24.04 có sẵn). Máy này có: $(python3 --version)" >&2
    exit 1
fi

echo "==> Tài khoản riêng để chạy bot: $APP_USER"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi
mkdir -p "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

as_app() { runuser -u "$APP_USER" -- "$@"; }
cd "$APP_DIR"

echo "==> Thư viện Python"
[ -x .venv/bin/python ] || as_app python3 -m venv .venv
as_app .venv/bin/pip install -q --no-cache-dir --upgrade pip
as_app .venv/bin/pip install -q --no-cache-dir -r requirements.txt

if [ ! -f .env ]; then
    echo "==> Tạo file .env và khóa mã hóa SECRET_KEY"
    as_app cp .env.example .env
    chmod 600 .env
    key="$(as_app .venv/bin/python -m app.cli gen-key)"
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$key|" .env
fi

echo "==> Dịch vụ systemd: $SERVICE"
sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@APP_USER@|$APP_USER|g" deploy/teleot.service \
    > "/etc/systemd/system/$SERVICE.service"
# Lệnh quản trị chạy đúng tài khoản của bot (file CSDL luôn thuộc về $APP_USER).
cat > /usr/local/bin/teleot <<EOF
#!/bin/sh
# Lệnh quản trị bot, ví dụ: sudo teleot status, sudo teleot tenant-list
cd "$APP_DIR" && exec runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/python" -m app.cli "\$@"
EOF
chmod 755 /usr/local/bin/teleot
systemctl daemon-reload
systemctl enable --quiet "$SERVICE"

echo
if grep -q '^TELEGRAM_BOT_TOKEN=.\+' .env; then
    systemctl restart "$SERVICE"
    echo "✔ Bot đã chạy và sẽ tự chạy lại khi lỗi hoặc khi máy khởi động lại."
    echo "  Xem log:      journalctl -u $SERVICE -f"
    echo "  Tình trạng:   sudo teleot status"
else
    echo "✔ Đã cài xong. Còn mấy bước:"
    echo "  1. sudo nano $APP_DIR/.env   (điền TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID)"
    echo "  2. sudo teleot tenant-add --name \"Tên NPP\""
    echo "  3. sudo teleot sync 1"
    echo "  4. sudo systemctl start $SERVICE"
    echo "  Hướng dẫn đầy đủ: $APP_DIR/CAI-DAT.md"
fi
