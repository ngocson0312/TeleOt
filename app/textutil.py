"""Vietnamese text helpers: accent-insensitive matching and number formatting."""
from __future__ import annotations

import html
import unicodedata


def unaccent(text: str) -> str:
    """'Bánh Quy Bơ Đặc Biệt' -> 'banh quy bo dac biet'."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(stripped.lower().split())


def tokens(text: str) -> list[str]:
    return unaccent(text).split()


def fmt_qty(value: float | None) -> str:
    """1234.0 -> '1.234', 2.5 -> '2,5' (Vietnamese separators)."""
    if value is None:
        return "-"
    if abs(value - round(value)) < 1e-9:
        s = f"{int(round(value)):,}"
    else:
        s = f"{value:,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)
