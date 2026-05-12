"""Branding assets (B3) for the Anthropic Connectors Directory submission
must be served at predictable HTTPS URLs on the MCP origin so the
submission form can link them."""
from __future__ import annotations

import httpx
import pytest


@pytest.fixture
async def client():
    from boniforce_mcp.server import build_app

    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c:
        yield c


@pytest.mark.parametrize(
    "path,mime_prefix",
    [
        ("/favicon/favicon.svg", "image/svg"),
        ("/favicon/favicon.ico", "image/"),
        ("/favicon/favicon-96x96.png", "image/png"),
        ("/favicon/apple-touch-icon.png", "image/png"),
        ("/favicon/web-app-manifest-512x512.png", "image/png"),
    ],
)
async def test_favicon_assets_served(client, path, mime_prefix):
    r = await client.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code}"
    assert r.headers["content-type"].startswith(mime_prefix), r.headers["content-type"]
    assert len(r.content) > 0


async def test_favicon_directory_listing_disabled(client):
    # StaticFiles by default does not serve directory indexes.
    r = await client.get("/favicon/")
    assert r.status_code in (403, 404)
