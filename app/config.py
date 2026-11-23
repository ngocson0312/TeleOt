"""App settings (from .env) and per-tenant report settings (stored in the DB)."""
from __future__ import annotations

import json
import logging
import sys
from datetime import time
from functools import lru_cache
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import ClassVar, Literal

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
    log_file: Path | None = None  # bot log to this file instead of the console (Windows service)
    max_parallel_syncs: int = 3  # NPPs syncing at the same time (SQLite writes one at a time anyway)
    backup_dir: Path = DATA_DIR / "backup"  # daily copy of the SQLite database
    backup_keep: int = 14  # number of daily copies kept

    # Phase 0 probe only.
    kiotviet_retailer: str = ""
    kiotviet_client_id: str = ""
    kiotviet_client_secret: SecretStr = SecretStr("")

    @field_validator("admin_chat_id", "log_file", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        return None if v in ("", None) else v


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _check_hhmm(t: str) -> str:
    h, _, m = t.partition(":")
    if not (h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60):
        raise ValueError(f"Giờ không hợp lệ: {t!r} (dạng HH:MM)")
    return f"{int(h):02d}:{m}"


def _off_or_hhmm(v: str | None) -> str | None:
    if v in (None, "", "off", "tat"):
        return None
    return _check_hhmm(v)


def parse_window(window: str) -> tuple[time, time]:
    """'21:00-07:00' -> (21:00, 07:00)."""
    start, sep, end = window.replace(" ", "").partition("-")
    if not sep:
        raise ValueError(f"Khung giờ không hợp lệ: {window!r} (dạng 21:00-07:00)")
    a, b = _check_hhmm(start), _check_hhmm(end)
    return time(int(a[:2]), int(a[3:])), time(int(b[:2]), int(b[3:]))


def in_window(window: str | None, t: time) -> bool:
    """Is t inside the window? Windows may wrap midnight (21:00-07:00)."""
    if not window:
        return False
    start, end = parse_window(window)
    if start <= end:
        return start <= t < end
    return t >= start or t < end


class TenantSettings(BaseModel):
    """Thresholds and schedule for one NPP. Defaults until the owner answers the questions."""

    report_times: list[str] = Field(default_factory=lambda: ["08:00", "12:00", "17:00"])
    sync_interval_minutes: int = 15  # also how often urgent alerts are checked

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

    # 5.5-5.7 Nhân viên
    revenue_mode: Literal["invoice_total", "lines"] = "invoice_total"  # pick after `staff-check`
    ranking_time: str | None = "18:00"  # None = no daily ranking
    ranking_to_npp_groups: bool = True  # False = only the owner group gets the ranking
    skip_sundays: bool = False  # month pace counts working days only
    behind_margin: float = 10  # "chậm tiến độ" when % đạt < tiến độ - this many points

    # 5.4 Cảnh báo gấp (phase 3): checked after every sync, only items that are newly over a threshold
    urgent_alerts: bool = True
    quiet_hours: str | None = "21:00-07:00"  # alerts found then wait until the window ends
    alert_cooldown_hours: float = 24  # item back to normal then bad again within this time: no new alert

    @field_validator("report_times")
    @classmethod
    def _check_times(cls, v: list[str]) -> list[str]:
        for t in v:
            _check_hhmm(t)
        return v

    @field_validator("ranking_time")
    @classmethod
    def _check_ranking_time(cls, v: str | None) -> str | None:
        return _off_or_hhmm(v)

    @field_validator("quiet_hours")
    @classmethod
    def _check_quiet(cls, v: str | None) -> str | None:
        if v in (None, "", "off", "tat"):
            return None
        a, b = parse_window(v)
        return f"{a:%H:%M}-{b:%H:%M}"

    @classmethod
    def from_json(cls, raw: str | None) -> "TenantSettings":
        return cls.model_validate(json.loads(raw or "{}"))

    def to_json(self) -> str:
        return self.model_dump_json(exclude_defaults=True)


class ChatSettings(BaseModel):
    """What one Telegram chat receives on schedule. Defaults depend on the chat's role:
    an NPP group gets everything of its NPP; the owner group gets one summary for all NPPs
    instead of every NPP's full report."""

    inventory: Literal["full", "summary", "off"] = "full"  # scheduled inventory reports
    summary_times: list[str] = Field(default_factory=lambda: ["08:00"])  # when inventory = summary
    ranking: bool | None = None  # None: NPP groups yes; owner groups only for NPPs whose groups don't get it
    urgent: bool = True  # urgent alerts
    compare_time: str | None = None  # daily NPP comparison

    ROLE_DEFAULTS: ClassVar[dict[str, dict]] = {
        "owner": {"inventory": "summary", "urgent": False, "compare_time": "18:30"},
    }

    @field_validator("summary_times")
    @classmethod
    def _check_times(cls, v: list[str]) -> list[str]:
        return [_check_hhmm(t) for t in v]

    @field_validator("compare_time")
    @classmethod
    def _check_compare(cls, v: str | None) -> str | None:
        return _off_or_hhmm(v)

    @classmethod
    def for_chat(cls, role: str, raw: str | None) -> "ChatSettings":
        return cls.model_validate({**cls.ROLE_DEFAULTS.get(role, {}), **json.loads(raw or "{}")})

    def to_json(self, role: str) -> str:
        """Only what differs from the role's defaults."""
        base = ChatSettings.for_chat(role, None).model_dump()
        return json.dumps({k: v for k, v in self.model_dump().items() if v != base[k]}, ensure_ascii=False)


def log_handlers(log_file: Path | None) -> list[logging.Handler]:
    """Console by default (systemd keeps it in the journal). With LOG_FILE: that file only,
    a new one every midnight, 14 days kept (Windows has no journal)."""
    if log_file is None:
        return [logging.StreamHandler()]
    log_file.parent.mkdir(parents=True, exist_ok=True)
    return [TimedRotatingFileHandler(log_file, when="midnight", backupCount=14, encoding="utf-8")]


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    # Vietnamese text on a Windows console / pipe that is not UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=log_handlers(log_file),
    )
    # httpx logs every URL at INFO. Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
