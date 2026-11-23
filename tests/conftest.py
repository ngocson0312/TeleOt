import asyncio

import pytest

from app.config import TenantSettings
from app.crypto import SecretBox, generate_key
from app.db.models import Tenant
from app.db.session import Database
from app.kiotviet.client import KiotVietClient
from app.sync.runner import sync_tenant
from tests.fakes import NOW, FakeShop


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    return database


@pytest.fixture
def box():
    return SecretBox(generate_key())


@pytest.fixture
def tenant_id(db, box):
    with db.session() as s:
        t = Tenant(name="NPP Thử", retailer="nppthu", client_id="cid", client_secret_enc=box.encrypt("sec"))
        s.add(t)
        s.flush()
        return t.id


@pytest.fixture
def shop():
    return FakeShop()


@pytest.fixture
def synced(db, tenant_id, shop):
    """DB after one full sync from the fake shop at NOW."""
    client = KiotVietClient("nppthu", "cid", "sec", http=shop.http())
    asyncio.run(sync_tenant(db, tenant_id, client, TenantSettings(), now=NOW))
    return tenant_id
