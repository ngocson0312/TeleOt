"""Telegram commands (section 6 of the plan, phase 1 part: /ton, /hethan, /baocao, /help)."""
from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.bot.formatting import HELP_TEXT, format_expiring, format_product_detail
from app.bot.text_parse import parse_free_text
from app.db.models import Tenant
from app.reports.excel import expiring_workbook, report_filename
from app.reports.inventory import expiring_rows, load_snapshot, product_detail, search_products
from app.services import Services
from app.textutil import esc
from app.timeutil import now_local

log = logging.getLogger(__name__)

MAX_CHOICES = 10
COMMANDS = [
    BotCommand("ton", "Tồn kho một mặt hàng: /ton banh quy"),
    BotCommand("hethan", "Lô sắp hết hạn: /hethan 60"),
    BotCommand("baocao", "Gửi ngay báo cáo tồn kho"),
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


# ---- commands -------------------------------------------------------------


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answers in any chat so the operator can register it. Reveals only the chat's own id."""
    chat = update.effective_chat
    await _reply(update, f"Mã chat này: <code>{chat.id}</code>\n"
                         f"Người vận hành cấp quyền bằng: <code>python -m app.cli chat-add {chat.id} --tenant ID</code>")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _scope(update, context) is None:
        return
    await _reply(update, HELP_TEXT)


async def cmd_ton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tenants = await _scope(update, context)
    if tenants is None:
        return
    await do_ton(update, context, tenants, " ".join(context.args or []))


async def cmd_hethan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tenants = await _scope(update, context)
    if tenants is None:
        return
    days = None
    if context.args:
        if not context.args[0].isdigit():
            await _reply(update, "Gõ số ngày, ví dụ: <code>/hethan 60</code>")
            return
        days = int(context.args[0])
    await do_hethan(update, context, tenants, days)


async def cmd_baocao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tenants = await _scope(update, context)
    if tenants is None:
        return
    await do_baocao(update, context, tenants)


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
        await _reply(update, HELP_TEXT)


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
        _, tid, pid = q.data.split(":")
        await _send_detail(update, context, tenants, int(tid), int(pid), edit=True)
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


async def do_baocao(update: Update, context: ContextTypes.DEFAULT_TYPE, tenants: list[Tenant]) -> None:
    svc = _svc(context)
    if not tenants:
        await _reply(update, "Chat này chưa gắn với NPP nào đang hoạt động.")
        return
    await _reply(update, "⏳ Đang lấy số liệu mới từ KiotViet…")
    for t in tenants:
        await svc.sync_if_stale(t.id, context.bot)
        await svc.send_inventory_report(context.bot, t.id, [update.effective_chat.id])


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Lỗi khi xử lý tin nhắn", exc_info=context.error)


async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands(COMMANDS)


def build_application(svc: Services) -> Application:
    application = (
        Application.builder()
        .token(svc.settings.telegram_bot_token.get_secret_value())
        .post_init(_post_init)
        .build()
    )
    application.bot_data["svc"] = svc
    application.add_handler(CommandHandler("id", cmd_id))
    application.add_handler(CommandHandler(["start", "help"], cmd_help))
    application.add_handler(CommandHandler("ton", cmd_ton))
    application.add_handler(CommandHandler("hethan", cmd_hethan))
    application.add_handler(CommandHandler("baocao", cmd_baocao))
    application.add_handler(CallbackQueryHandler(on_choice, pattern=r"^ton:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)
    return application
