"""HTTPX wrapper for the Sectorbench Public Data API.

Sectorbench publishes branch-health scores, score history, monthly news
reports, indicator catalogs, and insolvency history for 10 German industry
sectors at https://sectorbench.theaiwhisperer.cloud/api/v1.

Auth model is a single operator-issued bearer token (``sbk_…``) shared by
the entire MCP server, configured via ``BF_SECTORBENCH_TOKEN``. End users
do not provide their own Sectorbench token. The MCP user JWT still gates
the proxy endpoints (rest_api.py) so the shared 600 req/h quota is only
spent on authenticated calls.

A small in-memory TTL cache (default 600s) deduplicates repeat reads.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import get_settings


class SectorbenchError(RuntimeError):
    def __init__(self, status: int, body: Any):
        super().__init__(f"Sectorbench API error {status}: {body}")
        self.status = status
        self.body = body


_RETRYABLE = retry_if_exception_type((httpx.TransportError, httpx.ReadTimeout))


class SectorbenchClient:
    def __init__(self, client: httpx.AsyncClient | None = None):
        settings = get_settings()
        self._client = client or httpx.AsyncClient(
            base_url=settings.sectorbench_base, timeout=30.0
        )
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._ttl = settings.sectorbench_cache_ttl

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def enabled(self) -> bool:
        return bool(get_settings().sectorbench_token)

    def _cache_key(self, method: str, path: str, params: dict | None) -> tuple:
        params_tuple = tuple(sorted((params or {}).items()))
        return (method, path, params_tuple)

    def _cache_get(self, key: tuple) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._cache.pop(key, None)
            return None
        return value

    def _cache_set(self, key: tuple, value: Any) -> None:
        if self._ttl <= 0:
            return
        self._cache[key] = (time.monotonic() + self._ttl, value)

    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=0.2, max=1.0),
        retry=_RETRYABLE,
    )
    async def _request(
        self, method: str, path: str, *, params: dict | None = None
    ) -> Any:
        token = get_settings().sectorbench_token
        if not token:
            raise SectorbenchError(503, {"error": "sectorbench_disabled"})

        key = self._cache_key(method, path, params)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        resp = await self._client.request(
            method, path, headers=headers, params=params
        )
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise SectorbenchError(resp.status_code, body)
        if not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        data = resp.json() if "json" in ctype else resp.text
        self._cache_set(key, data)
        return data

    # ---- endpoints ----

    async def meta(self) -> Any:
        return await self._request("GET", "/meta")

    async def get_all_scores(self) -> Any:
        return await self._request("GET", "/scores")

    async def get_ranking(self) -> Any:
        return await self._request("GET", "/scores/ranking")

    async def get_branch(self, branch_key: str) -> Any:
        return await self._request("GET", f"/branches/{branch_key}")

    async def get_branch_history(
        self, branch_key: str, months: int = 12
    ) -> Any:
        return await self._request(
            "GET", f"/branches/{branch_key}/history", params={"months": months}
        )

    async def get_branch_news(self, branch_key: str) -> Any:
        return await self._request("GET", f"/branches/{branch_key}/news")

    async def get_branch_insolvency_history(
        self, branch_key: str, months: int = 12
    ) -> Any:
        return await self._request(
            "GET",
            f"/branches/{branch_key}/insolvency/history",
            params={"months": months},
        )

    async def get_indicator_catalog(self) -> Any:
        return await self._request("GET", "/indicators")

    async def get_indicator_history(
        self, branch_key: str, indicator_key: str, months: int = 12
    ) -> Any:
        return await self._request(
            "GET",
            f"/branches/{branch_key}/indicators/{indicator_key}/history",
            params={"months": months},
        )
