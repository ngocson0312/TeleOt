"""Start the bot and the scheduler in one process: python -m app.main"""
from __future__ import annotations

import logging
import sys

from telegram import Update

from app.bot.handlers import build_application
from app.config import get_settings, setup_logging
from app.crypto import CryptoError, SecretBox
from app.db.session import Database
from app.scheduler import schedule_jobs
from app.services import Services

log = logging.getLogger("app")


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    if not settings.telegram_bot_token.get_secret_value():
        sys.exit("Thiếu TELEGRAM_BOT_TOKEN trong .env (tạo bot qua @BotFather).")
    try:
        SecretBox(settings.secret_key.get_secret_value())
    except CryptoError as e:
        sys.exit(str(e))

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


if __name__ == "__main__":
    main()
