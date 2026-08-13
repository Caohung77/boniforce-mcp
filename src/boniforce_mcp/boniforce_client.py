import logging
import re
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


logger = logging.getLogger(__name__)


class BoniforceError(RuntimeError):
    def __init__(self, status: int, body: Any):
        super().__init__(f"Boniforce API error {status}: {body}")
        self.status = status
        self.body = body


_RETRYABLE = retry_if_exception_type((httpx.TransportError, httpx.ReadTimeout))


def _operation_name(method: str, path: str) -> str:
    """Return a stable endpoint name without logging report/job identifiers."""
    exact = {
        ("GET", "/v1/search"): "search_companies",
        ("GET", "/v1/search/advanced"): "search_companies_advanced",
        ("GET", "/v1/reports"): "list_reports",
        ("POST", "/v1/reports"): "create_report",
        ("GET", "/v1/financial_data"): "get_financial_data",
        ("GET", "/v1/financial_data/analysis"): "get_financial_analysis",
    }
    known = exact.get((method.upper(), path))
    if known:
        return known
    patterns = (
        (r"^/v1/jobs/[^/]+/status$", "get_job_status"),
        (r"^/v1/reports/[^/]+/financial_data/analysis$", "get_report_financial_analysis"),
        (r"^/v1/reports/[^/]+/financial_data$", "get_report_financial_data"),
        (r"^/v1/reports/[^/]+$", "get_report"),
        (r"^/v1/company/[^/]+/details$", "get_company_details"),
        (r"^/v1/company/[^/]+/shareholders$", "get_company_shareholders"),
        (r"^/v1/company/[^/]+/holdings$", "get_company_holdings"),
    )
    for pattern, name in patterns:
        if re.match(pattern, path):
            return name
    return "unknown"


class BoniforceClient:
    def __init__(self, client: httpx.AsyncClient | None = None):
        settings = get_settings()
        self._client = client or httpx.AsyncClient(
            base_url=settings.api_base, timeout=30.0
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(reraise=True, stop=stop_after_attempt(2), wait=wait_exponential(min=0.2, max=1.0), retry=_RETRYABLE)
    async def _request(self, method: str, path: str, token: str, **kwargs) -> Any:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Accept", "application/json")
        started = time.monotonic()
        operation = _operation_name(method, path)
        try:
            resp = await self._client.request(method, path, headers=headers, **kwargs)
        except Exception:
            logger.exception(
                "boniforce_upstream operation=%s duration_ms=%.1f",
                operation,
                (time.monotonic() - started) * 1000,
            )
            raise
        logger.info(
            "boniforce_upstream operation=%s status=%s duration_ms=%.1f",
            operation,
            resp.status_code,
            (time.monotonic() - started) * 1000,
        )
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise BoniforceError(resp.status_code, body)
        if not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype:
            return resp.json()
        return resp.text

    # ---- endpoints ----
    # POST /v1/user/api_keys is intentionally not proxied: MCP users must link
    # an existing personal key through OAuth, and the upstream docs currently
    # describe programmatic key creation as unsupported.

    async def search_companies(self, token: str, query: str) -> Any:
        return await self._request("GET", "/v1/search", token, params={"query": query})

    async def search_companies_advanced(self, token: str, query: str) -> Any:
        return await self._request(
            "GET", "/v1/search/advanced", token, params={"query": query}
        )

    async def list_reports(self, token: str) -> Any:
        return await self._request("GET", "/v1/reports", token)

    async def create_report(
        self,
        token: str,
        company_name: str | None = None,
        register_type: str | None = None,
        register_number: str | None = None,
        register_court: str | None = None,
        session_id: str | None = None,
        search_result_id: str | None = None,
    ) -> Any:
        body = {
            key: value
            for key, value in {
                "company_name": company_name,
                "register_type": register_type,
                "register_number": register_number,
                "register_court": register_court,
                "search_result_id": search_result_id,
            }.items()
            if value is not None
        }
        if session_id is not None:
            body["session_id"] = session_id
        return await self._request("POST", "/v1/reports", token, json=body)

    async def get_report(self, token: str, report_id: str) -> Any:
        return await self._request("GET", f"/v1/reports/{report_id}", token)

    async def get_job_status(self, token: str, job_id: str) -> Any:
        return await self._request("GET", f"/v1/jobs/{job_id}/status", token)

    async def wait_for_job(
        self, token: str, job_id: str, max_wait_s: float = 40.0, poll_every_s: float = 2.0
    ) -> Any:
        """Poll get_job_status until terminal state or max_wait_s exceeded."""
        import asyncio
        import time

        deadline = time.monotonic() + max_wait_s
        last = None
        while True:
            last = await self.get_job_status(token, job_id)
            status = (last or {}).get("status", "").lower()
            if status in ("completed", "finished", "failed", "error"):
                return last
            if time.monotonic() >= deadline:
                return last
            await asyncio.sleep(poll_every_s)

    async def get_financial_data(
        self,
        token: str,
        company_name: str | None = None,
        register_type: str | None = None,
        register_number: str | None = None,
        register_court: str | None = None,
        session_id: str | None = None,
        search_result_id: str | None = None,
    ) -> Any:
        params = {
            key: value
            for key, value in {
                "company_name": company_name,
                "register_type": register_type,
                "register_number": register_number,
                "register_court": register_court,
                "search_result_id": search_result_id,
            }.items()
            if value is not None
        }
        if session_id is not None:
            params["session_id"] = session_id
        return await self._request("GET", "/v1/financial_data", token, params=params)

    async def get_financial_analysis(
        self,
        token: str,
        company_name: str | None = None,
        register_type: str | None = None,
        register_number: str | None = None,
        register_court: str | None = None,
        session_id: str | None = None,
        search_result_id: str | None = None,
    ) -> Any:
        params = {
            key: value
            for key, value in {
                "company_name": company_name,
                "register_type": register_type,
                "register_number": register_number,
                "register_court": register_court,
                "search_result_id": search_result_id,
            }.items()
            if value is not None
        }
        if session_id is not None:
            params["session_id"] = session_id
        return await self._request(
            "GET", "/v1/financial_data/analysis", token, params=params
        )

    async def get_report_financial_data(self, token: str, report_id: str) -> Any:
        return await self._request(
            "GET", f"/v1/reports/{report_id}/financial_data", token
        )

    async def get_report_financial_analysis(self, token: str, report_id: str) -> Any:
        return await self._request(
            "GET", f"/v1/reports/{report_id}/financial_data/analysis", token
        )

    async def get_company_details(self, token: str, report_id: str) -> Any:
        return await self._request("GET", f"/v1/company/{report_id}/details", token)

    async def get_company_shareholders(self, token: str, report_id: str) -> Any:
        return await self._request(
            "GET", f"/v1/company/{report_id}/shareholders", token
        )

    async def get_company_holdings(self, token: str, report_id: str) -> Any:
        return await self._request("GET", f"/v1/company/{report_id}/holdings", token)
