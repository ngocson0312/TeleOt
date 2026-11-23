"""Read-only client for the KiotViet Public API.

The only non-GET request is the OAuth token request to id.kiotviet.vn.
This class has no way to POST/PUT/DELETE against public.kiotapi.com, on purpose:
the API key can write to the NPP's shop, the bot must never do it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

TOKEN_URL = "https://id.kiotviet.vn/connect/token"
API_BASE = "https://public.kiotapi.com"
SCOPE = "PublicApi.Access"
PAGE_SIZE = 100  # API maximum
MAX_PAGES = 2000
TOKEN_REFRESH_MARGIN = 300  # seconds before expiry

log = logging.getLogger(__name__)


class KiotVietError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class KiotVietAuthError(KiotVietError):
    pass


@dataclass
class PageResult:
    items: list[dict] = field(default_factory=list)
    remove_ids: list[int] = field(default_factory=list)
    total: int | None = None


class KiotVietClient:
    def __init__(
        self,
        retailer: str,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.AsyncClient | None = None,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.retailer = retailer
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._owns_http = http is None
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self.request_count = 0
        self.last_headers: httpx.Headers | None = None

    async def __aenter__(self) -> "KiotVietClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ---- token ---------------------------------------------------------

    async def fetch_token(self, scope_param: str = "both") -> dict:
        """POST the client-credentials request.

        The docs show both `scopes` and `scope`; by default send both.
        The probe command calls this with 'scopes' and 'scope' separately to find out.
        """
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "client_credentials",
        }
        if scope_param in ("both", "scopes"):
            data["scopes"] = SCOPE
        if scope_param in ("both", "scope"):
            data["scope"] = SCOPE
        try:
            resp = await self._http.post(TOKEN_URL, data=data)
        except httpx.TransportError as e:
            raise KiotVietAuthError(f"Lỗi mạng khi lấy token: {e}") from e
        if resp.status_code != 200:
            raise KiotVietAuthError(
                f"Lấy token thất bại: HTTP {resp.status_code} {resp.text[:200]}",
                status=resp.status_code,
            )
        payload = resp.json()
        if "access_token" not in payload:
            raise KiotVietAuthError("Phản hồi token không có access_token")
        return payload

    async def _ensure_token(self, *, stale: str | None = None) -> str:
        """Return a valid token. `stale` = a token the API just rejected with 401."""
        async with self._token_lock:
            fresh_enough = time.monotonic() < self._token_expires_at - TOKEN_REFRESH_MARGIN
            if self._token and fresh_enough and self._token != stale:
                return self._token
            payload = await self.fetch_token()
            self._token = payload["access_token"]
            self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 86400))
            log.info("KiotViet %s: lấy token mới", self.retailer)
            return self._token

    # ---- GET -----------------------------------------------------------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/"):
            raise ValueError("path must start with /")
        url = API_BASE + path
        attempt = 0
        refreshed = False
        while True:
            token = await self._ensure_token()
            headers = {
                "Retailer": self.retailer,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            try:
                resp = await self._http.get(url, params=params, headers=headers)
            except httpx.TransportError as e:
                if attempt >= self._max_retries:
                    raise KiotVietError(f"Lỗi mạng khi gọi GET {path}: {e}") from e
                await self._backoff(attempt, None)
                attempt += 1
                continue

            self.request_count += 1
            self.last_headers = resp.headers
            status = resp.status_code
            if status == 401 and not refreshed:
                refreshed = True
                await self._ensure_token(stale=token)
                continue
            if status == 429 or status >= 500:
                if attempt >= self._max_retries:
                    raise KiotVietError(f"GET {path} lỗi HTTP {status} sau {attempt + 1} lần thử", status)
                log.warning("KiotViet GET %s: HTTP %s, thử lại", path, status)
                await self._backoff(attempt, resp.headers.get("Retry-After"))
                attempt += 1
                continue
            if status >= 400:
                raise KiotVietError(f"GET {path} lỗi HTTP {status}: {resp.text[:300]}", status)
            return resp.json()

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = self._backoff_base * (2**attempt)
        if retry_after and retry_after.isdigit():
            delay = float(retry_after)
        await self._sleep(min(delay, 120.0))

    async def get_all(self, path: str, params: dict[str, Any] | None = None) -> PageResult:
        """Follow `currentItem` paging until `total` is reached."""
        result = PageResult()
        offset = 0
        for _ in range(MAX_PAGES):
            query = {**(params or {}), "pageSize": PAGE_SIZE, "currentItem": offset}
            page = await self.get(path, query)
            items = page.get("data") or []
            result.items.extend(items)
            result.remove_ids.extend(page.get("removeIds") or [])
            total = page.get("total")
            if total is not None:
                result.total = int(total)
            offset += len(items)
            if not items:
                break
            if result.total is not None:
                if offset >= result.total:
                    break
            elif len(items) < PAGE_SIZE:
                break
        else:
            raise KiotVietError(f"GET {path}: quá {MAX_PAGES} trang, dừng lại")
        return result
