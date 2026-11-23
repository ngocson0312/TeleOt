from datetime import datetime, timedelta
from io import BytesIO

from openpyxl import load_workbook

from app.bot.formatting import TG_LIMIT, format_inventory_report, format_product_detail, split_message
from app.config import TenantSettings
from app.reports.excel import inventory_workbook
from app.reports.inventory import build_inventory_report, load_snapshot, product_detail, search_products
from app.textutil import fmt_qty, unaccent
from tests.fakes import NOW, TODAY


def report(db, tenant_id, **overrides):
    settings = TenantSettings(**overrides)
    snap = load_snapshot(db, tenant_id, settings, TODAY)
    return build_inventory_report(snap, settings, TODAY)


def test_low_stock(db, synced):
    r = report(db, synced)
    assert [(x.code, x.branch_id) for x in r.low] == [("KD01", 1), ("BQ01", 1)]
    out, below_min = r.low
    assert out.reason == "Hết hàng" and out.avg_daily == 10
    assert below_min.reason == "Dưới định mức 20"
    # 2/day plus one thùng (12 hộp) in 30 days, thùng counted in hộp
    assert round(below_min.avg_daily, 2) == 2.4
    assert r.multi_branch


def test_low_stock_category_threshold_and_days_cover(db, synced):
    r = report(db, synced, low_stock_qty_by_category={"snack": 500}, low_days_cover=25)
    reasons = {x.code: x.reason for x in r.low}
    assert reasons["SK01"] == "Dưới ngưỡng 500"
    assert reasons["SC01"] == "Chỉ đủ bán ~20 ngày"  # 100 in stock / 5 a day


def test_expiring_levels(db, synced):
    r = report(db, synced)
    assert [(x.batch_name, x.level, x.days_left) for x in r.expiring] == [
        ("L3", "expired", -2), ("L1", "red", 5), ("L2", "orange", 25)]


def test_overstock_sorted_by_value(db, synced):
    r = report(db, synced)
    assert [(x.code, x.branch_id) for x in r.over] == [("KD01", 2), ("BQ01", 2), ("SK01", 1)]
    assert r.over[0].reason == "Vượt tối đa 200; Không bán được trong 90 ngày qua"
    codes = {x.code for x in r.low + r.over}
    assert not codes & {"BQ01-T", "OLD", "DV"}  # extra unit, inactive, service


def test_cancelled_invoices_do_not_count(db, synced):
    r = report(db, synced)
    assert next(x for x in r.over if x.code == "SK01").last_sold is None


def test_message_format_and_limits(db, synced):
    r = report(db, synced)
    msgs, truncated = format_inventory_report(r, NOW)
    text = "\n".join(msgs)
    assert not truncated and len(msgs) == 1
    assert "Báo cáo tồn kho · NPP Thử" in text and "Số liệu lúc 08:00 23/11/2026" in text
    assert "⛔ Đã hết hạn" in text and "lô L3" in text and "quá hạn 2 ngày" in text

    # 400 low-stock rows with nasty names: still short messages, flagged for Excel
    big = r.low[0]
    r.low = [type(big)(**{**big.__dict__, "name": f"Kẹo <số {i}> & co", "code": f"K{i}"}) for i in range(400)]
    msgs, truncated = format_inventory_report(r, NOW)
    assert truncated
    assert all(len(m) <= TG_LIMIT for m in msgs)
    assert "&lt;số 0&gt; &amp; co" in msgs[0]
    assert "… và 385 mặt hàng khác." in "\n".join(msgs)


def test_stale_data_warning(db, synced):
    r = report(db, synced)
    msgs, _ = format_inventory_report(r, NOW + timedelta(hours=3))
    assert "⚠️ Số liệu lúc 08:00 23/11/2026" in msgs[0]


def test_excel_has_all_rows(db, synced):
    r = report(db, synced)
    wb = load_workbook(BytesIO(inventory_workbook(r)))
    assert wb.sheetnames == ["Sắp hết hàng", "Sắp hết hạn", "Tồn nhiều - bán chậm"]
    assert wb["Sắp hết hàng"].max_row == 1 + len(r.low)
    assert wb["Sắp hết hạn"].max_row == 1 + len(r.expiring)
    assert wb["Tồn nhiều - bán chậm"]["L2"].value == 5_000_000


def test_search_and_detail(db, synced):
    assert [p.code for p in search_products(db, [synced], "banh quy")] == ["BQ01"]
    assert [p.code for p in search_products(db, [synced], "Kẹo DỪA")] == ["KD01"]
    assert [p.code for p in search_products(db, [synced], "bq01")] == ["BQ01"]
    assert search_products(db, [synced], "khong co") == []
    assert search_products(db, [synced], "100%") == []

    d = product_detail(db, synced, 101, TenantSettings(), TODAY)
    assert [s.branch_name for s in d.stock] == ["Kho Hà Nội", "Kho Hải Phòng"]
    assert [b.batch_name for b in d.batches] == ["L3", "L1", "L2", "L4"]
    assert [u.code for u in d.other_units] == ["BQ01-T"]
    text = "\n".join(format_product_detail(d, NOW))
    assert "1 Thùng = 12 Hộp" in text and "Tổng: <b>65</b> Hộp" in text


def test_text_helpers():
    assert unaccent("Bánh Quy Bơ ĐẶC BIỆT  ") == "banh quy bo dac biet"
    assert fmt_qty(1234567) == "1.234.567" and fmt_qty(2.5) == "2,5" and fmt_qty(None) == "-"
    parts = split_message("\n".join(f"dòng {i}" for i in range(2000)), limit=500)
    assert all(len(p) <= 500 for p in parts) and sum(p.count("\n") + 1 for p in parts) == 2000
