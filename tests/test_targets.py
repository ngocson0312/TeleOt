from datetime import datetime
from io import BytesIO

from openpyxl import Workbook, load_workbook

from app.config import TenantSettings
from app.reports.staff import month_progress
from app.reports.targets import HEADERS, import_targets, parse_month, parse_value, summary_text, template_workbook
from tests.fakes import TODAY

TUAN, LAN, ADMIN = 7, 8, 1


def xlsx(rows, headers=HEADERS) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Chỉ tiêu"
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_good_file(db, synced):
    data = xlsx([
        ["tuan", "Tuấn", "11/2026", "Doanh thu", None, "10tr"],
        [None, "Lan", datetime(2026, 11, 1), "DT", None, 4_000_000],
        ["tuan", None, "T11/2026", "mặt hàng", "BQ01-T", 3],  # 3 thùng = 36 hộp of BQ01
        ["lananh", "Lan Anh", "11/2026", "doanh thu", None, None],  # blank: skipped
        ["1", "Chủ cửa hàng", "11/2026", "doanh thu", None, "1.000.000đ"],  # Super Admin by id
    ])
    res = import_targets(db, synced, data, TODAY)
    assert res.errors == []
    assert summary_text(res).startswith("✅ Đã nạp 4 chỉ tiêu tháng 11/2026 cho 3 nhân viên: 3 doanh thu, 1 mặt hàng.")
    prog = month_progress(db, synced, "2026-11", TODAY, TenantSettings())
    assert [(t.kind, t.product_code, t.target) for t in prog[TUAN].targets] == [
        ("revenue", None, 10_000_000), ("product", "BQ01", 36)]
    assert prog[ADMIN].targets[0].target == 1_000_000

    # a second file for the same month replaces the first
    res = import_targets(db, synced, xlsx([["lan", "Lan", "11/2026", "doanh thu", None, 5_000_000]]), TODAY)
    prog = month_progress(db, synced, "2026-11", TODAY, TenantSettings())
    assert list(prog) == [LAN] and prog[LAN].targets[0].target == 5_000_000


def test_bad_file_is_rejected_whole(db, synced):
    import_targets(db, synced, xlsx([["tuan", "Tuấn", "11/2026", "doanh thu", None, 1]]), TODAY)
    data = xlsx([
        ["tuan", "Tuấn", "11/2026", "doanh thu", None, 9],
        ["ai", "Ai đó", "11/2026", "doanh thu", None, 5],
        [None, "Không Có", "11/2026", "doanh thu", None, 5],
        ["lan", "Lan", "13/2026", "doanh thu", None, 5],
        ["lan", "Lan", "11/2026", "thưởng", None, 5],
        ["lan", "Lan", "11/2026", "mặt hàng", None, 5],
        ["lan", "Lan", "11/2026", "mặt hàng", "ZZZ", 5],
        ["lan", "Lan", "11/2026", "doanh thu", None, "-3"],
        ["tuan", "Tuấn", "11/2026", "doanh thu", None, 7],
    ])
    res = import_targets(db, synced, data, TODAY)
    assert res.errors == [
        "Dòng 3: không có nhân viên mã “ai”",
        "Dòng 4: không có nhân viên tên “Không Có”",
        "Dòng 5: tháng “13/2026” không hợp lệ (ghi dạng 11/2026)",
        "Dòng 6: loại “thưởng” không hợp lệ (ghi “doanh thu” hoặc “mặt hàng”)",
        "Dòng 7: chỉ tiêu mặt hàng cần Mã hàng",
        "Dòng 8: không có mã hàng “ZZZ”",
        "Dòng 9: chỉ tiêu “-3” không phải số dương",
        "Dòng 10: trùng với dòng 2",
    ]
    assert summary_text(res).startswith("❌ File có lỗi, chưa nạp chỉ tiêu nào.")
    # the earlier target is untouched
    assert month_progress(db, synced, "2026-11", TODAY, TenantSettings())[TUAN].targets[0].target == 1


def test_not_a_template(db, synced):
    assert "Không thấy dòng tiêu đề" in import_targets(db, synced, xlsx([[1, 2]], ["a", "b"]), TODAY).errors[0]
    assert "Không đọc được file" in import_targets(db, synced, b"not excel", TODAY).errors[0]
    assert "cột Chỉ tiêu đang trống" in import_targets(db, synced, xlsx([]), TODAY).errors[0]


def test_template_round_trip(db, synced):
    import_targets(db, synced, xlsx([
        ["tuan", "Tuấn", "11/2026", "doanh thu", None, 10_000_000],
        ["tuan", "Tuấn", "11/2026", "mặt hàng", "BQ01", 40],
    ]), TODAY)
    data = template_workbook(db, synced, "2026-11")
    wb = load_workbook(BytesIO(data))
    assert wb.sheetnames == ["Chỉ tiêu", "Hướng dẫn", "Danh sách hàng"]
    rows = [r for r in wb["Chỉ tiêu"].iter_rows(min_row=2, values_only=True)]
    assert rows[:2] == [("tuan", "Tuấn", "11/2026", "doanh thu", None, 10_000_000),
                        ("tuan", "Tuấn", "11/2026", "mặt hàng", "BQ01", 40)]
    # every other seller gets an empty revenue row, Super Admin by id
    assert {r[1] for r in rows[2:]} == {"Chủ cửa hàng", "Lan", "Lan Anh"} and rows[2][0] == "1"
    assert ("BQ01-T", "Bánh quy bơ Danisa (Thùng)", "Thùng", "1 Thùng = 12 Hộp") in list(
        wb["Danh sách hàng"].iter_rows(min_row=2, values_only=True))
    # sending the template back unchanged keeps the same targets
    res = import_targets(db, synced, data, TODAY)
    assert res.errors == [] and len(res.targets) == 2


def test_parse_helpers():
    assert parse_month("tháng 3/2027", TODAY) == "2027-03"
    assert parse_month("2026-12", TODAY) == "2026-12"
    assert parse_month(12, TODAY) == "2026-12"
    assert parse_month("abc", TODAY) is None
    assert parse_value("150tr") == 150e6 and parse_value("1,5 triệu") == 1.5e6
    assert parse_value("12.000.000") == 12e6 and parse_value("500k") == 5e5
    assert parse_value("") is None and parse_value("mười") is None
