"""Products with stock, min/max and batches.

Always a full refresh: stock changes on every sale without touching the product's
modifiedDate, and 500 products are only 5 requests.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Batch, Branch, Inventory, Product
from app.kiotviet.client import KiotVietClient, PageResult
from app.sync.parse import max_qty, min_qty, pick, product_batches, to_float, to_int
from app.textutil import unaccent
from app.timeutil import parse_kv_date

PRODUCT_PARAMS = {
    "includeInventory": "true",
    "includeBatchExpires": "true",
    "includeRemoveIds": "true",
}


async def fetch_products(client: KiotVietClient) -> PageResult:
    return await client.get_all("/products", PRODUCT_PARAMS)


async def fetch_branches(client: KiotVietClient) -> PageResult:
    return await client.get_all("/branches")


def save_branches(session: Session, tenant_id: int, items: list[dict]) -> int:
    existing = {b.id: b for b in session.scalars(select(Branch).where(Branch.tenant_id == tenant_id))}
    for it in items:
        bid = int(it["id"])
        b = existing.get(bid)
        if b is None:
            b = Branch(tenant_id=tenant_id, id=bid)
            session.add(b)
        b.name = pick(it, "branchName", "name", default=str(bid))
    return len(items)


def save_products(session: Session, tenant_id: int, items: list[dict]) -> int:
    """Replace this tenant's stock and batches with `items` (the full product list)."""
    session.execute(delete(Inventory).where(Inventory.tenant_id == tenant_id))
    session.execute(delete(Batch).where(Batch.tenant_id == tenant_id))
    existing = {p.id: p for p in session.scalars(select(Product).where(Product.tenant_id == tenant_id))}

    seen: set[int] = set()
    for it in items:
        pid = int(it["id"])
        if pid in seen:
            continue
        seen.add(pid)
        p = existing.get(pid)
        if p is None:
            p = Product(tenant_id=tenant_id, id=pid)
            session.add(p)
        p.code = pick(it, "code", default="")
        p.name = pick(it, "fullName", "name", default=p.code or str(pid))
        p.name_norm = unaccent(f"{p.name} {p.code}")
        p.unit = pick(it, "unit")
        p.category = pick(it, "categoryName")
        p.product_type = to_int(pick(it, "type", "productType"))
        p.is_active = bool(pick(it, "isActive", default=True))
        p.is_batch_expire = bool(pick(it, "isBatchExpireControl", default=False))
        p.master_unit_id = to_int(pick(it, "masterUnitId"))
        p.conversion_value = to_float(pick(it, "conversionValue"), 1.0) or 1.0
        p.removed = False

        stock: dict[int, Inventory] = {}
        for inv in it.get("inventories") or []:
            bid = to_int(inv.get("branchId"))
            if bid is None:
                continue
            stock[bid] = Inventory(
                tenant_id=tenant_id,
                product_id=pid,
                branch_id=bid,
                on_hand=to_float(pick(inv, "onHand", "onhand")),
                reserved=to_float(inv.get("reserved")),
                min_qty=min_qty(inv),
                max_qty=max_qty(inv),
                cost=to_float(inv.get("cost"), 0.0) or None,
            )
        session.add_all(stock.values())

        for b in product_batches(it):
            if not isinstance(b, dict):
                continue
            session.add(
                Batch(
                    tenant_id=tenant_id,
                    product_id=pid,
                    branch_id=to_int(b.get("branchId")),
                    batch_name=pick(b, "batchName", "fullNameVirgule", default=""),
                    expire_date=parse_kv_date(b.get("expireDate")),
                    on_hand=to_float(pick(b, "onHand", "onhand")),
                )
            )

    for pid, p in existing.items():
        if pid not in seen:
            p.removed = True
    return len(seen)
