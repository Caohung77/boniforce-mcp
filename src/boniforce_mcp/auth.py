"""
OAuth 2.1 authorization server (PKCE + Dynamic Client Registration) +
Boniforce-token onboarding form, all served as Starlette routes.

Issued JWTs are validated by FastMCP's JWTVerifier via the JWKS endpoint.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import storage
from .config import get_settings


# ---------------- key handling ----------------

def _load_private_key():
    pem = get_settings().oauth_signing_key
    if not pem:
        raise RuntimeError(
            "BF_OAUTH_SIGNING_KEY is not set; run `boniforce-mcp gensigning`."
        )
    # Allow literal "\n" sequences as newline substitutes for env-var transport.
    if "\\n" in pem and "\n" not in pem:
        pem = pem.replace("\\n", "\n")
    return serialization.load_pem_private_key(pem.encode(), password=None)


def public_key_pem() -> str:
    pk = _load_private_key().public_key()
    return pk.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def _public_jwk() -> dict[str, Any]:
    pk = _load_private_key().public_key()
    numbers = pk.public_numbers()

    def b64u(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "boniforce-mcp-1",
        "n": b64u(numbers.n),
        "e": b64u(numbers.e),
    }


def generate_signing_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


# ---------------- PKCE helpers ----------------

def _redirect_uri_allowed(submitted: str, registered: list[str]) -> bool:
    """Exact match, or fnmatch against patterns containing '*'.
    Used so a single ChatGPT-Custom-GPT OAuth client can accept callbacks
    under any g-XXXXX id (the URL changes with every draft save)."""
    import fnmatch

    for pat in registered:
        if "*" in pat:
            if fnmatch.fnmatchcase(submitted, pat):
                return True
        elif submitted == pat:
            return True
    return False


def _verify_pkce(verifier: str, challenge: str, method: str) -> bool:
    if method == "S256":
        digest = hashlib.sha256(verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return secrets.compare_digest(expected, challenge)
    return False  # OAuth 2.1: plain forbidden


# ---------------- token helpers ----------------

def _issue_access_token(
    user_id: str, client_id: str, scope: str, resource: str | None = None
) -> tuple[str, int]:
    settings = get_settings()
    now = int(time.time())
    ttl = 3600
    audience = resource or settings.audience
    payload = {
        "iss": settings.issuer,
        "sub": user_id,
        "aud": audience,
        "iat": now,
        "exp": now + ttl,
        "jti": str(uuid.uuid4()),
        "scope": scope,
        "client_id": client_id,
    }
    token = jwt.encode(
        payload,
        _load_private_key(),
        algorithm="RS256",
        headers={"kid": "boniforce-mcp-1"},
    )
    return token, ttl


def _issue_refresh_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, digest


# ---------------- session cookie (post-login marker) ----------------

SESSION_COOKIE = "bf_session"


def _set_session(resp: Response, user_id: str) -> None:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "iss": settings.issuer,
        "sub": user_id,
        "iat": now,
        "exp": now + 1800,
        "purpose": "session",
    }
    cookie = jwt.encode(payload, _load_private_key(), algorithm="RS256")
    resp.set_cookie(
        SESSION_COOKIE,
        cookie,
        max_age=1800,
        httponly=True,
        secure=settings.issuer_url.startswith("https"),
        samesite="lax",
        path="/",
    )


def _read_session(request: Request) -> str | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        payload = jwt.decode(
            raw,
            _load_private_key().public_key(),
            algorithms=["RS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != "session":
        return None
    return payload.get("sub")


# ---------------- HTML helpers ----------------

def _apikey_page(redirect_to: str, error: str | None = None) -> str:
    err = (
        f'<p style="color:#d52b2a;margin:0 0 12px">{error}</p>' if error else ""
    )
    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>Boniforce MCP — Connect</title>
<style>body{{font-family:system-ui,sans-serif;max-width:460px;margin:80px auto;padding:24px}}
textarea{{width:100%;padding:10px;margin:6px 0 14px;border:1px solid #ccc;border-radius:6px;font-size:14px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;box-sizing:border-box}}
button{{width:100%;padding:10px;background:#009485;color:#fff;border:0;border-radius:6px;font-size:15px;cursor:pointer}}
h1{{margin:0 0 8px;font-size:20px}} p.hint{{color:#666;font-size:13px;margin:0 0 18px;line-height:1.5}}
a{{color:#009485}}</style></head><body>
<h1>Connect Boniforce to Claude / ChatGPT</h1>
<p class=hint>Paste your <strong>Boniforce API key</strong> below. It is the only credential needed — we validate it against your Boniforce account and store it encrypted. You can revoke it any time from the Boniforce dashboard.</p>
{err}<form method=POST action="/oauth/login">
<input type=hidden name=continue value="{redirect_to}">
<label>Boniforce API key<textarea name=token rows=3 required autofocus placeholder="sk_live-..."></textarea></label>
<button>Connect</button></form></body></html>"""


# ---------------- Route handlers ----------------

async def metadata_authorization_server(request: Request) -> JSONResponse:
    iss = get_settings().issuer
    return JSONResponse(
        {
            "issuer": iss,
            "authorization_endpoint": f"{iss}/oauth/authorize",
            "token_endpoint": f"{iss}/oauth/token",
            "registration_endpoint": f"{iss}/oauth/register",
            "jwks_uri": f"{iss}/jwks.json",
            "scopes_supported": ["mcp"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": [
                "none",
                "client_secret_post",
                "client_secret_basic",
            ],
        }
    )


async def metadata_protected_resource(request: Request) -> JSONResponse:
    iss = get_settings().issuer
    return JSONResponse(
        {
            "resource": f"{iss}/mcp",
            "authorization_servers": [iss],
            "scopes_supported": ["mcp"],
            "bearer_methods_supported": ["header"],
            "resource_documentation": f"{iss}/",
        }
    )


async def jwks(request: Request) -> JSONResponse:
    return JSONResponse({"keys": [_public_jwk()]})


async def register_client(request: Request) -> JSONResponse:
    """Dynamic Client Registration (RFC 7591)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse(
            {"error": "invalid_redirect_uri", "error_description": "redirect_uris required"},
            status_code=400,
        )
    client_name = body.get("client_name") or "Unnamed MCP Client"
    auth_method = body.get("token_endpoint_auth_method") or "none"
    if auth_method not in ("none", "client_secret_post", "client_secret_basic"):
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)

    client_id, secret = await storage.register_client(
        client_name, redirect_uris, auth_method
    )
    resp: dict[str, Any] = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": auth_method,
    }
    if secret is not None:
        resp["client_secret"] = secret
        resp["client_secret_expires_at"] = 0
    return JSONResponse(resp, status_code=201)


# ---- /oauth/authorize: GET shows login (or BF-link) form, then redirects with ?code= ----

async def authorize(request: Request) -> Response:
    params = request.query_params
    required = ("response_type", "client_id", "redirect_uri")
    if any(p not in params for p in required):
        return JSONResponse(
            {"error": "invalid_request", "error_description": "missing required parameter"},
            status_code=400,
        )
    if params["response_type"] != "code":
        return JSONResponse({"error": "unsupported_response_type"}, status_code=400)

    client_id = params["client_id"].strip()
    client = await storage.get_client(client_id)
    if not client:
        return JSONResponse({"error": "unauthorized_client"}, status_code=400)
    if not _redirect_uri_allowed(params["redirect_uri"], client.redirect_uris):
        return JSONResponse({"error": "invalid_request", "error_description": "redirect_uri mismatch"}, status_code=400)

    # PKCE: required for public clients (no secret), optional for confidential.
    has_challenge = "code_challenge" in params
    if not client.has_secret and not has_challenge:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "code_challenge required for public clients"},
            status_code=400,
        )
    if has_challenge and params.get("code_challenge_method", "plain") != "S256":
        return JSONResponse({"error": "invalid_request", "error_description": "S256 required"}, status_code=400)

    user_id = _read_session(request)
    if not user_id:
        # Show API-key form; preserve full /authorize URL as continue.
        cont = f"/oauth/authorize?{urlencode(params)}"
        return HTMLResponse(_apikey_page(cont))

    # Token-based session implies BF token is already linked. All good: mint code.
    code = secrets.token_urlsafe(32)
    await storage.save_auth_code(
        code=code,
        client_id=client_id,
        user_id=user_id,
        code_challenge=params.get("code_challenge", ""),
        code_challenge_method=params.get("code_challenge_method", "none"),
        redirect_uri=params["redirect_uri"],
        scope=params.get("scope", "mcp"),
    )
    sep = "&" if "?" in params["redirect_uri"] else "?"
    target = f"{params['redirect_uri']}{sep}code={code}"
    if "state" in params:
        target += f"&state={params['state']}"
    return RedirectResponse(target, status_code=302)


async def login(request: Request) -> Response:
    """Validate a Boniforce API token by hitting api.boniforce.de.
    Valid token => upsert a token-keyed user, set session, redirect on."""
    import hashlib

    import httpx

    form = await request.form()
    token = (form.get("token") or "").strip()
    cont = form.get("continue") or "/"
    if not token:
        return HTMLResponse(
            _apikey_page(cont, error="API key is required"), status_code=400
        )

    api_base = get_settings().api_base.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_base}/v1/reports",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError:
        return HTMLResponse(
            _apikey_page(cont, error="Could not reach Boniforce. Try again."),
            status_code=502,
        )
    if resp.status_code in (401, 403):
        return HTMLResponse(
            _apikey_page(cont, error="Boniforce rejected this API key. Check it and retry."),
            status_code=401,
        )
    if resp.status_code >= 500:
        return HTMLResponse(
            _apikey_page(cont, error=f"Boniforce returned {resp.status_code}. Try again later."),
            status_code=502,
        )
    if resp.status_code != 200:
        return HTMLResponse(
            _apikey_page(cont, error=f"Unexpected validation response ({resp.status_code})."),
            status_code=400,
        )

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = await storage.upsert_token_user(token_hash, token)
    redirect = RedirectResponse(cont, status_code=302)
    _set_session(redirect, user.id)
    return redirect


# ---- /oauth/token ----

async def token(request: Request) -> JSONResponse:
    form = await request.form()
    grant_type = form.get("grant_type")

    if grant_type == "authorization_code":
        return await _grant_authorization_code(form, request)
    if grant_type == "refresh_token":
        return await _grant_refresh_token(form, request)
    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


async def _authenticate_client(form, request: Request) -> tuple[str | None, JSONResponse | None]:
    client_id = form.get("client_id")
    client_secret = form.get("client_secret")
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode()
            client_id, client_secret = decoded.split(":", 1)
        except Exception:
            return None, JSONResponse({"error": "invalid_client"}, status_code=401)
    if not client_id:
        return None, JSONResponse({"error": "invalid_client"}, status_code=401)
    client = await storage.get_client(client_id)
    if not client:
        return None, JSONResponse({"error": "invalid_client"}, status_code=401)
    if client.token_endpoint_auth_method != "none":
        if not client_secret or not await storage.verify_client_secret(client_id, client_secret):
            return None, JSONResponse({"error": "invalid_client"}, status_code=401)
    return client_id, None


async def _grant_authorization_code(form, request: Request) -> JSONResponse:
    client_id, err = await _authenticate_client(form, request)
    if err:
        return err
    code = form.get("code")
    redirect_uri = form.get("redirect_uri")
    code_verifier = form.get("code_verifier")
    if not code or not redirect_uri:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    record = await storage.consume_auth_code(code, client_id, redirect_uri)
    if not record:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    # PKCE check only when challenge was provided at authorize time.
    if record["code_challenge"]:
        if not code_verifier or not _verify_pkce(
            code_verifier, record["code_challenge"], record["code_challenge_method"]
        ):
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE failed"}, status_code=400)

    access, ttl = _issue_access_token(record["user_id"], client_id, record["scope"])
    refresh_raw, refresh_hash = _issue_refresh_token()
    await storage.save_refresh_token(refresh_hash, record["user_id"], client_id, record["scope"])
    return JSONResponse(
        {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ttl,
            "refresh_token": refresh_raw,
            "scope": record["scope"],
        }
    )


async def _grant_refresh_token(form, request: Request) -> JSONResponse:
    client_id, err = await _authenticate_client(form, request)
    if err:
        return err
    refresh_raw = form.get("refresh_token")
    if not refresh_raw:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    refresh_hash = hashlib.sha256(refresh_raw.encode()).hexdigest()
    record = await storage.consume_refresh_token(refresh_hash)
    if not record or record["client_id"] != client_id:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    access, ttl = _issue_access_token(record["user_id"], client_id, record["scope"])
    new_raw, new_hash = _issue_refresh_token()
    await storage.save_refresh_token(new_hash, record["user_id"], client_id, record["scope"])
    return JSONResponse(
        {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ttl,
            "refresh_token": new_raw,
            "scope": record["scope"],
        }
    )


def routes() -> list[Route]:
    return [
        Route("/.well-known/oauth-authorization-server", metadata_authorization_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", metadata_protected_resource, methods=["GET"]),
        Route("/jwks.json", jwks, methods=["GET"]),
        Route("/oauth/register", register_client, methods=["POST"]),
        Route("/oauth/authorize", authorize, methods=["GET"]),
        Route("/oauth/login", login, methods=["POST"]),
        Route("/oauth/token", token, methods=["POST"]),
    ]
