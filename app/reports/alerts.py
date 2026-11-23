"""5.4 Cảnh báo gấp: after every sync, tell the NPP about items that are *newly* over an
urgent threshold since the last check. Never repeat an item while it stays bad.

Urgent = hết hàng, sắp hết hàng (the 5.1 list), lô đã hết hạn còn tồn, lô hết hạn trong
`expiry_red_days`. Tồn nhiều / bán chậm is never urgent: it waits for the scheduled report.

- First check for an NPP: remember everything that is bad now, send nothing (the scheduled
  report already lists it).
- Quiet hours: new items wait (last_sent stays empty) and go out at the first check after.
- An item that recovers and turns bad again within `alert_cooldown_hours` is not re-sent.
- A scheduled full report counts as telling: every waiting item is marked as sent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import TenantSettings, in_window
from app.db.models import AlertState, SyncState
from app.db.session import Database
from app.reports.inventory import ExpiryRow, InventoryReport, StockRow

KINDS = ("out", "expired", "low", "red")  # message order: worst first
FORGET_AFTER = timedelta(days=30)  # inactive rows older than this are deleted
MARKER = "alerts"  # sync_state row: the NPP has had its first (silent) check


@dataclass
class AlertItem:
    key: str
    kind: str
    row: StockRow | ExpiryRow


def current_alerts(report: InventoryReport) -> dict[str, AlertItem]:
    items: dict[str, AlertItem] = {}
    for r in report.low:
        kind = "out" if r.on_hand <= 0 else "low"
        key = f"{kind}:{r.product_id}:{r.branch_id}"
        items[key] = AlertItem(key, kind, r)
    for r in report.expiring:
        if r.level in ("expired", "red"):
            key = f"{r.level}:{r.product_id}:{r.branch_name}:{r.batch_name}:{r.expire_date:%Y%m%d}"
            items[key] = AlertItem(key, r.level, r)
    return items


def scan(db: Database, tenant_id: int, report: InventoryReport, settings: TenantSettings,
         now: datetime, *, send: bool = True) -> list[AlertItem]:
    """Update alert_state from the current report. Return the items to send now
    (already marked as sent). send=False: mark every waiting item as sent, return []."""
    current = current_alerts(report)
    cooldown = timedelta(hours=settings.alert_cooldown_hours)
    with db.session() as s:
        marker = s.get(SyncState, (tenant_id, MARKER))
        first_check = marker is None
        if marker is None:
            marker = SyncState(tenant_id=tenant_id, resource=MARKER, consecutive_failures=0, alerted=False)
            s.add(marker)
        marker.last_success = now
        states = {a.key: a for a in s.scalars(select(AlertState).where(AlertState.tenant_id == tenant_id))}

        for key, item in current.items():
            st = states.get(key)
            if st is None:
                st = AlertState(tenant_id=tenant_id, key=key, kind=item.kind, active=True, first_seen=now,
                                last_seen=now, last_sent=now if first_check else None)
                s.add(st)
                states[key] = st
            elif not st.active:  # bad again
                st.active, st.first_seen, st.last_seen = True, now, now
                if st.last_sent is None or now - st.last_sent >= cooldown:
                    st.last_sent = None
            else:
                st.last_seen = now
        for key, st in list(states.items()):
            if key in current:
                continue
            if st.active:
                st.active = False
            elif st.last_seen < now - FORGET_AFTER:
                s.delete(st)
                del states[key]

        waiting = [key for key, st in states.items() if st.active and st.last_sent is None]
        if not send:
            for key in waiting:
                states[key].last_sent = now
            return []
        if first_check or in_window(settings.quiet_hours, now.time()):
            return []
        for key in waiting:
            states[key].last_sent = now
    due = [current[k] for k in waiting]
    due.sort(key=lambda a: (KINDS.index(a.kind), a.row.name))
    return due


def active_count(db: Database, tenant_id: int) -> int:
    with db.session() as s:
        return len(list(s.scalars(select(AlertState.key).where(
            AlertState.tenant_id == tenant_id, AlertState.active.is_(True)))))
