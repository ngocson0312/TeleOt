"""Jobs on the bot's own event loop (python-telegram-bot JobQueue = APScheduler), Asia/Ho_Chi_Minh.

Every 5 minutes `reconcile_job` compares the jobs with the database: a new NPP, a paused NPP,
new report times or a new owner group take effect without restarting the bot. Job names
carry their schedule (report:1:08:00), so a changed time is a new job and the old one goes.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import time
from typing import Any

from telegram.ext import Application, ContextTypes

from app.services import Services
from app.timeutil import TZ

log = logging.getLogger(__name__)

RECONCILE_EVERY = 300  # seconds
MANAGED = ("sync:", "report:", "ranking:", "summary:", "compare:")


def _svc(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.application.bot_data["svc"]


async def sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = _svc(context)
    tenant_id = context.job.data
    if await svc.safe_sync(tenant_id, context.bot) is not None:
        try:
            await svc.check_alerts(tenant_id, context.bot)
        except Exception:
            log.exception("Kiểm tra cảnh báo gấp NPP %s lỗi", tenant_id)


async def report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = _svc(context)
    tenant_id = context.job.data
    await svc.sync_if_stale(tenant_id, context.bot)
    chats = await asyncio.to_thread(svc.report_recipients, tenant_id)
    if not chats:
        log.warning("NPP %s chưa có nhóm Telegram nào nhận báo cáo", tenant_id)
        return
    await svc.send_inventory_report(context.bot, tenant_id, chats)
    await svc.mark_alerts_seen(tenant_id)


async def ranking_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = _svc(context)
    tenant_id = context.job.data
    await svc.sync_if_stale(tenant_id, context.bot)
    chats = await asyncio.to_thread(svc.ranking_recipients, tenant_id)
    if chats:
        await svc.send_daily_ranking(context.bot, tenant_id, chats)


async def summary_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _svc(context).send_overview(context.bot, context.job.data)


async def compare_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _svc(context).send_daily_comparison(context.bot, context.job.data)


async def maintenance_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = _svc(context)
    for t in await asyncio.to_thread(svc.active_tenants):
        try:
            n = await asyncio.to_thread(svc.prune, t.id)
            log.info("NPP %s: xóa %s hóa đơn cũ", t.id, n)
        except Exception:
            log.exception("Dọn dữ liệu NPP %s lỗi", t.id)
    try:
        path = await asyncio.to_thread(svc.backup)
        if path:
            log.info("Đã sao lưu CSDL: %s", path)
    except Exception:
        log.exception("Sao lưu CSDL lỗi")


async def reconcile_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        specs = await asyncio.to_thread(desired_jobs, _svc(context))
        apply_jobs(context.application, specs, startup=False)
    except Exception:
        log.exception("Cập nhật lịch chạy lỗi")


@dataclass
class JobSpec:
    name: str
    callback: Any
    data: int
    at: time | None = None  # daily
    every: int | None = None  # seconds, repeating


def _at(hhmm: str) -> time:
    h, m = (int(x) for x in hhmm.split(":"))
    return time(h, m, tzinfo=TZ)


def desired_jobs(svc: Services) -> list[JobSpec]:
    specs: list[JobSpec] = []
    for t in svc.active_tenants():
        ts = svc.tenant_settings(t)
        every = max(ts.sync_interval_minutes, 5) * 60
        specs.append(JobSpec(f"sync:{t.id}:{every // 60}m", sync_job, t.id, every=every))
        specs += [JobSpec(f"report:{t.id}:{hhmm}", report_job, t.id, at=_at(hhmm)) for hhmm in ts.report_times]
        if ts.ranking_time:
            specs.append(JobSpec(f"ranking:{t.id}:{ts.ranking_time}", ranking_job, t.id, at=_at(ts.ranking_time)))
    for c in svc.chats():
        cs = svc.chat_settings(c)
        if cs.inventory == "summary":
            specs += [JobSpec(f"summary:{c.chat_id}:{hhmm}", summary_job, c.chat_id, at=_at(hhmm))
                      for hhmm in cs.summary_times]
        if cs.compare_time:
            specs.append(JobSpec(f"compare:{c.chat_id}:{cs.compare_time}", compare_job, c.chat_id,
                                 at=_at(cs.compare_time)))
    return specs


def schedule_jobs(application: Application, svc: Services) -> list[str]:
    """At startup: every job from the database, plus maintenance (03:00) and reconcile (5 min)."""
    return apply_jobs(application, desired_jobs(svc), startup=True)


def apply_jobs(application: Application, specs: list[JobSpec], *, startup: bool) -> list[str]:
    """Make the job queue match `specs`. Returns the names of the managed jobs."""
    jq = application.job_queue
    wanted = {s.name for s in specs}
    existing = {j.name for j in jq.jobs() if j.name and j.name.startswith(MANAGED)}
    for job in jq.jobs():
        if job.name in existing and job.name not in wanted:
            job.schedule_removal()
            log.info("Bỏ lịch %s", job.name)
    n_sync = 0
    for spec in specs:
        if spec.name in existing:
            continue
        if spec.every:
            first = 10 + 20 * n_sync if startup else 10  # spread the NPPs' first syncs
            n_sync += 1
            jq.run_repeating(spec.callback, interval=spec.every, first=first, data=spec.data, name=spec.name)
        else:
            jq.run_daily(spec.callback, time=spec.at, data=spec.data, name=spec.name)
        if not startup:
            log.info("Thêm lịch %s", spec.name)
    if startup:
        jq.run_daily(maintenance_job, time=time(3, 0, tzinfo=TZ), name="maintenance")
        jq.run_repeating(reconcile_job, interval=RECONCILE_EVERY, first=RECONCILE_EVERY, name="reconcile")
    return [s.name for s in specs]
