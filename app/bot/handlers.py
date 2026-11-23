"""Telegram commands (section 6 of the plan): /ton, /hethan, /baocao, /nv, /top, /chitieu, /help, /id,
and for the owner group (phase 3): /sosanh, /trangthai, an NPP name in /baocao, /hethan, /top."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.bot.formatting import HELP_TEXT, format_expiring, format_product_detail
from app.bot.owner_format import OWNER_HELP
from app.bot.staff_format import STAFF_HELP
from app.bot.text_parse import parse_free_text
from app.db.models import Tenant
from app.reports.excel import expiring_workbook, report_filename
from app.reports.inventory import expiring_rows, load_snapshot, product_detail, search_products
from app.reports.periods import Period, describe, parse_period, split_name_period, this_month
from app.reports.staff import search_staff, staff_names
from app.reports.targets import import_targets, parse_month, summary_text, template_workbook
from app.services import Services
from app.textutil import esc, unaccent
from app.timeutil import now_local

log = logging.getLogger(__name__)

MAX_CHOICES = 10
TARGET_WAIT = timedelta(minutes=30)  # after /chitieu, the next .xlsx from that person is the targets file
MAX_FILE = 5 * 1024 * 1024
TG_CONNECT_TIMEOUT, TG_READ_TIMEOUT, TG_WRITE_TIMEOUT = 15, 30, 60  # seconds
FULL_HELP = HELP_TEXT + "\n\n" + STAFF_HELP
COMMANDS = [
    BotCommand("ton", "Tồn kho một mặt hàng: /ton banh quy"),
    BotCommand("hethan", "Lô sắp hết hạn: /hethan 60"),
    BotCommand("baocao", "Gửi ngay báo cáo tồn kho"),
    BotCommand("nv", "Thống kê nhân viên: /nv Tuấn tháng 11"),
    BotCommand("top", "Xếp hạng nhân viên: /top hôm nay"),
    BotCommand("chitieu", "Nạp chỉ tiêu tháng từ file Excel"),
    BotCommand("sosanh", "So sánh các NPP: /sosanh tháng này"),
    BotCommand("trangthai", "Tình trạng đồng bộ KiotViet"),
    BotCommand("help", "Hướng dẫn"),
    BotCommand("id", "Xem mã chat"),
]


def _svc(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.application.bot_data["svc"]


async def _scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> list[Tenant] | None:
    chat = update.effective_chat
    if chat is None:
        return None
    tenants = await asyncio.to_thread(_svc(context).scope_for_chat, chat.id)
    if tenants is None:
        log.info("Bỏ qua chat chưa được cấp quyền: %s", chat.id)
    return tenants


async def _reply(update: Update, text: str, **kw) -> None:
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, **kw)


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Private chats: yes. Groups: only the group's owner and administrators."""
    chat, user = update.effective_chat, update.effective_user
    if chat.type == ChatType.PRIVATE:
        return True
    if user is None:
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except TelegramError:
        log.exception("Không kiểm tra được quyền quản trị trong chat %s", chat.id)
        return False
    return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)


def pick_tenants(tenants: list[Tenant], text: str) -> tuple[list[Tenant], str]:
    """'Hà Nội tháng 11' in a chat that sees several NPPs -> ([NPP Hà Nội], 'tháng 11').
    The NPP name must open the text, with or without the word "NPP". No match: all NPPs."""
    words = text.split()
    if len(tenants) < 2 or not words:
        return tenants, text.strip()
    norm = [unaccent(w) for w in words]
    best_len, best = 0, []
    for t in tenants:
        name = unaccent(t.name).split()
        variants = [name] + ([name[1:]] if len(name) > 1 and name[0] == "npp" else [])
        for v in variants:
            n = len(v)
            if n and norm[:n] == v:
                if n > best_len:
                    best_len, best = n, [t]
                elif n == best_len and t not in best:
                    best.append(t)
    if not best:
        return tenants, text.strip()
    return best, " ".join(words[best_len:])


def _npp_list(tenants: list[Tenant]) -> str:
    return ", ".join(esc(t.name) for t in tenants)


# ---- commands -------------------------------------------------------------


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answers in any chat so the operator can register it. Reveals only the chat's own id."""
    chat = update.effective_chat
    await _reply(update, f"Mã chat này: <code>{chat.id}</code>\n"
                         f"Người vận hành cấp quyền bằng: <code>python -m app.cli chat-add {chat.id} --tenant ID</code>")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tenants = await _scope(update, context)
    if tenants is None:  # /start in a chat without access yet: say how to get it instead of staying silent
        chat_id = update.effective_chat.id
        await _reply(update, f"👋 Chat này chưa được cấp quyền dùng bot.\nMã chat: <code>{chat_id}</code>\n"
                             f"Người vận hành cấp quyền bằng: <code>python -m app.cli chat-add {chat_id} --tenant ID</code>")
        return
    await _reply(update, _help(tenants))


def _help(tenants: list[Tenant]) -> str:
    return FULL_HELP + ("\n\n" + OWNER_HELP if len(tenants) > 1 else "")


async def cmd_ton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tenants = await _scope(update, context)
    if tenants is None:
        return
    await do_ton(update, context, tenants, " ".join(context.args or []))


async def cmd_hethan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tenants = await _scope(update, context)
    if tenants is None:
        return
    tenants, rest = pick_tenants(tenants, " ".join(context.args or []))
    days = None
    if rest:
        if not rest.isdigit():
            await _reply(update, "Gõ số ngày, ví dụ: <code>/hethan 60</code>")
            return
        days = int(rest)
    await do_hethan(update, context, tenants, days)


async def cmd_baocao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tenants = await _scope(update, context)
    if tenants is None:
        return
    await do_baocao(update, context, tenants, " ".join(context.args or []))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None or not msg.text:
        return
    intent = parse_free_text(msg.text)
    if intent is None:
        return
    tenants = await _scope(update, context)
    if tenants is None:
        return
    if intent.kind == "ton":
        await do_ton(update, context, tenants, str(intent.arg))
    elif intent.kind == "hethan":
        await do_hethan(update, context, tenants, intent.arg)
    elif intent.kind == "baocao":
        await do_baocao(update, context, tenants)
    elif intent.kind == "help":
        await _reply(update, _help(tenants))
    elif intent.kind == "sosanh":
        await do_sosanh(update, context, tenants, str(intent.arg or ""))
    elif intent.kind == "trangthai":
        await do_trangthai(update, context, tenants)
    elif intent.kind == "top":
        await do_top(update, context, tenants, str(intent.arg or ""))
    elif intent.kind == "nv":
        await do_nv(update, context, tenants, str(intent.arg))
    elif intent.kind == "maybe_nv":  # "Tuấn tháng 11": stay quiet unless it really is a staff name
        await do_nv(update, context, tenants, str(intent.arg), quiet=True)


# ---- actions ----------------------------------------------------------------


async def do_ton(update: Update, context: ContextTypes.DEFAULT_TYPE, tenants: list[Tenant], query: str) -> None:
    svc = _svc(context)
    if not query.strip():
        await _reply(update, "Gõ tên hoặc mã hàng, ví dụ: <code>/ton banh quy</code>")
        return
    if not tenants:
        await _reply(update, "Chat này chưa gắn với NPP nào đang hoạt động.")
        return
    found = await asyncio.to_thread(search_products, svc.db, [t.id for t in tenants], query, MAX_CHOICES + 1)
    if not found:
        await _reply(update, f"Không tìm thấy mặt hàng nào khớp “{esc(query)}”.")
        return
    if len(found) == 1:
        p = found[0]
        await _send_detail(update, context, tenants, p.tenant_id, p.id)
        return
    names = {t.id: t.name for t in tenants}
    buttons = []
    for p in found[:MAX_CHOICES]:
        label = f"{p.name} ({p.code})"
        if len(tenants) > 1:
            label = f"{names.get(p.tenant_id, '')}: {label}"
        buttons.append([InlineKeyboardButton(label[:64], callback_data=f"ton:{p.tenant_id}:{p.id}")])
    more = "\nChỉ hiện 10 kết quả đầu, gõ cụ thể hơn để lọc." if len(found) > MAX_CHOICES else ""
    await _reply(update, f"Có nhiều mặt hàng khớp “{esc(query)}”, chọn một:{more}",
                 reply_markup=InlineKeyboardMarkup(buttons))


async def _send_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, tenants: list[Tenant],
                       tenant_id: int, product_id: int, *, edit: bool = False) -> None:
    svc = _svc(context)
    tenant = next((t for t in tenants if t.id == tenant_id), None)
    if tenant is None:
        return
    detail = await asyncio.to_thread(product_detail, svc.db, tenant_id, product_id,
                                     svc.tenant_settings(tenant), now_local().date())
    if detail is None:
        await _reply(update, "Mặt hàng này không còn trên KiotViet.")
        return
    label = tenant.name if len(tenants) > 1 else None
    messages = format_product_detail(detail, now_local(), label)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(messages[0], parse_mode=ParseMode.HTML)
        messages = messages[1:]
    for m in messages:
        await _reply(update, m)


async def on_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    tenants = await _scope(update, context)
    if tenants is None:
        return
    try:
        kind, *parts = q.data.split(":")
        if kind == "ton":
            tid, pid = parts
            await _send_detail(update, context, tenants, int(tid), int(pid), edit=True)
        elif kind == "nv":
            tid, sid, code = parts
            period = Period.from_code(code, now_local().date())
            await _send_staff(update, context, tenants, int(tid), int(sid), period, edit=True)
        elif kind == "bc":
            (tid,) = parts
            if any(t.id == int(tid) for t in tenants):
                svc = _svc(context)
                await svc.sync_if_stale(int(tid), context.bot)
                await svc.send_inventory_report(context.bot, int(tid), [update.effective_chat.id])
        elif kind == "ct":
            tid, month = parts
            tenant = next((t for t in tenants if t.id == int(tid)), None)
            if tenant and await _is_admin(update, context):
                await _send_template(update, context, tenant, month)
    except ValueError:
        log.warning("callback_data lạ: %r", q.data)


async def do_hethan(update: Update, context: ContextTypes.DEFAULT_TYPE, tenants: list[Tenant],
                    days: int | None) -> None:
    svc = _svc(context)
    if not tenants:
        await _reply(update, "Chat này chưa gắn với NPP nào đang hoạt động.")
        return
    now = now_local()
    for t in tenants:
        ts = svc.tenant_settings(t)
        d = min(max(days or ts.expiry_warn_days, 1), 730)

        def load():
            snap = load_snapshot(svc.db, t.id, ts, now.date())
            multi = len({i.branch_id for i in snap.inventory}) > 1
            return expiring_rows(snap, now.date(), d, ts.expiry_red_days), multi, snap.data_as_of

        rows, multi, as_of = await asyncio.to_thread(load)
        messages, truncated = format_expiring(t.name, rows, d, ts.expiry_red_days, multi, as_of, now)
        for m in messages:
            await _reply(update, m)
        if truncated:
            await update.effective_message.reply_document(
                expiring_workbook(rows), filename=report_filename(t.name, "het-han", now))


async def do_baocao(update: Update, context: ContextTypes.DEFAULT_TYPE, tenants: list[Tenant],
                    text: str = "") -> None:
    svc = _svc(context)
    if not tenants:
        await _reply(update, "Chat này chưa gắn với NPP nào đang hoạt động.")
        return
    picked, rest = pick_tenants(tenants, text)
    if rest and len(tenants) > 1 and len(picked) == len(tenants):
        await _reply(update, f"Không có NPP “{esc(rest)}”. Các NPP: {_npp_list(tenants)}")
        return
    if len(picked) > 1:  # owner group: one overview, full reports on request
        messages = await asyncio.to_thread(svc.overview_messages, picked)
        buttons = [[InlineKeyboardButton(f"Báo cáo đầy đủ: {t.name}"[:64], callback_data=f"bc:{t.id}")]
                   for t in picked]
        for m in messages[:-1]:
            await _reply(update, m)
        await _reply(update, messages[-1], reply_markup=InlineKeyboardMarkup(buttons))
        return
    tenants = picked
    try:  # only a "please wait": a slow Telegram here must not cost the report itself
        await _reply(update, "⏳ Đang lấy số liệu mới từ KiotViet…")
    except TelegramError as e:
        log.warning("Không gửi được tin chờ: %s", e)
    for t in tenants:
        await svc.sync_if_stale(t.id, context.bot)
        await svc.send_inventory_report(context.bot, t.id, [update.effective_chat.id])


# ---- staff (phase 2) ---------------------------------------------------------------


async def cmd_nv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tenants = await _scope(update, context)
    if tenants is None:
        return
    await do_nv(update, context, tenants, " ".join(context.args or []))


async def do_nv(update: Update, context: ContextTypes.DEFAULT_TYPE, tenants: list[Tenant], text: str,
                *, quiet: bool = False) -> None:
    svc = _svc(context)
    today = now_local().date()
    name, period = split_name_period(text, today)
    period = period or this_month(today)
    if not name.strip():
        if not quiet:
            await _reply(update, "Gõ tên nhân viên, ví dụ: <code>/nv Tuấn tháng 11</code>")
        return
    if not tenants:
        if not quiet:
            await _reply(update, "Chat này chưa gắn với NPP nào đang hoạt động.")
        return
    found = await asyncio.to_thread(search_staff, svc.db, [t.id for t in tenants], name, MAX_CHOICES + 1)
    if not found:
        if not quiet:
            await _reply(update, f"Không tìm thấy nhân viên “{esc(name)}”.")
        return
    if len(found) == 1:
        await _send_staff(update, context, tenants, found[0].tenant_id, found[0].staff_id, period)
        return
    names = {t.id: t.name for t in tenants}
    buttons = []
    for m in found[:MAX_CHOICES]:
        label = f"{names.get(m.tenant_id, '')}: {m.name}" if len(tenants) > 1 else m.name
        buttons.append([InlineKeyboardButton(
            label[:64], callback_data=f"nv:{m.tenant_id}:{m.staff_id}:{period.code}")])
    await _reply(update, f"Có nhiều nhân viên khớp “{esc(name)}”, chọn một:",
                 reply_markup=InlineKeyboardMarkup(buttons))


async def _send_staff(update: Update, context: ContextTypes.DEFAULT_TYPE, tenants: list[Tenant],
                      tenant_id: int, staff_id: int, period: Period, *, edit: bool = False) -> None:
    svc = _svc(context)
    tenant = next((t for t in tenants if t.id == tenant_id), None)
    if tenant is None:
        return

    def build() -> list[str]:
        name = staff_names(svc.db, tenant_id).get(staff_id, f"Nhân viên {staff_id}")
        label = tenant.name if len(tenants) > 1 else None
        return svc.staff_detail_messages(tenant, staff_id, name, period, label)

    messages = await asyncio.to_thread(build)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(messages[0], parse_mode=ParseMode.HTML)
        messages = messages[1:]
    for m in messages:
        await _reply(update, m)


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tenants = await _scope(update, context)
    if tenants is None:
        return
    await do_top(update, context, tenants, " ".join(context.args or []))


async def do_top(update: Update, context: ContextTypes.DEFAULT_TYPE, tenants: list[Tenant], text: str) -> None:
    svc = _svc(context)
    tenants, text = pick_tenants(tenants, text)
    today = now_local().date()
    period = parse_period(text, today) if text.strip() else Period(today, today, describe(today, today, today))
    if period is None:
        await _reply(update, f"Không hiểu kỳ “{esc(text)}”. Ví dụ: <code>/top hôm nay</code>, "
                             "<code>/top tuần này</code>, <code>/top tháng 11</code>")
        return
    if not tenants:
        await _reply(update, "Chat này chưa gắn với NPP nào đang hoạt động.")
        return
    for t in tenants:
        for m in await asyncio.to_thread(svc.ranking_messages, t, period):
            await _reply(update, m)


# ---- owner group (phase 3) ---------------------------------------------------


async def cmd_sosanh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tenants = await _scope(update, context)
    if tenants is None:
        return
    await do_sosanh(update, context, tenants, " ".join(context.args or []))


async def do_sosanh(update: Update, context: ContextTypes.DEFAULT_TYPE, tenants: list[Tenant], text: str) -> None:
    today = now_local().date()
    period = parse_period(text, today) if text.strip() else Period(today, today, describe(today, today, today))
    if period is None:
        await _reply(update, f"Không hiểu kỳ “{esc(text)}”. Ví dụ: <code>/sosanh hôm qua</code>, "
                             "<code>/sosanh tháng này</code>")
        return
    if not tenants:
        await _reply(update, "Chat này chưa gắn với NPP nào đang hoạt động.")
        return
    for m in await asyncio.to_thread(_svc(context).comparison_messages, tenants, period):
        await _reply(update, m)


async def cmd_trangthai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = _svc(context)
    chat = update.effective_chat
    if chat is not None and svc.settings.admin_chat_id == chat.id:  # operator: every NPP, paused ones too
        tenants = await asyncio.to_thread(svc.all_tenants)
    else:
        tenants = await _scope(update, context)
        if tenants is None:
            return
    await do_trangthai(update, context, tenants)


async def do_trangthai(update: Update, context: ContextTypes.DEFAULT_TYPE, tenants: list[Tenant]) -> None:
    for m in await asyncio.to_thread(_svc(context).status_messages, tenants):
        await _reply(update, m)


async def cmd_chitieu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tenants = await _scope(update, context)
    if tenants is None:
        return
    if not await _is_admin(update, context):
        await _reply(update, "Chỉ quản trị viên nhóm được nạp chỉ tiêu.")
        return
    today = now_local().date()
    month = parse_month(context.args[0], today) if context.args else f"{today:%Y-%m}"
    if month is None:
        await _reply(update, "Ghi tháng dạng 11/2026, ví dụ: <code>/chitieu 12/2026</code>")
        return
    if not tenants:
        await _reply(update, "Chat này chưa gắn với NPP nào đang hoạt động.")
        return
    if len(tenants) > 1:
        buttons = [[InlineKeyboardButton(t.name[:64], callback_data=f"ct:{t.id}:{month}")] for t in tenants]
        await _reply(update, "Nạp chỉ tiêu cho NPP nào?", reply_markup=InlineKeyboardMarkup(buttons))
        return
    await _send_template(update, context, tenants[0], month)


async def _send_template(update: Update, context: ContextTypes.DEFAULT_TYPE, tenant: Tenant, month: str) -> None:
    svc = _svc(context)
    data = await asyncio.to_thread(template_workbook, svc.db, tenant.id, month)
    context.chat_data["await_targets"] = {
        "user": update.effective_user.id, "tenant": tenant.id, "until": now_local() + TARGET_WAIT}
    slug = "-".join(unaccent(tenant.name).split())
    await update.effective_message.reply_document(
        data, filename=f"chi-tieu-{slug}-{month}.xlsx",
        caption=(f"Mẫu chỉ tiêu tháng {month[5:]}/{month[:4]} · {tenant.name}. Điền cột Chỉ tiêu rồi gửi lại "
                 "file vào đây trong 30 phút (hoặc gửi bất cứ lúc nào, kèm ghi chú /chitieu)."))


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    doc = msg.document if msg else None
    if doc is None:
        return
    user = update.effective_user
    wait = context.chat_data.get("await_targets")
    by_caption = unaccent(msg.caption or "").startswith("/chitieu")
    by_wait = bool(wait and user and wait["user"] == user.id and now_local() <= wait["until"])
    if not (by_caption or by_wait):
        return  # any other file in the group: not for us
    tenants = await _scope(update, context)
    if tenants is None:
        return
    if not await _is_admin(update, context):
        await _reply(update, "Chỉ quản trị viên nhóm được nạp chỉ tiêu.")
        return
    if not (doc.file_name or "").lower().endswith(".xlsx"):
        await _reply(update, "Gửi file Excel .xlsx theo mẫu (gõ /chitieu để lấy mẫu).")
        return
    if doc.file_size and doc.file_size > MAX_FILE:
        await _reply(update, "File quá lớn (tối đa 5 MB).")
        return
    tenant = next((t for t in tenants if t.id == wait["tenant"]), None) if by_wait else None
    if tenant is None and len(tenants) == 1:
        tenant = tenants[0]
    if tenant is None:
        await _reply(update, "Gõ /chitieu để chọn NPP trước, rồi gửi file.")
        return
    file = await doc.get_file()
    data = bytes(await file.download_as_bytearray())
    res = await asyncio.to_thread(import_targets, _svc(context).db, tenant.id, data, now_local().date())
    await msg.reply_text(summary_text(res))  # plain text: error lines quote cells from the file
    if not res.errors:
        context.chat_data.pop("await_targets", None)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Lỗi khi xử lý tin nhắn", exc_info=context.error)
    chat = getattr(update, "effective_chat", None)
    if chat is None:
        return
    try:  # so nobody waits on an answer that is not coming
        await context.bot.send_message(chat.id, "⚠️ Bot gặp lỗi khi xử lý (có thể do mạng chậm). Bạn thử lại sau ít phút.")
    except TelegramError:
        pass


async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands(COMMANDS)


def build_application(svc: Services) -> Application:
    # The library waits 5 s by default; Telegram from Vietnam is sometimes slower than that
    # and Excel uploads need longer still.
    application = (
        Application.builder()
        .token(svc.settings.telegram_bot_token.get_secret_value())
        .connect_timeout(TG_CONNECT_TIMEOUT).read_timeout(TG_READ_TIMEOUT)
        .write_timeout(TG_WRITE_TIMEOUT).pool_timeout(TG_CONNECT_TIMEOUT)
        .get_updates_connect_timeout(TG_CONNECT_TIMEOUT).get_updates_read_timeout(TG_READ_TIMEOUT)
        .get_updates_pool_timeout(TG_CONNECT_TIMEOUT)
        .post_init(_post_init)
        .build()
    )
    application.bot_data["svc"] = svc
    application.add_handler(CommandHandler("id", cmd_id))
    application.add_handler(CommandHandler(["start", "help"], cmd_help))
    application.add_handler(CommandHandler("ton", cmd_ton))
    application.add_handler(CommandHandler("hethan", cmd_hethan))
    application.add_handler(CommandHandler("baocao", cmd_baocao))
    application.add_handler(CommandHandler("nv", cmd_nv))
    application.add_handler(CommandHandler("top", cmd_top))
    application.add_handler(CommandHandler("chitieu", cmd_chitieu))
    application.add_handler(CommandHandler("sosanh", cmd_sosanh))
    application.add_handler(CommandHandler("trangthai", cmd_trangthai))
    application.add_handler(CallbackQueryHandler(on_choice, pattern=r"^(ton|nv|ct|bc):"))
    application.add_handler(MessageHandler(filters.Document.ALL, on_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)
    return application
