"""
REST mirror of the MCP tools for ChatGPT Custom GPT "Actions".

The Custom GPT Actions feature speaks OpenAPI 3.1 + REST, not MCP. This
module exposes the same 7 Boniforce operations as JSON REST endpoints under
``/api/v1/*`` and serves an OpenAPI spec at ``/api/openapi.json``.

Auth: same JWT bearer that protects /mcp. Each request's user is read from
the JWT subject claim, the user's stored Boniforce API key is fetched, and
the call is proxied to api.boniforce.de.
"""
from __future__ import annotations

from typing import Any

import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, storage
from .config import get_settings


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


# ---------------- job-status helpers (shared with MCP server.py) ----------------

TERMINAL_JOB_STATUSES = frozenset({"completed", "finished", "failed", "error"})


def annotate_job_outcome(
    payload: Any, job_id: str | None, status_value: str | None
) -> Any:
    """Mutate ``payload`` in place to add ``done`` and (when not done)
    ``next_action`` fields so the model knows whether to keep polling.

    The model (ChatGPT Action / Claude tool call) only sees one HTTP response
    per call. ChatGPT's per-call timeout is ~45s and our long-poll caps at 40s,
    so reports >40s need 2-3 sequential calls. ``next_action`` makes that
    explicit instead of relying on instruction-following alone.
    """
    if not isinstance(payload, dict):
        return payload
    status_str = (status_value or "").lower().strip()
    done = status_str in TERMINAL_JOB_STATUSES
    payload["done"] = done
    if not done:
        jid = job_id or payload.get("job_id") or "<job_id>"
        payload["next_action"] = (
            f"Job not finished yet (status={status_str or 'unknown'}). "
            f"Call get_job_status again with job_id={jid} and wait_seconds=40. "
            "Keep calling until done=true (typically 1-3 calls, max ~120s total)."
        )
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
    required = ("company_name", "register_type", "register_number", "register_court")
    missing = [k for k in required if not body.get(k)]
    if missing:
        return _err(400, f"Missing fields: {', '.join(missing)}")
    try:
        data = await _client_holder().create_report(
            token,
            company_name=body["company_name"],
            register_type=body["register_type"],
            register_number=body["register_number"],
            register_court=body["register_court"],
            session_id=body.get("session_id"),
        )
    except Exception as exc:
        return _err(502, f"Boniforce upstream: {exc}")
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
            if (status_value or "").lower() in ("completed", "finished") and data.get("report_id"):
                try:
                    data["report"] = await client.get_report(token, data["report_id"])
                except Exception:
                    pass
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


# ---------------- OpenAPI spec ----------------

def _openapi_spec() -> dict[str, Any]:
    iss = get_settings().issuer
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Boniforce REST API (for ChatGPT Custom GPTs)",
            "version": "1.0.0",
            "description": (
                "Per-user proxy for the Boniforce credit-data API. Authenticate "
                "via OAuth 2.1 with the Boniforce MCP authorization server "
                f"({iss}) — each end user pastes their own Boniforce API key "
                "during the OAuth flow. After authorization the same JWT can "
                "be used here as Bearer token."
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
                        "register_type": {"type": "string"},
                        "register_number": {"type": "string"},
                        "register_court": {"type": "string"},
                    },
                },
                "Report": {
                    "type": "object",
                    "properties": {
                        "report_id": {"type": "string"},
                        "version": {"type": "number"},
                        "score": {"type": "number", "description": "Boniscore 0–100; higher = lower risk."},
                        "score_details": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "color_code": {"type": "integer"},
                            },
                        },
                        "credit_limit": {"type": "number"},
                        "credit_assessment_result": {
                            "type": "string",
                            "description": "APPROVE / REVIEW / DECLINE",
                        },
                    },
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
                                "?wait=40 to keep waiting."
                            ),
                        },
                        "next_action": {
                            "type": "string",
                            "description": (
                                "Present only when done=false. Plain-English "
                                "instruction for the model: keep polling until "
                                "done=true (typically 1-3 calls total, max ~120s)."
                            ),
                        },
                        "error_message": {"type": "string", "nullable": True},
                    },
                },
                "Error": {
                    "type": "object",
                    "properties": {"error": {"type": "string"}},
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
            "/api/v1/reports": {
                "get": {
                    "operationId": "listReports",
                    "summary": "List previously generated reports for the authenticated account.",
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "operationId": "createReport",
                    "summary": "Start Boniscore report. Pass ?wait=40 to long-poll up to 40s.",
                    "description": (
                        "Kicks off report generation. With ?wait=40 the server long-polls up "
                        "to 40s and inlines the finished report. Reports take 30-120s — if "
                        "done=false, immediately call getJobStatus with ?wait=40 and repeat "
                        "(max 3 calls) until done=true. Never reply 'still processing' before "
                        "3 calls."
                    ),
                    "parameters": [
                        {
                            "in": "query",
                            "name": "wait",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 0, "maximum": 40},
                            "description": "Seconds to wait server-side for the report to finish (0-40).",
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": [
                                        "company_name",
                                        "register_type",
                                        "register_number",
                                        "register_court",
                                    ],
                                    "properties": {
                                        "company_name": {"type": "string"},
                                        "register_type": {"type": "string"},
                                        "register_number": {"type": "string"},
                                        "register_court": {"type": "string"},
                                        "session_id": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Job accepted (and possibly inlined report when wait used)."}},
                },
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
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/v1/jobs/{job_id}/status": {
                "get": {
                    "operationId": "getJobStatus",
                    "summary": "Poll report job. Pass ?wait=40 to long-poll up to 40s.",
                    "description": (
                        "Returns latest job status (queued -> running -> completed/failed). "
                        "With ?wait=40 the server long-polls up to 40s. Response has done=true "
                        "(terminal) or done=false + next_action (still running — call again "
                        "with ?wait=40). Loop until done=true; max 3 calls before treating "
                        "the job as stuck."
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
                            "schema": {"type": "integer", "minimum": 0, "maximum": 40},
                            "description": "Seconds to wait server-side for status change (0-40).",
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/v1/reports/{report_id}/financial_data": {
                "get": {
                    "operationId": "getReportFinancialData",
                    "summary": "Balance-sheet history attached to a finished report.",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "report_id",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
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
        Route("/api/v1/reports", list_reports, methods=["GET"]),
        Route("/api/v1/reports", create_report, methods=["POST"]),
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
    ]
