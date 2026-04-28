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

def _login_page(redirect_to: str, error: str | None = None) -> str:
    err = (
        f'<p style="color:#d52b2a;margin:0 0 12px">{error}</p>' if error else ""
    )
    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>Boniforce MCP — Sign in</title>
<style>body{{font-family:system-ui,sans-serif;max-width:380px;margin:80px auto;padding:24px}}
input{{width:100%;padding:10px;margin:6px 0 14px;border:1px solid #ccc;border-radius:6px;font-size:14px;box-sizing:border-box}}
button{{width:100%;padding:10px;background:#009485;color:#fff;border:0;border-radius:6px;font-size:15px;cursor:pointer}}
h1{{margin:0 0 18px;font-size:20px}}</style></head><body>
<h1>Sign in to Boniforce MCP</h1>{err}
<form method=POST action="/oauth/login">
<input type=hidden name=continue value="{redirect_to}">
<label>Email<input name=email type=email required autofocus></label>
<label>Password<input name=password type=password required></label>
<button>Sign in</button></form></body></html>"""


def _setup_page(error: str | None = None, redirect_to: str = "/") -> str:
    err = (
        f'<p style="color:#d52b2a;margin:0 0 12px">{error}</p>' if error else ""
    )
    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>Boniforce MCP — Link API key</title>
<style>body{{font-family:system-ui,sans-serif;max-width:480px;margin:80px auto;padding:24px}}
input,textarea{{width:100%;padding:10px;margin:6px 0 14px;border:1px solid #ccc;border-radius:6px;font-size:14px;box-sizing:border-box;font-family:inherit}}
button{{width:100%;padding:10px;background:#009485;color:#fff;border:0;border-radius:6px;font-size:15px;cursor:pointer}}
h1{{margin:0 0 8px;font-size:20px}} p.hint{{color:#666;font-size:13px;margin:0 0 18px}}</style></head><body>
<h1>Link your Boniforce API key</h1>
<p class=hint>Paste the API token from your Boniforce account. It will be encrypted at rest and used only for your MCP requests.</p>
{err}<form method=POST action="/setup">
<input type=hidden name=continue value="{redirect_to}">
<label>Boniforce API token<textarea name=token rows=3 required></textarea></label>
<label>Label (optional)<input name=label placeholder="e.g. work account"></label>
<button>Save and continue</button></form></body></html>"""


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
    required = ("response_type", "client_id", "redirect_uri", "code_challenge")
    if any(p not in params for p in required):
        return JSONResponse(
            {"error": "invalid_request", "error_description": "missing required parameter"},
            status_code=400,
        )
    if params["response_type"] != "code":
        return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
    if params.get("code_challenge_method", "plain") != "S256":
        return JSONResponse({"error": "invalid_request", "error_description": "S256 required"}, status_code=400)

    client = await storage.get_client(params["client_id"])
    if not client:
        return JSONResponse({"error": "unauthorized_client"}, status_code=400)
    if params["redirect_uri"] not in client.redirect_uris:
        return JSONResponse({"error": "invalid_request", "error_description": "redirect_uri mismatch"}, status_code=400)

    user_id = _read_session(request)
    if not user_id:
        # Show login; preserve full /authorize URL as continue.
        cont = f"/oauth/authorize?{urlencode(params)}"
        return HTMLResponse(_login_page(cont))

    # Logged in but no Boniforce token yet → show /setup, return here after.
    bf = await storage.get_bf_token(user_id)
    if not bf:
        cont = f"/oauth/authorize?{urlencode(params)}"
        return HTMLResponse(_setup_page(redirect_to=cont))

    # All good: mint code, redirect.
    code = secrets.token_urlsafe(32)
    await storage.save_auth_code(
        code=code,
        client_id=params["client_id"],
        user_id=user_id,
        code_challenge=params["code_challenge"],
        code_challenge_method=params.get("code_challenge_method", "S256"),
        redirect_uri=params["redirect_uri"],
        scope=params.get("scope", "mcp"),
    )
    sep = "&" if "?" in params["redirect_uri"] else "?"
    target = f"{params['redirect_uri']}{sep}code={code}"
    if "state" in params:
        target += f"&state={params['state']}"
    return RedirectResponse(target, status_code=302)


async def login(request: Request) -> Response:
    form = await request.form()
    email = (form.get("email") or "").strip()
    password = form.get("password") or ""
    cont = form.get("continue") or "/"
    user = await storage.verify_user(email, password)
    if not user:
        return HTMLResponse(_login_page(cont, error="Invalid email or password"), status_code=401)
    resp = RedirectResponse(cont, status_code=302)
    _set_session(resp, user.id)
    return resp


async def setup_get(request: Request) -> Response:
    user_id = _read_session(request)
    if not user_id:
        return RedirectResponse("/oauth/login_required", status_code=302)
    cont = request.query_params.get("continue", "/")
    return HTMLResponse(_setup_page(redirect_to=cont))


async def setup_post(request: Request) -> Response:
    user_id = _read_session(request)
    if not user_id:
        return JSONResponse({"error": "not_authenticated"}, status_code=401)
    form = await request.form()
    token = (form.get("token") or "").strip()
    label = form.get("label") or None
    cont = form.get("continue") or "/"
    if not token:
        return HTMLResponse(_setup_page(error="Token is required", redirect_to=cont), status_code=400)
    await storage.set_bf_token(user_id, token, label)
    return RedirectResponse(cont, status_code=302)


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
    if not code or not redirect_uri or not code_verifier:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    record = await storage.consume_auth_code(code, client_id, redirect_uri)
    if not record:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if not _verify_pkce(code_verifier, record["code_challenge"], record["code_challenge_method"]):
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
        Route("/setup", setup_get, methods=["GET"]),
        Route("/setup", setup_post, methods=["POST"]),
    ]
