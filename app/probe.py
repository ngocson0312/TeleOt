"""Phase 0: call the real API once and answer the open questions in section 3 of the plan.

Raw responses are saved under data/probe/<retailer>/ (gitignored: real business data).
ket-qua.txt holds only field names, types and counts, so it is safe to share.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from app.kiotviet.client import KiotVietAuthError, KiotVietClient, KiotVietError
from app.sync.parse import invoice_details
from app.timeutil import fmt_kv_datetime, now_local

ICON = {True: "✔", False: "✘", None: "?"}


@dataclass
class Check:
    title: str
    answer: str
    ok: bool | None = True


def _keys(items: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for it in items:
        for k in it:
            seen.setdefault(k, None)
    return list(seen)


def _first_present(keys: list[str], *names: str) -> str | None:
    return next((n for n in names if n in keys), None)


class Probe:
    def __init__(self, client: KiotVietClient, out_dir: Path):
        self.client = client
        self.out = out_dir
        self.checks: list[Check] = []

    def add(self, title: str, answer: str, ok: bool | None = True) -> None:
        self.checks.append(Check(title, answer, ok))

    def save(self, name: str, data) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def run(self, now: datetime | None = None) -> list[Check]:
        now = now or now_local()
        if not await self._token():
            return self.checks
        for step in (self._branches, self._products, self._invoices, self._returns, self._users):
            try:
                await step(now)
            except KiotVietError as e:
                self.add(step.__name__.strip("_"), f"lỗi: {e}", False)
        self._rate_headers()
        return self.checks

    async def _token(self) -> bool:
        works = {}
        for param in ("scopes", "scope"):
            try:
                await self.client.fetch_token(param)
                works[param] = True
            except KiotVietAuthError as e:
                works[param] = False
                last = e
        if not any(works.values()):
            self.add("Lấy token", f"không lấy được token với cả 'scopes' và 'scope': {last}. "
                     "Kiểm tra lại tên gian hàng, Client ID, Client Secret.", False)
            return False
        used = [k for k, v in works.items() if v]
        self.add("Body lấy token dùng scopes hay scope", "chạy được với: " + ", ".join(used))
        return True

    async def _branches(self, now) -> None:
        page = await self.client.get_all("/branches")
        self.save("branches", page.items)
        self.add("Chi nhánh / kho", f"{len(page.items)} chi nhánh")

    async def _products(self, now) -> None:
        plain = await self.client.get("/products", {"pageSize": 20, "currentItem": 0})
        self.save("products_khong_includeInventory", plain)
        plain_items = plain.get("data") or []
        has_inv = any(it.get("inventories") for it in plain_items)
        self.add("inventories có cần includeInventory=true không",
                 "không cần, vẫn trả về" if has_inv else "có, thiếu tham số thì không trả tồn kho")

        full = await self.client.get("/products", {"pageSize": 100, "currentItem": 0, "includeInventory": "true",
                                                   "includeBatchExpires": "true", "includeRemoveIds": "true"})
        self.save("products_day_du", full)
        items = full.get("data") or []
        self.add("Số mặt hàng trên KiotViet", f"{full.get('total')} (so với số trên màn hình KiotViet)")
        pkeys = _keys(items)
        invs = [inv for it in items for inv in (it.get("inventories") or [])]
        ikeys = _keys(invs)
        self.add("Trường của inventories", ", ".join(ikeys) or "(trống)", bool(ikeys))
        mn = _first_present(ikeys, "minQuantity", "minQuality")
        mx = _first_present(ikeys, "maxQuantity", "maxQuality")
        self.add("Tên trường định mức tồn", f"min: {mn or 'KHÔNG CÓ'}, max: {mx or 'KHÔNG CÓ'}", bool(mn and mx))
        if mn:
            set_min = sum(1 for inv in invs if (inv.get(mn) or 0) > 0)
            self.add("Số dòng tồn có đặt định mức tối thiểu", f"{set_min}/{len(invs)} (trang đầu)",
                     None if set_min == 0 else True)

        flag = _first_present(pkeys, "isBatchExpireControl")
        batch_products = [it for it in items if it.get("isBatchExpireControl")]
        batches = [b for it in items for b in (it.get("productBatchExpires") or [])]
        self.add("Cờ quản lý lô / hạn dùng", f"trường {flag}: {len(batch_products)}/{len(items)} hàng bật"
                 if flag else "không thấy trường isBatchExpireControl", bool(flag))
        self.add("Lô (productBatchExpires)", f"{len(batches)} lô ở trang đầu; trường: {', '.join(_keys(batches)) or '-'}",
                 None if not batches else True)
        if batches:
            bkeys = _keys(batches)
            self.add("Lô có tồn theo kho không", "có onHand và branchId" if {"onHand", "branchId"} <= set(bkeys)
                     else f"thiếu: {sorted({'onHand', 'branchId'} - set(bkeys))}", {"onHand", "branchId"} <= set(bkeys))

        units = [it for it in items if it.get("masterUnitId")]
        self.add("Hàng nhiều đơn vị tính (thùng/hộp)",
                 f"{len(units)} dòng có masterUnitId; có conversionValue: "
                 f"{all('conversionValue' in u for u in units) if units else '-'}; "
                 f"có inventories: {any(u.get('inventories') for u in units) if units else '-'}",
                 None)
        self.add("Trường của sản phẩm", ", ".join(pkeys))

    async def _invoices(self, now) -> None:
        start = datetime.combine(now.date() - timedelta(days=1), time.min)
        end = datetime.combine(now.date() - timedelta(days=1), time.max).replace(microsecond=0)
        page = await self.client.get_all("/invoices", {"fromPurchaseDate": fmt_kv_datetime(start),
                                                       "toPurchaseDate": fmt_kv_datetime(end),
                                                       "includePayment": "false", "includeInvoiceDelivery": "false"})
        self.save("invoices_hom_qua", page.items)
        self.add("Hóa đơn hôm qua", f"{len(page.items)} hóa đơn ngày {start:%d/%m/%Y} (so với KiotViet)")
        if not page.items:
            self.add("invoiceDetails là mảng hay object", "hôm qua không có hóa đơn, chạy lại sau", None)
            return
        shapes = Counter(type(it.get("invoiceDetails")).__name__ for it in page.items)
        self.add("invoiceDetails là mảng hay object", ", ".join(f"{k}: {v}" for k, v in shapes.items()))
        with_lines = sum(1 for it in page.items if invoice_details(it))
        self.add("Hóa đơn có dòng hàng trong danh sách", f"{with_lines}/{len(page.items)}",
                 with_lines == len(page.items))
        keys = _keys(page.items)
        missing = [k for k in ("soldById", "soldByName", "customerId", "purchaseDate", "status", "total") if k not in keys]
        self.add("Trường hóa đơn cần dùng", "đủ" if not missing else f"thiếu: {', '.join(missing)}", not missing)
        lines = [d for it in page.items for d in invoice_details(it)]
        self.add("Trường dòng hóa đơn", ", ".join(_keys(lines)))
        status = Counter(it.get("status") for it in page.items)
        self.add("Trạng thái hóa đơn", ", ".join(f"{k}: {v}" for k, v in sorted(status.items(), key=str)))
        self._line_formula(page.items)

    def _line_formula(self, invoices: list[dict]) -> None:
        """5.5: which line formula adds up to the invoice? Compared with total + invoice-level discount."""
        formulas = {
            "subTotal": lambda d: d.get("subTotal"),
            "số lượng × (giá − giảm)": lambda d: d.get("quantity", 0) * (d.get("price", 0) - (d.get("discount") or 0)),
            "số lượng × giá − giảm": lambda d: d.get("quantity", 0) * d.get("price", 0) - (d.get("discount") or 0),
        }
        hits = Counter()
        checked = 0
        for it in invoices:
            lines = invoice_details(it)
            if not lines or it.get("total") is None:
                continue
            checked += 1
            expected = float(it["total"]) + float(it.get("discount") or 0)
            for name, f in formulas.items():
                values = [f(d) for d in lines]
                if None not in values and abs(sum(values) - expected) < 1:
                    hits[name] += 1
        if not checked:
            return
        self.add("Cộng dòng hàng khớp tổng hóa đơn (total + discount)",
                 "; ".join(f"{name}: {hits[name]}/{checked}" for name in formulas)
                 + ". Doanh thu nhân viên mặc định theo tổng hóa đơn; đối soát bằng lệnh staff-check.", None)

    async def _returns(self, now) -> None:
        page = await self.client.get("/returns", {"pageSize": 20, "currentItem": 0})
        self.save("returns", page)
        items = page.get("data") or []
        if not items:
            self.add("Trả hàng có soldById không", "chưa có phiếu trả hàng nào để kiểm tra", None)
            return
        keys = _keys(items)
        has = "soldById" in keys
        self.add("Trả hàng có soldById không", "có" if has else "không, sẽ gán theo hóa đơn gốc (invoiceId)",
                 has or "invoiceId" in keys)
        self.add("Trường phiếu trả hàng", ", ".join(keys))

    async def _users(self, now) -> None:
        page = await self.client.get_all("/users")
        self.save("users", page.items)
        self.add("Nhân viên (users)", f"{len(page.items)} người (không tính Super Admin)")

    def _rate_headers(self) -> None:
        h = self.client.last_headers or {}
        rate = {k: v for k, v in h.items() if "rate" in k.lower() or "limit" in k.lower()}
        self.add("Header giới hạn request", ", ".join(f"{k}={v}" for k, v in rate.items())
                 or "không có header nào, chưa biết 5.000/giờ tính theo gian hàng hay client", None if not rate else True)
        self.add("Số request đã dùng", str(self.client.request_count))


def render(checks: list[Check], retailer: str, when: datetime) -> str:
    lines = [f"Kết quả kiểm tra KiotViet API, gian hàng {retailer}, lúc {when:%H:%M %d/%m/%Y}", ""]
    for c in checks:
        lines.append(f"{ICON[c.ok]} {c.title}: {c.answer}")
    lines += ["", "✔ ổn   ✘ cần xử lý   ? cần xem thêm",
              "Chưa kiểm tra tự động được: gói KiotViet nào có mục 'Thiết lập kết nối API'."]
    return "\n".join(lines)


async def run_probe(retailer: str, client_id: str, client_secret: str, out_dir: Path) -> str:
    when = now_local()
    async with KiotVietClient(retailer, client_id, client_secret, max_retries=2) as client:
        checks = await Probe(client, out_dir).run(when)
    text = render(checks, retailer, when)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ket-qua.txt").write_text(text, encoding="utf-8")
    return text
