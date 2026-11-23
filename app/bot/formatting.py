"""Telegram messages (HTML parse mode). Every message stays under the 4096-char limit."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.reports.inventory import ExpiryRow, InventoryReport, ProductDetail, StockRow
from app.textutil import esc, fmt_qty

TG_LIMIT = 4096
STALE_AFTER = timedelta(hours=2)

LEVEL_TITLE = {"expired": "⛔ Đã hết hạn", "red": "🔴 Hết hạn trong {red} ngày", "orange": "🟠 Hết hạn trong {warn} ngày"}


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Split on blank lines, then on lines. Tags never span lines in our messages."""
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single absurd line
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        parts.append(current)
    return parts


def _as_of(data_as_of: datetime | None, now: datetime) -> str:
    if data_as_of is None:
        return "⚠️ Chưa đồng bộ được số liệu từ KiotViet."
    stamp = data_as_of.strftime("%H:%M %d/%m/%Y")
    if now - data_as_of > STALE_AFTER:
        return f"⚠️ Số liệu lúc {stamp}: chưa cập nhật được từ KiotViet, bot đang thử lại."
    return f"Số liệu lúc {stamp}"


def _unit(unit: str | None) -> str:
    return f" {esc(unit)}" if unit else ""


def _rate(r: StockRow) -> str:
    return f", bán ~{fmt_qty(round(r.avg_daily, 1))}/ngày" if r.avg_daily > 0 else ""


def stock_line(i: int, r: StockRow, multi_branch: bool) -> str:
    where = f" · {esc(r.branch_name)}" if multi_branch else ""
    return (f"{i}. <b>{esc(r.name)}</b> ({esc(r.code)}): còn {fmt_qty(r.on_hand)}{_unit(r.unit)}"
            f"{_rate(r)}. {esc(r.reason)}{where}")


def _days_text(days_left: int) -> str:
    if days_left < 0:
        return f"quá hạn {-days_left} ngày"
    if days_left == 0:
        return "hết hạn hôm nay"
    return f"còn {days_left} ngày"


def expiry_line(r: ExpiryRow, multi_branch: bool, *, with_name: bool = True) -> str:
    where = f" · {esc(r.branch_name)}" if multi_branch and r.branch_name else ""
    batch = esc(r.batch_name) if r.batch_name else "(không tên)"
    head = f"<b>{esc(r.name)}</b> lô {batch}" if with_name else f"Lô {batch}"
    return (f"• {head}: {fmt_qty(r.on_hand)}{_unit(r.unit)}, "
            f"HSD {r.expire_date:%d/%m/%Y} ({_days_text(r.days_left)}){where}")


def _expiry_block(rows: list[ExpiryRow], limit: int, multi: bool, red: int, warn: int) -> tuple[list[str], int]:
    lines: list[str] = []
    shown = 0
    for level in ("expired", "red", "orange"):
        group = [r for r in rows if r.level == level]
        if not group or shown >= limit:
            continue
        lines.append(LEVEL_TITLE[level].format(red=red, warn=warn))
        for r in group[: limit - shown]:
            lines.append(expiry_line(r, multi))
            shown += 1
    return lines, len(rows) - shown


def format_inventory_report(report: InventoryReport, now: datetime) -> tuple[list[str], bool]:
    """Return (messages, truncated). When truncated, send the Excel file too."""
    s = report.settings
    n = s.top_n
    multi = report.multi_branch
    truncated = False
    out = [f"📦 <b>Báo cáo tồn kho · {esc(report.tenant_name)}</b>", _as_of(report.data_as_of, now), ""]

    out.append(f"🔻 <b>Sắp hết hàng: {len(report.low)} mặt hàng</b>")
    if report.low:
        out += [stock_line(i, r, multi) for i, r in enumerate(report.low[:n], 1)]
        if len(report.low) > n:
            out.append(f"… và {len(report.low) - n} mặt hàng khác.")
            truncated = True
    else:
        out.append("✅ Không có.")
    out.append("")

    out.append(f"⏰ <b>Sắp hết hạn: {len(report.expiring)} lô</b>")
    if report.expiring:
        lines, rest = _expiry_block(report.expiring, n, multi, s.expiry_red_days, s.expiry_warn_days)
        out += lines
        if rest:
            out.append(f"… và {rest} lô khác.")
            truncated = True
    else:
        out.append(f"✅ Không có lô nào hết hạn trong {s.expiry_warn_days} ngày tới.")
    out.append("")

    out.append(f"🐢 <b>Tồn nhiều / bán chậm: {len(report.over)} mặt hàng</b>")
    if report.over:
        out += [stock_line(i, r, multi) for i, r in enumerate(report.over[:n], 1)]
        if len(report.over) > n:
            out.append(f"… và {len(report.over) - n} mặt hàng khác.")
            truncated = True
    else:
        out.append("✅ Không có.")

    if truncated:
        out += ["", "📎 Danh sách đầy đủ trong file Excel gửi kèm."]
    return split_message("\n".join(out)), truncated


def format_expiring(tenant_name: str, rows: list[ExpiryRow], days: int, red: int, multi: bool,
                    data_as_of: datetime | None, now: datetime, limit: int = 30) -> tuple[list[str], bool]:
    out = [f"⏰ <b>Lô hết hạn trong {days} ngày · {esc(tenant_name)}</b>", _as_of(data_as_of, now), ""]
    if not rows:
        out.append(f"✅ Không có lô nào hết hạn trong {days} ngày tới.")
        return split_message("\n".join(out)), False
    lines, rest = _expiry_block(rows, limit, multi, red, days)
    out += lines
    if rest:
        out += [f"… và {rest} lô khác.", "", "📎 Danh sách đầy đủ trong file Excel gửi kèm."]
    return split_message("\n".join(out)), bool(rest)


def format_product_detail(d: ProductDetail, now: datetime, tenant_name: str | None = None) -> list[str]:
    p = d.product
    multi = len(d.stock) > 1
    head = f"📦 <b>{esc(p.name)}</b> ({esc(p.code)})" + (f" · {esc(p.unit)}" if p.unit else "")
    out = [f"<i>{esc(tenant_name)}</i>"] if tenant_name else []
    out.append(head)
    for u in d.other_units:
        out.append(f"1 {esc(u.unit or u.name)} = {fmt_qty(u.conversion_value)}{_unit(p.unit)}")
    out += ["", "<b>Tồn theo kho</b>"]
    if not d.stock:
        out.append("Chưa có tồn kho.")
    total = 0.0
    for r in d.stock:
        total += r.on_hand
        extra = []
        if r.min_qty:
            extra.append(f"tối thiểu {fmt_qty(r.min_qty)}")
        if r.max_qty:
            extra.append(f"tối đa {fmt_qty(r.max_qty)}")
        if r.avg_daily > 0:
            extra.append(f"bán ~{fmt_qty(round(r.avg_daily, 1))}/ngày")
            if r.days_cover is not None:
                extra.append(f"đủ ~{r.days_cover:.0f} ngày")
        tail = f" ({', '.join(extra)})" if extra else ""
        out.append(f"• {esc(r.branch_name)}: <b>{fmt_qty(r.on_hand)}</b>{tail}")
    if multi:
        out.append(f"Tổng: <b>{fmt_qty(total)}</b>{_unit(p.unit)}")
    if d.batches:
        out += ["", "<b>Lô / hạn sử dụng</b>"]
        for r in d.batches[:20]:
            mark = " ⛔" if r.level == "expired" else " 🔴" if r.level == "red" else ""
            out.append(expiry_line(r, multi, with_name=False) + mark)
        if len(d.batches) > 20:
            out.append(f"… và {len(d.batches) - 20} lô khác.")
    out += ["", _as_of(d.data_as_of, now)]
    return split_message("\n".join(out))


HELP_TEXT = """🤖 <b>Bot báo cáo KiotViet</b>

<b>Tra cứu</b>
/ton tên hàng: tồn theo kho, kèm lô và hạn dùng. Ví dụ: <code>/ton banh quy</code>
/hethan [số ngày]: các lô hết hạn trong N ngày. Ví dụ: <code>/hethan 60</code>
/baocao: gửi ngay báo cáo tồn kho
/id: xem mã chat (để người vận hành cấp quyền)

Có thể nhắn không cần dấu /, ví dụ "tồn bánh quy", "hết hạn 60", "báo cáo".
Gõ có dấu hay không dấu đều được."""
