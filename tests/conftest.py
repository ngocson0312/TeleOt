import asyncio

import pytest

from app.config import Settings, TenantSettings
from app.crypto import SecretBox, generate_key
from app.db.models import Chat, Tenant
from app.db.session import Database
from app.kiotviet.client import KiotVietClient
from app.services import Services
from app.sync.runner import sync_tenant
from tests.fakes import NOW, FakeShop

ADMIN = 999  # operator chat
GROUP = -100123  # the NPP's group
OWNER = -100777  # owner group, sees every NPP


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


@pytest.fixture
def fixed_clock(monkeypatch):
    """Bot code reads the real clock; pin it to the fake shop's NOW."""
    for target in ("app.services.now_local", "app.bot.handlers.now_local", "app.sync.runner.now_local"):
        monkeypatch.setattr(target, lambda: NOW)
    monkeypatch.setattr("app.bot.text_parse.today_local", lambda: NOW.date())


@pytest.fixture
def svc(db, synced, box, shop):
    settings = Settings(_env_file=None, admin_chat_id=ADMIN)
    service = Services(settings, db, http_factory=shop.http)
    service._box = box  # same key the tenant fixture encrypted with
    with db.session() as s:
        s.add(Chat(chat_id=GROUP, tenant_id=synced, role="tenant"))
        s.add(Chat(chat_id=OWNER, tenant_id=None, role="owner"))
    return service
