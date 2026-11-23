"""Start the bot and the scheduler in one process: python -m app.main

Runs 24/7 under systemd (Linux) or Task Scheduler (Windows), see CAI-DAT.md.
Exit code 3: another copy of the bot already runs on this machine.
"""
from __future__ import annotations

import logging
import sys

from telegram import Update

from app.bot.handlers import build_application
from app.config import DATA_DIR, get_settings, setup_logging
from app.crypto import CryptoError, SecretBox
from app.db.session import Database
from app.scheduler import schedule_jobs
from app.services import Services
from app.singleton import acquire_lock

log = logging.getLogger("app")

ALREADY_RUNNING = 3
LOCK_FILE = DATA_DIR / "bot.lock"


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    if not settings.telegram_bot_token.get_secret_value():
        log.error("Thiếu TELEGRAM_BOT_TOKEN trong .env (tạo bot qua @BotFather).")
        sys.exit("Thiếu TELEGRAM_BOT_TOKEN trong .env (tạo bot qua @BotFather).")
    try:
        SecretBox(settings.secret_key.get_secret_value())
    except CryptoError as e:
        log.error("%s", e)
        sys.exit(str(e))

    lock = acquire_lock(LOCK_FILE)  # held until the process exits
    if lock is None:
        log.error("Bot đã đang chạy ở một tiến trình khác trên máy này (%s). Dừng bản kia trước.", LOCK_FILE)
        sys.exit(ALREADY_RUNNING)

    db = Database(settings.database_url)
    db.create_all()
    svc = Services(settings, db)
    tenants = svc.active_tenants()
    if not tenants:
        log.warning("Chưa có NPP nào. Thêm bằng: python -m app.cli tenant-add")
    if settings.admin_chat_id is None:
        log.warning("Chưa đặt ADMIN_CHAT_ID: lỗi đồng bộ chỉ ghi vào log, không báo qua Telegram")

    application = build_application(svc)
    jobs = schedule_jobs(application, svc)
    log.info("Bot chạy với %s NPP, %s lịch: %s", len(tenants), len(jobs), ", ".join(jobs))
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    lock.close()


if __name__ == "__main__":
    main()
