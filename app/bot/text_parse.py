"""Free-text messages ("tồn bánh quy", "hết hạn 60", "báo cáo") -> the same actions as the commands.

Anything else is ignored, so the bot stays quiet in a busy group.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.textutil import unaccent


@dataclass
class Intent:
    kind: str  # ton | hethan | baocao | help
    arg: str | int | None = None


_HETHAN = re.compile(r"^(?:sap )?het ?han(?:\s+(\d{1,3}))?(?:\s*ngay)?$")
_TON_PREFIXES = (("ton", "kho"), ("ton",), ("tra", "ton"), ("xem", "ton"))


def parse_free_text(text: str) -> Intent | None:
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
    return None
