"""KiotViet users = the people on invoices' soldById. Refreshed once a day with branches."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Staff
from app.kiotviet.client import KiotVietClient, PageResult
from app.sync.parse import pick
from app.textutil import unaccent


async def fetch_users(client: KiotVietClient) -> PageResult:
    return await client.get_all("/users")


def save_staff(session: Session, tenant_id: int, items: list[dict]) -> int:
    existing = {s.id: s for s in session.scalars(select(Staff).where(Staff.tenant_id == tenant_id))}
    seen = set()
    for it in items:
        sid = int(it["id"])
        seen.add(sid)
        st = existing.get(sid)
        if st is None:
            st = Staff(tenant_id=tenant_id, id=sid)
            session.add(st)
        st.user_name = pick(it, "userName", default="")
        st.given_name = pick(it, "givenName", "name", default="")
        st.name_norm = unaccent(f"{st.given_name} {st.user_name}")
        st.removed = False
    for sid, st in existing.items():
        if sid not in seen:
            st.removed = True
    return len(seen)
