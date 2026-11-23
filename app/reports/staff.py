"""Section 5.5-5.7: doanh thu nhân viên, chỉ tiêu, xếp hạng.

Revenue = completed invoices (status 1) in the period, grouped by soldById, minus returns
dated in the period. A return without soldById goes to the seller of the original invoice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, select

from app.config import TenantSettings
from app.db.models import Invoice, InvoiceLine, Product, Return, ReturnLine, Staff, Target
from app.db.session import Database
from app.reports.periods import Period, month_bounds, month_pace
from app.textutil import unaccent

UNKNOWN = 0  # invoices/returns with no seller
RETURN_CANCELLED = 2


@dataclass
class StaffStats:
    staff_id: int
    name: str
    gross: float = 0.0
    returns: float = 0.0
    orders: int = 0
    customers: set[int] = field(default_factory=set)
    qty: dict[int, float] = field(default_factory=dict)  # base product id -> net quantity
    returned_qty: float = 0.0

    @property
    def revenue(self) -> float:
        return self.gross - self.returns

    @property
    def stores(self) -> int:
        return len(self.customers)


@dataclass
class TargetProgress:
    kind: str  # revenue | product
    target: float
    actual: float
    product_id: int | None = None
    product_name: str | None = None
    product_code: str | None = None
    unit: str | None = None

    @property
    def pct(self) -> float:
        return 100.0 * self.actual / self.target if self.target else 0.0


@dataclass
class StaffMonth:
    staff_id: int
    name: str
    month: str
    pace: float
    targets: list[TargetProgress]
    margin: float

    def behind(self) -> list[TargetProgress]:
        return [t for t in self.targets if t.pct < self.pace - self.margin]


def line_amount(line: InvoiceLine) -> float:
    """Money of one invoice line. KiotViet's subTotal when present, otherwise
    quantity x (price - discount). `probe` checks which formula matches the invoice totals."""
    if line.sub_total is not None:
        return line.sub_total
    return line.quantity * (line.price - line.discount)


def _bounds(period: Period) -> tuple[datetime, datetime]:
    return datetime.combine(period.start, time.min), datetime.combine(period.end + timedelta(days=1), time.min)


def _unit_map(products: dict[int, Product]) -> dict[int, tuple[int, float]]:
    out = {}
    for p in products.values():
        if p.is_base_unit:
            out[p.id] = (p.id, 1.0)
        else:
            out[p.id] = (p.master_unit_id, p.conversion_value or 1.0)
    return out


def staff_names(db: Database, tenant_id: int) -> dict[int, str]:
    """KiotViet users, plus names seen on invoices (Super Admin is not in /users)."""
    with db.session() as s:
        names = {sid: name for sid, name in s.execute(
            select(Invoice.sold_by_id, Invoice.sold_by_name)
            .where(Invoice.tenant_id == tenant_id, Invoice.sold_by_id.is_not(None))
            .group_by(Invoice.sold_by_id, Invoice.sold_by_name)) if name}
        for st in s.scalars(select(Staff).where(Staff.tenant_id == tenant_id)):
            names[st.id] = st.display_name
    names[UNKNOWN] = "Không rõ người bán"
    return names


def compute_stats(db: Database, tenant_id: int, period: Period, settings: TenantSettings) -> dict[int, StaffStats]:
    start, end = _bounds(period)
    names = staff_names(db, tenant_id)
    stats: dict[int, StaffStats] = {}

    def acc(sid: int | None) -> StaffStats:
        sid = sid or UNKNOWN
        if sid not in stats:
            stats[sid] = StaffStats(staff_id=sid, name=names.get(sid, f"Nhân viên {sid}"))
        return stats[sid]

    with db.session() as s:
        units = _unit_map({p.id: p for p in s.scalars(select(Product).where(Product.tenant_id == tenant_id))})
        in_period = and_(Invoice.tenant_id == tenant_id, Invoice.status == 1,
                         Invoice.purchase_date >= start, Invoice.purchase_date < end)
        for inv in s.scalars(select(Invoice).where(in_period)):
            st = acc(inv.sold_by_id)
            st.orders += 1
            if inv.customer_id:
                st.customers.add(inv.customer_id)
            if settings.revenue_mode == "invoice_total":
                st.gross += inv.total

        lines = s.execute(select(InvoiceLine, Invoice.sold_by_id).join(
            Invoice, and_(Invoice.tenant_id == InvoiceLine.tenant_id, Invoice.id == InvoiceLine.invoice_id)
        ).where(in_period)).all()
        for line, sid in lines:
            st = acc(sid)
            base, factor = units.get(line.product_id, (line.product_id, 1.0))
            st.qty[base] = st.qty.get(base, 0.0) + line.quantity * factor
            if settings.revenue_mode == "lines":
                st.gross += line_amount(line)

        rets = list(s.scalars(select(Return).where(
            Return.tenant_id == tenant_id, Return.return_date >= start, Return.return_date < end,
            (Return.status.is_(None)) | (Return.status != RETURN_CANCELLED))))
        orig_ids = [r.invoice_id for r in rets if r.sold_by_id is None and r.invoice_id]
        orig_seller = dict(s.execute(select(Invoice.id, Invoice.sold_by_id).where(
            Invoice.tenant_id == tenant_id, Invoice.id.in_(orig_ids))).all()) if orig_ids else {}
        seller_of = {r.id: r.sold_by_id or orig_seller.get(r.invoice_id) for r in rets}
        for r in rets:
            acc(seller_of[r.id]).returns += r.total
        if rets:
            for rl in s.scalars(select(ReturnLine).where(
                    ReturnLine.tenant_id == tenant_id, ReturnLine.return_id.in_([r.id for r in rets]))):
                st = acc(seller_of.get(rl.return_id))
                base, factor = units.get(rl.product_id, (rl.product_id, 1.0))
                st.qty[base] = st.qty.get(base, 0.0) - rl.quantity * factor
                st.returned_qty += rl.quantity * factor
    return stats


def ranking(stats: dict[int, StaffStats]) -> list[StaffStats]:
    rows = [st for st in stats.values() if st.orders or st.returns]
    return sorted(rows, key=lambda st: (-st.revenue, st.name))


def month_progress(db: Database, tenant_id: int, month: str, today: date,
                   settings: TenantSettings) -> dict[int, StaffMonth]:
    """Month-to-date actual vs target for every staff member who has a target that month."""
    first, last = month_bounds(int(month[:4]), int(month[5:7]))
    end = min(last, today)
    with db.session() as s:
        targets = list(s.scalars(select(Target).where(Target.tenant_id == tenant_id, Target.month == month)))
        products = {p.id: p for p in s.scalars(select(Product).where(Product.tenant_id == tenant_id))}
    if not targets or end < first:
        return {}
    stats = compute_stats(db, tenant_id, Period(first, end, ""), settings)
    names = staff_names(db, tenant_id)
    pace = month_pace(month, today, settings.skip_sundays)
    out: dict[int, StaffMonth] = {}
    for t in sorted(targets, key=lambda t: (t.kind != "revenue", t.product_id or 0)):
        st = stats.get(t.staff_id)
        sm = out.setdefault(t.staff_id, StaffMonth(t.staff_id, names.get(t.staff_id, str(t.staff_id)),
                                                   month, pace, [], settings.behind_margin))
        if t.kind == "revenue":
            sm.targets.append(TargetProgress("revenue", t.value, st.revenue if st else 0.0))
        else:
            p = products.get(t.product_id)
            sm.targets.append(TargetProgress(
                "product", t.value, (st.qty.get(t.product_id, 0.0) if st else 0.0), product_id=t.product_id,
                product_name=p.name if p else str(t.product_id), product_code=p.code if p else None,
                unit=p.unit if p else None))
    return out


@dataclass
class StaffMatch:
    tenant_id: int
    staff_id: int
    name: str


def search_staff(db: Database, tenant_ids: list[int], query: str, limit: int = 10) -> list[StaffMatch]:
    """Exact name or login first ('Lan' does not also return 'Lan Anh'),
    then every typed word as the start of a word in the name or login."""
    q = unaccent(query)
    words = q.split()
    if not words:
        return []
    candidates: list[tuple[StaffMatch, set[str], list[str]]] = []  # match, exact keys, words
    for tid in tenant_ids:
        with db.session() as s:
            users = {st.id: st for st in s.scalars(select(Staff).where(Staff.tenant_id == tid))}
        for sid, name in staff_names(db, tid).items():
            st = users.get(sid)
            if sid == UNKNOWN or (st and st.removed):
                continue
            keys = {unaccent(name)} | ({unaccent(st.user_name)} if st and st.user_name else set())
            norm = st.name_norm if st else unaccent(name)
            candidates.append((StaffMatch(tid, sid, name), keys, norm.split()))

    exact = [m for m, keys, _ in candidates if q in keys]
    if exact:
        return exact[:limit]
    found = [m for m, _, ws in candidates if all(any(w.startswith(qw) for w in ws) for qw in words)]
    found.sort(key=lambda m: (len(m.name), m.name))
    return found[:limit]
