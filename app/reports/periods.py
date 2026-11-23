"""Periods typed in chat: "hôm nay", "tuần trước", "tháng 11", "11/2026", "22/11", "1/11-15/11"."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.textutil import unaccent

WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ nhật"]


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # inclusive
    label: str

    @property
    def single_day(self) -> bool:
        return self.start == self.end

    @property
    def month(self) -> str | None:
        """'YYYY-MM' when the whole period sits inside one month."""
        if (self.start.year, self.start.month) == (self.end.year, self.end.month):
            return f"{self.start:%Y-%m}"
        return None

    @property
    def code(self) -> str:  # fits in Telegram callback_data
        return f"{self.start:%Y%m%d}{self.end:%Y%m%d}"

    @classmethod
    def from_code(cls, code: str, today: date) -> "Period":
        start = datetime.strptime(code[:8], "%Y%m%d").date()
        end = datetime.strptime(code[8:16], "%Y%m%d").date()
        return cls(start, end, describe(start, end, today))

    def previous_day(self) -> "Period":
        d = self.start - timedelta(days=1)
        return Period(d, d, "hôm trước")


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def describe(start: date, end: date, today: date) -> str:
    if start == end:
        if start == today:
            return f"hôm nay {start:%d/%m}"
        if start == today - timedelta(days=1):
            return f"hôm qua {start:%d/%m}"
        return f"{WEEKDAYS[start.weekday()]} {start:%d/%m/%Y}"
    first, last = month_bounds(start.year, start.month)
    if start == first and end in (last, today) and end.month == start.month:
        suffix = f" (đến {end:%d/%m})" if end != last else ""
        return f"tháng {start.month}/{start.year}{suffix}"
    return f"{start:%d/%m} – {end:%d/%m/%Y}"


def this_month(today: date) -> Period:
    return Period(today.replace(day=1), today, describe(today.replace(day=1), today, today))


def _month(m: int, y: int | None, today: date) -> Period | None:
    if not 1 <= m <= 12:
        return None
    if y is None:
        y = today.year if m <= today.month else today.year - 1
    first, last = month_bounds(y, m)
    if first > today:
        return None
    end = min(last, today)
    return Period(first, end, describe(first, end, today))


def _day(d: int, m: int, y: int | None, today: date) -> date | None:
    try:
        when = date(y or today.year, m, d)
    except ValueError:
        return None
    if y is None and when > today:
        when = when.replace(year=today.year - 1)
    return when


_DATE = r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?"


def parse_period(text: str, today: date) -> Period | None:
    """The whole text must be a period."""
    t = unaccent(text).replace("–", "-").strip()
    t = re.sub(r"\s*-\s*", "-", t)
    if t in ("hom nay", "nay", "ngay"):
        return Period(today, today, describe(today, today, today))
    if t in ("hom qua", "qua"):
        d = today - timedelta(days=1)
        return Period(d, d, describe(d, d, today))
    if t in ("tuan nay", "tuan"):
        start = today - timedelta(days=today.weekday())
        return Period(start, today, f"tuần này ({start:%d/%m} – {today:%d/%m})")
    if t == "tuan truoc":
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        return Period(start, end, f"tuần trước ({start:%d/%m} – {end:%d/%m})")
    if t in ("thang nay", "thang"):
        return this_month(today)
    if t == "thang truoc":
        prev = today.replace(day=1) - timedelta(days=1)
        return _month(prev.month, prev.year, today)
    m = re.fullmatch(r"(?:thang|t)\s*(\d{1,2})(?:[/-](\d{4}))?", t) or re.fullmatch(r"(\d{1,2})[/-](\d{4})", t)
    if m:
        return _month(int(m.group(1)), int(m.group(2)) if m.group(2) else None, today)
    m = re.fullmatch(_DATE, t)
    if m:
        d = _day(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None, today)
        return Period(d, d, describe(d, d, today)) if d else None
    m = re.fullmatch(_DATE + "-" + _DATE, t)
    if m:
        g = m.groups()
        end_year = int(g[5]) if g[5] else None
        end = _day(int(g[3]), int(g[4]), end_year, today)
        start = _day(int(g[0]), int(g[1]), int(g[2]) if g[2] else (end.year if end else None), today)
        if start and end and start <= end:
            return Period(start, end, describe(start, end, today))
    return None


def split_name_period(text: str, today: date) -> tuple[str, Period | None]:
    """'tuan thang 11' -> ('tuan', tháng 11). The name must not end up empty:
    '/nv tuan' means the person Tuấn, not "this week"."""
    words = text.split()
    for n in range(min(5, len(words) - 1), 0, -1):
        period = parse_period(" ".join(words[-n:]), today)
        if period:
            return " ".join(words[:-n]), period
    return text.strip(), None


def month_pace(month: str, today: date, skip_sundays: bool = False) -> float:
    """Share of the month's (working) days elapsed, today included, in percent."""
    first, last = month_bounds(int(month[:4]), int(month[5:7]))
    if today < first:
        return 0.0
    if today >= last:
        return 100.0
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    if skip_sundays:
        days = [d for d in days if d.weekday() != 6]
    done = sum(1 for d in days if d <= today)
    return 100.0 * done / len(days)
