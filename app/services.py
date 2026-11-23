"""Operations shared by the bot, the scheduler and the CLI."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta

import httpx
from sqlalchemy import or_, select
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.bot.formatting import format_inventory_report
from app.config import Settings, TenantSettings
from app.crypto import SecretBox
from app.db.models import Chat, SyncState, Tenant
from app.db.session import Database
from app.kiotviet.client import KiotVietClient
from app.reports.excel import inventory_workbook, report_filename
from app.reports.inventory import InventoryReport, build_inventory_report, load_snapshot
from app.sync.invoices import prune_invoices
from app.sync.runner import SyncResult, last_stock_sync, sync_tenant
from app.textutil import esc
from app.timeutil import now_local

log = logging.getLogger(__name__)

ALERT_AFTER_FAILURES = 3
FRESH_ENOUGH = timedelta(minutes=5)


class Services:
    def __init__(self, settings: Settings, db: Database,
                 http_factory: Callable[[], httpx.AsyncClient] | None = None):
        self.settings = settings
        self.db = db
        self._http_factory = http_factory  # tests inject a fake KiotViet here
        self._box: SecretBox | None = None
        self._locks: dict[int, asyncio.Lock] = {}

    # ---- tenants & chats -------------------------------------------------

    @property
    def box(self) -> SecretBox:
        if self._box is None:
            self._box = SecretBox(self.settings.secret_key.get_secret_value())
        return self._box

    def tenant(self, tenant_id: int) -> Tenant | None:
        with self.db.session() as s:
            return s.get(Tenant, tenant_id)

    def active_tenants(self) -> list[Tenant]:
        with self.db.session() as s:
            return list(s.scalars(select(Tenant).where(Tenant.active.is_(True)).order_by(Tenant.id)))

    @staticmethod
    def tenant_settings(tenant: Tenant) -> TenantSettings:
        return TenantSettings.from_json(tenant.settings_json)

    def scope_for_chat(self, chat_id: int) -> list[Tenant] | None:
        """Tenants this chat may see; None = chat not registered (bot ignores it)."""
        with self.db.session() as s:
            chat = s.get(Chat, chat_id)
            if chat is None:
                return None
            stmt = select(Tenant).where(Tenant.active.is_(True)).order_by(Tenant.id)
            if chat.role != "owner":
                stmt = stmt.where(Tenant.id == chat.tenant_id)
            return list(s.scalars(stmt))

    def recipients(self, tenant_id: int) -> list[int]:
        with self.db.session() as s:
            return list(s.scalars(select(Chat.chat_id).where(
                or_(Chat.tenant_id == tenant_id, Chat.role == "owner"))))

    def make_client(self, tenant: Tenant) -> KiotVietClient:
        http = self._http_factory() if self._http_factory else None
        return KiotVietClient(tenant.retailer, tenant.client_id, self.box.decrypt(tenant.client_secret_enc), http=http)

    # ---- sync --------------------------------------------------------------

    async def sync(self, tenant_id: int, *, force_branches: bool = False) -> SyncResult:
        lock = self._locks.setdefault(tenant_id, asyncio.Lock())
        async with lock:
            tenant = await asyncio.to_thread(self.tenant, tenant_id)
            if tenant is None:
                raise ValueError(f"Không có NPP id={tenant_id}")
            async with self.make_client(tenant) as client:
                return await sync_tenant(self.db, tenant_id, client, self.tenant_settings(tenant),
                                         force_branches=force_branches)

    async def sync_if_stale(self, tenant_id: int, bot: Bot | None) -> None:
        last = await asyncio.to_thread(last_stock_sync, self.db, tenant_id)
        if last is None or now_local() - last > FRESH_ENOUGH:
            await self.safe_sync(tenant_id, bot)

    async def safe_sync(self, tenant_id: int, bot: Bot | None) -> SyncResult | None:
        """Sync, never raise; tell the operator after repeated failures and on recovery."""
        result = None
        try:
            result = await self.sync(tenant_id)
        except Exception:
            log.exception("Đồng bộ NPP %s lỗi", tenant_id)
        await self._alert_operator(tenant_id, bot)
        return result

    async def _alert_operator(self, tenant_id: int, bot: Bot | None) -> None:
        def check() -> list[str]:
            failing, recovered = [], []
            with self.db.session() as s:
                tenant = s.get(Tenant, tenant_id)
                name = tenant.name if tenant else str(tenant_id)
                for st in s.scalars(select(SyncState).where(SyncState.tenant_id == tenant_id)):
                    if st.consecutive_failures >= ALERT_AFTER_FAILURES and not st.alerted:
                        failing.append(f"• {st.resource}: {st.consecutive_failures} lần liên tiếp. {st.last_error or ''}")
                        st.alerted = True
                    elif st.alerted and st.consecutive_failures == 0:
                        recovered.append(st.resource)
                        st.alerted = False
            msgs = []
            if failing:
                msgs.append(f"🚨 Đồng bộ KiotViet của <b>{esc(name)}</b> đang lỗi:\n" + esc("\n".join(failing)))
            if recovered:
                msgs.append(f"✅ <b>{esc(name)}</b> đã đồng bộ lại được ({', '.join(recovered)}).")
            return msgs

        msgs = await asyncio.to_thread(check)
        admin = self.settings.admin_chat_id
        for m in msgs:
            log.warning("Báo người vận hành: %s", m)
            if bot and admin:
                try:
                    await bot.send_message(admin, m[:4000], parse_mode=ParseMode.HTML)
                except TelegramError:
                    log.exception("Không gửi được tin báo lỗi tới ADMIN_CHAT_ID")

    # ---- reports -------------------------------------------------------

    async def inventory_report(self, tenant_id: int) -> InventoryReport:
        def build() -> InventoryReport:
            tenant = self.tenant(tenant_id)
            ts = self.tenant_settings(tenant)
            today = now_local().date()
            return build_inventory_report(load_snapshot(self.db, tenant_id, ts, today), ts, today)

        return await asyncio.to_thread(build)

    async def send_inventory_report(self, bot: Bot, tenant_id: int, chat_ids: list[int],
                                    *, force_excel: bool = False) -> None:
        report = await self.inventory_report(tenant_id)
        now = now_local()
        messages, truncated = format_inventory_report(report, now)
        excel = inventory_workbook(report) if truncated or force_excel else None
        for chat_id in chat_ids:
            try:
                for m in messages:
                    await bot.send_message(chat_id, m, parse_mode=ParseMode.HTML)
                if excel:
                    await bot.send_document(chat_id, excel,
                                            filename=report_filename(report.tenant_name, "ton-kho", now))
            except TelegramError:
                log.exception("Không gửi được báo cáo tới chat %s", chat_id)

    def prune(self, tenant_id: int) -> int:
        tenant = self.tenant(tenant_id)
        with self.db.session() as s:
            return prune_invoices(s, tenant_id, self.tenant_settings(tenant).keep_invoice_days, now_local().date())
