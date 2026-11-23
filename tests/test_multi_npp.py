"""Phase 3: several NPPs, owner group, urgent alerts, NPP comparison, status, reconcile, backup."""
import asyncio
import sqlite3
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from telegram.ext import Application

from app.bot import handlers
from app.bot.handlers import pick_tenants
from app.bot.text_parse import Intent, parse_free_text
from app.config import ChatSettings, TenantSettings
from app.db.models import AlertState, Chat, SyncState, Tenant
from app.db.session import Database
from app.kiotviet.client import KiotVietClient
from app.reports.periods import Period, parse_period
from app.scheduler import apply_jobs, compare_job, desired_jobs, report_job, schedule_jobs, summary_job
from app.sync.runner import sync_tenant
from tests.conftest import ADMIN, GROUP, OWNER
from tests.fakes import NOW, TODAY, FakeShop
from tests.tg import FakeBot, context_for, update_for

pytestmark = pytest.mark.usefixtures("fixed_clock")
GROUP2 = -100456  # NPP Hải Phòng's group


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def second(db, box, svc):
    """NPP Hải Phòng: the same goods, but only 3 invoices (yesterday Tuấn 315k, day -2 Lan 315k, today Lan 60k)."""
    with db.session() as s:
        t = Tenant(name="NPP Hải Phòng", retailer="npphp", client_id="c2", client_secret_enc=box.encrypt("s2"))
        s.add(t)
        s.flush()
        tid = t.id
        s.add(Chat(chat_id=GROUP2, tenant_id=tid, role="tenant"))
    shop = FakeShop()
    shop.invoices = [i for i in shop.invoices if i["id"] in (1001, 1002, 3001)]
    shop.returns = []
    asyncio.run(sync_tenant(db, tid, KiotVietClient("npphp", "c2", "s2", http=shop.http()), TenantSettings(), now=NOW))
    return tid


def set_chat(db, chat_id, **values):
    with db.session() as s:
        c = s.get(Chat, chat_id)
        cs = ChatSettings.for_chat(c.role, c.settings_json).model_copy(update=values)
        c.settings_json = cs.to_json(c.role)


def set_tenant(db, tenant_id, **values):
    with db.session() as s:
        t = s.get(Tenant, tenant_id)
        t.settings_json = TenantSettings.from_json(t.settings_json).model_copy(update=values).to_json()


def stock(shop, product_id, on_hand, branch=1):
    p = next(p for p in shop.products if p["id"] == product_id)
    next(i for i in p["inventories"] if i["branchId"] == branch)["onHand"] = on_hand


# ---- who gets what --------------------------------------------------------------


def test_owner_group_defaults_and_overrides(svc, db, synced):
    assert ChatSettings.for_chat("owner", None).inventory == "summary"
    assert svc.report_recipients(synced) == [GROUP]
    assert svc.urgent_recipients(synced) == [GROUP]
    assert svc.ranking_recipients(synced) == [GROUP]

    set_chat(db, OWNER, inventory="full", urgent=True, ranking=True)
    assert svc.report_recipients(synced) == [OWNER, GROUP]
    assert svc.urgent_recipients(synced) == [OWNER, GROUP]
    assert svc.ranking_recipients(synced) == [OWNER, GROUP]

    # staff must not see the ranking: NPP groups never, owner group by default
    set_chat(db, OWNER, ranking=None)
    set_tenant(db, synced, ranking_to_npp_groups=False)
    assert svc.ranking_recipients(synced) == [OWNER]
    set_chat(db, GROUP, ranking=False, urgent=False)
    set_tenant(db, synced, ranking_to_npp_groups=True)
    assert svc.ranking_recipients(synced) == [] and svc.urgent_recipients(synced) == [OWNER]
    with db.session() as s:  # only the differences are stored
        assert s.get(Chat, OWNER).settings_json == '{"inventory": "full", "urgent": true}'


# ---- urgent alerts ----------------------------------------------------------------


def test_urgent_alerts_only_new_items(svc, shop, synced, db):
    bot = FakeBot()
    t0 = NOW
    assert run(svc.check_alerts(synced, bot, t0)) == 0  # first check: remember, send nothing
    assert bot.sent == []

    stock(shop, 105, 3)  # Sô cô la: below its min of 10
    run(svc.sync(synced))
    assert run(svc.check_alerts(synced, bot, t0 + timedelta(minutes=15))) == 1
    assert [c for c, _ in bot.sent] == [GROUP]  # the owner group does not get urgent alerts by default
    text = bot.sent[0][1]
    assert "⚡ <b>Cảnh báo mới · NPP Thử</b>" in text and "Sắp hết hàng: 1" in text
    assert "Sô cô la sữa" in text and "Kẹo dừa" not in text  # already bad at the first check
    assert run(svc.check_alerts(synced, bot, t0 + timedelta(minutes=30))) == 0  # not repeated

    stock(shop, 105, 0)  # sold out: a new, worse alert, but found at night
    run(svc.sync(synced))
    assert run(svc.check_alerts(synced, bot, datetime(2026, 11, 23, 22, 0))) == 0
    bot.sent.clear()
    assert run(svc.check_alerts(synced, bot, datetime(2026, 11, 24, 7, 5))) == 1  # after quiet hours
    assert "🚫 Hết hàng: 1" in bot.sent[0][1]

    stock(shop, 105, 100)  # restocked
    run(svc.sync(synced))
    assert run(svc.check_alerts(synced, bot, datetime(2026, 11, 24, 8, 0))) == 0
    stock(shop, 105, 3)  # low again 23h55 after the first alert: cooldown, no new message
    run(svc.sync(synced))
    assert run(svc.check_alerts(synced, bot, datetime(2026, 11, 24, 8, 10))) == 0
    with db.session() as s:
        st = s.get(AlertState, (synced, "low:105:1"))
        assert st.active and st.last_sent == t0 + timedelta(minutes=15)


def test_scheduled_report_counts_as_told(svc, shop, synced):
    bot = FakeBot()
    run(svc.check_alerts(synced, bot, NOW))
    stock(shop, 105, 3)
    run(svc.sync(synced))
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"svc": svc}), bot=bot, job=SimpleNamespace(data=synced))
    run(report_job(ctx))  # the full report lists Sô cô la already
    assert run(svc.check_alerts(synced, bot, NOW + timedelta(minutes=15))) == 0


def test_urgent_alerts_can_be_turned_off(svc, shop, synced, db):
    set_tenant(db, synced, urgent_alerts=False)
    run(svc.check_alerts(synced, None, NOW))
    stock(shop, 105, 3)
    run(svc.sync(synced))
    assert run(svc.check_alerts(synced, FakeBot(), NOW + timedelta(minutes=15))) == 0
    with db.session() as s:
        assert s.get(SyncState, (synced, "alerts")) is None


# ---- comparison and overview ---------------------------------------------------------


def test_compare_two_npps(svc, synced, second):
    today = Period(TODAY, TODAY, "hôm nay 23/11")
    text = "\n".join(svc.comparison_messages(svc.active_tenants(), today))
    first, hp = text.index("NPP Thử"), text.index("NPP Hải Phòng")
    assert first < hp  # by revenue: 95.000 > 60.000
    assert "🥇 <b>NPP Thử</b>: <b>95.000đ</b> (▼ 70% so với hôm trước)" in text
    assert "3 đơn · 2 cửa hàng · 3 người bán" in text
    assert "🥈 <b>NPP Hải Phòng</b>: <b>60.000đ</b> (▼ 81% so với hôm trước)" in text
    assert "Bán nhiều: Lan 60.000đ, Chủ cửa hàng 20.000đ, Tuấn 15.000đ" in text
    assert "<b>Tổng 2 NPP: 155.000đ</b> (▼ 75% so với hôm trước) · 4 đơn" in text
    assert "chưa nạp chỉ tiêu" in text and "📦 1 mặt hàng sắp hết" not in text
    assert "2 mặt hàng sắp hết · 2 lô hết hạn / sắp hết hạn" in text


def test_compare_month_with_targets(svc, db, synced, second):
    from app.db.models import Target
    with db.session() as s:
        s.add(Target(tenant_id=synced, staff_id=7, month="2026-11", kind="revenue", value=10_000_000))
        s.add(Target(tenant_id=synced, staff_id=8, month="2026-11", kind="revenue", value=4_000_000))
    period = parse_period("tháng này", TODAY)
    assert period.previous().label == "cùng kỳ tháng 10"
    text = "\n".join(svc.comparison_messages(svc.active_tenants(), period))
    assert "đạt " in text and "chỉ tiêu (tiến độ 77%)" in text and "1 NV chậm tiến độ" in text


def test_periods_to_compare_with():
    assert Period(date(2026, 11, 1), date(2026, 11, 23), "").previous() == \
        Period(date(2026, 10, 1), date(2026, 10, 23), "cùng kỳ tháng 10")
    assert Period(date(2026, 3, 1), date(2026, 3, 31), "").previous() == \
        Period(date(2026, 2, 1), date(2026, 2, 28), "tháng 2/2026")
    assert Period(date(2026, 3, 1), date(2026, 3, 30), "").previous().end == date(2026, 2, 28)
    assert Period(date(2026, 11, 16), date(2026, 11, 22), "").previous() == \
        Period(date(2026, 11, 9), date(2026, 11, 15), "kỳ trước")


def test_owner_baocao_shows_overview_with_buttons(svc, synced, second):
    update, msg = update_for(OWNER)
    run(handlers.cmd_baocao(update, context_for(svc)))
    text, kw = msg.replies[-1]
    assert "📦 <b>Tồn kho các NPP</b>" in text
    assert "<b>NPP Thử</b>: 🔻 2 sắp hết · ⏰ 3 lô sắp hết hạn · 🐢 3 tồn nhiều" in text
    assert "• <b>Kẹo dừa Bến Tre</b>: hết hàng · Kho Hà Nội" in text
    data = [row[0].callback_data for row in kw["reply_markup"].inline_keyboard]
    assert data == [f"bc:{synced}", f"bc:{second}"]

    async def answer():
        pass

    bot = FakeBot()
    update, msg = update_for(OWNER)
    update.callback_query = SimpleNamespace(data=f"bc:{second}", answer=answer)
    run(handlers.on_choice(update, context_for(svc, bot=bot)))
    assert [c for c, _ in bot.sent] == [OWNER] and "Báo cáo tồn kho · NPP Hải Phòng" in bot.sent[0][1]

    # an NPP group cannot ask for another NPP's report
    bot = FakeBot()
    update, msg = update_for(GROUP)
    update.callback_query = SimpleNamespace(data=f"bc:{second}", answer=answer)
    run(handlers.on_choice(update, context_for(svc, bot=bot)))
    assert bot.sent == []


def test_npp_name_in_owner_commands(svc, synced, second):
    tenants = svc.active_tenants()
    assert [t.name for t in pick_tenants(tenants, "Hải Phòng hôm qua")[0]] == ["NPP Hải Phòng"]
    assert pick_tenants(tenants, "npp hai phong hôm qua")[1] == "hôm qua"
    assert pick_tenants(tenants, "hôm qua") == (tenants, "hôm qua")
    assert pick_tenants(tenants[:1], "Hải Phòng") == (tenants[:1], "Hải Phòng")  # one NPP: nothing to pick

    bot = FakeBot()
    update, msg = update_for(OWNER)
    run(handlers.cmd_baocao(update, context_for(svc, ["hai", "phong"], bot=bot)))
    assert [t for _, t in bot.sent if "Báo cáo tồn kho" in t][0].startswith("📦 <b>Báo cáo tồn kho · NPP Hải Phòng")

    update, msg = update_for(OWNER)
    run(handlers.cmd_baocao(update, context_for(svc, ["Đà", "Nẵng"])))
    assert "Không có NPP “Đà Nẵng”. Các NPP: NPP Thử, NPP Hải Phòng" in msg.replies[0][0]

    update, msg = update_for(OWNER)
    run(handlers.cmd_top(update, context_for(svc, ["Hải", "Phòng", "hôm", "qua"])))
    text = "\n".join(t for t, _ in msg.replies)
    assert "NPP Hải Phòng" in text and "NPP Thử" not in text and "Tuấn: <b>315.000đ</b>" in text

    update, msg = update_for(OWNER)
    run(handlers.cmd_hethan(update, context_for(svc, ["Hải", "Phòng", "10"])))
    assert len(msg.replies) == 1 and "Lô hết hạn trong 10 ngày · NPP Hải Phòng" in msg.replies[0][0]


def test_sosanh_and_help_in_chats(svc, synced, second):
    update, msg = update_for(OWNER, "so sánh hôm nay")
    run(handlers.on_text(update, context_for(svc)))
    assert "So sánh NPP · hôm nay 23/11" in msg.replies[0][0] and "NPP Hải Phòng" in msg.replies[0][0]

    update, msg = update_for(GROUP)  # an NPP group only ever sees its own NPP
    run(handlers.cmd_sosanh(update, context_for(svc, ["tháng", "này"])))
    assert "NPP Thử" in msg.replies[0][0] and "Hải Phòng" not in msg.replies[0][0]

    update, msg = update_for(OWNER)
    run(handlers.cmd_sosanh(update, context_for(svc, ["abc"])))
    assert "Không hiểu kỳ" in msg.replies[0][0]

    update, msg = update_for(OWNER)
    run(handlers.cmd_help(update, context_for(svc)))
    assert "/sosanh" in msg.replies[0][0]
    update, msg = update_for(GROUP)
    run(handlers.cmd_help(update, context_for(svc)))
    assert "/sosanh" not in msg.replies[0][0]


def test_owner_daily_jobs(svc, synced, second):
    bot = FakeBot()
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"svc": svc}), bot=bot, job=SimpleNamespace(data=OWNER))
    run(summary_job(ctx))
    run(compare_job(ctx))
    assert [c for c, _ in bot.sent] == [OWNER, OWNER]
    assert "Tồn kho các NPP" in bot.sent[0][1] and "So sánh NPP · hôm nay 23/11/2026" in bot.sent[1][1]


# ---- status ---------------------------------------------------------------------


def test_trangthai(svc, db, synced, second):
    with db.session() as s:
        s.get(Tenant, second).active = False
        st = s.get(SyncState, (synced, "invoices"))
        st.consecutive_failures, st.last_error = 4, "KiotVietAuthError: sai mã"
    update, msg = update_for(ADMIN, chat_type="private")  # operator: not registered, sees every NPP
    run(handlers.cmd_trangthai(update, context_for(svc)))
    text = msg.replies[0][0]
    assert "🚨 <b>NPP Thử</b> (id 1): đồng bộ lúc 08:00 23/11 (0 phút trước)" in text
    assert "✘ invoices: lỗi 4 lần liên tiếp. KiotVietAuthError: sai mã" in text
    assert "7 mặt hàng · 3 đơn hôm nay · 2 nhóm chat · 0 cảnh báo đang mở" in text
    assert "⏸ <b>NPP Hải Phòng</b> (id 2): tạm dừng" in text

    update, msg = update_for(GROUP, "trạng thái")
    run(handlers.on_text(update, context_for(svc)))
    assert "NPP Thử" in msg.replies[0][0] and "Hải Phòng" not in msg.replies[0][0]

    update, msg = update_for(-1)  # unknown chat: nothing
    run(handlers.cmd_trangthai(update, context_for(svc)))
    assert msg.replies == []


# ---- scheduler, backup, upgrade -----------------------------------------------------------


def test_reconcile_follows_the_database(svc, db, synced, box):
    app = Application.builder().token("123:TEST").build()
    schedule_jobs(app, svc)
    with db.session() as s:
        t = Tenant(name="NPP Mới", retailer="moi", client_id="x", client_secret_enc=box.encrypt("y"))
        s.add(t)
        s.flush()
        new_id = t.id
    set_tenant(db, synced, report_times=["09:00"], ranking_time=None)
    set_chat(db, OWNER, compare_time=None, summary_times=["07:00", "17:00"])
    names = apply_jobs(app, desired_jobs(svc), startup=False)
    jobs = {j.name for j in app.job_queue.jobs() if not j.removed}
    assert jobs == set(names) | {"maintenance", "reconcile"}
    assert f"report:{synced}:09:00" in jobs and f"report:{synced}:08:00" not in jobs
    assert f"ranking:{synced}:18:00" not in jobs and f"compare:{OWNER}:18:30" not in jobs
    assert {f"summary:{OWNER}:07:00", f"summary:{OWNER}:17:00", f"sync:{new_id}:15m"} <= jobs

    with db.session() as s:
        s.get(Tenant, new_id).active = False
    apply_jobs(app, desired_jobs(svc), startup=False)
    left = [j.name for j in app.job_queue.jobs() if not j.removed]
    assert not [n for n in left if n.startswith(("sync:", "report:", "ranking:")) and n.split(":")[1] == str(new_id)]
    assert f"sync:{synced}:15m" in left


def test_backup_keeps_last_copies(tmp_path, db, synced):
    out = tmp_path / "backup"
    for day in (20, 21, 22):
        db.backup(out, keep=2, today=date(2026, 11, day))
    assert sorted(p.name for p in out.iterdir()) == ["teleot-20261121.db", "teleot-20261122.db"]
    con = sqlite3.connect(out / "teleot-20261122.db")
    assert con.execute("select name from tenants").fetchone() == ("NPP Thử",)
    con.close()
    assert Database("sqlite:///:memory:").backup(out, 2, date(2026, 11, 23)) is None


def test_old_chats_table_gets_settings_column(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE chats (chat_id BIGINT PRIMARY KEY, tenant_id INTEGER, role VARCHAR(20), "
                "title VARCHAR(200))")
    con.execute("INSERT INTO chats VALUES (-5, NULL, 'owner', NULL)")
    con.commit()
    con.close()
    Database(f"sqlite:///{path.as_posix()}").create_all()
    con = sqlite3.connect(path)
    assert con.execute("SELECT role, settings_json FROM chats").fetchone() == ("owner", None)
    assert con.execute("SELECT count(*) FROM sqlite_master WHERE name = 'alert_state'").fetchone()[0] == 1


@pytest.mark.parametrize("text,intent", [
    ("so sánh tháng này", Intent("sosanh", "tháng này")),
    ("So sanh NPP", Intent("sosanh", "")),
    ("so sánh giá kẹo", None),
    ("Trạng thái", Intent("trangthai")),
])
def test_parse_owner_free_text(text, intent):
    assert parse_free_text(text, TODAY) == intent


def test_quiet_hours_setting():
    from app.config import in_window
    from datetime import time
    assert TenantSettings(quiet_hours="22:00 - 6:30").quiet_hours == "22:00-06:30"
    assert TenantSettings(quiet_hours="off").quiet_hours is None
    assert in_window("21:00-07:00", time(23)) and in_window("21:00-07:00", time(6, 59))
    assert not in_window("21:00-07:00", time(7)) and not in_window(None, time(23))
    with pytest.raises(ValueError):
        TenantSettings(quiet_hours="21:00")
