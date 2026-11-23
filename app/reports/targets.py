"""Section 5.6: monthly targets from an Excel file.

Columns: Mã NV | Tên NV | Tháng | Loại (doanh thu / mặt hàng) | Mã hàng | Chỉ tiêu.
A file with any error is rejected as a whole, so targets never end up half-loaded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Product, Staff, Target
from app.db.session import Database
from app.reports.staff import UNKNOWN, staff_names
from app.textutil import fmt_qty, unaccent

HEADERS = ["Mã NV", "Tên NV", "Tháng", "Loại", "Mã hàng", "Chỉ tiêu"]
COLUMNS = {
    "code": {"ma nv", "ma nhan vien", "ten dang nhap"},
    "name": {"ten nv", "ten nhan vien", "nhan vien", "ho ten"},
    "month": {"thang", "ky"},
    "kind": {"loai", "loai chi tieu"},
    "product": {"ma hang", "ma sp", "ma san pham"},
    "value": {"chi tieu", "muc tieu"},
}
KINDS = {
    "revenue": {"doanh thu", "dt", "doanh so", "ds", "tien"},
    "product": {"mat hang", "san pham", "sp", "hang", "so luong", "sl"},
}
MAX_ERRORS = 20


@dataclass
class ParsedTarget:
    row: int
    staff_id: int
    month: str
    kind: str
    product_id: int | None
    value: float


@dataclass
class ImportResult:
    targets: list[ParsedTarget] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def months(self) -> list[str]:
        return sorted({t.month for t in self.targets})


@dataclass
class StaffRef:
    id: int
    name: str
    login: str


def _norm(v) -> str:
    return unaccent(str(v)) if v is not None else ""


def parse_month(v, today: date) -> str | None:
    if isinstance(v, (datetime, date)):
        return f"{v.year}-{v.month:02d}"
    if isinstance(v, (int, float)) and float(v).is_integer() and 1 <= int(v) <= 12:
        return f"{today.year}-{int(v):02d}"
    t = re.sub(r"^(thang|t)\s*", "", _norm(v))
    m = re.fullmatch(r"(\d{1,2})\s*[/.-]\s*(\d{4})", t)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r"(\d{4})\s*[/.-]\s*(\d{1,2})", t)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
        elif t.isdigit():
            year, month = today.year, int(t)
        else:
            return None
    return f"{year}-{month:02d}" if 1 <= month <= 12 and 2000 <= year <= 2100 else None


def parse_value(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    t = _norm(v).replace(" ", "").replace("vnd", "").rstrip("d")
    if not t:
        return None
    mult = 1.0
    for suffix, factor in (("trieu", 1e6), ("tr", 1e6), ("k", 1e3)):
        if t.endswith(suffix):
            t, mult = t[: -len(suffix)], factor
            num = t.replace(",", ".")
            try:
                return float(num) * mult
            except ValueError:
                return None
    digits = t.replace(".", "").replace(",", "")
    return float(digits) if digits.isdigit() else None


def _header_map(row: tuple) -> dict[str, int] | None:
    found = {}
    for i, cell in enumerate(row):
        key = _norm(cell)
        key = re.sub(r"\s*\(.*\)$", "", key)  # "Loại (doanh thu / mặt hàng)"
        for col, names in COLUMNS.items():
            if key in names and col not in found:
                found[col] = i
    return found if {"month", "kind", "value"} <= found.keys() and ({"code", "name"} & found.keys()) else None


def parse_targets_xlsx(data: bytes, staff: list[StaffRef], products: list[Product], today: date) -> ImportResult:
    res = ImportResult()
    try:
        wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception:
        res.errors.append("Không đọc được file. Gửi file .xlsx theo mẫu (gõ /chitieu để lấy mẫu).")
        return res
    ws = wb["Chỉ tiêu"] if "Chỉ tiêu" in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header_at, cols = next(((i, m) for i, r in enumerate(rows[:10]) if (m := _header_map(r))), (None, None))
    if cols is None:
        res.errors.append("Không thấy dòng tiêu đề. Cần các cột: " + " | ".join(HEADERS))
        return res

    by_login = {_norm(s.login): s for s in staff if s.login}
    by_id = {str(s.id): s for s in staff}
    by_name: dict[str, list[StaffRef]] = {}
    for s in staff:
        by_name.setdefault(_norm(s.name), []).append(s)
    by_code = {_norm(p.code): p for p in products if not p.removed}
    seen: dict[tuple, int] = {}

    def cell(r: tuple, col: str):
        i = cols.get(col)
        return r[i] if i is not None and i < len(r) else None

    for n, r in enumerate(rows[header_at + 1:], start=header_at + 2):
        if len(res.errors) >= MAX_ERRORS:
            break
        if not any(v not in (None, "") for v in r):
            continue
        err = lambda msg: res.errors.append(f"Dòng {n}: {msg}")  # noqa: E731
        raw_value = cell(r, "value")
        if raw_value in (None, ""):
            continue  # template row left blank = no target
        code, name = cell(r, "code"), cell(r, "name")
        who = None
        if code not in (None, ""):
            who = by_login.get(_norm(code)) or by_id.get(_norm(code))
            if who is None:
                err(f"không có nhân viên mã “{code}”")
                continue
        else:
            matches = by_name.get(_norm(name), [])
            if len(matches) != 1:
                err(f"không có nhân viên tên “{name}”" if not matches else f"có {len(matches)} người tên “{name}”, ghi thêm Mã NV")
                continue
            who = matches[0]
        month = parse_month(cell(r, "month"), today)
        if month is None:
            err(f"tháng “{cell(r, 'month')}” không hợp lệ (ghi dạng 11/2026)")
            continue
        kind_raw = _norm(cell(r, "kind"))
        kind = next((k for k, words in KINDS.items() if kind_raw in words), None)
        if kind is None:
            err(f"loại “{cell(r, 'kind')}” không hợp lệ (ghi “doanh thu” hoặc “mặt hàng”)")
            continue
        value = parse_value(raw_value)
        if value is None or value <= 0:
            err(f"chỉ tiêu “{raw_value}” không phải số dương")
            continue
        product_id = None
        if kind == "product":
            pcode = cell(r, "product")
            p = by_code.get(_norm(pcode)) if pcode not in (None, "") else None
            if p is None:
                err(f"không có mã hàng “{pcode or ''}”" if pcode else "chỉ tiêu mặt hàng cần Mã hàng")
                continue
            if not p.is_base_unit:  # target in thùng -> convert to the base unit
                value *= p.conversion_value or 1
                product_id = p.master_unit_id
            else:
                product_id = p.id
        key = (who.id, month, kind, product_id)
        if key in seen:
            err(f"trùng với dòng {seen[key]}")
            continue
        seen[key] = n
        res.targets.append(ParsedTarget(n, who.id, month, kind, product_id, value))
    if not res.targets and not res.errors:
        res.errors.append("Không có dòng chỉ tiêu nào (cột Chỉ tiêu đang trống).")
    return res


def staff_refs(db: Database, tenant_id: int) -> list[StaffRef]:
    with db.session() as s:
        users = {st.id: st for st in s.scalars(select(Staff).where(Staff.tenant_id == tenant_id))}
    refs = []
    for sid, name in staff_names(db, tenant_id).items():
        st = users.get(sid)
        if sid == UNKNOWN or (st and st.removed):
            continue
        refs.append(StaffRef(sid, name, st.user_name if st else ""))
    return sorted(refs, key=lambda r: r.name)


def import_targets(db: Database, tenant_id: int, data: bytes, today: date) -> ImportResult:
    with db.session() as s:
        products = list(s.scalars(select(Product).where(Product.tenant_id == tenant_id)))
    res = parse_targets_xlsx(data, staff_refs(db, tenant_id), products, today)
    if not res.errors:
        with db.session() as s:
            save_targets(s, tenant_id, res)
    return res


def save_targets(session: Session, tenant_id: int, res: ImportResult) -> None:
    """Replace every target of the months present in the file."""
    session.execute(delete(Target).where(Target.tenant_id == tenant_id, Target.month.in_(res.months)))
    session.add_all(Target(tenant_id=tenant_id, staff_id=t.staff_id, month=t.month, kind=t.kind,
                           product_id=t.product_id, value=t.value) for t in res.targets)


def summary_text(res: ImportResult) -> str:
    if res.errors:
        lines = ["❌ File có lỗi, chưa nạp chỉ tiêu nào. Sửa rồi gửi lại:"]
        lines += [f"• {e}" for e in res.errors[:MAX_ERRORS]]
        return "\n".join(lines)
    months = ", ".join(f"{m[5:]}/{m[:4]}" for m in res.months)
    rev = sum(1 for t in res.targets if t.kind == "revenue")
    prod = len(res.targets) - rev
    people = len({t.staff_id for t in res.targets})
    return (f"✅ Đã nạp {len(res.targets)} chỉ tiêu tháng {months} cho {people} nhân viên: "
            f"{rev} doanh thu, {prod} mặt hàng.\nChỉ tiêu cũ của tháng này đã được thay.")


def template_workbook(db: Database, tenant_id: int, month: str) -> bytes:
    """Pre-filled template: one row per staff member, plus current targets of `month`."""
    refs = staff_refs(db, tenant_id)
    with db.session() as s:
        products = {p.id: p for p in s.scalars(select(Product).where(
            Product.tenant_id == tenant_id, Product.removed.is_(False), Product.is_active.is_(True)))}
        targets = list(s.scalars(select(Target).where(Target.tenant_id == tenant_id, Target.month == month)))
    month_text = f"{month[5:]}/{month[:4]}"
    wb = Workbook()
    ws = wb.active
    ws.title = "Chỉ tiêu"
    ws.append(HEADERS)
    have = {t.staff_id for t in targets if t.kind == "revenue"}
    by_id = {r.id: r for r in refs}
    for t in sorted(targets, key=lambda t: (by_id[t.staff_id].name if t.staff_id in by_id else "", t.kind != "revenue")):
        ref = by_id.get(t.staff_id)
        p = products.get(t.product_id) if t.product_id else None
        ws.append([(ref.login or str(ref.id)) if ref else str(t.staff_id), ref.name if ref else "", month_text,
                   "doanh thu" if t.kind == "revenue" else "mặt hàng", p.code if p else None, t.value])
    for r in refs:
        if r.id not in have:
            ws.append([r.login or str(r.id), r.name, month_text, "doanh thu", None, None])
    for i, w in enumerate([14, 24, 10, 12, 14, 16], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDEBF7")
    for row in ws.iter_rows(min_row=2, min_col=6, max_col=6):
        row[0].number_format = "#,##0"
    ws.freeze_panes = "A2"

    guide = wb.create_sheet("Hướng dẫn")
    for line in [
        "Cách điền chỉ tiêu",
        "",
        "• Mỗi dòng là một chỉ tiêu của một nhân viên trong một tháng.",
        "• Loại: “doanh thu” (số tiền) hoặc “mặt hàng” (số lượng của một mặt hàng, ghi Mã hàng).",
        "• Tháng ghi dạng 11/2026. Chỉ tiêu ghi số, ví dụ 150000000 hoặc 150tr.",
        "• Muốn thêm chỉ tiêu mặt hàng: thêm dòng mới, chép Mã NV, ghi loại “mặt hàng” và Mã hàng.",
        "• Dòng để trống cột Chỉ tiêu sẽ được bỏ qua.",
        "• Nạp file mới sẽ THAY toàn bộ chỉ tiêu cũ của các tháng có trong file.",
        "• Mã hàng xem ở trang “Danh sách hàng”. Ghi mã thùng thì bot tự quy ra đơn vị gốc.",
        "",
        "Gửi lại file này vào nhóm, ghi chú /chitieu (hoặc gửi ngay sau khi gõ /chitieu).",
    ]:
        guide.append([line])
    guide["A1"].font = Font(bold=True, size=13)
    guide.column_dimensions["A"].width = 100

    plist = wb.create_sheet("Danh sách hàng")
    plist.append(["Mã hàng", "Tên hàng", "ĐVT", "Quy đổi"])
    for p in sorted(products.values(), key=lambda p: p.code):
        conv = ""
        if not p.is_base_unit and p.master_unit_id in products:
            base = products[p.master_unit_id]
            conv = f"1 {p.unit or ''} = {fmt_qty(p.conversion_value)} {base.unit or ''}".strip()
        plist.append([p.code, p.name, p.unit or "", conv])
    for i, w in enumerate([16, 45, 10, 22], 1):
        plist.column_dimensions[get_column_letter(i)].width = w
        plist.cell(row=1, column=i).font = Font(bold=True)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
