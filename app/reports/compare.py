"""Phase 3: compare NPPs (owner group) and a one-screen inventory overview of every NPP."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.config import TenantSettings
from app.db.models import Tenant
from app.db.session import Database
from app.reports.inventory import InventoryReport, build_inventory_report, load_snapshot
from app.reports.periods import Period, month_bounds, month_pace
from app.reports.staff import UNKNOWN, compute_stats, month_progress, ranking
from app.sync.runner import last_stock_sync

TOP_SELLERS = 3


@dataclass
class NppSummary:
    tenant_id: int
    name: str
    revenue: float
    orders: int
    stores: int
    sellers: int
    prev_revenue: float
    top: list[tuple[str, float]] = field(default_factory=list)
    month: str | None = None
    month_revenue: float | None = None  # month to date, when the period is shorter than that
    target: float = 0.0  # sum of the month's revenue targets (0 = none loaded)
    target_actual: float = 0.0
    pace: float | None = None
    behind: int = 0  # staff behind pace on at least one target
    low: int = 0
    expiring: int = 0  # lots expired or within expiry_red_days
    data_as_of: datetime | None = None

    @property
    def target_pct(self) -> float | None:
        return 100.0 * self.target_actual / self.target if self.target else None


def inventory_report(db: Database, tenant: Tenant, settings: TenantSettings, today: date) -> InventoryReport:
    return build_inventory_report(load_snapshot(db, tenant.id, settings, today), settings, today)


def summarize_npp(db: Database, tenant: Tenant, settings: TenantSettings, period: Period,
                  today: date) -> NppSummary:
    stats = compute_stats(db, tenant.id, period, settings)
    prev = compute_stats(db, tenant.id, period.previous(), settings)
    rows = ranking(stats)
    customers: set[int] = set()
    for st in stats.values():
        customers |= st.customers
    out = NppSummary(
        tenant_id=tenant.id, name=tenant.name,
        revenue=sum(st.revenue for st in stats.values()),
        orders=sum(st.orders for st in stats.values()),
        stores=len(customers),
        sellers=sum(1 for st in rows if st.staff_id != UNKNOWN and st.orders),
        prev_revenue=sum(st.revenue for st in prev.values()),
        top=[(st.name, st.revenue) for st in rows if st.revenue > 0][:TOP_SELLERS],
        data_as_of=last_stock_sync(db, tenant.id),
    )
    if period.month:
        out.month = period.month
        first, last = month_bounds(int(period.month[:4]), int(period.month[5:]))
        mtd = Period(first, min(last, today), "")
        if (period.start, period.end) != (mtd.start, mtd.end) and mtd.end >= first:
            out.month_revenue = sum(st.revenue for st in compute_stats(db, tenant.id, mtd, settings).values())
        progress = month_progress(db, tenant.id, period.month, today, settings)
        for sm in progress.values():
            for t in sm.targets:
                if t.kind == "revenue":
                    out.target += t.target
                    out.target_actual += t.actual
        out.behind = sum(1 for sm in progress.values() if sm.behind())
        out.pace = month_pace(period.month, today, settings.skip_sundays)
    report = inventory_report(db, tenant, settings, today)
    out.low = len(report.low)
    out.expiring = sum(1 for r in report.expiring if r.level in ("expired", "red"))
    return out


def compare_npps(db: Database, tenants: list[Tenant], period: Period, today: date) -> list[NppSummary]:
    rows = [summarize_npp(db, t, TenantSettings.from_json(t.settings_json), period, today) for t in tenants]
    rows.sort(key=lambda r: (-r.revenue, r.name))
    return rows
