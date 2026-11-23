"""A fake KiotViet shop for tests: 2 kho, a few products, 30 days of invoices, 3 sellers."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx

from app.kiotviet.client import TOKEN_URL

TODAY = date(2026, 11, 23)
NOW = datetime(2026, 11, 23, 8, 0, 0)


def d(days: int) -> str:
    return (datetime.combine(TODAY, datetime.min.time()) + timedelta(days=days)).isoformat() + ".0000000"


# Super Admin (id 1, "Chủ cửa hàng") sells too but is not returned by /users.
USERS = [
    {"id": 7, "userName": "tuan", "givenName": "Tuấn"},
    {"id": 8, "userName": "lan", "givenName": "Lan"},
    {"id": 9, "userName": "lananh", "givenName": "Lan Anh"},
]
RETURNS = [
    # no soldById: goes to the seller of invoice 1004 (day -4 = Lan)
    {"id": 1, "code": "TH1", "invoiceId": 1004, "returnDate": d(-2).replace("T00:00:00", "T10:00:00"),
     "returnTotal": 12000, "status": 1, "returnDetails": [{"productId": 102, "quantity": 1, "price": 12000}]},
    # today, explicit seller Tuấn
    {"id": 2, "code": "TH2", "invoiceId": 3002, "soldById": 7, "returnDate": d(0).replace("T00:00:00", "T07:45:00"),
     "returnTotal": 15000, "status": 1, "returnDetails": [{"productId": 105, "quantity": 1, "price": 15000}]},
    # cancelled: ignored
    {"id": 3, "code": "TH3", "invoiceId": 1006, "returnDate": d(-1), "returnTotal": 999000, "status": 2,
     "returnDetails": [{"productId": 102, "quantity": 9, "price": 111000}]},
]
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
    """30 days at kho 1: 102 x10/day, 101 x2/day, 105 x5/day (315.000đ); odd days Tuấn, even days Lan.
    Plus: one thùng of 101 (day -3, Tuấn), one cancelled invoice, three sales this morning."""
    out = []
    iid = 1000
    for day in range(1, 31):
        iid += 1
        seller = (7, "Tuấn") if day % 2 else (8, "Lan")
        out.append({
            "id": iid, "code": f"HD{iid}", "purchaseDate": d(-day).replace("T00:00:00", "T09:30:00"), "branchId": 1,
            "soldById": seller[0], "soldByName": seller[1], "customerId": 500 + day % 4, "status": 1, "total": 315000,
            "invoiceDetails": [
                {"productId": 102, "quantity": 10, "price": 12000, "discount": 0},
                {"productId": 101, "quantity": 2, "price": 60000, "discount": 0},
                {"productId": 105, "quantity": 5, "price": 15000, "discount": 0},
            ],
        })
    out.append({"id": 2001, "code": "HD2001", "purchaseDate": d(-3), "branchId": 1, "soldById": 7, "soldByName": "Tuấn",
                "status": 1, "total": 700000,
                "invoiceDetails": {"productId": 103, "quantity": 1, "price": 700000, "discount": 0}})
    out.append({"id": 2002, "code": "HD2002", "purchaseDate": d(-2), "branchId": 1, "soldById": 7, "soldByName": "Tuấn",
                "status": 2, "total": 50, "invoiceDetails": [{"productId": 104, "quantity": 50, "price": 1, "discount": 0}]})
    morning = d(0).replace("T00:00:00", "T0{}:00:00")
    out += [
        {"id": 3001, "code": "HD3001", "purchaseDate": morning.format(6), "branchId": 1, "soldById": 8,
         "soldByName": "Lan", "customerId": 600, "status": 1, "total": 60000,
         "invoiceDetails": [{"productId": 105, "quantity": 4, "price": 16000, "discount": 1000}]},
        {"id": 3002, "code": "HD3002", "purchaseDate": morning.format(7), "branchId": 1, "soldById": 7,
         "soldByName": "Tuấn", "customerId": 601, "status": 1, "total": 30000,
         "invoiceDetails": [{"productId": 105, "quantity": 2, "price": 15000, "discount": 0}]},
        {"id": 3003, "code": "HD3003", "purchaseDate": morning.format(7).replace(":00:00", ":30:00"), "branchId": 1,
         "soldById": 1, "soldByName": "Chủ cửa hàng", "status": 1, "total": 20000,
         "invoiceDetails": [{"productId": 105, "quantity": 1, "price": 20000, "discount": 0, "subTotal": 20000}]},
    ]
    return out


class FakeShop:
    def __init__(self):
        self.products = products()
        self.invoices = invoices()
        self.returns = [dict(r) for r in RETURNS]
        self.synced_once = False  # first /returns call returns everything
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
            if self.synced_once:
                return self._page(request, [r for r in self.returns if r.get("modifiedDate")])
            self.synced_once = True
            return self._page(request, self.returns)
        if path == "/users":
            return self._page(request, USERS)
        return httpx.Response(404)

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
