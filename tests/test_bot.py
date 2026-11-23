"""Bot handlers, scheduler and operator alerts, with stand-ins for Telegram objects."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from telegram.ext import Application

from app.bot import handlers
from app.bot.text_parse import Intent, parse_free_text
from app.config import Settings, TenantSettings
from app.db.models import Chat, Tenant
from app.scheduler import schedule_jobs
from app.services import Services
from tests.fakes import NOW

ADMIN = 999
GROUP = -100123
OWNER = -100777


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies: list[tuple[str, dict]] = []
        self.docs: list[str] = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))

    async def reply_document(self, document, filename=None, **kw):
        self.docs.append(filename)


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.docs: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def send_document(self, chat_id, document, filename=None, **kw):
        assert document[:2] == b"PK"  # xlsx is a zip
        self.docs.append((chat_id, filename))


def update_for(chat_id, text=""):
    msg = FakeMessage(text)
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), effective_message=msg, callback_query=None), msg


def context_for(svc, args=(), bot=None):
    return SimpleNamespace(application=SimpleNamespace(bot_data={"svc": svc}), args=list(args), bot=bot or FakeBot())


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    for mod in ("app.services", "app.bot.handlers", "app.sync.runner"):
        monkeypatch.setattr(f"{mod}.now_local", lambda: NOW)


@pytest.fixture
def svc(db, synced, box, shop):
    settings = Settings(_env_file=None, admin_chat_id=ADMIN)
    service = Services(settings, db, http_factory=shop.http)
    service._box = box  # same key the tenant fixture encrypted with
    with db.session() as s:
        s.add(Chat(chat_id=GROUP, tenant_id=synced, role="tenant"))
        s.add(Chat(chat_id=OWNER, tenant_id=None, role="owner"))
    return service


def run(coro):
    return asyncio.run(coro)


def test_unregistered_chat_is_ignored(svc):
    update, msg = update_for(-1)
    run(handlers.cmd_ton(update, context_for(svc, ["banh"])))
    update2, msg2 = update_for(-1, "tồn bánh quy")
    run(handlers.on_text(update2, context_for(svc)))
    assert msg.replies == [] and msg2.replies == []


def test_id_answers_anywhere(svc):
    update, msg = update_for(-555)
    run(handlers.cmd_id(update, context_for(svc)))
    assert "<code>-555</code>" in msg.replies[0][0]


def test_ton_single_match_shows_detail(svc):
    update, msg = update_for(GROUP)
    run(handlers.cmd_ton(update, context_for(svc, ["banh", "quy"])))
    text = msg.replies[0][0]
    assert "Bánh quy bơ Danisa" in text and "Kho Hà Nội: <b>5</b>" in text and "Lô L3: 4 Hộp" in text


def test_ton_many_matches_offers_buttons(svc, db, synced):
    update, msg = update_for(GROUP)
    run(handlers.cmd_ton(update, context_for(svc, ["k"])))  # keo, khoai, kho...
    text, kw = msg.replies[0]
    assert "chọn một" in text
    data = [row[0].callback_data for row in kw["reply_markup"].inline_keyboard]
    assert all(d.startswith(f"ton:{synced}:") for d in data)


def test_choice_callback_edits_message(svc, synced):
    edited = []

    async def answer():
        pass

    async def edit_message_text(text, **kw):
        edited.append(text)

    update, msg = update_for(GROUP)
    update.callback_query = SimpleNamespace(data=f"ton:{synced}:102", answer=answer, edit_message_text=edit_message_text)
    run(handlers.on_choice(update, context_for(svc)))
    assert "Kẹo dừa Bến Tre" in edited[0]


def test_choice_for_other_npp_is_refused(svc, db):
    with db.session() as s:
        other = Tenant(name="NPP khác", retailer="khac", client_id="x", client_secret_enc="x")
        s.add(other)
        s.flush()
        other_id = other.id
    edited = []

    async def answer():
        pass

    async def edit_message_text(text, **kw):
        edited.append(text)

    update, msg = update_for(GROUP)
    update.callback_query = SimpleNamespace(data=f"ton:{other_id}:102", answer=answer, edit_message_text=edit_message_text)
    run(handlers.on_choice(update, context_for(svc)))
    assert edited == [] and msg.replies == []


def test_free_text_and_hethan(svc):
    update, msg = update_for(GROUP, "Hết hạn 10")
    run(handlers.on_text(update, context_for(svc)))
    text = msg.replies[0][0]
    assert "Lô hết hạn trong 10 ngày" in text and "lô L1" in text and "lô L2" not in text

    update, msg = update_for(GROUP, "hôm nay trời đẹp")
    run(handlers.on_text(update, context_for(svc)))
    assert msg.replies == []


def test_baocao_sends_report_to_this_chat(svc):
    bot = FakeBot()
    update, msg = update_for(GROUP)
    run(handlers.cmd_baocao(update, context_for(svc, bot=bot)))
    assert "Đang lấy số liệu" in msg.replies[0][0]
    assert [c for c, _ in bot.sent] == [GROUP]
    assert "Báo cáo tồn kho · NPP Thử" in bot.sent[0][1]


def test_owner_sees_every_npp(svc, synced):
    assert sorted(svc.recipients(synced)) == sorted([GROUP, OWNER])
    assert [t.id for t in svc.scope_for_chat(OWNER)] == [synced]


def test_scheduled_report_goes_to_group_and_owner(svc, synced):
    from app.scheduler import report_job

    bot = FakeBot()
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"svc": svc}), bot=bot,
                          job=SimpleNamespace(data=synced))
    run(report_job(ctx))
    assert sorted({c for c, _ in bot.sent}) == sorted([GROUP, OWNER])


def test_operator_alert_after_three_failures_and_recovery(svc, synced):
    def broken():  # KiotViet down: the token request fails straight away (no retry)
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))

    bot = FakeBot()
    good = svc._http_factory
    svc._http_factory = broken
    for _ in range(4):
        run(svc.safe_sync(synced, bot))
    alerts = [t for c, t in bot.sent if c == ADMIN]
    assert len(alerts) == 1 and "đang lỗi" in alerts[0]  # once, not on every failure

    svc._http_factory = good
    run(svc.safe_sync(synced, bot))
    alerts = [t for c, t in bot.sent if c == ADMIN]
    assert len(alerts) == 2 and "đã đồng bộ lại" in alerts[1]


def test_schedule_registers_jobs(svc, db, synced):
    with db.session() as s:
        t = s.get(Tenant, synced)
        t.settings_json = TenantSettings(report_times=["07:30", "18:00"]).to_json()
    app = Application.builder().token("123:TEST").build()
    names = schedule_jobs(app, svc)
    assert names == [f"sync:{synced}", f"report:{synced}:07:30", f"report:{synced}:18:00", "maintenance"]


@pytest.mark.parametrize("text,intent", [
    ("tồn bánh quy", Intent("ton", "bánh quy")),
    ("Ton kho keo dua", Intent("ton", "keo dua")),
    ("@TeleOtBot tồn snack", Intent("ton", "snack")),
    ("tồn khoai tây", Intent("ton", "khoai tây")),
    ("hết hạn", Intent("hethan", None)),
    ("sắp hết hạn 45 ngày", Intent("hethan", 45)),
    ("Báo cáo", Intent("baocao")),
    ("tồn", None),
    ("cảm ơn anh", None),
])
def test_parse_free_text(text, intent):
    assert parse_free_text(text) == intent
