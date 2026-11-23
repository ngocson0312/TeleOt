"""App settings (from .env) and per-tenant report settings (stored in the DB)."""
from __future__ import annotations

import json
import logging
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    telegram_bot_token: SecretStr = SecretStr("")
    secret_key: SecretStr = SecretStr("")
    database_url: str = f"sqlite:///{(DATA_DIR / 'teleot.db').as_posix()}"
    admin_chat_id: int | None = None
    log_level: str = "INFO"

    # Phase 0 probe only.
    kiotviet_retailer: str = ""
    kiotviet_client_id: str = ""
    kiotviet_client_secret: SecretStr = SecretStr("")

    @field_validator("admin_chat_id", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        return None if v in ("", None) else v


@lru_cache
def get_settings() -> Settings:
    return Settings()


class TenantSettings(BaseModel):
    """Thresholds and schedule for one NPP. Defaults until the owner answers the questions."""

    report_times: list[str] = Field(default_factory=lambda: ["08:00", "12:00", "17:00"])
    sync_interval_minutes: int = 30

    # 5.2 Sắp hết hạn
    expiry_warn_days: int = 30
    expiry_red_days: int = 7

    # 5.1 Sắp hết hàng: used only when the item has no min set on KiotViet.
    low_stock_qty: float | None = None
    low_stock_qty_by_category: dict[str, float] = Field(default_factory=dict)
    low_days_cover: float = 7

    # 5.3 Tồn nhiều / bán chậm
    overstock_days_cover: float = 90
    no_sale_days: int = 30

    velocity_days: int = 30  # window for average daily sales
    history_days: int = 90  # invoices loaded on first sync
    keep_invoice_days: int = 120

    top_n: int = 15  # rows per section in a Telegram message

    @field_validator("report_times")
    @classmethod
    def _check_times(cls, v: list[str]) -> list[str]:
        for t in v:
            h, _, m = t.partition(":")
            if not (h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60):
                raise ValueError(f"Giờ không hợp lệ: {t!r} (dạng HH:MM)")
        return v

    @classmethod
    def from_json(cls, raw: str | None) -> "TenantSettings":
        return cls.model_validate(json.loads(raw or "{}"))

    def to_json(self) -> str:
        return self.model_dump_json(exclude_defaults=True)


def setup_logging(level: str = "INFO") -> None:
    # Vietnamese text on a Windows console / pipe that is not UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every URL at INFO. Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
