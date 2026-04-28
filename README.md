# Boniforce MCP

Remote **Model Context Protocol** server that wraps the
[Boniforce API](https://api.boniforce.de/v1/docs) and exposes its endpoints as
tools usable from **ChatGPT Connectors** and **Claude.ai Custom Connectors**.

The server speaks MCP Streamable HTTP and ships its own OAuth 2.1
authorization server (PKCE + Dynamic Client Registration), so it satisfies
both Claude's and ChatGPT's connector requirements.

## Tools

| MCP tool                          | Underlying Boniforce endpoint                                  |
|-----------------------------------|----------------------------------------------------------------|
| `search_companies`                | `GET /v1/search`                                               |
| `list_reports`                    | `GET /v1/reports`                                              |
| `create_report`                   | `POST /v1/reports`                                             |
| `get_report`                      | `GET /v1/reports/{report_id}`                                  |
| `get_job_status`                  | `GET /v1/jobs/{job_id}/status`                                 |
| `get_financial_data`              | `GET /v1/financial_data`                                       |
| `get_financial_analysis`          | `GET /v1/financial_data/analysis`                              |
| `get_report_financial_data`       | `GET /v1/reports/{report_id}/financial_data`                   |
| `get_report_financial_analysis`   | `GET /v1/reports/{report_id}/financial_data/analysis`          |

Each user logs into the MCP server once and links their personal Boniforce API
token; that token is encrypted at rest (Fernet) and used to authenticate the
underlying HTTP calls.

## Quickstart (local)

```bash
git clone <this repo> Boniforce-MCP
cd Boniforce-MCP
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# generate keys
echo "BF_ENCRYPTION_KEY=$(boniforce-mcp genkey)" > .env
printf 'BF_OAUTH_SIGNING_KEY="%s"\n' "$(boniforce-mcp gensigning | awk 'BEGIN{ORS="\\n"}1')" >> .env
cat >> .env <<'EOF'
BF_ISSUER_URL=http://localhost:8000
BF_DB_PATH=./boniforce-mcp.sqlite
BF_API_BASE=https://api.boniforce.de
BF_JWT_AUDIENCE=boniforce-mcp
EOF

boniforce-mcp adduser you@example.com
boniforce-mcp setkey you@example.com   # paste your Boniforce token

uvicorn boniforce_mcp.server:app --port 8000
```

Then point [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
at `http://localhost:8000/mcp` to walk the OAuth + tool flow:

```bash
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

## Deploy on Ubuntu 87.106.211.11

### 1. DNS

Pick a public hostname and point an `A` record at `87.106.211.11`. Two cheap
options:

* You own a domain — add `mcp.<your-domain>` → `87.106.211.11`.
* You don't — register `boniforce-mcp.duckdns.org` (free) and point that.

You need this *before* running the installer; Caddy uses it to issue a Let's
Encrypt certificate.

### 2. Firewall

```bash
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

### 3. systemd + Caddy (recommended)

```bash
git clone <this repo> /tmp/boniforce-mcp
cd /tmp/boniforce-mcp
sudo DOMAIN=mcp.boniforce.de ./deploy/install.sh
```

The script:

1. installs Python 3.11 and Caddy
2. creates a `boniforce` system user
3. copies the source to `/opt/boniforce-mcp` and builds a venv
4. generates fresh `BF_ENCRYPTION_KEY` and `BF_OAUTH_SIGNING_KEY` and writes
   `/opt/boniforce-mcp/.env`
5. installs the systemd unit (`boniforce-mcp.service`) and enables it
6. drops a `Caddyfile` for your domain and reloads Caddy

After it finishes, create your first user and link your Boniforce token:

```bash
sudo -u boniforce /opt/boniforce-mcp/.venv/bin/boniforce-mcp adduser you@example.com
sudo -u boniforce /opt/boniforce-mcp/.venv/bin/boniforce-mcp setkey  you@example.com
```

### 4. Docker Compose alternative

```bash
cd deploy
cp ../.env.example ../.env       # edit to taste
sed -i 's/mcp\.example\.com/your.domain/' Caddyfile
docker compose up -d
```

(SQLite data and Caddy state live in named volumes.)

## Adding the connector

### Claude.ai
Settings → Connectors → **Add custom connector** → URL:

```
https://mcp.boniforce.de/mcp
```

Claude walks the OAuth flow, you sign in with the email/password you set via
`adduser`, and the 9 Boniforce tools appear.

### ChatGPT
Settings → Connectors → **Add** → same URL. ChatGPT requires OAuth 2.1 + DCR;
this server provides both, so no extra config.

### First-run Boniforce-token link

If a user logs in but has no Boniforce token stored, the OAuth flow shows a
"Link your Boniforce API key" form before redirecting back — paste the token
once and continue. Admins can pre-provision via `boniforce-mcp setkey`.

## Endpoint reference

| Path                                           | Purpose                                |
|------------------------------------------------|----------------------------------------|
| `/.well-known/oauth-authorization-server`      | OAuth 2.1 metadata (RFC 8414)          |
| `/.well-known/oauth-protected-resource`        | Protected resource metadata (RFC 9728) |
| `/oauth/register`                              | Dynamic Client Registration (RFC 7591) |
| `/oauth/authorize`                             | OAuth authorization endpoint (PKCE)    |
| `/oauth/login`                                 | Username/password login form target    |
| `/oauth/token`                                 | Token endpoint                         |
| `/jwks.json`                                   | JSON Web Key Set                       |
| `/setup`                                       | Boniforce-token link form              |
| `/mcp`                                         | MCP Streamable HTTP transport          |

## CLI

```
boniforce-mcp genkey         # new Fernet key for BF_ENCRYPTION_KEY
boniforce-mcp gensigning     # new RSA private key (PEM) for BF_OAUTH_SIGNING_KEY
boniforce-mcp initdb         # create SQLite schema
boniforce-mcp adduser EMAIL  # create user (prompts for password)
boniforce-mcp setkey EMAIL   # store user's Boniforce token (prompts)
boniforce-mcp listusers
```

## Testing

```bash
pip install -e ".[dev]"
pytest
```

Tests cover:
* httpx wrapping of every Boniforce endpoint (mocked with `respx`),
* full OAuth 2.1 PKCE flow including DCR, login, code exchange and refresh,
* PKCE failure rejection,
* JWKS metadata.

## Security notes

* Boniforce tokens are stored encrypted with Fernet; the key lives in `.env`,
  which is `chmod 600` and owned by the service user.
* OAuth access tokens are short-lived (1 h) RS256 JWTs. Refresh tokens are
  opaque, hashed in DB, single-use (rotating).
* All HTTPS termination happens at Caddy; the FastMCP server only listens on
  `127.0.0.1`.
* PKCE is mandatory (`S256`); `plain` is rejected per OAuth 2.1.
* Passwords are bcrypt-hashed.
