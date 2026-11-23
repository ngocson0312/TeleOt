import asyncio
from datetime import timedelta

from sqlalchemy import func, select

from app.config import TenantSettings
from app.db.models import Batch, Branch, Inventory, Invoice, InvoiceLine, Product, SyncState
from app.kiotviet.client import KiotVietClient
from app.sync.invoices import prune_invoices
from app.sync.runner import sync_tenant
from tests.fakes import NOW, TODAY


def test_full_sync_stores_everything(db, synced, shop):
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Branch)) == 2
        assert s.scalar(select(func.count()).select_from(Product)) == 7
        inv = s.get(Inventory, (synced, 101, 1))
        assert (inv.on_hand, inv.min_qty, inv.max_qty) == (5, 20, None)  # 999999999 = not set
        assert s.get(Inventory, (synced, 102, 2)).max_qty == 200
        thung = s.get(Product, (synced, 103))
        assert thung.master_unit_id == 101 and thung.conversion_value == 12 and not thung.is_base_unit
        assert s.get(Product, (synced, 101)).name_norm == "banh quy bo danisa bq01"
        assert s.scalar(select(func.count()).select_from(Batch)) == 5
        assert s.scalar(select(func.count()).select_from(Invoice)) == 32
        # invoiceDetails given as a single object is still read
        assert s.scalar(select(func.count()).select_from(InvoiceLine).where(InvoiceLine.invoice_id == 2001)) == 1
        st = s.get(SyncState, (synced, "invoices"))
        assert st.last_modified == NOW and st.consecutive_failures == 0
    # Only GETs to the API, first invoice sync by purchase date
    inv_calls = [r for r in shop.requests if r.url.path == "/invoices"]
    assert "fromPurchaseDate" in inv_calls[0].url.params


def test_second_sync_is_incremental_and_marks_removed(db, synced, shop):
    shop.products = [p for p in shop.products if p["id"] != 104]
    shop.invoices[0]["status"] = 2  # cancelled after the fact
    shop.invoices[0]["modifiedDate"] = (NOW + timedelta(minutes=20)).isoformat()
    shop.requests.clear()

    client = KiotVietClient("nppthu", "cid", "sec", http=shop.http())
    result = asyncio.run(sync_tenant(db, synced, client, TenantSettings(), now=NOW + timedelta(minutes=30)))

    inv_call = next(r for r in shop.requests if r.url.path == "/invoices")
    assert inv_call.url.params["lastModifiedFrom"] == "2026-11-23T07:50:00"  # cursor - 10 min overlap
    assert result.branches is None  # branches only once a day
    assert result.invoices == 1
    with db.session() as s:
        assert s.get(Product, (synced, 104)).removed
        assert s.get(Invoice, (synced, shop.invoices[0]["id"])).status == 2
        assert s.scalar(select(func.count()).select_from(Invoice)) == 32


def test_failed_sync_is_recorded(db, tenant_id):
    import httpx

    def broken(request):
        return httpx.Response(400, json={"error": "invalid_client"})

    client = KiotVietClient("nppthu", "cid", "bad", http=httpx.AsyncClient(transport=httpx.MockTransport(broken)))
    for _ in range(2):
        try:
            asyncio.run(sync_tenant(db, tenant_id, client, TenantSettings(), now=NOW))
        except Exception:
            pass
    with db.session() as s:
        st = s.get(SyncState, (tenant_id, "branches"))
        assert st.consecutive_failures == 2
        assert "invalid_client" in st.last_error
        assert "bad" not in st.last_error  # secret never stored


def test_prune_old_invoices(db, synced):
    with db.session() as s:
        removed = prune_invoices(s, synced, keep_days=10, today=TODAY)
    assert removed == 20  # days -11..-30
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Invoice)) == 12
