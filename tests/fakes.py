"""A fake KiotViet shop for tests: 2 kho, a few products, 90 days of invoices."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx

from app.kiotviet.client import TOKEN_URL

TODAY = date(2026, 11, 23)
NOW = datetime(2026, 11, 23, 8, 0, 0)


def d(days: int) -> str:
    return (datetime.combine(TODAY, datetime.min.time()) + timedelta(days=days)).isoformat() + ".0000000"


USERS = [{"id": 7, "userName": "tuan", "givenName": "Tuấn"}]
RETURNS = [{"id": 1, "code": "TH1", "invoiceId": 1001, "returnDate": "2026-11-20T10:00:00",
            "returnDetails": [{"productId": 102, "quantity": 1, "price": 12000}]}]
BRANCHES = [{"id": 1, "branchName": "Kho Hà Nội"}, {"id": 2, "branchName": "Kho Hải Phòng"}]


def products() -> list[dict]:
    return [
        {  # sells 2/day at kho 1, below min there; batches expiring
            "id": 101, "code": "BQ01", "name": "Bánh quy bơ", "fullName": "Bánh quy bơ Danisa", "unit": "Hộp",
            "categoryName": "Bánh quy", "type": 2, "isActive": True, "isBatchExpireControl": True,
            "inventories": [
                {"branchId": 1, "onHand": 5, "minQuality": 20, "maxQuality": 999999999, "cost": 50000},
                {"branchId": 2, "onHand": 60, "minQuality": 0, "maxQuality": 999999999, "cost": 50000},
            ],
            "productBatchExpires": [
                {"batchName": "L1", "expireDate": d(5), "onHand": 3, "branchId": 1},
                {"batchName": "L2", "expireDate": d(25), "onHand": 2, "branchId": 1},
                {"batchName": "L3", "expireDate": d(-2), "onHand": 4, "branchId": 2},
                {"batchName": "L4", "expireDate": d(200), "onHand": 56, "branchId": 2},
                {"batchName": "L0", "expireDate": d(3), "onHand": 0, "branchId": 2},  # sold out: ignore
            ],
        },
        {  # sells 10/day at kho 1 -> out of stock there; overstocked at kho 2
            "id": 102, "code": "KD01", "name": "Kẹo dừa Bến Tre", "unit": "Gói", "categoryName": "Kẹo",
            "type": 2, "isActive": True,
            "inventories": [
                {"branchId": 1, "onHand": 0, "minQuality": 0, "maxQuality": 999999999},
                {"branchId": 2, "onHand": 500, "minQuality": 0, "maxQuality": 200, "cost": 10000},
            ],
        },
        {  # extra unit of 101 (thùng 12 hộp): never reported on its own
            "id": 103, "code": "BQ01-T", "name": "Bánh quy bơ", "fullName": "Bánh quy bơ Danisa (Thùng)",
            "unit": "Thùng", "type": 2, "isActive": True, "masterUnitId": 101, "conversionValue": 12,
            "inventories": [{"branchId": 1, "onHand": 0.4, "minQuality": 0, "maxQuality": 999999999}],
        },
        {  # never sold: slow
            "id": 104, "code": "SK01", "name": "Snack khoai tây", "unit": "Gói", "categoryName": "Snack",
            "type": 2, "isActive": True,
            "inventories": [{"branchId": 1, "onHand": 300, "minQuality": 0, "maxQuality": 999999999, "cost": 8000}],
        },
        {  # healthy: sells 5/day, 100 in stock = 20 days
            "id": 105, "code": "SC01", "name": "Sô cô la sữa", "unit": "Thanh", "type": 2, "isActive": True,
            "inventories": [{"branchId": 1, "onHand": 100, "minQuality": 10, "maxQuality": 999999999}],
        },
        {"id": 106, "code": "OLD", "name": "Hàng ngừng bán", "type": 2, "isActive": False,
         "inventories": [{"branchId": 1, "onHand": 0, "minQuality": 5}]},
        {"id": 107, "code": "DV", "name": "Phí giao hàng", "type": 3, "isActive": True,
         "inventories": [{"branchId": 1, "onHand": 0, "minQuality": 0}]},
    ]


def invoices() -> list[dict]:
    """30 days: 102 x10/day, 101 x2/day, 105 x5/day at kho 1; one thùng of 101 on day -3; one cancelled."""
    out = []
    iid = 1000
    for day in range(1, 31):
        iid += 1
        out.append({
            "id": iid, "code": f"HD{iid}", "purchaseDate": d(-day).replace("T00:00:00", "T09:30:00"), "branchId": 1,
            "soldById": 7, "soldByName": "Tuấn", "customerId": 500 + day % 4, "status": 1, "total": 1,
            "invoiceDetails": [
                {"productId": 102, "quantity": 10, "price": 12000, "discount": 0},
                {"productId": 101, "quantity": 2, "price": 60000, "discount": 0},
                {"productId": 105, "quantity": 5, "price": 15000, "discount": 0},
            ],
        })
    out.append({"id": 2001, "code": "HD2001", "purchaseDate": d(-3), "branchId": 1, "soldById": 7, "status": 1,
                "total": 1, "invoiceDetails": {"productId": 103, "quantity": 1, "price": 700000, "discount": 0}})
    out.append({"id": 2002, "code": "HD2002", "purchaseDate": d(-2), "branchId": 1, "soldById": 7, "status": 2,
                "total": 1, "invoiceDetails": [{"productId": 104, "quantity": 50, "price": 1, "discount": 0}]})
    return out


class FakeShop:
    def __init__(self):
        self.products = products()
        self.invoices = invoices()
        self.requests: list[httpx.Request] = []

    def _page(self, request: httpx.Request, items: list[dict], **extra) -> httpx.Response:
        offset = int(request.url.params.get("currentItem", 0))
        size = int(request.url.params.get("pageSize", 100))
        return httpx.Response(200, json={"total": len(items), "pageSize": size,
                                         "data": items[offset:offset + size], **extra})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 86400, "token_type": "Bearer"})
        assert request.method == "GET", f"write call {request.method} {request.url}"
        path = request.url.path
        if path == "/branches":
            return self._page(request, BRANCHES)
        if path == "/products":
            return self._page(request, self.products, removeIds=[])
        if path == "/invoices":
            if "lastModifiedFrom" in request.url.params:
                return self._page(request, [i for i in self.invoices if i.get("modifiedDate")])
            return self._page(request, self.invoices)
        if path == "/returns":
            return self._page(request, RETURNS)
        if path == "/users":
            return self._page(request, USERS)
        return httpx.Response(404)

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
