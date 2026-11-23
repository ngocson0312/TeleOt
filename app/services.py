"""Operations shared by the bot, the scheduler and the CLI."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import or_, select
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.bot.formatting import format_inventory_report, format_urgent
from app.bot.owner_format import format_comparison, format_inventory_overview, format_status
from app.bot.staff_format import format_daily_ranking, format_ranking, format_staff_detail
from app.config import ChatSettings, Settings, TenantSettings
from app.crypto import SecretBox
from app.db.models import Chat, Product, SyncState, Tenant
from app.db.session import Database
from app.demo import DEMO_RETAILER, DemoShop
from app.kiotviet.client import KiotVietClient
from app.reports.excel import inventory_workbook, report_filename
from app.reports import alerts
from app.reports.compare import compare_npps
from app.reports.inventory import InventoryReport, build_inventory_report, load_snapshot
from app.reports.periods import Period
from app.reports.status import tenant_status
from app.reports.staff import compute_stats, month_progress, ranking
from app.sync.invoices import prune_invoices
from app.sync.returns import prune_returns
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
        self._slots = asyncio.Semaphore(max(settings.max_parallel_syncs, 1))

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

    @staticmethod
    def chat_settings(chat: Chat) -> ChatSettings:
        return ChatSettings.for_chat(chat.role, chat.settings_json)

    def chat(self, chat_id: int) -> Chat | None:
        with self.db.session() as s:
            return s.get(Chat, chat_id)

    def chats(self) -> list[Chat]:
        with self.db.session() as s:
            return list(s.scalars(select(Chat).order_by(Chat.chat_id)))

    def _chats_for(self, tenant_id: int) -> list[Chat]:
        """The NPP's own groups and every owner group."""
        with self.db.session() as s:
            return list(s.scalars(select(Chat).where(
                or_(Chat.tenant_id == tenant_id, Chat.role == "owner")).order_by(Chat.chat_id)))

    def recipients(self, tenant_id: int) -> list[int]:
        """Every chat that may see this NPP."""
        return [c.chat_id for c in self._chats_for(tenant_id)]

    def report_recipients(self, tenant_id: int) -> list[int]:
        """Scheduled full inventory report. Owner groups get one summary of all NPPs instead (summary_job)."""
        return [c.chat_id for c in self._chats_for(tenant_id) if self.chat_settings(c).inventory == "full"]

    def urgent_recipients(self, tenant_id: int) -> list[int]:
        return [c.chat_id for c in self._chats_for(tenant_id) if self.chat_settings(c).urgent]

    def ranking_recipients(self, tenant_id: int) -> list[int]:
        """Question 6 for the owner: can staff see the ranking? ranking_to_npp_groups=false: owner groups only.
        Otherwise owner groups see the top sellers in the daily comparison, unless their chat sets ranking."""
        to_groups = self.tenant_settings(self.tenant(tenant_id)).ranking_to_npp_groups
        out = []
        for c in self._chats_for(tenant_id):
            want = self.chat_settings(c).ranking
            if c.role == "owner":
                ok = want if want is not None else not to_groups
            else:
                ok = to_groups and want is not False
            if ok:
                out.append(c.chat_id)
        return out

    def make_client(self, tenant: Tenant) -> KiotVietClient:
        if self._http_factory:
            http = self._http_factory()
        elif tenant.retailer == DEMO_RETAILER:  # the demo NPP never calls KiotViet
            http = DemoShop(now_local().date()).http()
        else:
            http = None
        return KiotVietClient(tenant.retailer, tenant.client_id, self.box.decrypt(tenant.client_secret_enc), http=http)

    # ---- sync --------------------------------------------------------------

    async def sync(self, tenant_id: int, *, force_branches: bool = False) -> SyncResult:
        lock = self._locks.setdefault(tenant_id, asyncio.Lock())
        async with lock, self._slots:
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

    # ---- urgent alerts (5.4, phase 3) --------------------------------------

    def _scan_alerts(self, tenant_id: int, now: datetime, send: bool):
        tenant = self.tenant(tenant_id)
        if tenant is None:
            return [], None
        ts = self.tenant_settings(tenant)
        if not ts.urgent_alerts:
            return [], None
        report = build_inventory_report(load_snapshot(self.db, tenant_id, ts, now.date()), ts, now.date())
        return alerts.scan(self.db, tenant_id, report, ts, now, send=send), report

    async def check_alerts(self, tenant_id: int, bot: Bot | None, now: datetime | None = None) -> int:
        """After a successful sync: send what is newly urgent. Returns the number of items sent."""
        now = now or now_local()
        items, report = await asyncio.to_thread(self._scan_alerts, tenant_id, now, True)
        if not items:
            return 0
        chats = await asyncio.to_thread(self.urgent_recipients, tenant_id)
        messages = format_urgent(report.tenant_name, items, report.multi_branch,
                                 report.settings.expiry_red_days, now)
        log.info("NPP %s: %s cảnh báo gấp tới %s chat", tenant_id, len(items), len(chats))
        if bot:
            for chat_id in chats:
                await self._send(bot, chat_id, messages, "cảnh báo gấp")
        return len(items)

    async def mark_alerts_seen(self, tenant_id: int, now: datetime | None = None) -> None:
        """A full report just went out: everything waiting has been told."""
        await asyncio.to_thread(self._scan_alerts, tenant_id, now or now_local(), False)

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

    # ---- staff -------------------------------------------------------------

    def staff_detail_messages(self, tenant: Tenant, staff_id: int, name: str, period: Period,
                              label: str | None = None) -> list[str]:
        ts = self.tenant_settings(tenant)
        stats = compute_stats(self.db, tenant.id, period, ts)
        progress = month_progress(self.db, tenant.id, period.month, now_local().date(), ts) if period.month else {}
        with self.db.session() as s:
            products = {p.id: p for p in s.scalars(select(Product).where(Product.tenant_id == tenant.id))}
        return format_staff_detail(name, stats.get(staff_id), period, progress.get(staff_id), products,
                                   last_stock_sync(self.db, tenant.id), label)

    def ranking_messages(self, tenant: Tenant, period: Period) -> list[str]:
        ts = self.tenant_settings(tenant)
        stats = compute_stats(self.db, tenant.id, period, ts)
        previous = compute_stats(self.db, tenant.id, period.previous_day(), ts) if period.single_day else None
        progress = month_progress(self.db, tenant.id, period.month, now_local().date(), ts) if period.month else None
        return format_ranking(tenant.name, period, ranking(stats), last_stock_sync(self.db, tenant.id),
                              previous=previous, progress=progress)

    def daily_ranking_messages(self, tenant: Tenant) -> list[str]:
        ts = self.tenant_settings(tenant)
        today = now_local().date()
        period = Period(today, today, f"hôm nay {today:%d/%m/%Y}")
        stats = compute_stats(self.db, tenant.id, period, ts)
        previous = compute_stats(self.db, tenant.id, period.previous_day(), ts)
        month = f"{today:%Y-%m}"
        progress = month_progress(self.db, tenant.id, month, today, ts)
        return format_daily_ranking(tenant.name, period, ranking(stats), previous, progress, month,
                                    last_stock_sync(self.db, tenant.id))

    async def send_daily_ranking(self, bot: Bot, tenant_id: int, chat_ids: list[int]) -> None:
        tenant = await asyncio.to_thread(self.tenant, tenant_id)
        messages = await asyncio.to_thread(self.daily_ranking_messages, tenant)
        for chat_id in chat_ids:
            try:
                for m in messages:
                    await bot.send_message(chat_id, m, parse_mode=ParseMode.HTML)
            except TelegramError:
                log.exception("Không gửi được bảng xếp hạng tới chat %s", chat_id)

    # ---- owner group (phase 3) ---------------------------------------------

    def overview_messages(self, tenants: list[Tenant]) -> list[str]:
        today = now_local().date()
        reports = []
        for t in tenants:
            ts = self.tenant_settings(t)
            reports.append(build_inventory_report(load_snapshot(self.db, t.id, ts, today), ts, today))
        return format_inventory_overview(reports, now_local())

    def comparison_messages(self, tenants: list[Tenant], period: Period) -> list[str]:
        return format_comparison(compare_npps(self.db, tenants, period, now_local().date()), period, now_local())

    def status_messages(self, tenants: list[Tenant]) -> list[str]:
        now = now_local()
        return format_status([tenant_status(self.db, t, now) for t in tenants], now)

    def all_tenants(self) -> list[Tenant]:
        with self.db.session() as s:
            return list(s.scalars(select(Tenant).order_by(Tenant.id)))

    async def _send(self, bot: Bot, chat_id: int, messages: list[str], what: str) -> None:
        try:
            for m in messages:
                await bot.send_message(chat_id, m, parse_mode=ParseMode.HTML)
        except TelegramError:
            log.exception("Không gửi được %s tới chat %s", what, chat_id)

    async def send_overview(self, bot: Bot, chat_id: int) -> None:
        tenants = await asyncio.to_thread(self.scope_for_chat, chat_id)
        if not tenants:
            return
        for t in tenants:
            await self.sync_if_stale(t.id, bot)
        await self._send(bot, chat_id, await asyncio.to_thread(self.overview_messages, tenants), "tóm tắt tồn kho")

    async def send_daily_comparison(self, bot: Bot, chat_id: int) -> None:
        tenants = await asyncio.to_thread(self.scope_for_chat, chat_id)
        if not tenants:
            return
        for t in tenants:
            await self.sync_if_stale(t.id, bot)
        today = now_local().date()
        period = Period(today, today, f"hôm nay {today:%d/%m/%Y}")
        await self._send(bot, chat_id, await asyncio.to_thread(self.comparison_messages, tenants, period),
                         "so sánh NPP")

    def backup(self) -> Path | None:
        return self.db.backup(Path(self.settings.backup_dir), self.settings.backup_keep, now_local().date())

    def prune(self, tenant_id: int) -> int:
        tenant = self.tenant(tenant_id)
        keep = self.tenant_settings(tenant).keep_invoice_days
        today = now_local().date()
        with self.db.session() as s:
            return prune_invoices(s, tenant_id, keep, today) + prune_returns(s, tenant_id, keep, today)
