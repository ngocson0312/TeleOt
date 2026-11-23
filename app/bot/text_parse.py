"""Free-text messages -> the same actions as the commands:
"tồn bánh quy", "hết hạn 60", "báo cáo", "top hôm nay", "doanh thu Lan tuần này", "Tuấn tháng 11".

Anything else is ignored, so the bot stays quiet in a busy group.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.reports.periods import parse_period, split_name_period
from app.textutil import unaccent
from app.timeutil import today_local


@dataclass
class Intent:
    kind: str  # ton | hethan | baocao | help | top | nv | maybe_nv
    arg: str | int | None = None


_HETHAN = re.compile(r"^(?:sap )?het ?han(?:\s+(\d{1,3}))?(?:\s*ngay)?$")
_TON_PREFIXES = (("ton", "kho"), ("ton",), ("tra", "ton"), ("xem", "ton"))
_TOP_PREFIXES = (("bang", "xep", "hang"), ("xep", "hang"), ("top",))
_NV_PREFIXES = (("doanh", "thu"), ("doanh", "so"), ("nhan", "vien"), ("nv",))
MAX_NAME_WORDS = 4  # "<tên> <kỳ>" without a keyword: only short messages


def parse_free_text(text: str, today: date | None = None) -> Intent | None:
    words = text.split()
    if words and words[0].startswith("@"):  # "@TenBot tồn bánh quy"
        words = words[1:]
    norm = unaccent(" ".join(words))
    if not norm:
        return None
    if norm in ("bao cao", "baocao", "bao cao ton kho", "gui bao cao"):
        return Intent("baocao")
    if norm in ("help", "huong dan", "tro giup", "giup"):
        return Intent("help")
    m = _HETHAN.match(norm)
    if m:
        return Intent("hethan", int(m.group(1)) if m.group(1) else None)
    norm_words = norm.split()
    for prefix in _TON_PREFIXES:
        n = len(prefix)
        if tuple(norm_words[:n]) == prefix and len(norm_words) > n:
            return Intent("ton", " ".join(words[n:]))
    today = today or today_local()
    for prefix in _TOP_PREFIXES:
        n = len(prefix)
        if tuple(norm_words[:n]) == prefix:
            rest = " ".join(words[n:])
            if not rest or parse_period(rest, today):
                return Intent("top", rest)
    for prefix in _NV_PREFIXES:
        n = len(prefix)
        if tuple(norm_words[:n]) == prefix and len(norm_words) > n:
            return Intent("nv", " ".join(words[n:]))
    name, period = split_name_period(" ".join(words), today)
    if period and 0 < len(name.split()) <= MAX_NAME_WORDS:
        return Intent("maybe_nv", " ".join(words))
    return None
