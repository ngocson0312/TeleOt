"""One sync pass for one NPP: branches (daily), products+stock (every time), invoices (incremental)."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from sqlalchemy import select

from app.config import TenantSettings
from app.db.models import SyncState
from app.db.session import Database
from app.kiotviet.client import KiotVietClient
from app.sync import invoices as inv_sync
from app.sync import products as prod_sync
from app.timeutil import now_local

log = logging.getLogger(__name__)

OVERLAP = timedelta(minutes=10)  # re-read a little before the cursor to absorb clock skew
BRANCHES_EVERY = timedelta(hours=24)


@dataclass
class SyncResult:
    tenant_id: int
    branches: int | None = None
    products: int = 0
    invoices: int = 0
    requests: int = 0
    finished_at: datetime | None = None


def _get_state(db: Database, tenant_id: int, resource: str) -> SyncState | None:
    with db.session() as s:
        return s.get(SyncState, (tenant_id, resource))


def _record(db: Database, tenant_id: int, resource: str, *, ok: bool, at: datetime,
            cursor: datetime | None = None, error: str | None = None) -> None:
    with db.session() as s:
        st = s.get(SyncState, (tenant_id, resource))
        if st is None:
            st = SyncState(tenant_id=tenant_id, resource=resource, consecutive_failures=0, alerted=False)
            s.add(st)
        if ok:
            st.last_success = at
            st.consecutive_failures = 0
            st.last_error = None
            if cursor is not None:
                st.last_modified = cursor
        else:
            st.consecutive_failures = (st.consecutive_failures or 0) + 1
            st.last_error = (error or "")[:1000]


async def _step(db: Database, tenant_id: int, resource: str, at: datetime,
                work: Callable[[], Awaitable[tuple[int, datetime | None]]]) -> int:
    try:
        count, cursor = await work()
    except Exception as e:
        await asyncio.to_thread(_record, db, tenant_id, resource, ok=False, at=at, error=f"{type(e).__name__}: {e}")
        raise
    await asyncio.to_thread(_record, db, tenant_id, resource, ok=True, at=at, cursor=cursor)
    return count


def _save(db: Database, fn, tenant_id: int, items: list[dict]) -> int:
    with db.session() as s:
        return fn(s, tenant_id, items)


async def sync_tenant(db: Database, tenant_id: int, client: KiotVietClient, settings: TenantSettings,
                      *, now: datetime | None = None, force_branches: bool = False) -> SyncResult:
    started = now or now_local()
    result = SyncResult(tenant_id=tenant_id)
    requests_before = client.request_count

    branch_state = await asyncio.to_thread(_get_state, db, tenant_id, "branches")
    if force_branches or branch_state is None or branch_state.last_success is None \
            or started - branch_state.last_success >= BRANCHES_EVERY:
        async def do_branches():
            page = await prod_sync.fetch_branches(client)
            return await asyncio.to_thread(_save, db, prod_sync.save_branches, tenant_id, page.items), None
        result.branches = await _step(db, tenant_id, "branches", started, do_branches)

    async def do_products():
        page = await prod_sync.fetch_products(client)
        return await asyncio.to_thread(_save, db, prod_sync.save_products, tenant_id, page.items), None
    result.products = await _step(db, tenant_id, "products", started, do_products)

    inv_state = await asyncio.to_thread(_get_state, db, tenant_id, "invoices")

    async def do_invoices():
        if inv_state is None or inv_state.last_modified is None:
            start = datetime.combine(started.date() - timedelta(days=settings.history_days), time.min)
            page = await inv_sync.fetch_invoices_by_date(client, start, started)
        else:
            page = await inv_sync.fetch_invoices_modified_since(client, inv_state.last_modified - OVERLAP)
        n = await asyncio.to_thread(_save, db, inv_sync.save_invoices, tenant_id, page.items)
        return n, started
    result.invoices = await _step(db, tenant_id, "invoices", started, do_invoices)

    result.requests = client.request_count - requests_before
    result.finished_at = now_local()
    log.info("Đồng bộ NPP %s xong: %s hàng, %s hóa đơn, %s request",
             tenant_id, result.products, result.invoices, result.requests)
    return result


def last_stock_sync(db: Database, tenant_id: int) -> datetime | None:
    st = _get_state(db, tenant_id, "products")
    return st.last_success if st else None


def failing_states(db: Database, tenant_id: int, threshold: int) -> list[SyncState]:
    with db.session() as s:
        return list(s.scalars(select(SyncState).where(
            SyncState.tenant_id == tenant_id, SyncState.consecutive_failures >= threshold)))
