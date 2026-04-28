from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import get_settings


class BoniforceError(RuntimeError):
    def __init__(self, status: int, body: Any):
        super().__init__(f"Boniforce API error {status}: {body}")
        self.status = status
        self.body = body


_RETRYABLE = retry_if_exception_type((httpx.TransportError, httpx.ReadTimeout))


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
        resp = await self._client.request(method, path, headers=headers, **kwargs)
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

    async def search_companies(self, token: str, query: str) -> Any:
        return await self._request("GET", "/v1/search", token, params={"query": query})

    async def list_reports(self, token: str) -> Any:
        return await self._request("GET", "/v1/reports", token)

    async def create_report(
        self,
        token: str,
        company_name: str,
        register_type: str,
        register_number: str,
        register_court: str,
        session_id: str | None = None,
    ) -> Any:
        body = {
            "company_name": company_name,
            "register_type": register_type,
            "register_number": register_number,
            "register_court": register_court,
        }
        if session_id is not None:
            body["session_id"] = session_id
        return await self._request("POST", "/v1/reports", token, json=body)

    async def get_report(self, token: str, report_id: str) -> Any:
        return await self._request("GET", f"/v1/reports/{report_id}", token)

    async def get_job_status(self, token: str, job_id: str) -> Any:
        return await self._request("GET", f"/v1/jobs/{job_id}/status", token)

    async def get_financial_data(
        self,
        token: str,
        company_name: str,
        register_type: str,
        register_number: str,
        register_court: str,
        session_id: str | None = None,
    ) -> Any:
        params = {
            "company_name": company_name,
            "register_type": register_type,
            "register_number": register_number,
            "register_court": register_court,
        }
        if session_id is not None:
            params["session_id"] = session_id
        return await self._request("GET", "/v1/financial_data", token, params=params)

    async def get_financial_analysis(
        self,
        token: str,
        company_name: str,
        register_type: str,
        register_number: str,
        register_court: str,
        session_id: str | None = None,
    ) -> Any:
        params = {
            "company_name": company_name,
            "register_type": register_type,
            "register_number": register_number,
            "register_court": register_court,
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
