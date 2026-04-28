# Boniforce MCP

Remote **Model Context Protocol** server that wraps the
[Boniforce API](https://api.boniforce.de/v1/docs) and exposes its endpoints as
tools usable from **Claude.ai Custom Connectors** and **ChatGPT Connectors**.

The server speaks MCP Streamable HTTP and ships its own OAuth 2.1
authorization server (PKCE + Dynamic Client Registration), so it satisfies
both Claude's and ChatGPT's connector requirements out of the box.

---

## Use it (for Boniforce customers)

You don't need to install anything. The official Boniforce-hosted instance is at:

```
https://mcp.boniforce.de/mcp
```

### Add to Claude

1. Open [claude.ai](https://claude.ai) → **Settings → Connectors → Add custom connector**.
2. Paste the URL above.
3. A browser tab opens — paste your **Boniforce API key**.
4. The Boniforce tools appear in your chat.

### Add to ChatGPT

Settings → **Connectors → Add** → paste the same URL. The OAuth flow is identical.

### Don't have an API key yet?

Generate one in your Boniforce dashboard. The API key is the only credential
the connector needs — no separate MCP password to manage.

---

## Tools

After connecting, your AI assistant can call any of these:

| Tool                              | What it does                                                       |
|-----------------------------------|--------------------------------------------------------------------|
| `search_companies`                | Find a German company by name; returns register details.           |
| `create_report`                   | Kick off Boniscore generation for a company.                       |
| `get_job_status`                  | Poll a running report job until finished.                          |
| `get_report`                      | Fetch the finished report: Boniscore, credit limit, assessment.    |
| `get_report_financial_data`       | Drill into the underlying balance-sheet history.                   |
| `get_report_financial_analysis`   | Drill into per-year financial ratios + sub-scores.                 |
| `list_reports`                    | List previously generated reports for the account.                 |

The assistant follows the workflow: **search → create_report → poll → get_report**.

Example prompt:

> *"What's the Boniscore and credit limit for Boniforce GmbH?"*

---

## Self-hosting

Anyone can run their own instance against `api.boniforce.de` — useful for
private routing, self-controlled OAuth, or development.

### Requirements

* A Linux host with Docker + Docker Compose
* A public domain pointing to the host (Let's Encrypt needs port 80/443)
* A Boniforce API token for each user you'll provision

### Quick deploy (Docker + Caddy)

```bash
git clone https://github.com/Caohung77/boniforce-mcp
cd boniforce-mcp/deploy

# Edit Caddyfile: replace the example domain with yours
sed -i 's/mcp\.boniforce\.de/your.domain.tld/' Caddyfile

# Generate secrets and write .env (one-time)
cat > ../.env <<EOF
BF_ISSUER_URL=https://your.domain.tld
BF_DB_PATH=/var/lib/boniforce-mcp/db.sqlite
BF_ENCRYPTION_KEY=$(docker run --rm python:3.11-slim sh -c "pip install -q cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")
BF_OAUTH_SIGNING_KEY="$(docker run --rm python:3.11-slim sh -c "pip install -q cryptography && python -c 'from cryptography.hazmat.primitives import serialization as s; from cryptography.hazmat.primitives.asymmetric import rsa; k=rsa.generate_private_key(65537,2048); print(k.private_bytes(s.Encoding.PEM,s.PrivateFormat.PKCS8,s.NoEncryption()).decode().replace(chr(10), chr(92)+chr(110)), end=\"\")'")"
BF_API_BASE=https://api.boniforce.de
BF_HOST=0.0.0.0
BF_PORT=8000
EOF
chmod 600 ../.env

docker compose up -d --build
```

The connector URL is now `https://your.domain.tld/mcp`. Users self-provision
by pasting their Boniforce API key on first connection — no admin step needed.

### Behind an existing Traefik

If your host already runs Traefik on a `traefik-public` network with an
`letsencrypt` cert resolver, use the labelled compose file instead — no Caddy:

```bash
docker compose -f deploy/docker-compose.traefik.yml up -d --build
```

Adjust the `Host(...)` rule in `docker-compose.traefik.yml` to your domain.

### Local development

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

boniforce-mcp genkey > /dev/null   # confirms install
cat > .env <<EOF
BF_ISSUER_URL=http://localhost:8000
BF_DB_PATH=./boniforce-mcp.sqlite
BF_ENCRYPTION_KEY=$(boniforce-mcp genkey)
BF_OAUTH_SIGNING_KEY="$(boniforce-mcp gensigning | awk 'BEGIN{ORS="\\n"}1')"
BF_API_BASE=https://api.boniforce.de
EOF

uvicorn boniforce_mcp.server:app --port 8000
# Open http://localhost:8000/oauth/authorize?... in MCP Inspector;
# the API-key form will accept any token api.boniforce.de validates.
```

Probe with the official MCP Inspector:

```bash
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

---

## CLI

```
boniforce-mcp genkey         # Fernet key for BF_ENCRYPTION_KEY
boniforce-mcp gensigning     # RSA private key for BF_OAUTH_SIGNING_KEY
boniforce-mcp initdb         # create SQLite schema (idempotent)
boniforce-mcp listusers      # list users (synthetic emails for token-only flow)
```

In the standard flow users are auto-provisioned from their Boniforce API key
on first connection — no admin commands needed.

## Endpoints

| Path                                           | Purpose                                |
|------------------------------------------------|----------------------------------------|
| `/mcp`                                         | MCP Streamable HTTP transport          |
| `/.well-known/oauth-authorization-server`      | OAuth 2.1 metadata (RFC 8414)          |
| `/.well-known/oauth-protected-resource`        | Protected resource metadata (RFC 9728) |
| `/oauth/register`                              | Dynamic Client Registration (RFC 7591) |
| `/oauth/authorize`                             | OAuth authorization (PKCE)             |
| `/oauth/login`                                 | API-key validation form                |
| `/oauth/token`                                 | Token endpoint                         |
| `/jwks.json`                                   | JSON Web Key Set                       |

## Testing

```bash
pip install -e ".[dev]"
pytest
```

Tests cover the httpx client (mocked Boniforce backend), the full OAuth 2.1
PKCE flow including DCR + refresh, PKCE failure rejection, and JWKS shape.

## Security

* The Boniforce API key is the only user credential. It is validated against
  `api.boniforce.de` on every login form submission and stored Fernet-encrypted
  in SQLite (encryption key in `.env`, chmod 600, owned by the service user).
* The user identifier is `sha256(token)` — same key on a different device maps
  to the same MCP user.
* OAuth access tokens are short-lived (1 h) RS256 JWTs bound to the canonical
  MCP resource URL. Refresh tokens are opaque, single-use, hashed in DB.
* PKCE mandatory (`S256` only); `plain` rejected per OAuth 2.1.
* Reverse proxy (Caddy or Traefik) handles TLS; the application listens only
  on the internal port.

## License

MIT.
