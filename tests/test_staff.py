"""Phase 2: staff sync, revenue, returns, periods, targets."""
import asyncio
from datetime import date, timedelta

from app.config import TenantSettings
from app.db.models import Return, Staff, Target
from app.kiotviet.client import KiotVietClient
from app.reports.periods import Period, month_pace, parse_period, split_name_period
from app.reports.staff import compute_stats, month_progress, ranking, search_staff, staff_names
from app.sync.runner import sync_tenant
from tests.fakes import NOW, TODAY

TUAN, LAN, ADMIN = 7, 8, 1


def day(offset: int = 0) -> Period:
    d = TODAY + timedelta(days=offset)
    return Period(d, d, "")


def november() -> Period:
    return parse_period("tháng này", TODAY)


def test_sync_stores_staff_and_returns(db, synced):
    with db.session() as s:
        assert s.get(Staff, (synced, LAN)).name_norm == "lan lan"
        assert s.get(Return, (synced, 1)).sold_by_id is None and s.get(Return, (synced, 1)).total == 12000
        assert s.get(Return, (synced, 3)).status == 2
    names = staff_names(db, synced)
    assert names[ADMIN] == "Chủ cửa hàng"  # Super Admin: name taken from invoices


def test_returns_sync_is_incremental(db, synced, shop):
    shop.returns.append({"id": 4, "code": "TH4", "invoiceId": 1002, "returnDate": "2026-11-23T07:50:00",
                         "modifiedDate": "2026-11-23T08:20:00", "returnTotal": 5000, "status": 1})
    shop.requests.clear()
    client = KiotVietClient("nppthu", "cid", "sec", http=shop.http())
    result = asyncio.run(sync_tenant(db, synced, client, TenantSettings(), now=NOW + timedelta(minutes=30)))
    call = next(r for r in shop.requests if r.url.path == "/returns")
    assert call.url.params["lastModifiedFrom"] == "2026-11-23T07:50:00"
    assert result.returns == 1 and result.staff is None


def test_today_revenue_ranking(db, synced):
    stats = compute_stats(db, synced, day(0), TenantSettings())
    rows = ranking(stats)
    assert [(r.name, r.revenue, r.orders, r.stores) for r in rows] == [
        ("Lan", 60000, 1, 1), ("Chủ cửa hàng", 20000, 1, 0), ("Tuấn", 15000, 1, 1)]
    tuan = stats[TUAN]
    assert (tuan.gross, tuan.returns, tuan.qty[105], tuan.returned_qty) == (30000, 15000, 1, 1)


def test_month_revenue_counts_returns_and_units(db, synced):
    stats = compute_stats(db, synced, november(), TenantSettings())
    tuan, lan = stats[TUAN], stats[LAN]
    # 11 days x 315k + thùng 700k + today 30k - return 15k; cancelled invoice ignored
    assert (tuan.revenue, tuan.orders, tuan.stores) == (4_180_000, 13, 3)
    # thùng (12 hộp) counted in hộp
    assert tuan.qty[101] == 34 and 103 not in tuan.qty
    # return without soldById goes to the original seller (Lan); cancelled return ignored
    assert (lan.revenue, lan.orders, lan.stores) == (3_513_000, 12, 3)
    assert lan.qty[102] == 109


def test_lines_mode(db, synced):
    stats = compute_stats(db, synced, day(0), TenantSettings(revenue_mode="lines"))
    # Lan: 4 x (16000 - 1000); admin: subTotal 20000
    assert stats[LAN].gross == 60000 and stats[ADMIN].gross == 20000


def test_targets_and_pace(db, synced):
    with db.session() as s:
        s.add_all([
            Target(tenant_id=synced, staff_id=TUAN, month="2026-11", kind="revenue", value=10_000_000),
            Target(tenant_id=synced, staff_id=TUAN, month="2026-11", kind="product", product_id=101, value=40),
            Target(tenant_id=synced, staff_id=LAN, month="2026-11", kind="revenue", value=4_000_000),
            Target(tenant_id=synced, staff_id=LAN, month="2026-11", kind="product", product_id=102, value=300),
            Target(tenant_id=synced, staff_id=LAN, month="2026-10", kind="revenue", value=1),
        ])
    prog = month_progress(db, synced, "2026-11", TODAY, TenantSettings())
    assert round(prog[TUAN].pace, 1) == 76.7  # 23 / 30 days
    tuan = {(t.kind, t.product_id): round(t.pct, 1) for t in prog[TUAN].targets}
    assert tuan == {("revenue", None): 41.8, ("product", 101): 85.0}
    assert [t.kind for t in prog[TUAN].behind()] == ["revenue"]
    assert [(t.kind, t.product_code) for t in prog[LAN].behind()] == [("product", "KD01")]
    assert prog[LAN].targets[1].unit == "Gói"


def test_month_pace():
    assert month_pace("2026-11", date(2026, 11, 15)) == 50.0
    assert month_pace("2026-10", date(2026, 11, 15)) == 100.0
    assert month_pace("2026-12", date(2026, 11, 15)) == 0.0
    # November 2026 has 5 Sundays (1, 8, 15, 22, 29): 25 working days, 12 done by the 15th
    assert month_pace("2026-11", date(2026, 11, 15), skip_sundays=True) == 48.0


def test_parse_period():
    t = TODAY  # Monday 23/11/2026
    assert parse_period("hôm nay", t) == Period(t, t, "hôm nay 23/11")
    assert parse_period("Hôm qua", t).start == date(2026, 11, 22)
    assert (parse_period("tuần này", t).start, parse_period("tuần này", t).end) == (t, t)
    assert (parse_period("tuần trước", t).start, parse_period("tuần trước", t).end) == (date(2026, 11, 16), date(2026, 11, 22))
    assert parse_period("tháng 11", t) == Period(date(2026, 11, 1), t, "tháng 11/2026 (đến 23/11)")
    assert parse_period("tháng 10", t) == Period(date(2026, 10, 1), date(2026, 10, 31), "tháng 10/2026")
    assert parse_period("tháng 12", t).start == date(2025, 12, 1)  # future month = last year
    assert parse_period("tháng trước", t).end == date(2026, 10, 31)
    assert parse_period("10/2026", t).month == "2026-10"
    assert parse_period("22/11", t).single_day
    assert parse_period("1/11 - 15/11", t) == Period(date(2026, 11, 1), date(2026, 11, 15), "01/11 – 15/11/2026")
    assert parse_period("tháng 13", t) is None and parse_period("31/2", t) is None
    assert parse_period("bánh quy", t) is None
    p = Period(date(2026, 11, 1), date(2026, 11, 15), "x")
    assert Period.from_code(p.code, t).start == p.start


def test_split_name_period():
    t = TODAY
    assert split_name_period("tuan thang 11", t) == ("tuan", parse_period("thang 11", t))
    assert split_name_period("Lan Anh hôm qua", t)[0] == "Lan Anh"
    assert split_name_period("tuan", t) == ("tuan", None)  # Tuấn, not "this week"
    assert split_name_period("Tuấn tuần trước", t) == ("Tuấn", parse_period("tuần trước", t))


def test_search_staff(db, synced):
    assert [m.name for m in search_staff(db, [synced], "lan")] == ["Lan"]  # exact beats "Lan Anh"
    assert [m.name for m in search_staff(db, [synced], "Tuấn")] == ["Tuấn"]
    assert [m.name for m in search_staff(db, [synced], "la")] == ["Lan", "Lan Anh"]
    assert [m.name for m in search_staff(db, [synced], "lananh")] == ["Lan Anh"]  # login name
    assert [m.name for m in search_staff(db, [synced], "chu")] == ["Chủ cửa hàng"]
    assert search_staff(db, [synced], "xyz") == []
