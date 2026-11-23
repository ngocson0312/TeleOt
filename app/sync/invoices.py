"""Invoices: 90 days on first run, then everything modified since the last run
(lastModifiedFrom), so edited and cancelled invoices are picked up too."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Invoice, InvoiceLine
from app.kiotviet.client import KiotVietClient, PageResult
from app.sync.parse import invoice_details, pick, to_float, to_int
from app.timeutil import fmt_kv_datetime, parse_kv_datetime

INVOICE_PARAMS = {"includePayment": "false", "includeInvoiceDelivery": "false"}
CHUNK = 500


async def fetch_invoices_by_date(client: KiotVietClient, start: datetime, end: datetime) -> PageResult:
    params = {**INVOICE_PARAMS, "fromPurchaseDate": fmt_kv_datetime(start), "toPurchaseDate": fmt_kv_datetime(end)}
    return await client.get_all("/invoices", params)


async def fetch_invoices_modified_since(client: KiotVietClient, since: datetime) -> PageResult:
    params = {**INVOICE_PARAMS, "lastModifiedFrom": fmt_kv_datetime(since)}
    return await client.get_all("/invoices", params)


def _chunks(seq: list, n: int = CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def save_invoices(session: Session, tenant_id: int, items: list[dict]) -> int:
    """Upsert invoices and replace their lines."""
    by_id: dict[int, dict] = {}
    for it in items:
        by_id[int(it["id"])] = it  # later copy wins (same invoice on two pages)
    ids = list(by_id)

    existing: dict[int, Invoice] = {}
    for chunk in _chunks(ids):
        for inv in session.scalars(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.id.in_(chunk))):
            existing[inv.id] = inv
        session.execute(
            delete(InvoiceLine).where(InvoiceLine.tenant_id == tenant_id, InvoiceLine.invoice_id.in_(chunk))
        )

    for iid, it in by_id.items():
        purchase = parse_kv_datetime(pick(it, "purchaseDate", "createdDate"))
        if purchase is None:
            continue
        inv = existing.get(iid)
        if inv is None:
            inv = Invoice(tenant_id=tenant_id, id=iid)
            session.add(inv)
        inv.code = pick(it, "code", default="")
        inv.branch_id = to_int(it.get("branchId"))
        inv.sold_by_id = to_int(it.get("soldById"))
        inv.sold_by_name = it.get("soldByName")
        inv.customer_id = to_int(it.get("customerId"))
        inv.purchase_date = purchase
        inv.status = to_int(it.get("status")) or 0
        inv.total = to_float(it.get("total"))
        inv.modified_date = parse_kv_datetime(pick(it, "modifiedDate", "createdDate"))
        for d in invoice_details(it):
            pid = to_int(d.get("productId"))
            if pid is None:
                continue
            session.add(
                InvoiceLine(
                    tenant_id=tenant_id,
                    invoice_id=iid,
                    product_id=pid,
                    quantity=to_float(d.get("quantity")),
                    price=to_float(d.get("price")),
                    discount=to_float(d.get("discount")),
                    sub_total=to_float(d["subTotal"]) if d.get("subTotal") is not None else None,
                )
            )
    return len(by_id)


def prune_invoices(session: Session, tenant_id: int, keep_days: int, today: date) -> int:
    cutoff = datetime.combine(today - timedelta(days=keep_days), time.min)
    old = list(
        session.scalars(select(Invoice.id).where(Invoice.tenant_id == tenant_id, Invoice.purchase_date < cutoff))
    )
    for chunk in _chunks(old):
        session.execute(delete(InvoiceLine).where(InvoiceLine.tenant_id == tenant_id, InvoiceLine.invoice_id.in_(chunk)))
        session.execute(delete(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.id.in_(chunk)))
    return len(old)
