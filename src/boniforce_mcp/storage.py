import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
import bcrypt as _bcrypt


def _hash_pw(pw: str) -> str:
    return _bcrypt.hashpw(pw.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def _verify_pw(pw: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False

from .config import get_settings
from .crypto import decrypt, encrypt

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id            TEXT PRIMARY KEY,
    client_secret_hash   TEXT,
    client_name          TEXT NOT NULL,
    redirect_uris        TEXT NOT NULL,  -- JSON array
    token_endpoint_auth_method TEXT NOT NULL DEFAULT 'none',
    registered_at        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code              TEXT PRIMARY KEY,
    client_id         TEXT NOT NULL,
    user_id           TEXT NOT NULL,
    code_challenge    TEXT NOT NULL,
    code_challenge_method TEXT NOT NULL,
    redirect_uri      TEXT NOT NULL,
    scope             TEXT NOT NULL,
    expires_at        INTEGER NOT NULL,
    used              INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    token_hash    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    client_id     TEXT NOT NULL,
    scope         TEXT NOT NULL,
    expires_at    INTEGER NOT NULL,
    revoked       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bf_tokens (
    user_id          TEXT PRIMARY KEY,
    encrypted_token  TEXT NOT NULL,
    label            TEXT,
    updated_at       INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
"""


@dataclass
class User:
    id: str
    email: str


@dataclass
class OAuthClient:
    client_id: str
    client_name: str
    redirect_uris: list[str]
    token_endpoint_auth_method: str
    has_secret: bool


def _now() -> int:
    return int(time.time())


def _ensure_parent(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)


async def init_db() -> None:
    db_path = get_settings().db_path
    _ensure_parent(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


def _connect():
    settings = get_settings()
    _ensure_parent(settings.db_path)
    return aiosqlite.connect(settings.db_path)


def _row(db: aiosqlite.Connection) -> None:
    db.row_factory = aiosqlite.Row


async def create_user(email: str, password: str) -> User:
    user_id = str(uuid.uuid4())
    pwd_hash = _hash_pw(password)
    async with _connect() as db:
        _row(db)
        await db.execute(
            "INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)",
            (user_id, email.lower(), pwd_hash, _now()),
        )
        await db.commit()
    return User(id=user_id, email=email.lower())


async def verify_user(email: str, password: str) -> User | None:
    async with _connect() as db:
        _row(db)
        async with db.execute(
            "SELECT id,email,password_hash FROM users WHERE email=?",
            (email.lower(),),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    if not _verify_pw(password, row["password_hash"]):
        return None
    return User(id=row["id"], email=row["email"])


async def upsert_token_user(token_hash_hex: str, token_plain: str) -> User:
    """Get-or-create a user keyed by SHA-256(BF token). The same token always
    maps to the same user_id so re-connecting from a new MCP client preserves
    state. Stores the encrypted token under that user."""
    synthetic_email = f"bf-{token_hash_hex[:16]}@auto.boniforce"
    async with _connect() as db:
        _row(db)
        async with db.execute(
            "SELECT id,email FROM users WHERE email=?", (synthetic_email,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            user = User(id=row["id"], email=row["email"])
        else:
            user_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)",
                (user_id, synthetic_email, token_hash_hex, _now()),
            )
            await db.commit()
            user = User(id=user_id, email=synthetic_email)
    await set_bf_token(user.id, token_plain, "auto")
    return user


async def list_users() -> list[User]:
    async with _connect() as db:
        _row(db)
        async with db.execute("SELECT id,email FROM users ORDER BY created_at") as cur:
            rows = await cur.fetchall()
    return [User(id=r["id"], email=r["email"]) for r in rows]


async def get_user_by_email(email: str) -> User | None:
    async with _connect() as db:
        _row(db)
        async with db.execute("SELECT id,email FROM users WHERE email=?", (email.lower(),)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return User(id=row["id"], email=row["email"])


# ---------------- OAuth clients (DCR) ----------------

async def register_client(
    client_name: str,
    redirect_uris: list[str],
    token_endpoint_auth_method: str = "none",
) -> tuple[str, str | None]:
    """Returns (client_id, client_secret_or_None). Public clients (PKCE) get no secret."""
    import json

    client_id = secrets.token_urlsafe(16)
    secret_plain: str | None = None
    secret_hash: str | None = None
    if token_endpoint_auth_method != "none":
        secret_plain = secrets.token_urlsafe(32)
        secret_hash = _hash_pw(secret_plain)
    async with _connect() as db:
        _row(db)
        await db.execute(
            "INSERT INTO oauth_clients(client_id,client_secret_hash,client_name,redirect_uris,token_endpoint_auth_method,registered_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                client_id,
                secret_hash,
                client_name,
                json.dumps(redirect_uris),
                token_endpoint_auth_method,
                _now(),
            ),
        )
        await db.commit()
    return client_id, secret_plain


async def get_client(client_id: str) -> OAuthClient | None:
    import json

    async with _connect() as db:
        _row(db)
        async with db.execute(
            "SELECT client_id,client_name,redirect_uris,token_endpoint_auth_method,client_secret_hash FROM oauth_clients WHERE client_id=?",
            (client_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return OAuthClient(
        client_id=row["client_id"],
        client_name=row["client_name"],
        redirect_uris=json.loads(row["redirect_uris"]),
        token_endpoint_auth_method=row["token_endpoint_auth_method"],
        has_secret=bool(row["client_secret_hash"]),
    )


async def verify_client_secret(client_id: str, secret: str) -> bool:
    async with _connect() as db:
        _row(db)
        async with db.execute(
            "SELECT client_secret_hash FROM oauth_clients WHERE client_id=?",
            (client_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row or not row["client_secret_hash"]:
        return False
    return _verify_pw(secret, row["client_secret_hash"])


# ---------------- Authorization codes ----------------

async def save_auth_code(
    code: str,
    client_id: str,
    user_id: str,
    code_challenge: str,
    code_challenge_method: str,
    redirect_uri: str,
    scope: str,
    ttl_seconds: int = 600,
) -> None:
    async with _connect() as db:
        _row(db)
        await db.execute(
            "INSERT INTO oauth_codes(code,client_id,user_id,code_challenge,code_challenge_method,redirect_uri,scope,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                code,
                client_id,
                user_id,
                code_challenge,
                code_challenge_method,
                redirect_uri,
                scope,
                _now() + ttl_seconds,
            ),
        )
        await db.commit()


async def consume_auth_code(code: str, client_id: str, redirect_uri: str) -> dict | None:
    async with _connect() as db:
        _row(db)
        async with db.execute(
            "SELECT * FROM oauth_codes WHERE code=? AND used=0",
            (code,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        if row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
            return None
        if row["expires_at"] < _now():
            return None
        await db.execute("UPDATE oauth_codes SET used=1 WHERE code=?", (code,))
        await db.commit()
    return {
        "user_id": row["user_id"],
        "client_id": row["client_id"],
        "code_challenge": row["code_challenge"],
        "code_challenge_method": row["code_challenge_method"],
        "scope": row["scope"],
    }


# ---------------- Refresh tokens ----------------

async def save_refresh_token(
    token_hash: str, user_id: str, client_id: str, scope: str, ttl_seconds: int = 30 * 24 * 3600
) -> None:
    async with _connect() as db:
        _row(db)
        await db.execute(
            "INSERT INTO oauth_refresh_tokens(token_hash,user_id,client_id,scope,expires_at) VALUES(?,?,?,?,?)",
            (token_hash, user_id, client_id, scope, _now() + ttl_seconds),
        )
        await db.commit()


async def consume_refresh_token(token_hash: str) -> dict | None:
    async with _connect() as db:
        _row(db)
        async with db.execute(
            "SELECT user_id,client_id,scope,expires_at,revoked FROM oauth_refresh_tokens WHERE token_hash=?",
            (token_hash,),
        ) as cur:
            row = await cur.fetchone()
        if not row or row["revoked"] or row["expires_at"] < _now():
            return None
        await db.execute(
            "UPDATE oauth_refresh_tokens SET revoked=1 WHERE token_hash=?", (token_hash,)
        )
        await db.commit()
    return {"user_id": row["user_id"], "client_id": row["client_id"], "scope": row["scope"]}


# ---------------- Boniforce token mapping ----------------

async def set_bf_token(user_id: str, token: str, label: str | None = None) -> None:
    enc = encrypt(token)
    async with _connect() as db:
        _row(db)
        await db.execute(
            "INSERT INTO bf_tokens(user_id,encrypted_token,label,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET encrypted_token=excluded.encrypted_token, label=excluded.label, updated_at=excluded.updated_at",
            (user_id, enc, label, _now()),
        )
        await db.commit()


async def get_bf_token(user_id: str) -> str | None:
    async with _connect() as db:
        _row(db)
        async with db.execute(
            "SELECT encrypted_token FROM bf_tokens WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return decrypt(row["encrypted_token"])
