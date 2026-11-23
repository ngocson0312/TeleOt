"""Phiếu trả hàng: everything modified since the last run (first run: since history start)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Return, ReturnLine
from app.kiotviet.client import KiotVietClient, PageResult
from app.sync.parse import pick, to_float, to_int
from app.timeutil import fmt_kv_datetime, parse_kv_datetime

CHUNK = 500


async def fetch_returns_modified_since(client: KiotVietClient, since: datetime) -> PageResult:
    return await client.get_all("/returns", {"lastModifiedFrom": fmt_kv_datetime(since)})


def return_details(ret: dict) -> list[dict]:
    raw = ret.get("returnDetails")
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [raw] if "productId" in raw else next((v for v in raw.values() if isinstance(v, list)), [])
    return []


def save_returns(session: Session, tenant_id: int, items: list[dict]) -> int:
    by_id = {int(it["id"]): it for it in items}
    ids = list(by_id)
    existing: dict[int, Return] = {}
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i : i + CHUNK]
        for r in session.scalars(select(Return).where(Return.tenant_id == tenant_id, Return.id.in_(chunk))):
            existing[r.id] = r
        session.execute(delete(ReturnLine).where(ReturnLine.tenant_id == tenant_id, ReturnLine.return_id.in_(chunk)))

    for rid, it in by_id.items():
        when = parse_kv_datetime(pick(it, "returnDate", "createdDate"))
        if when is None:
            continue
        r = existing.get(rid)
        if r is None:
            r = Return(tenant_id=tenant_id, id=rid)
            session.add(r)
        r.code = pick(it, "code", default="")
        r.invoice_id = to_int(it.get("invoiceId"))
        r.branch_id = to_int(it.get("branchId"))
        r.sold_by_id = to_int(it.get("soldById"))
        r.received_by_id = to_int(it.get("receivedById"))
        r.customer_id = to_int(it.get("customerId"))
        r.return_date = when
        r.status = to_int(it.get("status"))
        r.total = to_float(pick(it, "returnTotal", "total", "totalPayment"))
        r.modified_date = parse_kv_datetime(pick(it, "modifiedDate", "createdDate"))
        for d in return_details(it):
            pid = to_int(d.get("productId"))
            if pid is None:
                continue
            session.add(ReturnLine(tenant_id=tenant_id, return_id=rid, product_id=pid,
                                   quantity=to_float(d.get("quantity")), price=to_float(d.get("price"))))
    return len(by_id)


def prune_returns(session: Session, tenant_id: int, keep_days: int, today: date) -> int:
    cutoff = datetime.combine(today - timedelta(days=keep_days), time.min)
    old = list(session.scalars(select(Return.id).where(Return.tenant_id == tenant_id, Return.return_date < cutoff)))
    for i in range(0, len(old), CHUNK):
        chunk = old[i : i + CHUNK]
        session.execute(delete(ReturnLine).where(ReturnLine.tenant_id == tenant_id, ReturnLine.return_id.in_(chunk)))
        session.execute(delete(Return).where(Return.tenant_id == tenant_id, Return.id.in_(chunk)))
    return len(old)
