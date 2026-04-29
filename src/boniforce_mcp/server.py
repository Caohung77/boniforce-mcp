"""
FastMCP server exposing Boniforce endpoints as tools.

Composition:
- FastMCP app handles MCP protocol on /mcp (Streamable HTTP).
- Starlette wraps it and adds OAuth 2.1 issuer routes from auth.py.
- Tools read the authenticated user via FastMCP's AccessToken context,
  fetch the user's stored Boniforce token, and call BoniforceClient.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Mount
from starlette.types import ASGIApp

from . import auth, rest_api, storage
from .boniforce_client import BoniforceClient, BoniforceError
from .config import get_settings
from .rest_api import annotate_job_outcome


def _build_verifier() -> JWTVerifier:
    settings = get_settings()
    return JWTVerifier(
        public_key=auth.public_key_pem(),
        issuer=settings.issuer,
        audience=settings.audience,
        algorithm="RS256",
    )


def _bf_client_from_state() -> BoniforceClient:
    return _client_holder["client"]


_client_holder: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: Starlette):
    await storage.init_db()
    _client_holder["client"] = BoniforceClient()
    try:
        yield
    finally:
        await _client_holder["client"].aclose()


def _make_mcp() -> FastMCP:
    mcp = FastMCP(
        name="Boniforce",
        instructions=(
            "Tools for the Boniforce credit/financial-data API for German companies.\n\n"
            "STANDARD WORKFLOW (use this in 99% of cases):\n"
            "  1. search_companies(query) -> get register_type, register_number, register_court.\n"
            "  2. create_report(...) with wait_seconds=40 (default) -> response often already\n"
            "     contains `report` with the finished Boniscore. Check the `done` field.\n"
            "  3. If done=false, call get_job_status(job_id, wait_seconds=40). Repeat until\n"
            "     done=true. See LOOP RULE below.\n"
            "  4. When status='completed', call get_report(report_id) (only needed if `report`\n"
            "     wasn't already inlined by create_report).\n"
            "  5. Optional drill-down: get_report_financial_data(report_id) and\n"
            "     get_report_financial_analysis(report_id).\n\n"
            "LOOP RULE — long-running jobs (CRITICAL for ChatGPT Actions):\n"
            "  Boniforce reports take 30-120s. Each tool call long-polls up to 40s server-side\n"
            "  (the upper bound for ChatGPT's per-call HTTP timeout). A 120s report therefore\n"
            "  needs UP TO 3 sequential tool calls within the SAME user turn:\n"
            "    call 1 = create_report(..., wait_seconds=40)         (~0-40s elapsed)\n"
            "    call 2 = get_job_status(job_id, wait_seconds=40)     (~40-80s elapsed)\n"
            "    call 3 = get_job_status(job_id, wait_seconds=40)     (~80-120s elapsed)\n"
            "  After EACH call, inspect `done` in the response:\n"
            "    - done=true  -> read the report (or call get_report) and reply to user.\n"
            "    - done=false -> call get_job_status again immediately. DO NOT stop, DO NOT\n"
            "                    tell the user 'still processing'. The server response also\n"
            "                    includes a `next_action` string — follow it.\n"
            "  Only AFTER 3 polls have all returned done=false should you tell the user\n"
            "  'Der Bericht dauert ungewöhnlich lange (>2min). Status: <status>.' and stop.\n"
            "  Never ask the user to retry manually; never return early with done=false.\n\n"
            "list_reports() shows previously generated reports — check it first if the user\n"
            "asks about a company that may already have a fresh report.\n\n"
            "404 from get_report_financial_* means no Bundesanzeiger annual filing exists\n"
            "yet for that company; report this as a data-availability issue, not an API\n"
            "error. The Boniscore itself from get_report is still valid in that case."
        ),
        auth=_build_verifier(),
    )

    async def _user_token() -> tuple[str, str]:
        access = get_access_token()
        if access is None or not access.claims:
            raise ToolError("Not authenticated.")
        user_id = access.claims.get("sub")
        if not user_id:
            raise ToolError("Token missing subject claim.")
        bf = await storage.get_bf_token(user_id)
        if not bf:
            issuer = get_settings().issuer
            raise ToolError(
                f"No Boniforce API key linked to your account. Visit {issuer}/setup to add one."
            )
        return user_id, bf

    def _wrap(exc: BoniforceError) -> ToolError:
        return ToolError(f"Boniforce API returned {exc.status}: {exc.body}")

    @mcp.tool
    async def search_companies(query: str) -> Any:
        """Step 1 of Boniscore workflow: search Boniforce for a German company by
        name or partial name. Returns company entries each with company_name,
        register_type (e.g. HRB, HRA, VR), register_number, register_court.
        Pass these four fields verbatim into create_report next."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().search_companies(token, query)
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool
    async def list_reports() -> Any:
        """List previously generated reports for the account. Useful to check
        whether a company already has a finished report (avoids re-running
        create_report). Returns name, report_id, status, created_at."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().list_reports(token)
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool
    async def create_report(
        company_name: str,
        register_type: str,
        register_number: str,
        register_court: str,
        session_id: str | None = None,
        wait_seconds: int = 40,
    ) -> Any:
        """Step 2 of Boniscore workflow: kick off report generation AND wait
        server-side for it to finish (default 40s, max 40s). With the default,
        the response inlines `final_status` and (if completed) the full
        `report`. Reports take 30-120s, so the response also includes a
        `done` boolean: if `done` is False, immediately call
        get_job_status(job_id, wait_seconds=40) and repeat until done=True
        (typically ≤3 calls total). Never tell the user the job is "still
        processing" before you have called get_job_status at least 2 more
        times after this one."""
        _, token = await _user_token()
        client = _bf_client_from_state()
        try:
            data = await client.create_report(
                token,
                company_name=company_name,
                register_type=register_type,
                register_number=register_number,
                register_court=register_court,
                session_id=session_id,
            )
        except BoniforceError as e:
            raise _wrap(e)
        ws = max(0, min(40, wait_seconds))
        status_value: str | None = None
        if ws and data.get("job_id"):
            try:
                status = await client.wait_for_job(token, data["job_id"], max_wait_s=ws)
                data["final_status"] = status
                status_value = (status or {}).get("status")
                if (status_value or "").lower() in ("completed", "finished") and data.get(
                    "report_id"
                ):
                    data["report"] = await client.get_report(token, data["report_id"])
            except BoniforceError as e:
                raise _wrap(e)
        annotate_job_outcome(data, data.get("job_id"), status_value)
        return data

    @mcp.tool
    async def get_report(report_id: str) -> Any:
        """Step 4 of Boniscore workflow: fetch a finished report. Returns the
        Boniscore (0-100, higher=better creditworthiness), score_details
        (label/color), credit_limit, credit_assessment_result (APPROVE / DECLINE /
        REVIEW), and per-criterion assessments. Only call once get_job_status
        reports status='finished'."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().get_report(token, report_id)
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool
    async def get_job_status(job_id: str, wait_seconds: int = 40) -> Any:
        """Step 3 of Boniscore workflow: poll a report-generation job. status
        moves queued -> running -> completed (or failed). Typical time 30-120s.
        Default wait_seconds=40 long-polls server-side. The response includes
        a `done` boolean: if `done` is False, call this tool AGAIN with the
        same job_id and wait_seconds=40. Repeat until done=True (max ~3 calls,
        ~120s total). Only treat the job as stuck after 3 unsuccessful polls.
        Once status='completed', call get_report(report_id)."""
        _, token = await _user_token()
        ws = max(0, min(40, wait_seconds))
        client = _bf_client_from_state()
        try:
            if ws:
                data = await client.wait_for_job(token, job_id, max_wait_s=ws)
            else:
                data = await client.get_job_status(token, job_id)
        except BoniforceError as e:
            raise _wrap(e)
        annotate_job_outcome(data, job_id, (data or {}).get("status") if isinstance(data, dict) else None)
        return data

    @mcp.tool
    async def get_report_financial_data(report_id: str) -> Any:
        """Optional drill-down: balance-sheet history for a finished report.
        Returns yearly Eigenkapital, Verbindlichkeiten, Bilanzsumme, etc.
        from the Bundesanzeiger filings the score is built on. 404 means
        no annual filings indexed for the company yet."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().get_report_financial_data(token, report_id)
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool
    async def get_report_financial_analysis(report_id: str) -> Any:
        """Optional drill-down: per-year financial ratios + sub-scores
        (Eigenkapitalquote, Verbindlichkeitenquote, etc.) underlying the
        Boniscore. 404 means no annual filings indexed for the company yet."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().get_report_financial_analysis(token, report_id)
        except BoniforceError as e:
            raise _wrap(e)

    return mcp


class WWWAuthenticateResourceMetadataMiddleware(BaseHTTPMiddleware):
    """Inject resource_metadata=... into WWW-Authenticate header per RFC 9728."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if response.status_code == 401:
            existing = response.headers.get("www-authenticate", "")
            iss = get_settings().issuer
            hint = f'resource_metadata="{iss}/.well-known/oauth-protected-resource"'
            if existing.lower().startswith("bearer"):
                if "resource_metadata" not in existing:
                    response.headers["www-authenticate"] = f"{existing}, {hint}"
            else:
                response.headers["www-authenticate"] = f"Bearer {hint}"
        return response


def build_app() -> Starlette:
    mcp = _make_mcp()
    mcp_app = mcp.http_app(path="/mcp", transport="http")
    outer = Starlette(
        routes=[*auth.routes(), *rest_api.routes(), Mount("/", app=mcp_app)],
        middleware=[Middleware(WWWAuthenticateResourceMetadataMiddleware)],
        lifespan=lambda _outer: _combined_lifespan(mcp_app),
    )
    return outer


@asynccontextmanager
async def _combined_lifespan(mcp_app: Starlette):
    await storage.init_db()
    _client_holder["client"] = BoniforceClient()
    inner_lifespan = mcp_app.router.lifespan_context
    try:
        async with inner_lifespan(mcp_app):
            yield
    finally:
        await _client_holder["client"].aclose()


# uvicorn entry point: `uvicorn boniforce_mcp.server:app`
app = build_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "boniforce_mcp.server:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
