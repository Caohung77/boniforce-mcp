"""End-to-end test of the OAuth 2.1 issuer using Starlette TestClient."""
import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


@pytest.fixture
def app():
    """Build a Starlette app with only auth routes (no FastMCP) for OAuth tests."""
    from contextlib import asynccontextmanager

    from boniforce_mcp import auth as auth_mod
    from boniforce_mcp import storage

    @asynccontextmanager
    async def _lifespan(_app):
        await storage.init_db()
        yield

    return Starlette(routes=auth_mod.routes(), lifespan=_lifespan)


VALID_TOKEN = "bf-secret-token"


@pytest.fixture(autouse=True)
def _mock_bf_validation(respx_mock):
    """Stub api.boniforce.de/v1/reports — 200 for VALID_TOKEN, 401 otherwise."""
    import httpx
    import respx as _respx

    def _handler(request):
        auth = request.headers.get("authorization", "")
        if auth == f"Bearer {VALID_TOKEN}":
            return httpx.Response(200, json=[])
        return httpx.Response(401, json={"detail": "invalid"})

    respx_mock.get("https://api.boniforce.de/v1/reports").mock(side_effect=_handler)
    yield


def _register_client(client: TestClient) -> str:
    r = client.post(
        "/oauth/register",
        json={"client_name": "test", "redirect_uris": ["https://example.com/cb"]},
    )
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def test_metadata_endpoint(app):
    with TestClient(app) as c:
        r = c.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    assert body["issuer"] == "http://testserver"
    assert body["authorization_endpoint"].endswith("/oauth/authorize")
    assert "S256" in body["code_challenge_methods_supported"]


def test_protected_resource_metadata(app):
    with TestClient(app) as c:
        r = c.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    assert r.json()["resource"] == "http://testserver/mcp"
    assert r.json()["authorization_servers"] == ["http://testserver"]


def test_jwks_returns_one_rsa_key(app):
    with TestClient(app) as c:
        r = c.get("/jwks.json")
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["kty"] == "RSA"
    assert keys[0]["alg"] == "RS256"


def test_dcr_returns_client_id(app):
    with TestClient(app) as c:
        cid = _register_client(c)
    assert cid


def test_invalid_bf_token_rejected(app):
    with TestClient(app) as c:
        r = c.post(
            "/oauth/login",
            data={"token": "bogus", "continue": "/"},
            follow_redirects=False,
        )
    assert r.status_code == 401
    assert "rejected" in r.text.lower()


@pytest.mark.asyncio
async def test_full_pkce_flow(app):
    from boniforce_mcp.auth import public_key_pem

    verifier, challenge = _pkce_pair()
    redirect_uri = "https://example.com/cb"

    with TestClient(app) as c:
        cid = _register_client(c)

        params = {
            "response_type": "code",
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp",
            "state": "xyz",
        }

        # Anonymous → API-key form
        r = c.get(f"/oauth/authorize?{urlencode(params)}", follow_redirects=False)
        assert r.status_code == 200
        assert "Boniforce API key" in r.text

        # Submit token → 302 back to /authorize with session cookie set
        r2 = c.post(
            "/oauth/login",
            data={"token": VALID_TOKEN, "continue": f"/oauth/authorize?{urlencode(params)}"},
            follow_redirects=False,
        )
        assert r2.status_code == 302, r2.text
        cont_url = r2.headers["location"]

        r3 = c.get(cont_url, follow_redirects=False)
        assert r3.status_code == 302
        loc = r3.headers["location"]
        assert loc.startswith(redirect_uri)
        code = parse_qs(urlparse(loc).query)["code"][0]
        assert parse_qs(urlparse(loc).query)["state"][0] == "xyz"

        # Exchange code for tokens
        r4 = c.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": cid,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        assert r4.status_code == 200, r4.text
        body = r4.json()
        assert body["token_type"] == "Bearer"
        assert "access_token" in body
        assert "refresh_token" in body

        decoded = jwt.decode(
            body["access_token"],
            public_key_pem(),
            algorithms=["RS256"],
            audience="http://testserver/mcp",
            issuer="http://testserver",
        )
        assert decoded["client_id"] == cid
        assert decoded["sub"]  # synthetic user_id

        # Refresh
        r5 = c.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": cid,
                "refresh_token": body["refresh_token"],
            },
        )
        assert r5.status_code == 200, r5.text
        assert "access_token" in r5.json()


@pytest.mark.asyncio
async def test_pkce_failure_rejected(app):
    _, challenge = _pkce_pair()
    bad_verifier = "wrong-verifier"
    redirect_uri = "https://example.com/cb"

    with TestClient(app) as c:
        cid = _register_client(c)
        params = {
            "response_type": "code",
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp",
        }
        c.post(
            "/oauth/login",
            data={"token": VALID_TOKEN, "continue": f"/oauth/authorize?{urlencode(params)}"},
            follow_redirects=False,
        )
        r = c.get(f"/oauth/authorize?{urlencode(params)}", follow_redirects=False)
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]

        bad = c.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": cid,
                "redirect_uri": redirect_uri,
                "code_verifier": bad_verifier,
            },
        )
        assert bad.status_code == 400
        assert bad.json()["error"] == "invalid_grant"
