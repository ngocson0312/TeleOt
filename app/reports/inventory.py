"""Section 5.1-5.3 of the plan: sắp hết hàng, sắp hết hạn, tồn nhiều / bán chậm.

Only base-unit, active, ordinary goods are reported. Sales of extra units (thùng = 12 hộp)
are converted into the base unit before computing the daily rate.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, case, func, select

from app.config import TenantSettings
from app.db.models import Batch, Branch, Inventory, Invoice, InvoiceLine, Product, Tenant
from app.db.session import Database
from app.sync.runner import last_stock_sync
from app.textutil import fmt_qty, tokens, unaccent

SERVICE_TYPES = {1, 3}  # combo, dịch vụ: no stock of their own


@dataclass
class Sales:
    qty_window: float = 0.0  # base units sold in the velocity window
    last_sold: datetime | None = None


@dataclass
class Snapshot:
    tenant_id: int
    tenant_name: str
    products: dict[int, Product]  # eligible base-unit products
    all_products: dict[int, Product]
    branches: dict[int, str]
    inventory: list[Inventory]
    batches: list[Batch]
    sales: dict[tuple[int, int | None], Sales]
    data_as_of: datetime | None


@dataclass
class StockRow:
    product_id: int
    code: str
    name: str
    unit: str | None
    category: str | None
    branch_id: int
    branch_name: str
    on_hand: float
    min_qty: float | None
    max_qty: float | None
    cost: float | None
    avg_daily: float
    days_cover: float | None
    last_sold: datetime | None
    reason: str

    @property
    def value(self) -> float:
        return self.on_hand * (self.cost or 0)


@dataclass
class ExpiryRow:
    product_id: int
    code: str
    name: str
    unit: str | None
    branch_name: str
    batch_name: str
    expire_date: date
    days_left: int
    on_hand: float
    level: str  # expired | red | orange


@dataclass
class InventoryReport:
    tenant_id: int
    tenant_name: str
    today: date
    data_as_of: datetime | None
    multi_branch: bool
    settings: TenantSettings
    low: list[StockRow] = field(default_factory=list)
    expiring: list[ExpiryRow] = field(default_factory=list)
    over: list[StockRow] = field(default_factory=list)


def _eligible(p: Product) -> bool:
    return p.is_active and not p.removed and p.is_base_unit and p.product_type not in SERVICE_TYPES


def load_snapshot(db: Database, tenant_id: int, settings: TenantSettings, today: date) -> Snapshot:
    vel_start = datetime.combine(today - timedelta(days=settings.velocity_days), time.min)
    today_start = datetime.combine(today, time.min)
    with db.session() as s:
        tenant = s.get(Tenant, tenant_id)
        all_products = {p.id: p for p in s.scalars(select(Product).where(Product.tenant_id == tenant_id))}
        branches = {b.id: b.name for b in s.scalars(select(Branch).where(Branch.tenant_id == tenant_id))}
        inventory = list(s.scalars(select(Inventory).where(Inventory.tenant_id == tenant_id)))
        batches = list(s.scalars(select(Batch).where(Batch.tenant_id == tenant_id)))
        in_window = and_(Invoice.purchase_date >= vel_start, Invoice.purchase_date < today_start)
        rows = s.execute(
            select(
                InvoiceLine.product_id,
                Invoice.branch_id,
                func.sum(case((in_window, InvoiceLine.quantity), else_=0.0)),
                func.max(Invoice.purchase_date),
            )
            .join(Invoice, and_(Invoice.tenant_id == InvoiceLine.tenant_id, Invoice.id == InvoiceLine.invoice_id))
            .where(InvoiceLine.tenant_id == tenant_id, Invoice.status == 1)
            .group_by(InvoiceLine.product_id, Invoice.branch_id)
        ).all()

    sales: dict[tuple[int, int | None], Sales] = {}
    for pid, bid, qty, last in rows:
        p = all_products.get(pid)
        base_id, factor = pid, 1.0
        if p is not None and not p.is_base_unit:
            base_id, factor = p.master_unit_id, p.conversion_value or 1.0
        acc = sales.setdefault((base_id, bid), Sales())
        acc.qty_window += (qty or 0) * factor
        if last and (acc.last_sold is None or last > acc.last_sold):
            acc.last_sold = last

    return Snapshot(
        tenant_id=tenant_id,
        tenant_name=tenant.name if tenant else str(tenant_id),
        products={pid: p for pid, p in all_products.items() if _eligible(p)},
        all_products=all_products,
        branches=branches,
        inventory=inventory,
        batches=batches,
        sales=sales,
        data_as_of=last_stock_sync(db, tenant_id),
    )


def _threshold(settings: TenantSettings, category: str | None) -> float | None:
    if category and settings.low_stock_qty_by_category:
        by_cat = {unaccent(k): v for k, v in settings.low_stock_qty_by_category.items()}
        if unaccent(category) in by_cat:
            return by_cat[unaccent(category)]
    return settings.low_stock_qty


def _stock_row(snap: Snapshot, inv: Inventory, settings: TenantSettings, reason: str = "") -> StockRow:
    p = snap.products[inv.product_id]
    sales = snap.sales.get((inv.product_id, inv.branch_id), Sales())
    avg = sales.qty_window / settings.velocity_days if settings.velocity_days else 0.0
    cover = inv.on_hand / avg if avg > 0 else None
    return StockRow(
        product_id=p.id, code=p.code, name=p.name, unit=p.unit, category=p.category,
        branch_id=inv.branch_id, branch_name=snap.branches.get(inv.branch_id, str(inv.branch_id)),
        on_hand=inv.on_hand, min_qty=inv.min_qty, max_qty=inv.max_qty, cost=inv.cost,
        avg_daily=avg, days_cover=cover, last_sold=sales.last_sold, reason=reason,
    )


def low_stock_reason(row: StockRow, settings: TenantSettings) -> str | None:
    threshold = _threshold(settings, row.category)
    if row.on_hand <= 0:
        if row.avg_daily > 0 or row.min_qty or threshold is not None:
            return "Hết hàng"
        return None  # nothing in stock and nobody buys it: not worth a line
    if row.min_qty and row.on_hand < row.min_qty:
        return f"Dưới định mức {fmt_qty(row.min_qty)}"
    if row.min_qty is None and threshold is not None and row.on_hand <= threshold:
        return f"Dưới ngưỡng {fmt_qty(threshold)}"
    if row.days_cover is not None and row.days_cover < settings.low_days_cover:
        return f"Chỉ đủ bán ~{row.days_cover:.0f} ngày"
    return None


def over_stock_reasons(row: StockRow, settings: TenantSettings, today: date) -> list[str]:
    if row.on_hand <= 0:
        return []
    reasons = []
    if row.max_qty and row.on_hand > row.max_qty:
        reasons.append(f"Vượt tối đa {fmt_qty(row.max_qty)}")
    if row.days_cover is not None and row.days_cover > settings.overstock_days_cover:
        reasons.append(f"Đủ bán ~{row.days_cover:.0f} ngày")
    if row.last_sold is None:
        reasons.append(f"Không bán được trong {settings.history_days} ngày qua")
    else:
        idle = (today - row.last_sold.date()).days
        if idle >= settings.no_sale_days:
            reasons.append(f"{idle} ngày chưa bán")
    return reasons


def expiring_rows(snap: Snapshot, today: date, warn_days: int, red_days: int) -> list[ExpiryRow]:
    rows = []
    for b in snap.batches:
        p = snap.products.get(b.product_id)
        if p is None or b.expire_date is None or b.on_hand <= 0:
            continue
        days_left = (b.expire_date - today).days
        if days_left < 0:
            level = "expired"
        elif days_left <= red_days:
            level = "red"
        elif days_left <= warn_days:
            level = "orange"
        else:
            continue
        rows.append(ExpiryRow(
            product_id=p.id, code=p.code, name=p.name, unit=p.unit,
            branch_name=snap.branches.get(b.branch_id, "") if b.branch_id is not None else "",
            batch_name=b.batch_name, expire_date=b.expire_date, days_left=days_left,
            on_hand=b.on_hand, level=level,
        ))
    rows.sort(key=lambda r: (r.days_left, r.name))
    return rows


def build_inventory_report(snap: Snapshot, settings: TenantSettings, today: date) -> InventoryReport:
    report = InventoryReport(
        tenant_id=snap.tenant_id, tenant_name=snap.tenant_name, today=today,
        data_as_of=snap.data_as_of, settings=settings,
        multi_branch=len({inv.branch_id for inv in snap.inventory}) > 1,
    )
    for inv in snap.inventory:
        if inv.product_id not in snap.products:
            continue
        row = _stock_row(snap, inv, settings)
        reason = low_stock_reason(row, settings)
        if reason:
            report.low.append(replace(row, reason=reason))
        over = over_stock_reasons(row, settings, today)
        if over:
            report.over.append(replace(row, reason="; ".join(over)))

    inf = float("inf")
    report.low.sort(key=lambda r: (r.on_hand > 0, r.days_cover if r.days_cover is not None else inf, r.name))
    report.over.sort(key=lambda r: (-r.value, -r.on_hand, r.name))
    report.expiring = expiring_rows(snap, today, settings.expiry_warn_days, settings.expiry_red_days)
    return report


# ---- lookups for bot commands ----------------------------------------------


@dataclass
class ProductDetail:
    product: Product
    stock: list[StockRow]
    batches: list[ExpiryRow]  # every batch in stock, any expiry
    other_units: list[Product]
    data_as_of: datetime | None


def search_products(db: Database, tenant_ids: list[int], query: str, limit: int = 10) -> list[Product]:
    words = tokens(query)
    if not words:
        return []
    with db.session() as s:
        stmt = select(Product).where(
            Product.tenant_id.in_(tenant_ids),
            Product.removed.is_(False),
            Product.is_active.is_(True),
        )
        for w in words:
            stmt = stmt.where(Product.name_norm.contains(w, autoescape=True))
        found = [p for p in s.scalars(stmt) if p.is_base_unit and p.product_type not in SERVICE_TYPES]
    q = unaccent(query)
    found.sort(key=lambda p: (unaccent(p.code) != q, len(p.name), p.name))
    return found[:limit]


def product_detail(db: Database, tenant_id: int, product_id: int, settings: TenantSettings,
                   today: date) -> ProductDetail | None:
    snap = load_snapshot(db, tenant_id, settings, today)
    p = snap.products.get(product_id)
    if p is None:
        return None
    stock = [_stock_row(snap, inv, settings) for inv in snap.inventory if inv.product_id == product_id]
    stock.sort(key=lambda r: r.branch_name)
    far = 100_000
    batches = [r for r in expiring_rows(snap, today, far, settings.expiry_red_days) if r.product_id == product_id]
    others = [q for q in snap.all_products.values()
              if q.master_unit_id == product_id and q.id != product_id and not q.removed]
    return ProductDetail(product=p, stock=stock, batches=batches, other_units=others, data_as_of=snap.data_as_of)
