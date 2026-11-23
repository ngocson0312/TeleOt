"""Messages for the owner group (phase 3): NPP comparison, inventory overview, sync status."""
from __future__ import annotations

from datetime import datetime

from app.bot.formatting import STALE_AFTER, _unit, expiry_line, split_message
from app.bot.staff_format import MEDALS, fmt_money
from app.reports.alerts import KINDS, current_alerts
from app.reports.compare import NppSummary
from app.reports.inventory import InventoryReport, StockRow
from app.reports.periods import Period
from app.reports.status import TenantStatus
from app.textutil import esc, fmt_qty

OVERVIEW_ITEMS = 3  # worst items shown per NPP in the overview


def _change(now_v: float, before: float) -> str:
    if before <= 0:
        return ""
    pct = 100.0 * (now_v - before) / before
    return f"{'▲' if pct >= 0 else '▼'} {abs(pct):.0f}%"


def _ago(when: datetime, now: datetime) -> str:
    mins = max(int((now - when).total_seconds() // 60), 0)
    if mins < 60:
        return f"{mins} phút trước"
    if mins < 48 * 60:
        return f"{mins // 60} giờ trước"
    return f"{mins // (24 * 60)} ngày trước"


def _freshness(as_of: list[tuple[str, datetime | None]], now: datetime) -> str:
    stale = [(n, t) for n, t in as_of if t is None or now - t > STALE_AFTER]
    if stale:
        parts = [f"{esc(n)} ({'chưa đồng bộ' if t is None else f'số liệu lúc {t:%H:%M %d/%m}'})" for n, t in stale]
        return "⚠️ Chưa cập nhật được từ KiotViet: " + ", ".join(parts)
    times = [t for _, t in as_of if t]
    return f"Số liệu lúc {min(times):%H:%M %d/%m/%Y}" if times else ""


def format_comparison(rows: list[NppSummary], period: Period, now: datetime) -> list[str]:
    out = [f"📊 <b>So sánh NPP · {esc(period.label)}</b>", ""]
    if not rows:
        return ["Chưa có NPP nào đang chạy."]
    prev_label = period.previous().label
    for i, r in enumerate(rows, 1):
        head = MEDALS.get(i, f"{i}.") if r.revenue > 0 else "–"
        ch = _change(r.revenue, r.prev_revenue)
        vs = f" ({ch} so với {esc(prev_label)})" if ch else ""
        out.append(f"{head} <b>{esc(r.name)}</b>: <b>{fmt_money(r.revenue)}</b>{vs}")
        out.append(f"    {r.orders} đơn · {r.stores} cửa hàng · {r.sellers} người bán")
        if r.month:
            parts = []
            if r.month_revenue is not None:
                parts.append(f"tháng {r.month[5:]}: {fmt_money(r.month_revenue)}")
            if r.target_pct is not None:
                parts.append(f"đạt {r.target_pct:.0f}% chỉ tiêu (tiến độ {r.pace:.0f}%)")
                if r.behind:
                    parts.append(f"{r.behind} NV chậm tiến độ")
            else:
                parts.append("chưa nạp chỉ tiêu")
            out.append("    🎯 " + " · ".join(parts))
        if r.top:
            out.append("    Bán nhiều: " + ", ".join(f"{esc(n)} {fmt_money(v)}" for n, v in r.top))
        inv = []
        if r.low:
            inv.append(f"{r.low} mặt hàng sắp hết")
        if r.expiring:
            inv.append(f"{r.expiring} lô hết hạn / sắp hết hạn")
        out.append("    📦 " + (" · ".join(inv) if inv else "tồn kho ổn"))
        out.append("")
    if len(rows) > 1:
        total, prev_total = sum(r.revenue for r in rows), sum(r.prev_revenue for r in rows)
        ch = _change(total, prev_total)
        vs = f" ({ch} so với {esc(prev_label)})" if ch else ""
        out.append(f"<b>Tổng {len(rows)} NPP: {fmt_money(total)}</b>{vs} · {sum(r.orders for r in rows)} đơn")
    out.append(_freshness([(r.name, r.data_as_of) for r in rows], now))
    return split_message("\n".join(out))


def _overview_item(row, multi: bool) -> str:
    if isinstance(row, StockRow):
        where = f" · {esc(row.branch_name)}" if multi else ""
        state = "hết hàng" if row.on_hand <= 0 else f"còn {fmt_qty(row.on_hand)}{_unit(row.unit)}. {esc(row.reason)}"
        return f"• <b>{esc(row.name)}</b>: {state}{where}"
    return expiry_line(row, multi)


def format_inventory_overview(reports: list[InventoryReport], now: datetime) -> list[str]:
    out = [f"📦 <b>Tồn kho các NPP</b> · {now:%H:%M %d/%m}", ""]
    for rep in reports:
        counts = [f"🔻 {len(rep.low)} sắp hết", f"⏰ {len(rep.expiring)} lô sắp hết hạn", f"🐢 {len(rep.over)} tồn nhiều"]
        if not (rep.low or rep.expiring or rep.over):
            out += [f"<b>{esc(rep.tenant_name)}</b>: ✅ không có gì cần xử lý", ""]
            continue
        out.append(f"<b>{esc(rep.tenant_name)}</b>: " + " · ".join(counts))
        worst = sorted(current_alerts(rep).values(), key=lambda a: KINDS.index(a.kind))
        out += [_overview_item(a.row, rep.multi_branch) for a in worst[:OVERVIEW_ITEMS]]
        out.append("")
    out.append(_freshness([(r.tenant_name, r.data_as_of) for r in reports], now))
    out.append("Xem đầy đủ một NPP: <code>/baocao tên NPP</code>")
    return split_message("\n".join(out))


def format_status(rows: list[TenantStatus], now: datetime) -> list[str]:
    out = [f"🩺 <b>Tình trạng đồng bộ</b> · {now:%H:%M %d/%m}", ""]
    if not rows:
        out.append("Chưa có NPP nào. Thêm bằng: <code>python -m app.cli tenant-add</code>")
    for r in rows:
        if not r.active:
            out.append(f"⏸ <b>{esc(r.name)}</b> (id {r.tenant_id}): tạm dừng")
            continue
        fresh = r.last_sync is not None and now - r.last_sync <= STALE_AFTER
        icon = "✅" if fresh and not r.failing else "🚨"
        when = f"đồng bộ lúc {r.last_sync:%H:%M %d/%m} ({_ago(r.last_sync, now)})" if r.last_sync else "chưa đồng bộ lần nào"
        out.append(f"{icon} <b>{esc(r.name)}</b> (id {r.tenant_id}): {when}")
        out.append(f"    {r.products} mặt hàng · {r.invoices_today} đơn hôm nay · {r.chats} nhóm chat"
                   f" · {r.open_alerts} cảnh báo đang mở")
        for resource, n, err in r.failing:
            out.append(f"    ✘ {esc(resource)}: lỗi {n} lần liên tiếp. {esc((err or '')[:200])}")
    return split_message("\n".join(out))


OWNER_HELP = """<b>Nhóm tổng (nhiều NPP)</b>
/sosanh [kỳ]: so sánh doanh thu, số đơn, chỉ tiêu, tồn kho giữa các NPP. Ví dụ: <code>/sosanh tháng này</code>
/baocao [tên NPP]: không ghi tên thì xem tóm tắt tất cả NPP
/top [tên NPP] [kỳ]: ví dụ <code>/top Hà Nội hôm qua</code>
/trangthai: tình trạng đồng bộ từng NPP"""
