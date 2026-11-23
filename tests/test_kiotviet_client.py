import asyncio
import inspect

import httpx
import pytest

from app.kiotviet.client import API_BASE, TOKEN_URL, KiotVietAuthError, KiotVietClient, KiotVietError


class FakeKiotViet:
    """Records every request; answers token + paged GETs."""

    def __init__(self, total=250, fail_first: dict | None = None):
        self.requests: list[httpx.Request] = []
        self.total = total
        self.tokens_issued = 0
        self.fail_first = dict(fail_first or {})  # status -> times
        self.valid_token = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            self.tokens_issued += 1
            self.valid_token = f"tok{self.tokens_issued}"
            return httpx.Response(200, json={"access_token": self.valid_token, "expires_in": 86400})
        for status, times in list(self.fail_first.items()):
            if times > 0:
                self.fail_first[status] -= 1
                return httpx.Response(status, headers={"Retry-After": "0"})
        if request.headers["Authorization"] != f"Bearer {self.valid_token}":
            return httpx.Response(401)
        offset = int(request.url.params.get("currentItem", 0))
        size = int(request.url.params.get("pageSize", 100))
        ids = list(range(offset, min(offset + size, self.total)))
        return httpx.Response(200, json={"total": self.total, "data": [{"id": i} for i in ids], "removeIds": [9] if offset == 0 else []})


def make_client(fake: FakeKiotViet) -> tuple[KiotVietClient, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return KiotVietClient("shop", "cid", "secret", http=http, sleep=fake_sleep), sleeps


def test_paging_follows_total_and_sends_headers():
    fake = FakeKiotViet(total=250)
    client, _ = make_client(fake)
    result = asyncio.run(client.get_all("/products", {"includeInventory": "true"}))
    assert [it["id"] for it in result.items] == list(range(250))
    assert result.total == 250
    assert result.remove_ids == [9]
    api_calls = [r for r in fake.requests if str(r.url) != TOKEN_URL]
    assert [r.url.params["currentItem"] for r in api_calls] == ["0", "100", "200"]
    assert all(r.headers["Retailer"] == "shop" for r in api_calls)
    assert fake.tokens_issued == 1  # token cached across pages


def test_token_request_sends_client_credentials():
    fake = FakeKiotViet(total=1)
    client, _ = make_client(fake)
    asyncio.run(client.get("/branches", {"pageSize": 100, "currentItem": 0}))
    token_req = fake.requests[0]
    body = token_req.content.decode()
    assert "grant_type=client_credentials" in body
    assert "scopes=PublicApi.Access" in body and "scope=PublicApi.Access" in body


def test_401_refreshes_token_once():
    fake = FakeKiotViet(total=1)
    client, _ = make_client(fake)
    asyncio.run(client.get("/branches"))
    fake.valid_token = "revoked"  # server stops accepting tok1
    data = asyncio.run(client.get("/branches"))
    assert data["total"] == 1
    assert fake.tokens_issued == 2


def test_retries_429_and_5xx_with_backoff():
    fake = FakeKiotViet(total=1, fail_first={429: 1, 503: 1})
    client, sleeps = make_client(fake)
    data = asyncio.run(client.get("/branches", {"pageSize": 100, "currentItem": 0}))
    assert data["total"] == 1
    assert len(sleeps) == 2


def test_gives_up_after_max_retries():
    fake = FakeKiotViet(total=1, fail_first={500: 99})
    client, sleeps = make_client(fake)
    with pytest.raises(KiotVietError):
        asyncio.run(client.get("/branches"))
    assert len(sleeps) == 5


def test_bad_credentials_raise_auth_error():
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_client"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = KiotVietClient("shop", "cid", "wrong", http=http)
    with pytest.raises(KiotVietAuthError):
        asyncio.run(client.get("/branches"))


def test_client_has_no_write_methods():
    public = {name for name, _ in inspect.getmembers(KiotVietClient, inspect.isfunction) if not name.startswith("_")}
    assert public == {"aclose", "fetch_token", "get", "get_all"}
    src = inspect.getsource(KiotVietClient)
    # The only POST is the token request.
    assert src.count(".post(") == 1 and "TOKEN_URL" in src
    for verb in (".put(", ".patch(", ".delete(", ".request("):
        assert verb not in src
    assert API_BASE.startswith("https://public.kiotapi.com")
