"""Excel attachment with the full lists (Telegram messages only show the top N)."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.reports.inventory import ExpiryRow, InventoryReport, StockRow
from app.textutil import unaccent

HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
LEVEL_FILL = {
    "expired": PatternFill("solid", fgColor="F4B6B6"),
    "red": PatternFill("solid", fgColor="FCE4E4"),
    "orange": PatternFill("solid", fgColor="FFF2CC"),
}
LEVEL_TEXT = {"expired": "Đã hết hạn", "red": "Đỏ", "orange": "Cam"}

STOCK_HEADERS = ["Mã hàng", "Tên hàng", "Nhóm", "ĐVT", "Kho", "Tồn", "Tối thiểu", "Tối đa",
                 "Bán TB/ngày", "Đủ bán (ngày)", "Bán lần cuối", "Giá trị tồn", "Lý do"]
EXPIRY_HEADERS = ["Mã hàng", "Tên hàng", "ĐVT", "Kho", "Lô", "Hạn sử dụng", "Còn (ngày)", "Tồn lô", "Mức"]


def _sheet(wb: Workbook, title: str, headers: list[str], widths: list[int]):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
    return ws


def _stock_rows(ws, rows: list[StockRow]) -> None:
    for r in rows:
        ws.append([
            r.code, r.name, r.category or "", r.unit or "", r.branch_name, r.on_hand, r.min_qty, r.max_qty,
            round(r.avg_daily, 2) if r.avg_daily else 0,
            round(r.days_cover, 1) if r.days_cover is not None else None,
            r.last_sold.date() if r.last_sold else None,
            round(r.value) if r.cost else None, r.reason,
        ])
        ws.cell(row=ws.max_row, column=11).number_format = "DD/MM/YYYY"
        ws.cell(row=ws.max_row, column=12).number_format = "#,##0"


def _expiry_rows(ws, rows: list[ExpiryRow]) -> None:
    for r in rows:
        ws.append([r.code, r.name, r.unit or "", r.branch_name, r.batch_name, r.expire_date,
                   r.days_left, r.on_hand, LEVEL_TEXT[r.level]])
        ws.cell(row=ws.max_row, column=6).number_format = "DD/MM/YYYY"
        for c in range(1, len(EXPIRY_HEADERS) + 1):
            ws.cell(row=ws.max_row, column=c).fill = LEVEL_FILL[r.level]


def inventory_workbook(report: InventoryReport) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    stock_widths = [14, 40, 16, 8, 18, 10, 10, 10, 12, 12, 13, 14, 40]
    _stock_rows(_sheet(wb, "Sắp hết hàng", STOCK_HEADERS, stock_widths), report.low)
    _expiry_rows(_sheet(wb, "Sắp hết hạn", EXPIRY_HEADERS, [14, 40, 8, 18, 14, 13, 11, 10, 12]), report.expiring)
    _stock_rows(_sheet(wb, "Tồn nhiều - bán chậm", STOCK_HEADERS, stock_widths), report.over)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def expiring_workbook(rows: list[ExpiryRow]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    _expiry_rows(_sheet(wb, "Sắp hết hạn", EXPIRY_HEADERS, [14, 40, 8, 18, 14, 13, 11, 10, 12]), rows)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def report_filename(tenant_name: str, kind: str, stamp: datetime) -> str:
    slug = "-".join(unaccent(tenant_name).split()) or "npp"
    return f"{kind}-{slug}-{stamp:%Y%m%d-%H%M}.xlsx"
