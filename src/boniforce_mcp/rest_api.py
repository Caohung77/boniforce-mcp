"""
REST mirror of the MCP tools for ChatGPT Custom GPT "Actions".

The Custom GPT Actions feature speaks OpenAPI 3.1 + REST, not MCP. This
module exposes the current Boniforce operations as JSON REST endpoints under
``/api/v1/*`` and serves an OpenAPI spec at ``/api/openapi.json``.

Auth: same JWT bearer that protects /mcp. Each request's user is read from
the JWT subject claim, the user's stored Boniforce API key is fetched, and
the call is proxied to api.boniforce.de.
"""
from __future__ import annotations

import time
from typing import Any

import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, storage
from .config import get_settings
from .sectorbench_client import SectorbenchError


SECTORBENCH_BRANCH_KEYS: frozenset[str] = frozenset(
    {
        "automotive",
        "healthcare",
        "construction",
        "renewable_energy",
        "logistics",
        "fintech",
        "it_services",
        "retail",
        "hospitality",
        "manufacturing",
    }
)


# ---------------- bearer JWT extraction ----------------

class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


async def _authenticate(request: Request) -> tuple[str, str]:
    """Returns (user_id, bf_token) or raises HTTPError."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPError(401, "Missing or malformed Authorization header.")
    token = header[7:].strip()
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            auth.public_key_pem(),
            algorithms=["RS256"],
            audience=settings.audience,
            issuer=settings.issuer,
        )
    except jwt.PyJWTError as exc:
        raise HTTPError(401, f"Invalid token: {exc}") from exc
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPError(401, "Token missing subject claim.")
    bf_token = await storage.get_bf_token(user_id)
    if not bf_token:
        raise HTTPError(403, "No Boniforce API key linked to this user.")
    return user_id, bf_token


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _client_holder() -> Any:
    from .server import _client_holder as h

    return h["client"]


def _sectorbench_client() -> Any:
    from .server import _client_holder as h

    return h["sectorbench"]


async def _authenticate_only(request: Request) -> str:
    """Validate the user JWT but don't require a linked Boniforce key.

    Used by the Sectorbench proxy endpoints — they call upstream with the
    server's shared token and only need to know the request comes from an
    authenticated MCP user.
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPError(401, "Missing or malformed Authorization header.")
    token = header[7:].strip()
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            auth.public_key_pem(),
            algorithms=["RS256"],
            audience=settings.audience,
            issuer=settings.issuer,
        )
    except jwt.PyJWTError as exc:
        raise HTTPError(401, f"Invalid token: {exc}") from exc
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPError(401, "Token missing subject claim.")
    return user_id


def _validate_branch_key(branch_key: str) -> None:
    if branch_key not in SECTORBENCH_BRANCH_KEYS:
        raise HTTPError(
            404,
            f"Unknown branch key '{branch_key}'. "
            f"Valid: {', '.join(sorted(SECTORBENCH_BRANCH_KEYS))}.",
        )


def _wrap_sectorbench(exc: SectorbenchError) -> JSONResponse:
    """Map upstream Sectorbench errors to the proxy's HTTP response.

    - 401/403 from upstream means the server's shared token is bad — surface
      as 502 (server config), not user-facing 401, since the user JWT is fine.
    - 404 / 429 / 503 forward as-is (semantics carry over to the GPT).
    - Anything else → 502.
    """
    if exc.status in (404, 429, 503):
        return JSONResponse(
            {"error": exc.body if isinstance(exc.body, dict) else {"message": exc.body}},
            status_code=exc.status,
        )
    if exc.status in (401, 403):
        return _err(502, "Sectorbench upstream rejected the operator token.")
    return _err(502, f"Sectorbench upstream {exc.status}: {exc.body}")


def _parse_months(request: Request, *, default: int = 12, maximum: int = 36) -> int:
    raw = request.query_params.get("months")
    if raw is None:
        return default
    try:
        n = int(raw)
    except ValueError:
        raise HTTPError(400, "months must be an integer.") from None
    if n < 1 or n > maximum:
        raise HTTPError(400, f"months must be between 1 and {maximum}.")
    return n


COMPANY_IDENTIFIER_FIELDS = (
    "company_name",
    "register_type",
    "register_number",
    "register_court",
    "search_result_id",
    "session_id",
)


def _validate_company_identifier(values: dict[str, Any]) -> None:
    if values.get("search_result_id"):
        return
    missing = [
        field
        for field in ("register_type", "register_number", "register_court")
        if not values.get(field)
    ]
    if missing:
        raise HTTPError(
            400,
            "Provide search_result_id or all register fields. Missing: "
            + ", ".join(missing),
        )


def _company_query_params(request: Request) -> dict[str, str]:
    params = {
        field: request.query_params[field]
        for field in COMPANY_IDENTIFIER_FIELDS
        if request.query_params.get(field) is not None
    }
    _validate_company_identifier(params)
    return params


# ---------------- job-status helpers (shared with MCP server.py) ----------------

TERMINAL_JOB_STATUSES = frozenset({"completed", "finished", "failed", "error"})
REPORT_STATUS_POLL_SECONDS = 30
EXPECTED_REPORT_DURATION_SECONDS = 120
MAX_REPORT_WAIT_SECONDS = 180
_job_started_at: dict[str, float] = {}


def _job_elapsed_seconds(job_id: str) -> int:
    """Return best-effort elapsed time for user-facing progress copy."""
    now = time.monotonic()
    if len(_job_started_at) >= 1_000:
        stale_before = now - 3_600
        stale_jobs = [
            known_job_id
            for known_job_id, started_at in _job_started_at.items()
            if started_at < stale_before
        ]
        for known_job_id in stale_jobs:
            _job_started_at.pop(known_job_id, None)
    started_at = _job_started_at.setdefault(job_id, now)
    return max(0, int(now - started_at))


def _job_progress_text(status: str, elapsed_seconds: int) -> tuple[str, str]:
    """Map real status + elapsed time to honest, non-percentage progress copy."""
    if status in ("completed", "finished"):
        return "completed", "✅ Boniscore report ready."
    if status in ("failed", "error"):
        return "failed", "❌ Boniscore report generation failed."
    if status == "queued":
        return (
            "queued",
            "⏳ Boniscore report queued. Typical completion time is about 2 minutes.",
        )
    if elapsed_seconds < 30:
        return (
            "started",
            "⏳ Boniscore report started. Typical completion time is about 2 minutes.",
        )
    if elapsed_seconds < 60:
        return "processing", "🔍 Register data is being collected and checked."
    if elapsed_seconds < 90:
        return "analysing", "📊 Financial and risk data are being analysed."
    if elapsed_seconds < 120:
        return "calculating", "🧮 The Boniscore is being calculated."
    if elapsed_seconds < MAX_REPORT_WAIT_SECONDS:
        return (
            "finalising",
            "⏳ The report is being finalised and is taking slightly longer than usual.",
        )
    return (
        "delayed",
        "⏳ The report is still processing and is taking longer than usual.",
    )


def annotate_job_outcome(
    payload: Any, job_id: str | None, status_value: str | None
) -> Any:
    """Mutate ``payload`` in place to add ``done`` and (when not done)
    ``next_action`` fields so the model knows whether to keep polling.

    The model (ChatGPT Action / Claude tool call) only sees one HTTP response
    per call. ChatGPT's per-call timeout is ~45s, so the GPT uses 30-second
    polls during the typical 120-second report window. ``next_action`` makes
    both the visible text update and final score-reading step explicit.
    """
    if not isinstance(payload, dict):
        return payload
    payload_status = payload.get("status")
    status_str = (status_value or payload_status or "").lower().strip()
    done = status_str in TERMINAL_JOB_STATUSES
    payload["done"] = done
    jid = job_id or payload.get("job_id") or "<job_id>"
    elapsed_seconds = _job_elapsed_seconds(jid)
    progress_stage, progress_message = _job_progress_text(
        status_str, elapsed_seconds
    )
    payload["progress_stage"] = progress_stage
    payload["progress_message"] = progress_message
    payload["elapsed_seconds"] = elapsed_seconds
    payload["expected_duration_seconds"] = EXPECTED_REPORT_DURATION_SECONDS
    payload["poll_after_seconds"] = 0 if done else REPORT_STATUS_POLL_SECONDS
    if status_str in ("completed", "finished"):
        report_id = payload.get("report_id")
        if isinstance(payload.get("report"), dict):
            payload["next_action"] = (
                "Show progress_message to the user. Read report.score for the Boniscore, then "
                "report.credit_limit and report.credit_assessment_result."
            )
        elif report_id:
            payload["next_action"] = (
                f"Report completed. Call getReport with report_id={report_id} "
                "now to fetch the Boniscore; the job status itself is not the "
                "credit report."
            )
        else:
            payload["next_action"] = (
                "Report completed. Call getReport now with the report_id returned "
                "by createReport to fetch the Boniscore."
            )
    elif not done:
        payload["next_action"] = (
            "Show progress_message to the user now. Then call getJobStatus again "
            f"with job_id={jid} and wait=30. Continue until done=true; expect about "
            "4 polls/120 seconds and allow up to 6 polls/180 seconds."
        )
    return payload


async def _attach_completed_report(
    client: Any,
    token: str,
    payload: Any,
    status_value: str | None,
) -> Any:
    """Inline the finished report so action clients receive the Boniscore."""
    if not isinstance(payload, dict):
        return payload
    status_str = (status_value or "").lower().strip()
    report_id = payload.get("report_id")
    if (
        status_str in ("completed", "finished")
        and report_id
        and not isinstance(payload.get("report"), dict)
    ):
        try:
            payload["report"] = await client.get_report(token, report_id)
        except Exception:
            # Keep the completed status usable. next_action will tell the model
            # to call getReport explicitly if the convenience fetch failed.
            pass
    return payload


# ---------------- handlers ----------------

async def search_companies(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    query = request.query_params.get("query")
    if not query:
        return _err(400, "Missing required query parameter: query.")
    try:
        data = await _client_holder().search_companies(token, query)
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    return JSONResponse(data)


async def search_companies_advanced(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    query = request.query_params.get("query")
    if not query:
        return _err(400, "Missing required query parameter: query.")
    try:
        data = await _client_holder().search_companies_advanced(token, query)
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    return JSONResponse(data)


async def list_reports(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _client_holder().list_reports(token)
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    return JSONResponse(data)


async def create_report(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        body = await request.json()
    except Exception:
        return _err(400, "Body must be valid JSON.")
    try:
        _validate_company_identifier(body)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _client_holder().create_report(
            token,
            company_name=body.get("company_name"),
            register_type=body.get("register_type"),
            register_number=body.get("register_number"),
            register_court=body.get("register_court"),
            search_result_id=body.get("search_result_id"),
            session_id=body.get("session_id"),
        )
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    if isinstance(data, dict) and data.get("job_id"):
        _job_elapsed_seconds(data["job_id"])
    # Optional inline wait: ?wait=N seconds (max 40). When set, we poll
    # get_job_status server-side and additionally fetch the finished report
    # so the caller gets a one-shot answer instead of needing to poll.
    wait_param = request.query_params.get("wait")
    status_value: str | None = None
    if wait_param and data.get("job_id"):
        try:
            wait_s = max(0.0, min(40.0, float(wait_param)))
        except ValueError:
            wait_s = 0.0
        if wait_s > 0:
            client = _client_holder()
            status = await client.wait_for_job(token, data["job_id"], max_wait_s=wait_s)
            data["final_status"] = status
            status_value = (status or {}).get("status")
            await _attach_completed_report(client, token, data, status_value)
    annotate_job_outcome(data, data.get("job_id"), status_value)
    return JSONResponse(data)


async def get_report(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    report_id = request.path_params["report_id"]
    try:
        data = await _client_holder().get_report(token, report_id)
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    return JSONResponse(data)


async def get_job_status(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    job_id = request.path_params["job_id"]
    wait_param = request.query_params.get("wait")
    try:
        if wait_param:
            wait_s = max(0.0, min(40.0, float(wait_param)))
            data = await _client_holder().wait_for_job(token, job_id, max_wait_s=wait_s)
        else:
            data = await _client_holder().get_job_status(token, job_id)
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    if isinstance(data, dict):
        await _attach_completed_report(
            _client_holder(), token, data, data.get("status")
        )
    annotate_job_outcome(data, job_id, (data or {}).get("status") if isinstance(data, dict) else None)
    return JSONResponse(data)


async def get_report_financial_data(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    report_id = request.path_params["report_id"]
    try:
        data = await _client_holder().get_report_financial_data(token, report_id)
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    return JSONResponse(data)


async def get_report_financial_analysis(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    report_id = request.path_params["report_id"]
    try:
        data = await _client_holder().get_report_financial_analysis(token, report_id)
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    return JSONResponse(data)


async def get_financial_data(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
        params = _company_query_params(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _client_holder().get_financial_data(token, **params)
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    return JSONResponse(data)


async def get_financial_analysis(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
        params = _company_query_params(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _client_holder().get_financial_analysis(token, **params)
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    return JSONResponse(data)


async def get_company_details(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _client_holder().get_company_details(
            token, request.path_params["report_id"]
        )
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    return JSONResponse(data)


async def get_company_shareholders(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _client_holder().get_company_shareholders(
            token, request.path_params["report_id"]
        )
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    return JSONResponse(data)


async def get_company_holdings(request: Request) -> Response:
    try:
        _, token = await _authenticate(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _client_holder().get_company_holdings(
            token, request.path_params["report_id"]
        )
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
    return JSONResponse(data)


# ---------------- Sectorbench proxy handlers ----------------


async def list_branch_scores(request: Request) -> Response:
    try:
        await _authenticate_only(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _sectorbench_client().get_all_scores()
    except SectorbenchError as exc:
        return _wrap_sectorbench(exc)
    except Exception as exc:
        return _err(502, f"Sectorbench upstream: {exc}")
    return JSONResponse(data)


async def get_branch_ranking(request: Request) -> Response:
    try:
        await _authenticate_only(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _sectorbench_client().get_ranking()
    except SectorbenchError as exc:
        return _wrap_sectorbench(exc)
    except Exception as exc:
        return _err(502, f"Sectorbench upstream: {exc}")
    return JSONResponse(data)


async def get_branch(request: Request) -> Response:
    try:
        await _authenticate_only(request)
        branch_key = request.path_params["branch_key"]
        _validate_branch_key(branch_key)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _sectorbench_client().get_branch(branch_key)
    except SectorbenchError as exc:
        return _wrap_sectorbench(exc)
    except Exception as exc:
        return _err(502, f"Sectorbench upstream: {exc}")
    return JSONResponse(data)


async def get_branch_history(request: Request) -> Response:
    try:
        await _authenticate_only(request)
        branch_key = request.path_params["branch_key"]
        _validate_branch_key(branch_key)
        months = _parse_months(request, default=12, maximum=24)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _sectorbench_client().get_branch_history(branch_key, months)
    except SectorbenchError as exc:
        return _wrap_sectorbench(exc)
    except Exception as exc:
        return _err(502, f"Sectorbench upstream: {exc}")
    return JSONResponse(data)


async def get_branch_news(request: Request) -> Response:
    try:
        await _authenticate_only(request)
        branch_key = request.path_params["branch_key"]
        _validate_branch_key(branch_key)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _sectorbench_client().get_branch_news(branch_key)
    except SectorbenchError as exc:
        return _wrap_sectorbench(exc)
    except Exception as exc:
        return _err(502, f"Sectorbench upstream: {exc}")
    return JSONResponse(data)


async def get_branch_insolvency_history(request: Request) -> Response:
    try:
        await _authenticate_only(request)
        branch_key = request.path_params["branch_key"]
        _validate_branch_key(branch_key)
        months = _parse_months(request, default=12, maximum=36)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _sectorbench_client().get_branch_insolvency_history(
            branch_key, months
        )
    except SectorbenchError as exc:
        return _wrap_sectorbench(exc)
    except Exception as exc:
        return _err(502, f"Sectorbench upstream: {exc}")
    return JSONResponse(data)


async def get_branch_indicator_history(request: Request) -> Response:
    try:
        await _authenticate_only(request)
        branch_key = request.path_params["branch_key"]
        _validate_branch_key(branch_key)
        indicator_key = request.path_params["indicator_key"]
        months = _parse_months(request, default=12, maximum=24)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _sectorbench_client().get_indicator_history(
            branch_key, indicator_key, months
        )
    except SectorbenchError as exc:
        return _wrap_sectorbench(exc)
    except Exception as exc:
        return _err(502, f"Sectorbench upstream: {exc}")
    return JSONResponse(data)


async def list_indicators(request: Request) -> Response:
    try:
        await _authenticate_only(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _sectorbench_client().get_indicator_catalog()
    except SectorbenchError as exc:
        return _wrap_sectorbench(exc)
    except Exception as exc:
        return _err(502, f"Sectorbench upstream: {exc}")
    return JSONResponse(data)


async def get_sectorbench_meta(request: Request) -> Response:
    try:
        await _authenticate_only(request)
    except HTTPError as e:
        return _err(e.status, e.message)
    try:
        data = await _sectorbench_client().meta()
    except SectorbenchError as exc:
        return _wrap_sectorbench(exc)
    except Exception as exc:
        return _err(502, f"Sectorbench upstream: {exc}")
    return JSONResponse(data)


# ---------------- OpenAPI spec ----------------

def _company_lookup_parameters() -> list[dict[str, Any]]:
    descriptions = {
        "company_name": "Company name; improves matching when register fields are used.",
        "register_type": "German register type, such as HRB or HRA.",
        "register_number": "German register number.",
        "register_court": "German register court.",
        "search_result_id": "Identifier returned by either company-search endpoint.",
        "session_id": "Optional Boniforce session identifier.",
    }
    return [
        {
            "in": "query",
            "name": name,
            "required": False,
            "schema": {"type": "string"},
            "description": description,
        }
        for name, description in descriptions.items()
    ]


def _openapi_spec() -> dict[str, Any]:
    iss = get_settings().issuer
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Boniforce REST API (for ChatGPT Custom GPTs)",
            "version": "1.1.0",
            "description": (
                "Per-user proxy for the Boniforce credit-data API. Authenticate "
                "via OAuth 2.1 with the Boniforce MCP authorization server "
                f"({iss}) — each end user pastes their own Boniforce API key "
                "during the OAuth flow. After authorization the same JWT can "
                "be used here as Bearer token.\n\n"
                "**MANDATORY workflow for company questions:**\n"
                "0. GET /api/v1/reports FIRST. If a completed report for the "
                "company exists with created_at ≤30 days old, REUSE its "
                "report_id (call /reports/{id} or /reports/{id}/financial_data). "
                "Do NOT call POST /reports — that charges 75 credits and re-runs "
                "a 30-120s computation that returns the same data.\n"
                "1. Only if no fresh report exists: GET /api/v1/search (then "
                "/search/advanced only if needed) → POST /api/v1/reports using "
                "search_result_id without wait. Show progress_message immediately, "
                "then poll GET /api/v1/jobs/{id}/status?wait=30 and show each new "
                "progress_message before polling again. Typical duration is 120s; "
                "allow up to 6 polls/180s. "
                "When done=true, read the Boniscore from report.score. If report "
                "is absent, immediately call GET /api/v1/reports/{report_id}; a "
                "completed job status alone is not the credit report.\n"
                "Follow-up questions about a company you already have a report_id "
                "for: ALWAYS reuse that report_id, never POST /reports again."
            ),
        },
        "servers": [{"url": iss}],
        "components": {
            "securitySchemes": {
                "OAuth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": f"{iss}/oauth/authorize",
                            "tokenUrl": f"{iss}/oauth/token",
                            "scopes": {"mcp": "Boniforce MCP scope"},
                        }
                    },
                }
            },
            "schemas": {
                "Company": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "active": {"type": "boolean"},
                        "register_type": {"type": ["string", "null"]},
                        "register_number": {"type": ["string", "null"]},
                        "register_court": {"type": ["string", "null"]},
                        "search_result_id": {"type": ["string", "null"]},
                        "registered_office": {
                            "type": ["string", "null"],
                            "description": (
                                "City used to disambiguate advanced-search results."
                            ),
                        },
                    },
                    "required": ["name", "active"],
                },
                "Report": {
                    "type": "object",
                    "properties": {
                        "report_id": {"type": "string"},
                        "version": {"type": "number"},
                        "score": {
                            "type": ["integer", "null"],
                            "description": "Boniscore 0–100; higher = lower risk.",
                        },
                        "score_details": {
                            "oneOf": [
                                {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "color_code": {"type": "integer"},
                                        "range": {"type": ["string", "null"]},
                                        "description": {"type": ["string", "null"]},
                                    },
                                },
                                {"type": "null"},
                            ]
                        },
                        "credit_limit": {"type": ["number", "null"]},
                        "credit_assessment_result": {
                            "type": ["string", "null"],
                            "description": "APPROVE / REVIEW / DECLINE",
                        },
                        "assessments": {"type": ["array", "null"]},
                        "company": {"type": ["object", "null"]},
                        "status": {
                            "type": ["string", "null"],
                            "description": "Company state, e.g. active or liquidation.",
                        },
                        "created_at": {
                            "type": ["string", "null"],
                            "format": "date-time",
                        },
                    },
                    "required": ["report_id"],
                },
                "JobStatus": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "report_id": {"type": "string"},
                        "status": {
                            "type": "string",
                            "description": "queued | running | completed | failed",
                        },
                        "done": {
                            "type": "boolean",
                            "description": (
                                "True if the job reached a terminal state "
                                "(completed/finished/failed/error). False means "
                                "the caller MUST call this endpoint again with "
                                "?wait=30 to keep waiting."
                            ),
                        },
                        "next_action": {
                            "type": "string",
                            "description": (
                                "Required follow-up for the model: keep polling "
                                "when done=false; when completed, read report.score "
                                "or call getReport if report is absent."
                            ),
                        },
                        "progress_stage": {
                            "type": "string",
                            "enum": [
                                "started",
                                "queued",
                                "processing",
                                "analysing",
                                "calculating",
                                "finalising",
                                "delayed",
                                "completed",
                                "failed",
                            ],
                            "description": "Current user-facing processing stage.",
                        },
                        "progress_message": {
                            "type": "string",
                            "description": (
                                "Display this text to the user before the next poll."
                            ),
                        },
                        "elapsed_seconds": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Best-effort elapsed processing time.",
                        },
                        "expected_duration_seconds": {
                            "type": "integer",
                            "const": 120,
                        },
                        "poll_after_seconds": {
                            "type": "integer",
                            "enum": [0, 30],
                            "description": "Zero when done; otherwise poll after 30s.",
                        },
                        "report": {
                            "description": (
                                "Finished credit report, included when status is "
                                "completed and the convenience fetch succeeds. Read "
                                "report.score for the Boniscore."
                            ),
                            "oneOf": [
                                {"$ref": "#/components/schemas/Report"},
                                {"type": "null"},
                            ],
                        },
                        "error_message": {"type": "string", "nullable": True},
                    },
                },
                "Error": {
                    "type": "object",
                    "properties": {"error": {"type": "string"}},
                },
                "FinancialFeaturesYear": {
                    "type": "object",
                    "description": (
                        "Per-year summary metrics used by the Boniscore engine. "
                        "All amounts in EUR unless stated otherwise."
                    ),
                    "properties": {
                        "jahr": {"type": "integer", "description": "Fiscal year (YYYY)."},
                        "jahresueberschuss": {"type": "number", "nullable": True},
                        "eigenkapital": {"type": "number", "nullable": True},
                        "verbindlichkeiten": {"type": "number", "nullable": True},
                        "umlaufvermoegen": {"type": "number", "nullable": True},
                        "bilanzsumme": {"type": "number", "nullable": True},
                        "forderungen": {"type": "number", "nullable": True},
                        "liquide_mittel": {"type": "number", "nullable": True},
                    },
                },
                "AktivaAnlagevermoegenDetails": {
                    "type": "object",
                    "properties": {
                        "sachanlagen": {"type": "number", "nullable": True},
                        "immaterielle_vermoegensgegenstaende": {"type": "number", "nullable": True},
                        "finanzanlagen": {"type": "number", "nullable": True},
                    },
                },
                "AktivaUmlaufvermoegenDetails": {
                    "type": "object",
                    "properties": {
                        "vorraete": {"type": "number", "nullable": True},
                        "forderungen": {"type": "number", "nullable": True},
                        "kassenbestand_kreditinstitut": {"type": "number", "nullable": True},
                    },
                },
                "AktivaVorraeteDetails": {
                    "type": "object",
                    "properties": {
                        "fertige_erzeugnisse_waren": {"type": "number", "nullable": True},
                        "geleistete_anzahlungen": {"type": "number", "nullable": True},
                        "roh_hilfs_betriebsstoffe": {"type": "number", "nullable": True},
                        "unfertige_erzeugnisse": {"type": "number", "nullable": True},
                    },
                },
                "Aktiva": {
                    "type": "object",
                    "description": "Full assets side of the balance sheet.",
                    "properties": {
                        "anlagevermoegen": {"type": "number", "nullable": True},
                        "anlagevermoegen_details": {
                            "$ref": "#/components/schemas/AktivaAnlagevermoegenDetails"
                        },
                        "umlaufvermoegen": {"type": "number", "nullable": True},
                        "umlaufvermoegen_details": {
                            "$ref": "#/components/schemas/AktivaUmlaufvermoegenDetails"
                        },
                        "vorraete": {"type": "number", "nullable": True},
                        "vorraete_details": {
                            "$ref": "#/components/schemas/AktivaVorraeteDetails"
                        },
                        "bilanzsumme": {"type": "number", "nullable": True},
                    },
                },
                "PassivaEigenkapitalDetails": {
                    "type": "object",
                    "properties": {
                        "gezeichnetes_kapital": {"type": "number", "nullable": True},
                        "gewinnvortrag": {"type": "number", "nullable": True},
                        "verlustvortrag": {"type": "number", "nullable": True},
                        "jahresueberschuss": {"type": "number", "nullable": True},
                        "jahresfehlbetrag": {"type": "number", "nullable": True},
                        "nicht_gedeckter_fehlbetrag": {"type": "number", "nullable": True},
                    },
                },
                "PassivaRueckstellungenDetails": {
                    "type": "object",
                    "properties": {
                        "steuerrueckstellungen": {"type": "number", "nullable": True},
                        "sonstige_rueckstellungen": {"type": "number", "nullable": True},
                        "pensionsrueckstellungen": {"type": "number", "nullable": True},
                    },
                },
                "PassivaVerbindlichkeitenDetails": {
                    "type": "object",
                    "properties": {
                        "lieferungen_leistungen": {"type": "number", "nullable": True},
                        "gegenueber_gesellschaftern": {"type": "number", "nullable": True},
                        "gegenueber_kreditinstituten": {"type": "number", "nullable": True},
                        "gegen_verbundene_unternehmen": {"type": "number", "nullable": True},
                        "sonstige": {"type": "number", "nullable": True},
                        "anleihen": {"type": "number", "nullable": True},
                        "restlaufzeit_bis_1_jahr": {"type": "number", "nullable": True},
                        "restlaufzeit_mehr_als_1_jahr": {"type": "number", "nullable": True},
                    },
                },
                "Passiva": {
                    "type": "object",
                    "description": "Full liabilities + equity side of the balance sheet.",
                    "properties": {
                        "eigenkapital": {"type": "number", "nullable": True},
                        "eigenkapital_details": {
                            "$ref": "#/components/schemas/PassivaEigenkapitalDetails"
                        },
                        "rueckstellungen": {"type": "number", "nullable": True},
                        "rueckstellungen_details": {
                            "$ref": "#/components/schemas/PassivaRueckstellungenDetails"
                        },
                        "verbindlichkeiten": {"type": "number", "nullable": True},
                        "verbindlichkeiten_details": {
                            "$ref": "#/components/schemas/PassivaVerbindlichkeitenDetails"
                        },
                        "bilanzsumme": {"type": "number", "nullable": True},
                    },
                },
                "FinancialReport": {
                    "type": "object",
                    "description": (
                        "Full annual filing breakdown: Aktiva, Passiva, "
                        "Gewinn- und Verlustrechnung (GuV). Sourced from "
                        "the Bundesanzeiger annual filing."
                    ),
                    "properties": {
                        "year": {"type": "integer"},
                        "currency": {"type": "string", "example": "EUR"},
                        "aktiva": {"$ref": "#/components/schemas/Aktiva"},
                        "passiva": {"$ref": "#/components/schemas/Passiva"},
                        "guv": {
                            "type": "object",
                            "description": (
                                "Profit & loss statement. Open dict; field set "
                                "depends on the filing's level of detail."
                            ),
                            "additionalProperties": True,
                        },
                    },
                },
                "FinancialDataResponse": {
                    "type": "object",
                    "description": (
                        "Balance-sheet history returned directly or for a finished report. "
                        "`financials` is the per-year summary; `financial_reports` "
                        "is the full Aktiva/Passiva/GuV breakdown when available."
                    ),
                    "properties": {
                        "report_id": {"type": "string"},
                        "register_type": {"type": "string"},
                        "register_number": {"type": "string"},
                        "register_court": {"type": "string"},
                        "financials": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/FinancialFeaturesYear"},
                        },
                        "financial_reports": {
                            "type": "array",
                            "nullable": True,
                            "items": {"$ref": "#/components/schemas/FinancialReport"},
                        },
                        "created_at": {
                            "type": "string",
                            "format": "date-time",
                            "nullable": True,
                        },
                    },
                },
                "FinancialRatio": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "value": {"type": "number"},
                        "score": {"type": "integer"},
                        "color": {"type": "integer"},
                    },
                    "required": ["name", "value", "score", "color"],
                },
                "FinancialAnalysisYear": {
                    "allOf": [
                        {"$ref": "#/components/schemas/FinancialFeaturesYear"},
                        {
                            "type": "object",
                            "properties": {
                                "score": {"type": ["number", "null"]},
                                "ratios": {
                                    "type": "array",
                                    "items": {
                                        "$ref": "#/components/schemas/FinancialRatio"
                                    },
                                },
                            },
                        },
                    ]
                },
                "FinancialAnalysisResponse": {
                    "type": "object",
                    "properties": {
                        "report_id": {"type": "string"},
                        "register_type": {"type": "string"},
                        "register_number": {"type": "string"},
                        "register_court": {"type": "string"},
                        "financials": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/FinancialAnalysisYear"
                            },
                        },
                        "created_at": {"type": ["string", "null"]},
                    },
                },
                "CompanyRegisterInformation": {
                    "type": "object",
                    "properties": {
                        "register_type": {"type": ["string", "null"]},
                        "register_number": {"type": ["string", "null"]},
                        "register_court": {"type": ["string", "null"]},
                    },
                },
                "CompanyFirmographics": {
                    "type": "object",
                    "properties": {
                        "employees": {"type": ["integer", "null"]},
                        "employees_class": {"type": ["string", "null"]},
                        "legal_type": {"type": ["string", "null"]},
                        "foundation_year": {"type": ["integer", "null"]},
                    },
                },
                "Representative": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": ["string", "null"]},
                        "type": {"type": "string"},
                        "start_date": {"type": ["string", "null"], "format": "date"},
                        "end_date": {"type": ["string", "null"], "format": "date"},
                    },
                    "required": ["name", "type"],
                },
                "CompanyDetailsResponse": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "report_id": {"type": "string"},
                        "address": {"type": ["string", "null"]},
                        "register_info": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/CompanyRegisterInformation"},
                                {"type": "null"},
                            ]
                        },
                        "firmographics": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/CompanyFirmographics"},
                                {"type": "null"},
                            ]
                        },
                        "representatives": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Representative"},
                        },
                    },
                    "required": ["name", "report_id"],
                },
                "OwnershipEntry": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "percentage_share": {"type": ["number", "null"]},
                        "nominal_share": {"type": ["number", "null"]},
                        "currency": {"type": ["string", "null"], "default": "EUR"},
                        "register_info": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/CompanyRegisterInformation"},
                                {"type": "null"},
                            ]
                        },
                    },
                    "required": ["name", "type"],
                },
                "ShareholdersResponse": {
                    "type": "object",
                    "properties": {
                        "report_id": {"type": "string"},
                        "shareholders": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/OwnershipEntry"},
                        },
                        "last_updated": {
                            "type": ["string", "null"],
                            "format": "date-time",
                        },
                        "available": {"type": "boolean", "default": True},
                        "message": {"type": ["string", "null"]},
                    },
                    "required": ["report_id"],
                },
                "HoldingsResponse": {
                    "type": "object",
                    "properties": {
                        "report_id": {"type": "string"},
                        "holdings": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/OwnershipEntry"},
                        },
                        "last_updated": {
                            "type": ["string", "null"],
                            "format": "date-time",
                        },
                        "available": {"type": "boolean", "default": True},
                        "message": {"type": ["string", "null"]},
                    },
                    "required": ["report_id"],
                },
                "BranchKey": {
                    "type": "string",
                    "description": "WZ-2008-aligned key for one of the 10 covered German sectors.",
                    "enum": [
                        "automotive",
                        "healthcare",
                        "construction",
                        "renewable_energy",
                        "logistics",
                        "fintech",
                        "it_services",
                        "retail",
                        "hospitality",
                        "manufacturing",
                    ],
                },
                "BranchScore": {
                    "type": "object",
                    "properties": {
                        "branch_key": {"$ref": "#/components/schemas/BranchKey"},
                        "branch_name_de": {"type": "string"},
                        "branch_name_en": {"type": "string"},
                        "composite_score": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 100,
                            "description": "Composite branch-health score 0-100; higher = healthier sector.",
                        },
                        "risk_level": {
                            "type": "string",
                            "description": "Free-form upstream label, e.g. low/medium/high or Excellent/Critical.",
                        },
                        "confidence": {"type": "string"},
                        "dimensions": {
                            "type": "object",
                            "properties": {
                                "financial_health": {"type": "number", "nullable": True},
                                "market_dynamics": {"type": "number", "nullable": True},
                                "regulatory_climate": {"type": "number", "nullable": True},
                                "innovation_index": {"type": "number", "nullable": True},
                                "labor_market": {"type": "number", "nullable": True},
                                "external_risk": {"type": "number", "nullable": True},
                            },
                        },
                        "rank": {"type": "integer", "minimum": 1, "maximum": 10},
                        "percentile": {"type": "number"},
                        "rank_delta": {"type": "integer", "nullable": True},
                        "fetch_run_id": {"type": "integer"},
                        "fetched_at": {"type": "string", "format": "date-time"},
                        "weight_profile": {
                            "type": "string",
                            "enum": ["bank", "default", "equal"],
                        },
                    },
                    "required": [
                        "branch_key",
                        "composite_score",
                        "risk_level",
                        "confidence",
                        "dimensions",
                        "rank",
                        "fetched_at",
                    ],
                },
                "BranchScoreHistoryPoint": {
                    "type": "object",
                    "properties": {
                        "reference_period": {"type": "string", "format": "date"},
                        "fetched_at": {"type": "string", "format": "date-time"},
                        "composite_score": {"type": "number"},
                        "risk_level": {"type": "string"},
                        "dimensions": {
                            "type": "object",
                            "additionalProperties": {"type": "number"},
                        },
                    },
                },
                "IndicatorCatalogEntry": {
                    "type": "object",
                    "properties": {
                        "indicator_key": {"type": "string"},
                        "name_de": {"type": "string"},
                        "name_en": {"type": "string"},
                        "description_de": {"type": "string"},
                        "description_en": {"type": "string"},
                        "unit": {"type": "string"},
                        "higher_is_better": {"type": "boolean"},
                        "publication_lag_months": {"type": "integer", "nullable": True},
                    },
                },
                "IndicatorHistoryPoint": {
                    "type": "object",
                    "properties": {
                        "reference_period": {"type": "string", "format": "date"},
                        "reference_period_inferred": {"type": "boolean"},
                        "fetched_at": {"type": "string", "format": "date-time"},
                        "value": {"type": "number", "nullable": True},
                    },
                },
                "InsolvencyHistoryPoint": {
                    "type": "object",
                    "properties": {
                        "reference_period": {"type": "string", "format": "date"},
                        "opened_cases": {"type": "integer", "nullable": True},
                        "dismissed_cases": {"type": "integer", "nullable": True},
                        "total_cases": {"type": "integer", "nullable": True},
                    },
                },
                "NewsReport": {
                    "type": "object",
                    "properties": {
                        "branch_key": {"$ref": "#/components/schemas/BranchKey"},
                        "window_start": {"type": "string", "format": "date"},
                        "window_end": {"type": "string", "format": "date"},
                        "executive_overview": {"type": "string"},
                        "key_developments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "summary": {"type": "string"},
                                    "impact": {"type": "string"},
                                    "citations": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                    },
                                },
                            },
                        },
                        "impact_assessment": {"type": "string"},
                        "risk_watchlist": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "item": {"type": "string"},
                                    "severity": {"type": "string"},
                                },
                            },
                        },
                        "next_week_outlook": {"type": "string"},
                        "citations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "integer"},
                                    "title": {"type": "string"},
                                    "url": {"type": "string", "format": "uri"},
                                    "source": {"type": "string"},
                                },
                            },
                        },
                        "citation_count": {"type": "integer"},
                        "published_at": {"type": "string", "format": "date-time"},
                        "model": {"type": "string"},
                    },
                },
                "SectorbenchMeta": {
                    "type": "object",
                    "properties": {
                        "api_version": {"type": "string"},
                        "latest_fetch_run_id": {"type": "integer"},
                        "latest_fetch_run_at": {"type": "string", "format": "date-time"},
                        "weight_profile": {"type": "string"},
                        "branch_count": {"type": "integer"},
                    },
                },
            },
        },
        "security": [{"OAuth2": ["mcp"]}],
        "paths": {
            "/api/v1/search": {
                "get": {
                    "operationId": "searchCompanies",
                    "summary": "Search the Boniforce database for German companies by name.",
                    "parameters": [
                        {
                            "in": "query",
                            "name": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Company name or partial name.",
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "List of matching companies.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Company"},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/search/advanced": {
                "get": {
                    "operationId": "searchCompaniesAdvanced",
                    "summary": "Fallback search using broader company-name matching.",
                    "description": (
                        "Use only when normal search returns no suitable result. "
                        "Costs 5 credits and includes registered_office for disambiguation."
                    ),
                    "parameters": [
                        {
                            "in": "query",
                            "name": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "List of matching companies.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "$ref": "#/components/schemas/Company"
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/reports": {
                "get": {
                    "operationId": "listReports",
                    "summary": "List previously generated reports for the authenticated account.",
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "operationId": "createReport",
                    "x-openai-isConsequential": False,
                    "summary": "Start Boniscore report and return text progress immediately.",
                    "description": (
                        "Costs 75 credits. Omit wait so progress_message appears immediately. "
                        "Show that message, then call getJobStatus with wait=30 until done=true "
                        "(typically 4 polls/120s; up to 6/180s). Read report.score when ready."
                    ),
                    "parameters": [
                        {
                            "in": "query",
                            "name": "wait",
                            "required": False,
                            "schema": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 40,
                                "default": 0,
                            },
                            "description": (
                                "Compatibility option. Omit for immediate GPT progress text."
                            ),
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "company_name": {"type": ["string", "null"]},
                                        "register_type": {"type": ["string", "null"]},
                                        "register_number": {"type": ["string", "null"]},
                                        "register_court": {"type": ["string", "null"]},
                                        "search_result_id": {
                                            "type": ["string", "null"]
                                        },
                                        "session_id": {"type": ["string", "null"]},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": (
                                "Job accepted. With wait, a completed response includes "
                                "the finished credit report at report and its Boniscore "
                                "at report.score."
                            ),
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/JobStatus"
                                    }
                                }
                            },
                        }
                    },
                },
            },
            "/api/v1/financial_data": {
                "get": {
                    "operationId": "getFinancialData",
                    "summary": "Fetch raw financial statements directly (25 credits).",
                    "description": (
                        "Identify the company with search_result_id or all register fields."
                    ),
                    "parameters": _company_lookup_parameters(),
                    "responses": {
                        "200": {
                            "description": "Financial statement data.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/FinancialDataResponse"
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/financial_data/analysis": {
                "get": {
                    "operationId": "getFinancialAnalysis",
                    "summary": "Fetch financial score and ratio analysis (50 credits).",
                    "description": (
                        "Identify the company with search_result_id or all register fields."
                    ),
                    "parameters": _company_lookup_parameters(),
                    "responses": {
                        "200": {
                            "description": "Financial analysis.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/FinancialAnalysisResponse"
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/reports/{report_id}": {
                "get": {
                    "operationId": "getReport",
                    "summary": "Fetch a finished report (Boniscore + credit limit + assessment).",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "report_id",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Finished Boniscore report.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Report"}
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/jobs/{job_id}/status": {
                "get": {
                    "operationId": "getJobStatus",
                    "summary": "Poll report job and return the next text progress update.",
                    "description": (
                        "Use wait=30 until done=true. Show progress_message after each response "
                        "before polling again. Expect about 4 polls/120s; allow up to 6/180s. "
                        "When complete, read report.score or call getReport if report is absent."
                    ),
                    "parameters": [
                        {
                            "in": "path",
                            "name": "job_id",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "in": "query",
                            "name": "wait",
                            "required": False,
                            "schema": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 40,
                                "default": 30,
                            },
                            "description": "Use 30 seconds for visible GPT progress updates.",
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": (
                                "Current job status; completed responses normally include "
                                "the finished report and report.score."
                            ),
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/JobStatus"
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/reports/{report_id}/financial_data": {
                "get": {
                    "operationId": "getReportFinancialData",
                    "summary": (
                        "Balance-sheet history attached to a finished report — "
                        "per-year summary plus full Aktiva/Passiva/GuV breakdown."
                    ),
                    "parameters": [
                        {
                            "in": "path",
                            "name": "report_id",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/FinancialDataResponse"
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/reports/{report_id}/financial_data/analysis": {
                "get": {
                    "operationId": "getReportFinancialAnalysis",
                    "summary": "Per-year financial ratios + sub-scores for a finished report.",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "report_id",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/FinancialAnalysisResponse"
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/company/{report_id}/details": {
                "get": {
                    "operationId": "getCompanyDetails",
                    "summary": "Company metadata and representatives for a report.",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "report_id",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Company details.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/CompanyDetailsResponse"
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/company/{report_id}/shareholders": {
                "get": {
                    "operationId": "getCompanyShareholders",
                    "summary": "Shareholders; cached free or 25 credits on refresh.",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "report_id",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Shareholders.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ShareholdersResponse"
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/company/{report_id}/holdings": {
                "get": {
                    "operationId": "getCompanyHoldings",
                    "summary": "Holdings; cached free or 25 credits on refresh.",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "report_id",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Holdings.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/HoldingsResponse"
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/branches": {
                "get": {
                    "operationId": "listBranchScores",
                    "summary": "Current branch-health scores for all 10 German sectors.",
                    "description": (
                        "Returns the latest composite score (0-100), risk level, "
                        "dimensions, and ranking for every covered sector. Sourced "
                        "from Sectorbench (Destatis, Eurostat, Bundesbank). Use "
                        "this to give the user industry context alongside a "
                        "company-level Boniscore."
                    ),
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "fetch_run_id": {"type": "integer"},
                                            "fetched_at": {"type": "string", "format": "date-time"},
                                            "weight_profile": {"type": "string"},
                                            "scores": {
                                                "type": "array",
                                                "items": {"$ref": "#/components/schemas/BranchScore"},
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/branches/ranking": {
                "get": {
                    "operationId": "getBranchRanking",
                    "summary": "Cross-sector ranking sorted by rank.",
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/v1/branches/{branch_key}": {
                "get": {
                    "operationId": "getBranch",
                    "summary": "Current scores for a single branch.",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "branch_key",
                            "required": True,
                            "schema": {"$ref": "#/components/schemas/BranchKey"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/BranchScore"}
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/branches/{branch_key}/history": {
                "get": {
                    "operationId": "getBranchHistory",
                    "summary": "12-month history of composite + dimension scores.",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "branch_key",
                            "required": True,
                            "schema": {"$ref": "#/components/schemas/BranchKey"},
                        },
                        {
                            "in": "query",
                            "name": "months",
                            "required": False,
                            "schema": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 24,
                                "default": 12,
                            },
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/v1/branches/{branch_key}/news": {
                "get": {
                    "operationId": "getBranchNews",
                    "summary": "Latest monthly sector news report (AI-summarised).",
                    "description": (
                        "Returns the most recent monthly briefing for the sector "
                        "with executive overview, key developments, risk watchlist, "
                        "and cited sources."
                    ),
                    "parameters": [
                        {
                            "in": "path",
                            "name": "branch_key",
                            "required": True,
                            "schema": {"$ref": "#/components/schemas/BranchKey"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/NewsReport"}
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/branches/{branch_key}/insolvency/history": {
                "get": {
                    "operationId": "getBranchInsolvencyHistory",
                    "summary": "Monthly insolvency case counts per sector (Destatis 52411-0019).",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "branch_key",
                            "required": True,
                            "schema": {"$ref": "#/components/schemas/BranchKey"},
                        },
                        {
                            "in": "query",
                            "name": "months",
                            "required": False,
                            "schema": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 36,
                                "default": 12,
                            },
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/v1/branches/{branch_key}/indicators/{indicator_key}/history": {
                "get": {
                    "operationId": "getBranchIndicatorHistory",
                    "summary": "Time series for one economic indicator within a branch.",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "branch_key",
                            "required": True,
                            "schema": {"$ref": "#/components/schemas/BranchKey"},
                        },
                        {
                            "in": "path",
                            "name": "indicator_key",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "e.g. financial.insolvency_cases. See listIndicators.",
                        },
                        {
                            "in": "query",
                            "name": "months",
                            "required": False,
                            "schema": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 24,
                                "default": 12,
                            },
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/v1/indicators": {
                "get": {
                    "operationId": "listIndicators",
                    "summary": "Catalog of available sector indicators with metadata.",
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/v1/sectorbench/meta": {
                "get": {
                    "operationId": "getSectorbenchMeta",
                    "summary": "Sectorbench API + data freshness metadata.",
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }


async def openapi_json(request: Request) -> JSONResponse:
    return JSONResponse(_openapi_spec())


def routes() -> list[Route]:
    return [
        Route("/api/openapi.json", openapi_json, methods=["GET"]),
        Route("/api/v1/search", search_companies, methods=["GET"]),
        Route(
            "/api/v1/search/advanced", search_companies_advanced, methods=["GET"]
        ),
        Route("/api/v1/reports", list_reports, methods=["GET"]),
        Route("/api/v1/reports", create_report, methods=["POST"]),
        Route("/api/v1/financial_data", get_financial_data, methods=["GET"]),
        Route(
            "/api/v1/financial_data/analysis",
            get_financial_analysis,
            methods=["GET"],
        ),
        Route("/api/v1/reports/{report_id}", get_report, methods=["GET"]),
        Route("/api/v1/jobs/{job_id}/status", get_job_status, methods=["GET"]),
        Route(
            "/api/v1/reports/{report_id}/financial_data",
            get_report_financial_data,
            methods=["GET"],
        ),
        Route(
            "/api/v1/reports/{report_id}/financial_data/analysis",
            get_report_financial_analysis,
            methods=["GET"],
        ),
        Route(
            "/api/v1/company/{report_id}/details",
            get_company_details,
            methods=["GET"],
        ),
        Route(
            "/api/v1/company/{report_id}/shareholders",
            get_company_shareholders,
            methods=["GET"],
        ),
        Route(
            "/api/v1/company/{report_id}/holdings",
            get_company_holdings,
            methods=["GET"],
        ),
        # Sectorbench proxy. Static segments (`branches`, `branches/ranking`,
        # `indicators`, `sectorbench/meta`) come BEFORE the {branch_key}
        # variants so Starlette resolves them first.
        Route("/api/v1/branches", list_branch_scores, methods=["GET"]),
        Route("/api/v1/branches/ranking", get_branch_ranking, methods=["GET"]),
        Route("/api/v1/indicators", list_indicators, methods=["GET"]),
        Route("/api/v1/sectorbench/meta", get_sectorbench_meta, methods=["GET"]),
        Route(
            "/api/v1/branches/{branch_key}/history",
            get_branch_history,
            methods=["GET"],
        ),
        Route(
            "/api/v1/branches/{branch_key}/news",
            get_branch_news,
            methods=["GET"],
        ),
        Route(
            "/api/v1/branches/{branch_key}/insolvency/history",
            get_branch_insolvency_history,
            methods=["GET"],
        ),
        Route(
            "/api/v1/branches/{branch_key}/indicators/{indicator_key}/history",
            get_branch_indicator_history,
            methods=["GET"],
        ),
        Route(
            "/api/v1/branches/{branch_key}",
            get_branch,
            methods=["GET"],
        ),
    ]
