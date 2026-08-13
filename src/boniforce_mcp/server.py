"""
FastMCP server exposing Boniforce endpoints as tools.

Composition:
- FastMCP app handles MCP protocol on /mcp (Streamable HTTP).
- Starlette wraps it and adds OAuth 2.1 issuer routes from auth.py.
- Tools read the authenticated user via FastMCP's AccessToken context,
  fetch the user's stored Boniforce token, and call BoniforceClient.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp
from pathlib import Path

from . import auth, rest_api, storage
from .boniforce_client import BoniforceClient, BoniforceError
from .config import get_settings
from .rest_api import SECTORBENCH_BRANCH_KEYS, annotate_job_outcome
from .sectorbench_client import SectorbenchClient, SectorbenchError


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


def _sectorbench_client_from_state() -> SectorbenchClient:
    return _client_holder["sectorbench"]


_client_holder: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: Starlette):
    await storage.init_db()
    _client_holder["client"] = BoniforceClient()
    _client_holder["sectorbench"] = SectorbenchClient()
    try:
        yield
    finally:
        await _client_holder["client"].aclose()
        await _client_holder["sectorbench"].aclose()


def _make_mcp() -> FastMCP:
    mcp = FastMCP(
        name="Boniforce",
        instructions=(
            "Tools for the Boniforce credit/financial-data API for German companies.\n\n"
            "STANDARD WORKFLOW (use this in 99% of cases):\n"
            "  0. **MANDATORY FIRST STEP** — list_reports() to see existing reports for\n"
            "     this account. Match on company name (case-insensitive, substring OK).\n"
            "     If a matching report exists with status='completed' AND created_at is\n"
            "     ≤30 days old, **REUSE that report_id** — skip steps 1-3 entirely and\n"
            "     jump to step 4 (get_report) or step 5 (financial_data). This costs\n"
            "     ZERO credits and is instant. Only fall through to steps 1-3 if no\n"
            "     fresh report exists, the existing one is >30 days old, or status is\n"
            "     failed/error.\n"
            "  1. search_companies(query) -> get register fields + search_result_id. If it\n"
            "     returns no match, use search_companies_advanced(query) as the fallback.\n"
            "  2. create_report(...) with wait_seconds=40 (default) -> response often already\n"
            "     contains `report` with the finished Boniscore. Check the `done` field.\n"
            "     ⚠️ COSTS 75 CREDITS per call and takes 30-120s — see step 0 first.\n"
            "  3. If done=false, call get_job_status(job_id, wait_seconds=40). Repeat until\n"
            "     done=true. See LOOP RULE below.\n"
            "  4. When status='completed', call get_report(report_id) (only needed if `report`\n"
            "     wasn't already inlined by create_report). Free, idempotent.\n"
            "  5. Optional drill-down: get_report_financial_data(report_id) and\n"
            "     get_report_financial_analysis(report_id). Free, idempotent.\n\n"
            "FOLLOW-UP QUESTIONS (same chat, same company):\n"
            "  If you already have a `report_id` for the company from earlier in this\n"
            "  conversation, REUSE it for any follow-up score/financial question. Call\n"
            "  get_report / get_report_financial_data / get_report_financial_analysis\n"
            "  with that report_id. NEVER call create_report a second time for the same\n"
            "  company in the same chat — that wastes 75 credits and gives the same result.\n\n"
            "NEW CHAT, SAME COMPANY:\n"
            "  list_reports is account-wide and persists across chats. Step 0 catches this\n"
            "  case automatically — always run it before considering create_report.\n\n"
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
            "error. The Boniscore itself from get_report is still valid in that case.\n\n"
            "ADDITIONAL COMPANY TOOLS:\n"
            "  get_company_details(report_id) is free and returns address, firmographics,\n"
            "  and representatives. get_company_shareholders / get_company_holdings are\n"
            "  free from a fresh one-week cache but cost 25 credits when refreshed.\n"
            "  get_financial_data costs 25 credits and get_financial_analysis costs 50;\n"
            "  use them only when direct financial data is requested without a full report.\n\n"
            "SECTORBENCH BRANCH-DATA TOOLS (deutsche Branchen-Intelligenz):\n"
            "  Use these tools — NEVER websearch — when the question mentions:\n"
            "  Branche, Branchen, Industrie, Sektor, sector, Branchen-Score,\n"
            "  Branchen-Trend, Branchen-Lage, Branchen-Ranking, Branchen-Vergleich,\n"
            "  Branchen-News, Branchen-Briefing, Insolvenzen, Pleiten, Insolvenzfälle,\n"
            "  market overview, sector outlook, industry health, ifo, PMI.\n\n"
            "  Branch-key mapping (German label → branch_key argument):\n"
            "    Automobilindustrie / Autobranche / Automobil       → automotive\n"
            "    Gesundheitswesen / Pharma / Medizin                → healthcare\n"
            "    Bauwirtschaft / Bau / Bauindustrie                 → construction\n"
            "    Erneuerbare Energien / Solar / Wind                → renewable_energy\n"
            "    Logistik / Transport / Spedition                   → logistics\n"
            "    Fintech / Banken / Finanzdienstleister             → fintech\n"
            "    IT / IT-Dienstleister / Software                   → it_services\n"
            "    Einzelhandel / Retail / Handel                     → retail\n"
            "    Gastgewerbe / Hotellerie / Gastronomie             → hospitality\n"
            "    Industrie / Produzierendes Gewerbe / Manufacturing → manufacturing\n\n"
            "  Tool selection:\n"
            "    'Score / Lage / Stand der <Branche>'               → get_branch\n"
            "    'Verlauf / Trend / Entwicklung der <Branche>'      → get_branch_history\n"
            "    'Insolvenzen / Pleiten in <Branche>'               → get_branch_insolvency_history\n"
            "    'News / Briefing / aktuelle Lage <Branche>'        → get_branch_news\n"
            "    'Ranking / welche Branche am besten/schlechtesten' → get_branch_ranking\n"
            "    'alle Branchen / Übersicht / Vergleich'            → list_branch_scores\n"
            "    'ifo / PMI / Einzel-Indikator'                     → list_branch_indicators\n"
            "                                                          → get_branch_indicator_history\n"
            "    'wie aktuell sind die Daten'                       → get_sectorbench_meta\n\n"
            "  Daten kommen aus Sectorbench (Destatis-Insolvenzen, ifo-Index,\n"
            "  composite-PMI, ZEW, etc.). Für Branchen-Fragen NIEMALS websearch\n"
            "  verwenden — diese Tools liefern offizielle, aktuelle deutsche Daten.\n"
            "  Kombinier gerne Boniscore (einzelne Firma) + Branchen-Score (Kontext)\n"
            "  in einer Antwort, z.B. 'Müller Bau GmbH Boniscore plus Bauwirtschaft-Trend'."
        ),
        auth=_build_verifier(),
    )

    async def _user_only() -> str:
        """Validate the JWT and return the user_id. No Boniforce key required.

        Used by Sectorbench tools — they call upstream with the operator's
        shared sbk_… token, not the user's Boniforce key.
        """
        access = get_access_token()
        if access is None or not access.claims:
            raise ToolError("Not authenticated.")
        user_id = access.claims.get("sub")
        if not user_id:
            raise ToolError("Token missing subject claim.")
        return user_id

    async def _user_token() -> tuple[str, str]:
        user_id = await _user_only()
        bf = await storage.get_bf_token(user_id)
        if not bf:
            issuer = get_settings().issuer
            raise ToolError(
                f"No Boniforce API key linked to your account. Visit {issuer}/setup to add one."
            )
        return user_id, bf

    def _wrap(exc: BoniforceError) -> ToolError:
        return ToolError(f"Boniforce API returned {exc.status}: {exc.body}")

    def _validate_company_identifier(
        search_result_id: str | None,
        register_type: str | None,
        register_number: str | None,
        register_court: str | None,
    ) -> None:
        if search_result_id:
            return
        missing = [
            name
            for name, value in (
                ("register_type", register_type),
                ("register_number", register_number),
                ("register_court", register_court),
            )
            if not value
        ]
        if missing:
            raise ToolError(
                "Provide search_result_id or all register fields. Missing: "
                + ", ".join(missing)
            )

    @mcp.tool(
        annotations={
            "title": "Search German companies",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def search_companies(query: str) -> Any:
        """Step 1 of Boniscore workflow: search Boniforce for a German company by
        name or partial name (cost: 1 credit). Results include register fields
        and search_result_id. Pass search_result_id into create_report; if there
        is no match, call search_companies_advanced."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().search_companies(token, query)
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool(
        annotations={
            "title": "Advanced German company search",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def search_companies_advanced(query: str) -> Any:
        """Fallback when search_companies returns no suitable match (cost: 5
        credits). Finds partial/alternative names and returns search_result_id,
        nullable register fields, and registered_office for disambiguation."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().search_companies_advanced(token, query)
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool(
        annotations={
            "title": "List previously generated reports",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def list_reports() -> Any:
        """**MANDATORY FIRST STEP** for ANY question about a German company by name.

        Lists previously generated reports for the account (persists across
        chat sessions). Returns name, report_id, status, created_at.

        Decision rule after calling this:
          - Match company name (case-insensitive, substring OK).
          - If a match exists with status='completed' AND created_at is
            ≤30 days old → REUSE that report_id. Call get_report or
            get_report_financial_data with it. DO NOT call create_report
            (which costs 75 credits and takes 30-120s for the same result).
          - Else (no match, stale >30d, or failed) → proceed with
            search_companies + create_report."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().list_reports(token)
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool(
        annotations={
            "title": "Start a Boniscore report",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def create_report(
        company_name: str | None = None,
        register_type: str | None = None,
        register_number: str | None = None,
        register_court: str | None = None,
        search_result_id: str | None = None,
        session_id: str | None = None,
        wait_seconds: int = 40,
    ) -> Any:
        """Step 2 of Boniscore workflow: kick off report generation AND wait
        server-side for it to finish (default 40s, max 40s).

        ⚠️ PRECONDITION: call list_reports FIRST. Only call create_report
        if no completed report for this company exists with created_at
        within the last 30 days. Each call CHARGES 75 CREDITS and re-runs a
        30-120s computation that returns the same data. Re-creating for a
        company that already has a fresh report wastes credits.

        Identify the company with search_result_id from either search tool, or
        provide register_type, register_number, and register_court. Company
        name is optional when search_result_id is supplied.

        With the default wait_seconds=40, the response inlines `final_status`
        and (if completed) the full `report`. Reports take 30-120s, so the
        response also includes a `done` boolean: if `done` is False,
        immediately call get_job_status(job_id, wait_seconds=40) and repeat
        until done=True (typically ≤3 calls total). Never tell the user the
        job is "still processing" before you have called get_job_status at
        least 2 more times after this one."""
        _validate_company_identifier(
            search_result_id, register_type, register_number, register_court
        )
        _, token = await _user_token()
        client = _bf_client_from_state()
        try:
            data = await client.create_report(
                token,
                company_name=company_name,
                register_type=register_type,
                register_number=register_number,
                register_court=register_court,
                search_result_id=search_result_id,
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

    @mcp.tool(
        annotations={
            "title": "Fetch finished Boniscore report",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
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

    @mcp.tool(
        annotations={
            "title": "Poll Boniscore job status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
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

    @mcp.tool(
        annotations={
            "title": "Balance-sheet history",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def get_report_financial_data(report_id: str) -> Any:
        """Optional drill-down: balance-sheet history for a finished report.

        Returns two parallel views, both sourced from the Bundesanzeiger
        annual filings the Boniscore is built on:
          - financials[]: per-year summary metrics (jahr, jahresueberschuss,
            eigenkapital, verbindlichkeiten, umlaufvermoegen, bilanzsumme,
            forderungen, liquide_mittel) — fast for charts/trends.
          - financial_reports[]: full breakdown per year — aktiva (with
            anlagevermoegen_details, umlaufvermoegen_details, vorraete_details),
            passiva (with eigenkapital_details, rueckstellungen_details,
            verbindlichkeiten_details), and guv (P&L; open dict).

        404 means no annual filings indexed for the company yet."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().get_report_financial_data(token, report_id)
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool(
        annotations={
            "title": "Per-year financial ratio analysis",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def get_report_financial_analysis(report_id: str) -> Any:
        """Optional drill-down: per-year financial ratios + sub-scores
        (Eigenkapitalquote, Verbindlichkeitenquote, etc.) underlying the
        Boniscore. 404 means no annual filings indexed for the company yet."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().get_report_financial_analysis(token, report_id)
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool(
        annotations={
            "title": "Fetch financial statements directly",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def get_financial_data(
        company_name: str | None = None,
        register_type: str | None = None,
        register_number: str | None = None,
        register_court: str | None = None,
        search_result_id: str | None = None,
        session_id: str | None = None,
    ) -> Any:
        """Fetch raw per-year financial statements without creating a Boniscore
        report. Costs 25 credits per request. Identify the company using
        search_result_id or all three register fields."""
        _validate_company_identifier(
            search_result_id, register_type, register_number, register_court
        )
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().get_financial_data(
                token,
                company_name=company_name,
                register_type=register_type,
                register_number=register_number,
                register_court=register_court,
                search_result_id=search_result_id,
                session_id=session_id,
            )
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool(
        annotations={
            "title": "Analyze financial statements directly",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def get_financial_analysis(
        company_name: str | None = None,
        register_type: str | None = None,
        register_number: str | None = None,
        register_court: str | None = None,
        search_result_id: str | None = None,
        session_id: str | None = None,
    ) -> Any:
        """Fetch financial features, score, and ratio analysis without creating
        a Boniscore report. Costs 50 credits per request. Identify the company
        using search_result_id or all three register fields."""
        _validate_company_identifier(
            search_result_id, register_type, register_number, register_court
        )
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().get_financial_analysis(
                token,
                company_name=company_name,
                register_type=register_type,
                register_number=register_number,
                register_court=register_court,
                search_result_id=search_result_id,
                session_id=session_id,
            )
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool(
        annotations={
            "title": "Company details and representatives",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def get_company_details(report_id: str) -> Any:
        """Free metadata lookup for a previously generated report. Returns the
        company address, register information, firmographics, and current or
        former representatives such as managing directors and Prokuristen."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().get_company_details(token, report_id)
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool(
        annotations={
            "title": "Company shareholders",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def get_company_shareholders(report_id: str) -> Any:
        """Return shareholders for a previously generated report. Fresh cached
        data is free; a missing or week-old cache is refreshed for 25 credits.
        The response includes last_updated so freshness is visible."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().get_company_shareholders(
                token, report_id
            )
        except BoniforceError as e:
            raise _wrap(e)

    @mcp.tool(
        annotations={
            "title": "Company holdings",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def get_company_holdings(report_id: str) -> Any:
        """Return companies owned by the company in a previously generated
        report. Fresh cached data is free; a missing or week-old cache is
        refreshed for 25 credits. The response includes last_updated."""
        _, token = await _user_token()
        try:
            return await _bf_client_from_state().get_company_holdings(token, report_id)
        except BoniforceError as e:
            raise _wrap(e)

    # ---- Sectorbench branch-data tools ----
    #
    # Auth model differs from the Boniforce tools above: the per-user JWT
    # only gates access (via _user_token), upstream is called with the
    # operator-issued shared sbk_… token configured server-side. End users
    # do NOT need a Sectorbench key. Mirrors rest_api.py /api/v1/branches/*.

    def _wrap_sb(exc: SectorbenchError) -> ToolError:
        if exc.status in (401, 403):
            return ToolError(
                "Sectorbench upstream rejected the operator token "
                "(server config issue, not a user problem)."
            )
        return ToolError(f"Sectorbench API returned {exc.status}: {exc.body}")

    def _validate_branch(branch_key: str) -> None:
        if branch_key not in SECTORBENCH_BRANCH_KEYS:
            raise ToolError(
                f"Unknown branch_key '{branch_key}'. Valid: "
                f"{', '.join(sorted(SECTORBENCH_BRANCH_KEYS))}."
            )

    def _clamp_months(months: int, maximum: int) -> int:
        if months < 1 or months > maximum:
            raise ToolError(f"months must be between 1 and {maximum}.")
        return months

    @mcp.tool(
        annotations={
            "title": "List German sector scores",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def list_branch_scores() -> Any:
        """Branchen-Übersicht: aktuelle Score (0-100) für alle 10 deutschen
        Branchen (Automobil, Healthcare, Bau, Erneuerbare, Logistik, Fintech,
        IT, Einzelhandel, Gastgewerbe, Industrie). Use for 'alle Branchen',
        'Branchen-Übersicht', 'Sektor-Vergleich', sector overview."""
        await _user_only()
        try:
            return await _sectorbench_client_from_state().get_all_scores()
        except SectorbenchError as e:
            raise _wrap_sb(e)

    @mcp.tool(
        annotations={
            "title": "Rank German sectors by health score",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def get_branch_ranking() -> Any:
        """Branchen-Ranking 1-10 nach Score, mit Rank-Delta zum Vormonat. Use
        for 'Ranking', 'welche Branche steht am besten/schlechtesten',
        'Gewinner/Verlierer Branchen', sector league table."""
        await _user_only()
        try:
            return await _sectorbench_client_from_state().get_ranking()
        except SectorbenchError as e:
            raise _wrap_sb(e)

    @mcp.tool(
        annotations={
            "title": "Sector snapshot",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def get_branch(branch_key: str) -> Any:
        """Aktueller Branchen-Score (composite 0-100, dimensions, risk_level,
        rank) für eine deutsche Branche. branch_key ∈ automotive, healthcare,
        construction, renewable_energy, logistics, fintech, it_services,
        retail, hospitality, manufacturing. (Mapping deutsch → key siehe
        Server-Instructions.)"""
        await _user_only()
        _validate_branch(branch_key)
        try:
            return await _sectorbench_client_from_state().get_branch(branch_key)
        except SectorbenchError as e:
            raise _wrap_sb(e)

    @mcp.tool(
        annotations={
            "title": "Sector score history",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def get_branch_history(branch_key: str, months: int = 12) -> Any:
        """Score-Verlauf einer deutschen Branche, monatlich (months 1-24,
        default 12). Use for 'Verlauf', 'Trend', 'Entwicklung', 'wie hat sich
        <Branche> entwickelt', 'historischer Score', monthly trend."""
        await _user_only()
        _validate_branch(branch_key)
        m = _clamp_months(months, 24)
        try:
            return await _sectorbench_client_from_state().get_branch_history(
                branch_key, m
            )
        except SectorbenchError as e:
            raise _wrap_sb(e)

    @mcp.tool(
        annotations={
            "title": "Sector monthly briefing",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def get_branch_news(branch_key: str) -> Any:
        """Aktuelles monatliches Branchen-Briefing (KI-geschrieben) für eine
        deutsche Branche: Treiber, Risiken, Ausblick. Use for 'aktuelle Lage',
        'News', 'Briefing', 'was ist los in <Branche>', 'monatliche
        Zusammenfassung', 'sector update', 'monthly outlook'."""
        await _user_only()
        _validate_branch(branch_key)
        try:
            return await _sectorbench_client_from_state().get_branch_news(branch_key)
        except SectorbenchError as e:
            raise _wrap_sb(e)

    @mcp.tool(
        annotations={
            "title": "Sector insolvency history",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def get_branch_insolvency_history(
        branch_key: str, months: int = 12
    ) -> Any:
        """Insolvenz-Trend einer deutschen Branche (Destatis-Daten, months
        1-36, default 12). Insolvenzen, Pleiten, Insolvenzfälle pro Monat.
        Use for 'Insolvenzen Einzelhandel', 'Pleiten Bau', 'wie viele
        Insolvenzen', 'Insolvenz-Verlauf', 'bankruptcy trend'."""
        await _user_only()
        _validate_branch(branch_key)
        m = _clamp_months(months, 36)
        try:
            return await _sectorbench_client_from_state().get_branch_insolvency_history(
                branch_key, m
            )
        except SectorbenchError as e:
            raise _wrap_sb(e)

    @mcp.tool(
        annotations={
            "title": "Sector indicator history",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def get_branch_indicator_history(
        branch_key: str, indicator_key: str, months: int = 12
    ) -> Any:
        """Verlauf eines Einzel-Indikators (z.B. ifo_index, composite_pmi,
        zew_indicator, energiepreis) in einer deutschen Branche (months
        1-24, default 12). Erst list_branch_indicators für gültige
        indicator_key aufrufen. Use for 'ifo Bauwirtschaft', 'PMI Industrie'."""
        await _user_only()
        _validate_branch(branch_key)
        m = _clamp_months(months, 24)
        try:
            return await _sectorbench_client_from_state().get_indicator_history(
                branch_key, indicator_key, m
            )
        except SectorbenchError as e:
            raise _wrap_sb(e)

    @mcp.tool(
        annotations={
            "title": "List sector indicators",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def list_branch_indicators() -> Any:
        """Katalog aller verfügbaren Indikatoren (indicator_key, Einheit,
        Quelle, Beschreibung) für deutsche Branchen. Vor
        get_branch_indicator_history aufrufen, um gültige indicator_key zu
        finden. Use for 'welche Indikatoren', 'list indicators'."""
        await _user_only()
        try:
            return await _sectorbench_client_from_state().get_indicator_catalog()
        except SectorbenchError as e:
            raise _wrap_sb(e)

    @mcp.tool(
        annotations={
            "title": "Sectorbench data freshness",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def get_sectorbench_meta() -> Any:
        """Sectorbench Daten-Aktualität: letzte fetch_run_id, fetched_at,
        Branchen-Abdeckung, weight_profile. Use for 'wie aktuell sind die
        Daten', 'wann zuletzt aktualisiert', data freshness check."""
        await _user_only()
        try:
            return await _sectorbench_client_from_state().meta()
        except SectorbenchError as e:
            raise _wrap_sb(e)

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


# Anthropic Connectors Directory requires Origin-header validation on MCP
# endpoints. Allowlist the two production chat-platform origins plus the
# server's own issuer origin (used by the in-browser OAuth login form). A
# missing Origin header is treated as a non-browser server-to-server call
# and allowed through — those are still gated by OAuth.
_DEFAULT_MCP_ORIGINS = {
    "https://claude.ai",
    "https://chatgpt.com",
}


def _allowed_mcp_origins() -> frozenset[str]:
    extra = os.environ.get("BF_EXTRA_ALLOWED_ORIGINS", "")
    extras = {o.strip() for o in extra.split(",") if o.strip()}
    issuer_origin = ""
    parsed = urlparse(get_settings().issuer)
    if parsed.scheme and parsed.netloc:
        issuer_origin = f"{parsed.scheme}://{parsed.netloc}"
    return frozenset(_DEFAULT_MCP_ORIGINS | extras | ({issuer_origin} if issuer_origin else set()))


class OriginAllowlistMiddleware(BaseHTTPMiddleware):
    """Reject /mcp requests whose Origin header is set but not on the allowlist.

    Required by Anthropic's Connectors Directory submission and defends
    against DNS-rebinding attacks from malicious local pages.
    """

    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/mcp"):
            origin = request.headers.get("origin")
            if origin and origin not in _allowed_mcp_origins():
                return JSONResponse(
                    {"error": "origin_not_allowed", "origin": origin},
                    status_code=403,
                )
        return await call_next(request)


def _allowed_hosts() -> list[str]:
    parsed = urlparse(get_settings().issuer)
    host = parsed.hostname
    extra = os.environ.get("BF_EXTRA_ALLOWED_HOSTS", "")
    extras = [h.strip() for h in extra.split(",") if h.strip()]
    base = [host] if host else []
    # Test/loopback hosts only enabled in dev (issuer points at localhost) or
    # when explicitly opted in via BF_ALLOW_TEST_HOSTS=1. Keeps "testserver"
    # and bare "localhost" out of the production allowlist where the Host
    # header is fully attacker-controlled.
    dev_issuer = host in ("localhost", "127.0.0.1")
    opt_in = os.environ.get("BF_ALLOW_TEST_HOSTS", "").lower() in ("1", "true", "yes")
    if dev_issuer or opt_in:
        return base + extras + ["localhost", "127.0.0.1", "testserver"]
    return base + extras


def _public_dir() -> Path | None:
    """Locate the bundled `public/` directory at runtime. Checked in order:
    1. `BF_PUBLIC_DIR` env override.
    2. `<cwd>/public` — set when running via `uvicorn` from the repo root
       or the Docker WORKDIR `/app`.
    3. `<repo-root>/public` relative to the source file — used in
       editable installs / local dev.
    Returns None if no candidate exists."""
    candidates = []
    env = os.environ.get("BF_PUBLIC_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / "public")
    candidates.append(Path(__file__).resolve().parents[2] / "public")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _favicon_routes() -> list:
    """Serve the Anthropic Connectors Directory branding assets (logo,
    favicon, manifest icons) at /favicon/* so the submission form can
    reference stable HTTPS URLs hosted on the MCP origin itself."""
    pub = _public_dir()
    if pub is None:
        return []
    favicon_dir = pub / "favicon"
    if not favicon_dir.is_dir():
        return []
    return [Mount("/favicon", app=StaticFiles(directory=str(favicon_dir)))]


def build_app() -> Starlette:
    mcp = _make_mcp()
    mcp_app = mcp.http_app(path="/mcp", transport="http")
    outer = Starlette(
        routes=[
            *auth.routes(),
            *rest_api.routes(),
            *_favicon_routes(),
            Mount("/", app=mcp_app),
        ],
        middleware=[
            Middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts()),
            Middleware(OriginAllowlistMiddleware),
            Middleware(WWWAuthenticateResourceMetadataMiddleware),
        ],
        lifespan=lambda _outer: _combined_lifespan(mcp_app),
    )
    return outer


@asynccontextmanager
async def _combined_lifespan(mcp_app: Starlette):
    await storage.init_db()
    _client_holder["client"] = BoniforceClient()
    _client_holder["sectorbench"] = SectorbenchClient()
    inner_lifespan = mcp_app.router.lifespan_context
    try:
        async with inner_lifespan(mcp_app):
            yield
    finally:
        await _client_holder["client"].aclose()
        await _client_holder["sectorbench"].aclose()


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
