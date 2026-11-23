"""Jobs on the bot's own event loop (python-telegram-bot JobQueue = APScheduler), Asia/Ho_Chi_Minh."""
from __future__ import annotations

import asyncio
import logging
from datetime import time

from telegram.ext import Application, ContextTypes

from app.services import Services
from app.timeutil import TZ

log = logging.getLogger(__name__)


def _svc(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.application.bot_data["svc"]


async def sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _svc(context).safe_sync(context.job.data, context.bot)


async def report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = _svc(context)
    tenant_id = context.job.data
    await svc.sync_if_stale(tenant_id, context.bot)
    chats = await asyncio.to_thread(svc.recipients, tenant_id)
    if not chats:
        log.warning("NPP %s chưa có nhóm Telegram nào nhận báo cáo", tenant_id)
        return
    await svc.send_inventory_report(context.bot, tenant_id, chats)


async def ranking_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = _svc(context)
    tenant_id = context.job.data
    await svc.sync_if_stale(tenant_id, context.bot)
    chats = await asyncio.to_thread(svc.ranking_recipients, tenant_id)
    if chats:
        await svc.send_daily_ranking(context.bot, tenant_id, chats)


async def maintenance_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = _svc(context)
    for t in await asyncio.to_thread(svc.active_tenants):
        try:
            n = await asyncio.to_thread(svc.prune, t.id)
            log.info("NPP %s: xóa %s hóa đơn cũ", t.id, n)
        except Exception:
            log.exception("Dọn dữ liệu NPP %s lỗi", t.id)


def schedule_jobs(application: Application, svc: Services) -> list[str]:
    """Register jobs for every active NPP. Adding an NPP or changing times needs a restart."""
    jq = application.job_queue
    names = []
    for i, t in enumerate(svc.active_tenants()):
        ts = svc.tenant_settings(t)
        name = f"sync:{t.id}"
        jq.run_repeating(sync_job, interval=ts.sync_interval_minutes * 60, first=10 + 20 * i,
                         data=t.id, name=name)
        names.append(name)
        for hhmm in ts.report_times:
            h, m = (int(x) for x in hhmm.split(":"))
            name = f"report:{t.id}:{hhmm}"
            jq.run_daily(report_job, time=time(h, m, tzinfo=TZ), data=t.id, name=name)
            names.append(name)
        if ts.ranking_time:
            h, m = (int(x) for x in ts.ranking_time.split(":"))
            name = f"ranking:{t.id}:{ts.ranking_time}"
            jq.run_daily(ranking_job, time=time(h, m, tzinfo=TZ), data=t.id, name=name)
            names.append(name)
    jq.run_daily(maintenance_job, time=time(3, 0, tzinfo=TZ), name="maintenance")
    names.append("maintenance")
    return names
