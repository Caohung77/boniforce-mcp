# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.9] — 2026-05-27

### Security
- **Starlette bumped to `>=1.0.1`** to pull in the fix for
  **CVE-2026-48710 ("BadHost")**. The bug let a crafted `Host` header
  bypass path-based authorization in Starlette's router, and was
  explicitly called out as impacting MCP servers / FastMCP-style mounts.
  Boniforce-MCP mounts the bearer-gated `/mcp` app inside an outer
  Starlette that also serves public OAuth metadata, which is the exact
  pattern exploited — so this is a HIGH-severity upgrade.
- **`TrustedHostMiddleware` allowlist tightened.** `localhost`,
  `127.0.0.1`, and `testserver` are no longer permanently whitelisted in
  production. They are only re-enabled when the issuer hostname is
  `localhost`/`127.0.0.1` (dev) or when `BF_ALLOW_TEST_HOSTS=1` is
  explicitly set. Defense-in-depth against future Host-header tricks
  along the lines of BadHost.

## [0.4.8] — 2026-05-23

### Added
- **Full `FinancialDataResponse` schema in OpenAPI** for
  `GET /api/v1/reports/{report_id}/financial_data`. New nested schemas
  `FinancialFeaturesYear`, `FinancialReport`, `Aktiva` (+ details),
  `Passiva` (+ details). Mirrors the upgraded upstream Boniforce dev API
  which now returns the full Bundesanzeiger Aktiva/Passiva/GuV breakdown
  alongside the per-year summary metrics. Pass-through client unchanged.
- **`get_report_financial_data` MCP tool docstring** expanded to describe
  both `financials[]` summary and the new `financial_reports[]` deep
  breakdown.

### Changed
- **MCP `instructions` block and `list_reports` docstring** promote
  `list_reports` to **mandatory step 0** for any company question. New
  rule: if a completed report exists with `created_at` ≤30 days old,
  reuse its `report_id` and skip `create_report` entirely. Prevents
  redundant credit charges on cross-session follow-up questions about the
  same company.
- **`create_report` docstring** carries an explicit credit-cost warning
  and a precondition pointing at `list_reports`.
- **OpenAPI `info.description`** mirrors the same workflow guidance for
  ChatGPT Custom GPT REST-Action consumers (they don't see MCP
  `instructions`).

## [0.4.0] — 2026-05-12

Prepares the server for submission to the Anthropic Connectors Directory.
No breaking API changes.

### Added
- **Tool annotations on all 16 MCP tools.** Each `@mcp.tool` decorator
  now carries `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`,
  and `openWorldHint` (MCP spec). `create_report` is the only non-read-only
  / non-idempotent tool; everything else is read-only and idempotent.
  Required field on the Anthropic submission form.
- **Origin-header validation on `/mcp`.** New `OriginAllowlistMiddleware`
  rejects requests whose `Origin` header is set but not on the allowlist
  (`https://claude.ai`, `https://chatgpt.com`, the issuer origin, plus
  anything in `BF_EXTRA_ALLOWED_ORIGINS`). Missing-Origin server-to-server
  calls still pass; OAuth gates the actual access. Closes the
  DNS-rebinding vector explicitly called out in the Anthropic Connectors
  Directory submission policy.
- **Host-header allowlist** via Starlette's `TrustedHostMiddleware`.
  Defaults to the issuer hostname plus `localhost`, `127.0.0.1`,
  `testserver`; extra hosts via `BF_EXTRA_ALLOWED_HOSTS`.
- **Branding assets mount at `/favicon/*`.** Serves `favicon.svg`,
  `favicon.ico`, `favicon-96x96.png`, `apple-touch-icon.png`, and
  `web-app-manifest-512x512.png` from the new `public/favicon/` directory
  so the Anthropic submission form can reference stable HTTPS URLs hosted
  on the MCP origin. `deploy/Dockerfile` updated to `COPY public ./public`.
- **Submission documentation under `docs/`:**
  - `CONNECTOR_DIRECTORY_SUBMISSION.md` — phased spec, blocker matrix,
    form-payload table, pre-submit verification script.
  - `CONNECTOR_PUBLIC_DOCS.md` — drop-in user-facing docs page intended
    for `boniforce.de/docs/mcp`.
  - `PRIVACY_POLICY_MCP_ADDENDUM.md` — gap analysis of the existing
    Datenschutzerklärung plus German + English text to splice in.
- **New tests** (12 total, 42-pass suite):
  - `tests/test_origin_rejection.py` — 6 tests covering allowed origins,
    disallowed origins, missing Origin, well-known endpoints, Host
    rejection.
  - `tests/test_favicon_assets.py` — 6 tests covering each branding
    asset path + MIME type + no directory-listing.

### Configuration
- New optional env vars: `BF_EXTRA_ALLOWED_ORIGINS`,
  `BF_EXTRA_ALLOWED_HOSTS`. Defaults are safe for production at
  `mcp.boniforce.de`; override only for staging or alternate hostnames.

### Notes
- No tool surface changes; existing connector users keep working.
- Deployment requires the new `public/` directory to be present alongside
  `src/`. Docker users get this automatically via the updated Dockerfile;
  bare-metal operators should `git pull` before restarting.

## [0.3.1] — 2026-05-03

### Changed
- **Sectorbench tool descriptions rewritten with German keyword bait.**
  Models (Claude.ai + ChatGPT) were falling through to web search for
  German-language branch questions because the v0.3.0 docstrings were
  English-only and terse. Each Sectorbench tool now mentions the German
  trigger phrases it should respond to (Branche, Insolvenzen, Pleiten,
  Verlauf, Trend, Branchen-Briefing, etc.).
- **Server `instructions` block** gains a German→`branch_key` mapping
  table (Bauwirtschaft → construction, Einzelhandel → retail, …) plus a
  per-tool selection guide ("Insolvenzen / Pleiten in <Branche>" →
  `get_branch_insolvency_history`) and an explicit "NIEMALS websearch
  verwenden" directive for branch questions.
- All Sectorbench tool docstrings stay under 300 chars (GPT Builder cap).

### Notes
- No API or behaviour change. Pure prompting / discoverability fix.
- After deploy, end users may need to start a fresh chat (or
  disconnect / reconnect the connector) so the client re-fetches the
  updated `tools/list` and `instructions`.

## [0.3.0] — 2026-05-03

### Added
- **Sectorbench branch-data tools on MCP.** The 9 sector-intelligence
  endpoints (previously REST-only for ChatGPT Custom GPT Actions) are now
  also exposed as native MCP tools, so Claude.ai connectors and the
  ChatGPT MCP connector see them in the tool list:
  - `list_branch_scores`
  - `get_branch_ranking`
  - `get_branch(branch_key)`
  - `get_branch_history(branch_key, months=12)` — months 1-24
  - `get_branch_news(branch_key)`
  - `get_branch_insolvency_history(branch_key, months=12)` — months 1-36
  - `get_branch_indicator_history(branch_key, indicator_key, months=12)` — months 1-24
  - `list_branch_indicators`
  - `get_sectorbench_meta`
- New internal `_user_only()` helper in `server.py`: validates the JWT
  without requiring a linked Boniforce key. Sectorbench tools call upstream
  with the operator's shared `BF_SECTORBENCH_TOKEN`, so users without a
  Boniforce `sk_live-…` key can still query branch data through MCP.

### Changed
- MCP `instructions` block expanded to document the Sectorbench tools and
  hint at common follow-ups (sector context for a Boniscore answer).
- Total MCP tools surfaced: **16** (was 7).

### Notes
- No behaviour change for existing 7 Boniforce tools.
- REST `/api/v1/branches/*` mirror unchanged — Custom GPT Actions still
  speak the same OpenAPI 3.1 spec.
- No client-side migration needed: Claude / ChatGPT MCP clients
  re-discover tools on next connection.

## [0.2.0] — 2026-05-03

### Added
- **Sectorbench branch-data API.** Nine new GET endpoints proxy the
  [Sectorbench Public Data API](https://sectorbench.theaiwhisperer.cloud)
  through the existing REST mirror, so a Custom GPT user can ask about
  German sector health alongside a Boniforce credit check in the same
  chat:
  - `GET /api/v1/branches` — current scores for all 10 sectors
  - `GET /api/v1/branches/ranking` — cross-sector ranking
  - `GET /api/v1/branches/{branch_key}` — single branch scores
  - `GET /api/v1/branches/{branch_key}/history` — 12-month score trend
  - `GET /api/v1/branches/{branch_key}/news` — monthly AI briefing
  - `GET /api/v1/branches/{branch_key}/insolvency/history` — Destatis insolvency series
  - `GET /api/v1/branches/{branch_key}/indicators/{indicator_key}/history` — indicator time series
  - `GET /api/v1/indicators` — indicator catalog
  - `GET /api/v1/sectorbench/meta` — data freshness metadata
- New `BF_SECTORBENCH_TOKEN` env var (operator-issued shared `sbk_…`
  token). Endpoints return `503 sectorbench_disabled` when unset.
- `BF_SECTORBENCH_BASE` and `BF_SECTORBENCH_CACHE_TTL` env vars (defaults:
  `https://sectorbench.theaiwhisperer.cloud/api/v1`, 600s).
- New module `boniforce_mcp.sectorbench_client` (httpx wrapper with
  tenacity retry + in-memory TTL cache that protects the shared 600 req/h
  Sectorbench quota).
- New OpenAPI schemas: `BranchKey`, `BranchScore`, `BranchScoreHistoryPoint`,
  `IndicatorCatalogEntry`, `IndicatorHistoryPoint`, `InsolvencyHistoryPoint`,
  `NewsReport`, `SectorbenchMeta`.
- 20 new tests across `tests/test_sectorbench_client.py` and
  `tests/test_rest_sectorbench.py`.
- README: top-of-page sector intelligence positioning, sample prompts,
  agent-task table rows, architecture diagram update, and a new
  "Sectorbench REST endpoints" section in the developer block.

### Changed
- Per-user JWT (existing OAuth flow) now also gates the Sectorbench
  endpoints — end users do **not** paste a Sectorbench token. Same
  Boniforce `sk_live-…` key already in their session covers both
  surfaces.
- Some upstream Sectorbench fields (`risk_level`, dimension scores,
  insolvency case counts) marked nullable / unconstrained in the proxy's
  OpenAPI to match real upstream responses and avoid Custom GPT validator
  errors.

### Notes
- No new MCP tools added — only the REST/OpenAPI surface (used by Custom
  GPT Actions). Claude.ai connectors and ChatGPT custom MCP connectors
  see the same Boniforce tools as before.
- No client-side migration needed. Existing GPTs only need to
  re-import the OpenAPI schema URL once to surface the new operations.

## [0.1.0] — 2026-04 (initial public release)

### Added
- FastMCP server exposing 7 Boniforce tools: `search_companies`,
  `list_reports`, `create_report`, `get_report`, `get_job_status`,
  `get_report_financial_data`, `get_report_financial_analysis`.
- OAuth 2.1 issuer with Dynamic Client Registration (RFC 7591), PKCE
  (`S256`), JWKS, and per-user `sk_live-…` storage (Fernet-encrypted
  SQLite, user identity = `sha256(token)`).
- REST mirror at `/api/v1/*` plus OpenAPI 3.1 spec at
  `/api/openapi.json` for ChatGPT Custom GPT Actions, advertising
  `OAuth2: [mcp]` security scheme.
- Server-side long-polling on `create_report` / `get_job_status` (up to
  40s per call) plus `done` + `next_action` annotations so models can
  loop polling across multiple sequential calls within a single user
  turn (handles 30–120s report jobs).
- Wildcard redirect-URI support and PKCE-optional confidential clients
  for ChatGPT GPT Actions.
- Docker / Compose deploy with both Caddy and Traefik variants, plus
  `install.sh` helper.
- Pytest suite (10 tests) covering httpx client, full OAuth 2.1 PKCE +
  DCR + refresh, JWKS shape, and REST endpoints.

[0.3.1]: https://github.com/Caohung77/boniforce-mcp/releases/tag/v0.3.1
[0.3.0]: https://github.com/Caohung77/boniforce-mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/Caohung77/boniforce-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/Caohung77/boniforce-mcp/commits/main
