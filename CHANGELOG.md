# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0]: https://github.com/Caohung77/boniforce-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/Caohung77/boniforce-mcp/commits/main
