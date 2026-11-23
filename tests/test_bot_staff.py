"""Phase 2 bot commands: /nv, /top, /chitieu, file upload, 18:00 ranking."""
import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from app.bot import handlers
from app.config import TenantSettings
from app.db.models import Tenant
from app.scheduler import ranking_job
from tests.conftest import GROUP, OWNER
from tests.tg import FakeBot, context_for, fake_document, update_for

pytestmark = pytest.mark.usefixtures("fixed_clock")


def run(coro):
    return asyncio.run(coro)


def nv(svc, *args, **kw):
    update, msg = update_for(GROUP, **kw)
    run(handlers.cmd_nv(update, context_for(svc, args)))
    return msg


def test_nv_month_detail(svc):
    text = nv(svc, "Tuấn", "tháng", "11").replies[0][0]
    assert "👤 <b>Tuấn</b> · tháng 11/2026 (đến 23/11)" in text
    assert "Doanh thu: <b>4.180.000đ</b> (đã trừ 15.000đ hàng trả)" in text
    assert "Số đơn: 13 · Số cửa hàng: 3" in text
    assert "1. Kẹo dừa Bến Tre: 110 Gói" in text and "Bánh quy bơ Danisa: 34 Hộp" in text
    assert "Chỉ tiêu" not in text  # none loaded yet


def test_nv_defaults_to_this_month_and_handles_misses(svc):
    assert "tháng 11/2026 (đến 23/11)" in nv(svc, "lan").replies[0][0]
    assert "Gõ tên nhân viên" in nv(svc).replies[0][0]
    assert "Không tìm thấy nhân viên “xyz”" in nv(svc, "xyz").replies[0][0]
    assert "Chưa có đơn nào" in nv(svc, "Lan", "Anh").replies[0][0]


def test_nv_ambiguous_then_pick(svc, synced):
    text, kw = nv(svc, "la", "hôm", "nay").replies[0]
    assert "chọn một" in text
    buttons = [row[0] for row in kw["reply_markup"].inline_keyboard]
    assert [b.text for b in buttons] == ["Lan", "Lan Anh"]
    assert buttons[0].callback_data == f"nv:{synced}:8:2026112320261123"

    edited = []

    async def answer():
        pass

    async def edit_message_text(text, **kw):
        edited.append(text)

    update, _ = update_for(GROUP)
    update.callback_query = SimpleNamespace(data=buttons[0].callback_data, answer=answer,
                                            edit_message_text=edit_message_text)
    run(handlers.on_choice(update, context_for(svc)))
    assert "👤 <b>Lan</b> · hôm nay 23/11" in edited[0] and "60.000đ" in edited[0]


def test_free_text_staff(svc):
    update, msg = update_for(GROUP, "Tuấn hôm qua")
    run(handlers.on_text(update, context_for(svc)))
    assert "Doanh thu: <b>315.000đ</b>" in msg.replies[0][0]
    update, msg = update_for(GROUP, "giao hàng hôm nay")  # looks like "<name> <period>", no such person
    run(handlers.on_text(update, context_for(svc)))
    assert msg.replies == []
    update, msg = update_for(GROUP, "top hôm qua")
    run(handlers.on_text(update, context_for(svc)))
    assert "Xếp hạng doanh thu · hôm qua 22/11" in msg.replies[0][0]


def test_top_today_compares_with_yesterday(svc):
    update, msg = update_for(GROUP)
    run(handlers.cmd_top(update, context_for(svc)))
    text = msg.replies[0][0]
    lines = text.split("\n")
    assert lines[0] == "🏆 <b>Xếp hạng doanh thu · hôm nay 23/11</b>"
    assert lines[3] == "🥇 Lan: <b>60.000đ</b> · 1 đơn · 1 cửa hàng (hôm trước 0đ)"
    assert lines[4] == "🥈 Chủ cửa hàng: 20.000đ · 1 đơn (hôm trước 0đ)"
    assert lines[5] == "🥉 Tuấn: 15.000đ · 1 đơn · 1 cửa hàng (hôm trước 315.000đ, ▼ 95%)"
    assert "<b>Tổng: 95.000đ · 3 đơn</b>" in text


def test_top_bad_period(svc):
    update, msg = update_for(GROUP)
    run(handlers.cmd_top(update, context_for(svc, ["abc"])))
    assert "Không hiểu kỳ “abc”" in msg.replies[0][0]


def load_targets_via_bot(svc, rows: dict[str, float]) -> str:
    """/chitieu -> fill the template -> send it back. Returns the bot's answer."""
    chat_data = {}
    bot = FakeBot()
    update, msg = update_for(GROUP)
    run(handlers.cmd_chitieu(update, context_for(svc, bot=bot, chat_data=chat_data)))
    filename, data, caption = msg.docs[0]
    assert filename == "chi-tieu-npp-thu-2026-11.xlsx" and "trong 30 phút" in caption
    wb = load_workbook(BytesIO(data))
    ws = wb["Chỉ tiêu"]
    for row in ws.iter_rows(min_row=2):
        if row[1].value in rows:
            row[5].value = rows[row[1].value]
    buf = BytesIO()
    wb.save(buf)
    update, msg = update_for(GROUP, document=fake_document(buf.getvalue()))
    run(handlers.on_document(update, context_for(svc, bot=bot, chat_data=chat_data)))
    assert "await_targets" not in chat_data  # done waiting
    return msg.replies[0][0]


def test_chitieu_round_trip_and_month_ranking(svc):
    answer = load_targets_via_bot(svc, {"Tuấn": 10_000_000, "Lan": 4_000_000})
    assert answer.startswith("✅ Đã nạp 2 chỉ tiêu tháng 11/2026 cho 2 nhân viên")

    text = nv(svc, "Tuấn").replies[0][0]
    assert "🎯 <b>Chỉ tiêu tháng 11/2026</b> (tiến độ tháng 77%)" in text
    assert "• Doanh thu: 4.180.000đ / 10.000.000đ = <b>42%</b> 🐢 chậm tiến độ" in text

    update, msg = update_for(GROUP)
    run(handlers.cmd_top(update, context_for(svc, ["tháng", "này"])))
    text = msg.replies[0][0]
    assert "🥇 Tuấn: <b>4.180.000đ</b> · 13 đơn · 3 cửa hàng (đạt 42% chỉ tiêu tháng)" in text
    assert "🥈 Lan: 3.513.000đ · 12 đơn · 3 cửa hàng (đạt 88% chỉ tiêu tháng)" in text


def test_chitieu_admin_only(svc):
    update, msg = update_for(GROUP)
    run(handlers.cmd_chitieu(update, context_for(svc, bot=FakeBot("member"))))
    assert msg.replies[0][0] == "Chỉ quản trị viên nhóm được nạp chỉ tiêu." and msg.docs == []
    # a private chat with the bot counts as admin
    update, msg = update_for(GROUP, chat_type="private")
    run(handlers.cmd_chitieu(update, context_for(svc, bot=FakeBot("member"))))
    assert msg.docs


def test_documents_without_chitieu_are_ignored(svc):
    update, msg = update_for(GROUP, document=fake_document(b"PK..."))
    run(handlers.on_document(update, context_for(svc)))
    assert msg.replies == []
    # with the caption, a member still cannot load targets
    update, msg = update_for(GROUP, caption="/chitieu", document=fake_document(b"PK..."))
    run(handlers.on_document(update, context_for(svc, bot=FakeBot("member"))))
    assert msg.replies[0][0] == "Chỉ quản trị viên nhóm được nạp chỉ tiêu."
    # admin with caption, broken file: clear error, plain text
    update, msg = update_for(GROUP, caption="/chitieu", document=fake_document(b"not a workbook"))
    run(handlers.on_document(update, context_for(svc)))
    text, kw = msg.replies[0]
    assert text.startswith("❌ File có lỗi") and "parse_mode" not in kw
    update, msg = update_for(GROUP, caption="/chitieu", document=fake_document(b"x", name="chi-tieu.pdf"))
    run(handlers.on_document(update, context_for(svc)))
    assert "Gửi file Excel .xlsx" in msg.replies[0][0]


def test_daily_ranking_job(svc, synced, db):
    load_targets_via_bot(svc, {"Tuấn": 10_000_000, "Lan": 4_000_000})
    bot = FakeBot()
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"svc": svc}), bot=bot, job=SimpleNamespace(data=synced))
    run(ranking_job(ctx))
    # the owner group sees the top sellers in its daily comparison instead
    assert {c for c, _ in bot.sent} == {GROUP}
    text = bot.sent[0][1]
    assert "🏆 <b>Xếp hạng cuối ngày · hôm nay 23/11/2026</b>" in text
    assert "🐢 <b>Chậm tiến độ tháng 11/2026</b> (tiến độ tháng 77%)" in text
    assert "• Tuấn: doanh thu đạt 42%" in text and "• Lan" not in text

    # owner said staff should not see the ranking: owner group only
    with db.session() as s:
        s.get(Tenant, synced).settings_json = TenantSettings(ranking_to_npp_groups=False).to_json()
    bot = FakeBot()
    ctx.bot = bot
    run(ranking_job(ctx))
    assert {c for c, _ in bot.sent} == {OWNER}


def test_help_lists_staff_commands(svc):
    update, msg = update_for(GROUP)
    run(handlers.cmd_help(update, context_for(svc)))
    assert "/nv tên [kỳ]" in msg.replies[0][0] and "/chitieu" in msg.replies[0][0]
