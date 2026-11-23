"""Telegram messages for /nv, /top and the 18:00 ranking."""
from __future__ import annotations

from datetime import datetime

from app.bot.formatting import split_message
from app.db.models import Product
from app.reports.periods import Period
from app.reports.staff import StaffMonth, StaffStats, TargetProgress
from app.textutil import esc, fmt_qty

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
TOP_PRODUCTS = 10
MAX_RANK_ROWS = 40


def fmt_money(v: float) -> str:
    return f"{fmt_qty(round(v))}đ"


def _month_text(month: str) -> str:
    return f"{month[5:]}/{month[:4]}"


def _as_of(data_as_of: datetime | None) -> str:
    return f"Số liệu lúc {data_as_of:%H:%M %d/%m/%Y}" if data_as_of else "⚠️ Chưa đồng bộ được số liệu từ KiotViet."


def _target_line(t: TargetProgress, pace: float, margin: float) -> str:
    slow = " 🐢 chậm tiến độ" if t.pct < pace - margin else ""
    if t.kind == "revenue":
        what = f"Doanh thu: {fmt_money(t.actual)} / {fmt_money(t.target)}"
    else:
        unit = f" {esc(t.unit)}" if t.unit else ""
        what = f"{esc(t.product_name)}: {fmt_qty(t.actual)} / {fmt_qty(t.target)}{unit}"
    return f"• {what} = <b>{t.pct:.0f}%</b>{slow}"


def month_block(sm: StaffMonth) -> list[str]:
    title = f"🎯 <b>Chỉ tiêu tháng {_month_text(sm.month)}</b> (tiến độ tháng {sm.pace:.0f}%)"
    return [title] + [_target_line(t, sm.pace, sm.margin) for t in sm.targets]


def format_staff_detail(name: str, st: StaffStats | None, period: Period, month: StaffMonth | None,
                        products: dict[int, Product], data_as_of: datetime | None,
                        tenant_label: str | None = None) -> list[str]:
    out = [f"<i>{esc(tenant_label)}</i>"] if tenant_label else []
    out.append(f"👤 <b>{esc(name)}</b> · {esc(period.label)}")
    if st is None or (not st.orders and not st.returns):
        out.append("Chưa có đơn nào trong kỳ này.")
    else:
        ret = f" (đã trừ {fmt_money(st.returns)} hàng trả)" if st.returns else ""
        out.append(f"Doanh thu: <b>{fmt_money(st.revenue)}</b>{ret}")
        out.append(f"Số đơn: {st.orders} · Số cửa hàng: {st.stores}")
        top = sorted(((pid, q) for pid, q in st.qty.items() if q > 0), key=lambda x: -x[1])
        if top:
            out += ["", "<b>Mặt hàng bán nhiều nhất</b>"]
            for i, (pid, q) in enumerate(top[:TOP_PRODUCTS], 1):
                p = products.get(pid)
                label = esc(p.name) if p else f"Mã {pid}"
                unit = f" {esc(p.unit)}" if p and p.unit else ""
                out.append(f"{i}. {label}: {fmt_qty(q)}{unit}")
            if len(top) > TOP_PRODUCTS:
                out.append(f"… và {len(top) - TOP_PRODUCTS} mặt hàng khác.")
    if month and month.targets:
        out += [""] + month_block(month)
    out += ["", _as_of(data_as_of)]
    return split_message("\n".join(out))


def _change(today: float, before: float) -> str:
    if before <= 0:
        return f"hôm trước {fmt_money(before)}"
    pct = 100.0 * (today - before) / before
    arrow = "▲" if pct >= 0 else "▼"
    return f"hôm trước {fmt_money(before)}, {arrow} {abs(pct):.0f}%"


def format_ranking(tenant_name: str, period: Period, rows: list[StaffStats], data_as_of: datetime | None,
                   *, previous: dict[int, StaffStats] | None = None,
                   progress: dict[int, StaffMonth] | None = None, title: str = "Xếp hạng doanh thu") -> list[str]:
    out = [f"🏆 <b>{esc(title)} · {esc(period.label)}</b>", f"{esc(tenant_name)} · {_as_of(data_as_of)}", ""]
    if previous:  # people who sold the day before but not today still show, with 0
        present = {r.staff_id for r in rows}
        rows = rows + [StaffStats(p.staff_id, p.name) for p in previous.values()
                       if p.staff_id not in present and p.revenue]
    if not rows:
        out.append("Chưa có đơn nào trong kỳ này.")
    total, orders = 0.0, 0
    for i, r in enumerate(rows, 1):
        total += r.revenue
        orders += r.orders
        if i > MAX_RANK_ROWS:
            continue
        head = MEDALS.get(i, f"{i}.") if r.revenue > 0 else "–"
        money = f"<b>{fmt_money(r.revenue)}</b>" if i == 1 else fmt_money(r.revenue)
        parts = [f"{head} {esc(r.name)}: {money}", f"{r.orders} đơn"]
        if r.stores:
            parts.append(f"{r.stores} cửa hàng")
        line = " · ".join(parts)
        extra = []
        if previous is not None:
            before = previous.get(r.staff_id)
            extra.append(_change(r.revenue, before.revenue if before else 0.0))
        sm = (progress or {}).get(r.staff_id)
        rev_target = next((t for t in sm.targets if t.kind == "revenue"), None) if sm else None
        if rev_target:
            extra.append(f"đạt {rev_target.pct:.0f}% chỉ tiêu tháng")
        if extra:
            line += f" ({'; '.join(extra)})"
        out.append(line)
    if len(rows) > MAX_RANK_ROWS:
        out.append(f"… và {len(rows) - MAX_RANK_ROWS} người khác.")
    if rows:
        out += ["", f"<b>Tổng: {fmt_money(total)} · {orders} đơn</b>"]
    return split_message("\n".join(out))


def behind_block(progress: dict[int, StaffMonth], month: str) -> list[str]:
    if not progress:
        return [f"🎯 Chưa có chỉ tiêu tháng {_month_text(month)}. Gõ /chitieu để nạp."]
    pace = next(iter(progress.values())).pace
    lines = [f"🐢 <b>Chậm tiến độ tháng {_month_text(month)}</b> (tiến độ tháng {pace:.0f}%)"]
    slow = []
    for sm in sorted(progress.values(), key=lambda s: s.name):
        for t in sm.behind():
            what = "doanh thu" if t.kind == "revenue" else esc(t.product_name)
            slow.append(f"• {esc(sm.name)}: {what} đạt {t.pct:.0f}%")
    lines += slow or ["✅ Không ai chậm tiến độ."]
    return lines


def format_daily_ranking(tenant_name: str, period: Period, rows: list[StaffStats], previous: dict[int, StaffStats],
                         progress: dict[int, StaffMonth], month: str, data_as_of: datetime | None) -> list[str]:
    msgs = format_ranking(tenant_name, period, rows, data_as_of, previous=previous, progress=progress,
                          title="Xếp hạng cuối ngày")
    text = "\n".join(msgs) + "\n\n" + "\n".join(behind_block(progress, month))
    return split_message(text)


STAFF_HELP = """<b>Nhân viên</b>
/nv tên [kỳ]: doanh thu, số đơn, số cửa hàng, mặt hàng, % chỉ tiêu. Ví dụ: <code>/nv Tuấn tháng 11</code>
/top [kỳ]: xếp hạng nhân viên. Ví dụ: <code>/top hôm nay</code>
/chitieu: lấy file mẫu, điền rồi gửi lại để nạp chỉ tiêu (chỉ quản trị viên nhóm)
Kỳ: hôm nay, hôm qua, tuần này, tuần trước, tháng này, tháng trước, tháng 11, 22/11, 1/11-15/11"""
