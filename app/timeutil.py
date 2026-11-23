"""Dates. KiotViet sends local (Vietnam) times without an offset; we store them naive, local."""
from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Ho_Chi_Minh")

_FRACTION = re.compile(r"(\.\d{6})\d+")


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


def today_local() -> date:
    return now_local().date()


def parse_kv_datetime(value: str | None) -> datetime | None:
    """Parse '2026-11-22T10:15:30.1234567', '...Z' or '...+07:00' into naive local time."""
    if not value:
        return None
    s = _FRACTION.sub(r"\1", value.strip())
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(TZ).replace(tzinfo=None)
    return dt


def parse_kv_date(value: str | None) -> date | None:
    dt = parse_kv_datetime(value)
    return dt.date() if dt else None


def fmt_kv_datetime(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()
