"""Phase 3: is every NPP syncing? (/trangthai, `python -m app.cli status`)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from sqlalchemy import func, or_, select

from app.db.models import Chat, Invoice, Product, SyncState, Tenant
from app.db.session import Database
from app.reports.alerts import MARKER, active_count


@dataclass
class TenantStatus:
    tenant_id: int
    name: str
    active: bool
    last_sync: datetime | None  # last successful products+stock sync
    failing: list[tuple[str, int, str | None]] = field(default_factory=list)  # resource, failures, error
    products: int = 0
    invoices_today: int = 0
    chats: int = 0
    open_alerts: int = 0


def tenant_status(db: Database, tenant: Tenant, now: datetime) -> TenantStatus:
    start = datetime.combine(now.date(), time.min)
    with db.session() as s:
        states = {st.resource: st for st in s.scalars(select(SyncState).where(SyncState.tenant_id == tenant.id))}
        products = s.scalar(select(func.count()).select_from(Product).where(
            Product.tenant_id == tenant.id, Product.removed.is_(False))) or 0
        invoices = s.scalar(select(func.count()).select_from(Invoice).where(
            Invoice.tenant_id == tenant.id, Invoice.status == 1,
            Invoice.purchase_date >= start, Invoice.purchase_date < start + timedelta(days=1))) or 0
        chats = s.scalar(select(func.count()).select_from(Chat).where(
            or_(Chat.tenant_id == tenant.id, Chat.role == "owner"))) or 0
    stock = states.get("products")
    return TenantStatus(
        tenant_id=tenant.id, name=tenant.name, active=tenant.active,
        last_sync=stock.last_success if stock else None,
        failing=[(r, st.consecutive_failures, st.last_error) for r, st in sorted(states.items())
                 if r != MARKER and st.consecutive_failures],
        products=products, invoices_today=invoices, chats=chats, open_alerts=active_count(db, tenant.id),
    )
