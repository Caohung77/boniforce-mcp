# Anthropic Connectors Directory — Submission Spec

Target: list `Boniforce` MCP server at https://mcp.boniforce.de/mcp in
Anthropic's public Connectors Directory.

Reference: https://claude.com/docs/connectors/building/submission

## Status at start of this spec (v0.3.1)

| Requirement | State |
|---|---|
| Public HTTPS endpoint | ✅ `https://mcp.boniforce.de/mcp` |
| Streamable HTTP transport (not SSE) | ✅ `server.py:464` `transport="http"` |
| OAuth 2.1 + PKCE S256 + DCR | ✅ `auth.py` |
| RS256 JWT, audience-bound, 1h TTL | ✅ `auth.py` |
| RFC 8414 / 9728 metadata | ✅ `/.well-known/*` |
| JWKS endpoint | ✅ `/jwks.json` |
| TLS termination | ✅ Caddy + Let's Encrypt |
| Privacy policy URL exists | ⚠️ `boniforce.de/datenschutz` — needs MCP addendum |
| **Origin-header validation** | ❌ |
| **Tool annotations on all 22 tools** | ❌ |
| **Logo + favicon assets** | ❌ |
| **Public docs URL (live by publish)** | ⚠️ only README on GitHub |
| **Privacy-policy MCP coverage** | ⚠️ needs review |

## Phase 1 — Technical blockers

### B1. Origin + Host validation middleware

**Why:** Anthropic explicitly requires Origin-header validation. Also
defends against DNS rebinding when MCP server is invoked from browser
contexts.

**File:** `src/boniforce_mcp/server.py`

**Change:** add middlewares to `build_app()`.

```python
from urllib.parse import urlparse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

ALLOWED_MCP_ORIGINS = {
    "https://claude.ai",
    "https://chatgpt.com",
}

class OriginAllowlistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/mcp"):
            origin = request.headers.get("origin")
            if origin and origin not in ALLOWED_MCP_ORIGINS:
                return JSONResponse(
                    {"error": "origin_not_allowed"}, status_code=403
                )
        return await call_next(request)

def build_app() -> Starlette:
    mcp = _make_mcp()
    mcp_app = mcp.http_app(path="/mcp", transport="http")
    issuer_host = urlparse(get_settings().issuer).hostname or "localhost"
    return Starlette(
        routes=[*auth.routes(), *rest_api.routes(), Mount("/", app=mcp_app)],
        middleware=[
            Middleware(TrustedHostMiddleware, allowed_hosts=[issuer_host]),
            Middleware(OriginAllowlistMiddleware),
            Middleware(WWWAuthenticateResourceMetadataMiddleware),
        ],
        lifespan=lambda _outer: _combined_lifespan(mcp_app),
    )
```

**Tests:** `tests/test_origin_rejection.py`
- `Origin: https://evil.tld` → 403
- `Origin: https://claude.ai` → passes (still requires auth → 401)
- `Host: foo.bar` → 400
- No `Origin` header (server-to-server) → passes

**Risk:** breaking legitimate clients. Mitigation: empty/missing Origin
must pass; allowlist starts permissive then tightened post-launch.

### B2. Tool annotations

**Why:** "Confirmed tool annotations" is a required submission field.

**File:** `src/boniforce_mcp/server.py` — replace every `@mcp.tool` with
annotated form.

**Annotation matrix:**

| Tool | title | readOnly | destructive | idempotent | openWorld |
|---|---|---|---|---|---|
| `search_companies` | "Search German companies" | T | F | T | T |
| `search_companies_advanced` | "Advanced German company search" | T | F | T | T |
| `list_reports` | "List previously generated reports" | T | F | T | F |
| `create_report` | "Start a Boniscore report" | **F** | F | **F** | T |
| `get_report` | "Fetch finished report" | T | F | T | F |
| `get_job_status` | "Poll report job" | T | F | T | F |
| `get_financial_data` | "Fetch financial statements directly" | **F** | F | **F** | T |
| `get_financial_analysis` | "Analyze financial statements directly" | **F** | F | **F** | T |
| `get_report_financial_data` | "Balance-sheet history" | T | F | T | F |
| `get_report_financial_analysis` | "Per-year ratio analysis" | T | F | T | F |
| `get_company_details` | "Company details and representatives" | T | F | T | F |
| `get_company_shareholders` | "Company shareholders" | **F** | F | **F** | T |
| `get_company_holdings` | "Company holdings" | **F** | F | **F** | T |
| `list_branch_scores` | "List German sector scores" | T | F | T | T |
| `get_branch_ranking` | "Rank sectors by health" | T | F | T | T |
| `get_branch` | "Sector snapshot" | T | F | T | T |
| `get_branch_history` | "Sector score history" | T | F | T | T |
| `get_branch_news` | "Sector monthly briefing" | T | F | T | T |
| `get_branch_insolvency_history` | "Sector insolvency series" | T | F | T | T |
| `get_branch_indicator_history` | "Indicator series" | T | F | T | T |
| `list_branch_indicators` | "List indicators" | T | F | T | T |
| `get_sectorbench_meta` | "Sectorbench freshness meta" | T | F | T | T |

**Rationale for `create_report`:**
- not read-only — kicks off a side-effectful Bundesanzeiger fetch job
- not destructive — no data lost
- not idempotent — repeated calls produce new report rows
- openWorld true — calls external Boniforce API

**Pattern:**
```python
@mcp.tool(annotations={
    "title": "Search German companies",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
})
async def search_companies(query: str) -> Any: ...
```

**Acceptance:** `tools/list` response includes `annotations` on every
tool. Verify with MCP Inspector.

### B3. Branding assets

**Required artifacts** (host under `https://boniforce.de/static/connector/`):
- `logo.svg` — square, ≥512×512 viewBox, transparent background, brand
  blue `#0F3D6E` + accent. Used by Anthropic in directory listing.
- `favicon.ico` (multi-size 16/32/48) + `favicon-192.png`
- Optional `logo-light.svg`, `logo-dark.svg` for theme variants

**Screenshots:** include the inline Boniscore progress card for MCP Apps
compatible hosts. Claude clients that do not render MCP App resources continue
to receive the complete plain tool result.

**Hosting:** static directory served by existing boniforce.de site.
Verify: `curl -I https://boniforce.de/static/connector/logo.svg` →
`200 image/svg+xml`, no auth.

### B4. Privacy-policy MCP addendum

**Why:** "missing or incomplete privacy policies result in immediate
rejection".

**Target URL:** `https://boniforce.de/datenschutz/mcp` (or section
inside the main Datenschutz page).

**Mandatory content** (GDPR Art. 13 + Anthropic policy expectations):

1. **Data sent to Boniforce by the connector**
   - Company identifiers: name, register_type, register_number,
     register_court
   - No chat content, no PII beyond the API key holder's identity
2. **Data sent to Sectorbench**
   - branch_key (one of 10 enum values), indicator_key, month windows
   - No user-identifying data
3. **Credential handling**
   - Per-user Boniforce `sk_live-…` API key
   - Encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA-256)
   - Encryption key in `BF_ENCRYPTION_KEY` env var, never logged
   - Never echoed back in any tool response
4. **User identifier**
   - `sha256(api_key)` — one-way hash, no reversal
5. **Retention**
   - OAuth authorization codes: 10 minutes
   - Access tokens: 1 hour (JWT, not stored)
   - Refresh tokens: until user revokes; stored hashed
   - Encrypted API keys: until user revokes upstream in Boniforce
     dashboard
6. **Third-party processors**
   - Anthropic / OpenAI — chat platform
   - Boniforce GmbH — data controller for credit data
   - Sectorbench — operator-token-mediated, end-user anonymous
7. **No chat-history forwarding** — tool calls send only the structured
   arguments the model decides on
8. **Revocation path** — user revokes the API key in
   `boniforce.de` → next connector call returns 401 → connector dies
9. **Lawful basis** — Art. 6(1)(b) GDPR contract execution
10. **Contact** — DPO email, legal address
11. **Transfers** — note any non-EU transfers (Anthropic = US, OpenAI = US,
    Standard Contractual Clauses)
12. **Subprocessor list** with link

**Process:** draft → DPO review → publish at stable URL before
submission date.

### B5. Public docs page

**Why:** Anthropic prefers a help-center / blog URL over GitHub README.

**Target URL:** `https://boniforce.de/docs/mcp` or
`https://help.boniforce.de/connector`.

**Content outline:**
1. 60-second quickstart — paste URL into Claude → paste API key → done
2. API-key creation walkthrough — reuse `assets/get-api-key.png`
3. Tool catalogue (table with German + English use phrases)
4. FAQ (auth, revocation, costs, plans, Sectorbench coverage)
5. Troubleshooting (401, slow report, sector data outage)
6. Changelog link

**Acceptance:** publicly reachable, no auth, English + German.

## Phase 2 — Submission-form payload

| Form field | Value |
|---|---|
| Server name | `Boniforce` |
| MCP URL | `https://mcp.boniforce.de/mcp` |
| Tagline | "Instant German credit checks + sector intelligence inside Claude." |
| Description | Two paragraphs — Boniscore + Bundesanzeiger filings + 10 Destatis sectors |
| Use cases | (1) B2B sales credit screening (2) AR / credit-controller workflows (3) M&A and investment due diligence (4) Sector outlook briefings for German SMEs |
| Auth type | OAuth 2.1 (PKCE S256, DCR) |
| Transport | Streamable HTTP |
| Read-only capabilities | 17 tools — listed |
| Credit-spending capabilities | 5 tools — report, direct financial, ownership refreshes |
| Tools list | 22 tools with annotations from B2 |
| Data handling | "Per-user OAuth token; Fernet at rest; sha256 user-id; no chat content stored" |
| Third-party connections | `api.boniforce.de`, `sectorbench.theaiwhisperer.cloud` |
| Category | Productivity (or Finance if available) |
| Public docs URL | from B5 |
| Privacy policy URL | from B4 |
| Logo | URL from B3 |
| Favicon | URL from B3 |
| Screenshots | inline Boniscore progress/result card |
| Allowed link URIs | `https://boniforce.de`, `https://api.boniforce.de`, `https://sectorbench.theaiwhisperer.cloud` |

## Phase 3 — Pre-submit verification

```bash
# 1. OAuth metadata reachable + correct
curl -s https://mcp.boniforce.de/.well-known/oauth-authorization-server | jq
curl -s https://mcp.boniforce.de/.well-known/oauth-protected-resource | jq

# 2. JWKS
curl -s https://mcp.boniforce.de/jwks.json | jq '.keys[0] | {kty, alg, use, kid}'

# 3. Origin rejection (B1)
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Origin: https://evil.tld" \
  https://mcp.boniforce.de/mcp
# expect: 403

# 4. Host rejection
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Host: foo.bar" \
  https://mcp.boniforce.de/mcp
# expect: 400

# 5. Tool list with annotations (B2)
npx @modelcontextprotocol/inspector https://mcp.boniforce.de/mcp
# walk OAuth, confirm every tool shows annotations block

# 6. TLS / HTTP-2
curl -sI https://mcp.boniforce.de/mcp | head

# 7. Asset reachability (B3)
curl -sI https://boniforce.de/static/connector/logo.svg
curl -sI https://boniforce.de/static/connector/favicon.ico

# 8. Docs + policy live
curl -sI https://boniforce.de/docs/mcp
curl -sI https://boniforce.de/datenschutz/mcp

# 9. Unit tests
pytest -q
```

All nine must pass before form submission.

## Phase 4 — Execution order

| # | Task | File / target | Effort | Risk |
|---|---|---|---|---|
| 1 | B2 tool annotations | `server.py` | 1h | low |
| 2 | B1 origin / host middleware + tests | `server.py`, `tests/test_origin_rejection.py` | 2h | medium |
| 3 | B3 logo + favicon | `assets/` + boniforce.de hosting | 2h design + 30min ops | low |
| 4 | B5 docs page | boniforce.de CMS | 1d copy | low |
| 5 | B4 privacy-policy MCP section | boniforce.de + DPO review | days (legal) | high |
| 6 | Verification (Phase 3) | shell | 30min | — |
| 7 | Bump `pyproject.toml` to `0.4.0`, CHANGELOG entry | repo | 15min | — |
| 8 | Deploy to `mcp.boniforce.de` | `deploy/` Docker | 30min | medium |
| 9 | Submit Anthropic form | web | 30min | — |

Total dev time: ~6h code + ~1d copy. Long pole: legal review on B4.

## Open decisions

1. **MCP App?** Yes — `create_report` links to a standards-based inline
   progress/result card. Confirm the current Claude directory's screenshot and
   rendering support at submission time; plain-tool fallback remains complete.
2. **Allowed link URIs** — include Sectorbench? Yes if users may be
   redirected to source; otherwise skip to keep approval scope small.
3. **Category** — confirm against Anthropic's current category list at
   submission time (Productivity vs Finance vs Research).
4. **Sectorbench branding** — disclose as upstream data provider in
   description and privacy policy.

## Rollback plan

If Anthropic rejects:
- Read review comments → re-open this spec → patch named blocker → re-submit.
- No production rollback needed — directory listing is additive; existing
  custom-connector users keep working regardless.
