"""Tests for the Origin + Host validation middleware required by the
Anthropic Connectors Directory submission (B1).

The Origin allowlist applies to /mcp only. Imports are deferred into
fixtures so the conftest autouse env fixture has already populated the
required signing key / encryption key by the time server.build_app
loads (server.py executes `app = build_app()` at module top).
"""
from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def app():
    from boniforce_mcp.server import build_app

    return build_app()


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c:
        yield c


async def test_mcp_rejects_disallowed_origin(client):
    r = await client.post(
        "/mcp",
        headers={"Origin": "https://evil.tld"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["error"] == "origin_not_allowed"


async def test_mcp_accepts_claude_origin(client):
    # Allowed origin passes the middleware; still hits auth so we expect
    # something other than 403 (most likely 401/406 from FastMCP).
    r = await client.post(
        "/mcp",
        headers={"Origin": "https://claude.ai"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert r.status_code != 403


async def test_mcp_accepts_chatgpt_origin(client):
    r = await client.post(
        "/mcp",
        headers={"Origin": "https://chatgpt.com"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert r.status_code != 403


async def test_mcp_accepts_missing_origin(client):
    # Server-to-server callers (no browser context) typically omit Origin.
    # They must still be allowed through — OAuth gates the actual request.
    r = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert r.status_code != 403


async def test_well_known_endpoints_not_gated_by_origin(client):
    # OAuth discovery must remain reachable from any origin.
    r = await client.get(
        "/.well-known/oauth-authorization-server",
        headers={"Origin": "https://evil.tld"},
    )
    assert r.status_code == 200


async def test_host_header_rejected_when_not_allowed(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post(
            "/mcp",
            headers={"Host": "foo.bar"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    # Starlette's TrustedHostMiddleware returns 400 with text body.
    assert r.status_code == 400
