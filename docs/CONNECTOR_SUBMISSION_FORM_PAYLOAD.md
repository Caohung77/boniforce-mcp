# Anthropic Connectors Directory — Submission Form Payload

Paste-ready values for the form at
[claude.com/docs/connectors/building/submission](https://claude.com/docs/connectors/building/submission).

Form section order matches Anthropic's current submission flow as of
2026-05. Verify each field name against the live form when filling it
out — Anthropic occasionally renames fields.

---

## 1. Server basics

| Field | Value |
|---|---|
| **Server name** | `Boniforce` |
| **MCP URL** | `https://mcp.boniforce.de/mcp` |
| **Tagline** (≤90 chars) | `Instant German credit checks and sector intelligence inside Claude.` |
| **Short description** (≤200 chars) | `Ask Claude for any German company's Boniscore, credit limit, balance-sheet history — plus the current health of 10 German sectors. Pulled live from Bundesanzeiger and Destatis.` |
| **Long description** | see § Long description below |
| **Category** | `Productivity` (fall back to `Finance` if available) |
| **Logo URL** | `https://mcp.boniforce.de/favicon/web-app-manifest-512x512.png` |
| **Favicon URL** | `https://mcp.boniforce.de/favicon/favicon.svg` |

### Long description (paste verbatim)

> The Boniforce MCP connector exposes Boniforce's German credit-data API
> and the Sectorbench sector-intelligence dataset as 16 native MCP tools.
> Claude can answer questions like *"What's Müller GmbH's Boniscore and
> credit limit?"* or *"How is the German construction sector doing right
> now?"* directly inside the chat — no tab-switching, no copy-paste.
>
> Each end-user authenticates with their own personal Boniforce API key
> via OAuth 2.1 + PKCE. The key is validated against `api.boniforce.de`
> on first connection and stored Fernet-encrypted at rest; the user
> identifier is `sha256(api_key)` — one-way and irreversible.
> Sectorbench data is served via a shared operator token, so end-users
> need no separate Sectorbench account.
>
> The connector is open source (MIT) and self-hostable; the public
> hosted instance runs at `mcp.boniforce.de`. Boniforce is operated by
> Boniforce GmbH in Germany; data sources include Bundesanzeiger
> (annual filings), Destatis (insolvency statistics), ifo, S&P Global
> PMI, Eurostat, and Bundesbank.

---

## 2. Use cases

Paste each line as a separate bullet:

- B2B sales — qualify a lead's creditworthiness mid-conversation; ask
  the assistant to draft the follow-up email with the numbers attached.
- Accounts-receivable / credit-controlling — quarterly limit reviews
  for existing customers, with balance-sheet trend + sector context in
  one prompt.
- M&A and investment due diligence — generate a one-page brief on a
  target company (Boniscore, equity ratio history, sector outlook)
  before the first meeting.
- Sector briefings — current health score, monthly outlook, and
  insolvency trend for 10 German sectors (automotive, construction,
  healthcare, retail, …).

---

## 3. Connection details

| Field | Value |
|---|---|
| **Authentication** | `OAuth 2.1` (PKCE S256, Dynamic Client Registration RFC 7591) |
| **OAuth metadata URL** | `https://mcp.boniforce.de/.well-known/oauth-authorization-server` |
| **Protected resource metadata** | `https://mcp.boniforce.de/.well-known/oauth-protected-resource` |
| **Transport** | `Streamable HTTP` (not SSE) |
| **Origin-header validation** | ✅ enforced — only `claude.ai`, `chatgpt.com`, and the issuer origin pass |
| **Read capabilities** | 15 tools (see Tools section) |
| **Write capabilities** | 1 tool — `create_report` initiates a Bundesanzeiger fetch job |

---

## 4. Tools (full list with annotations)

If the form asks for a JSON or table dump, paste this table.

| Tool | Title | readOnly | destructive | idempotent | openWorld |
|---|---|---|---|---|---|
| `search_companies` | Search German companies | T | F | T | T |
| `list_reports` | List previously generated reports | T | F | T | F |
| `create_report` | Start a Boniscore report | **F** | F | **F** | T |
| `get_report` | Fetch finished Boniscore report | T | F | T | F |
| `get_job_status` | Poll Boniscore job status | T | F | T | F |
| `get_report_financial_data` | Balance-sheet history | T | F | T | F |
| `get_report_financial_analysis` | Per-year financial ratio analysis | T | F | T | F |
| `list_branch_scores` | List German sector scores | T | F | T | T |
| `get_branch_ranking` | Rank German sectors by health score | T | F | T | T |
| `get_branch` | Sector snapshot | T | F | T | T |
| `get_branch_history` | Sector score history | T | F | T | T |
| `get_branch_news` | Sector monthly briefing | T | F | T | T |
| `get_branch_insolvency_history` | Sector insolvency history | T | F | T | T |
| `get_branch_indicator_history` | Sector indicator history | T | F | T | T |
| `list_branch_indicators` | List sector indicators | T | F | T | T |
| `get_sectorbench_meta` | Sectorbench data freshness | T | F | T | T |

Note: `create_report` is the only non-read-only tool — it starts a
background job that pulls and indexes Bundesanzeiger filings. All
others are idempotent reads. None are destructive.

---

## 5. Data & compliance

| Field | Value |
|---|---|
| **Data the connector receives** | Structured tool arguments only (company name, register data, sector key, time windows). No chat content. |
| **Data sent to third parties** | (a) `api.boniforce.de` — credit data, per-user API key; (b) `sectorbench.theaiwhisperer.cloud` — sector data, operator token (end-users anonymous). |
| **Credential storage** | Fernet (AES-128-CBC + HMAC-SHA-256) at rest. Key in `BF_ENCRYPTION_KEY` env var, never logged. |
| **User identifier** | `sha256(api_key)` — one-way hash. |
| **Token retention** | OAuth codes 10 min; access tokens 1 h (JWT, not stored); refresh tokens hashed until revocation or 90 d inactivity; encrypted API key until user revocation. |
| **Region** | EU (Germany — provider STRATO GmbH; Anthropic / OpenAI US under EU SCCs). |
| **No-training assertion** | No training use by Boniforce. Zero-retention DPA with AI subprocessors. |

---

## 6. URLs and links

| Field | Value |
|---|---|
| **Public documentation URL** | `https://www.boniforce.de/mcp-connector/` *(WordPress page id 23983, publish before submission)* |
| **Privacy policy URL** | `https://www.boniforce.de/datenschutzerklaerung-2/` *(splice in MCP addendum from `docs/PRIVACY_POLICY_MCP_ADDENDUM.md` before submission)* |
| **Terms of service URL** | `https://www.boniforce.de/agb/` |
| **Source code (optional)** | `https://github.com/Caohung77/boniforce-mcp` |
| **Support contact** | (Datenschutz / DPO e-mail from boniforce.de imprint) |

---

## 7. Allowed link URIs (optional)

Paste each origin on its own line:

```
https://www.boniforce.de
https://api.boniforce.de
https://dashboard.boniforce.de
https://sectorbench.theaiwhisperer.cloud
https://github.com/Caohung77/boniforce-mcp
```

---

## 8. Pre-submit verification (already passed)

| Check | Status |
|---|---|
| Streamable HTTP transport (not SSE) | ✅ |
| OAuth 2.1 + PKCE S256 (plain rejected) | ✅ |
| Dynamic Client Registration RFC 7591 | ✅ |
| `.well-known/oauth-authorization-server` reachable | ✅ |
| `.well-known/oauth-protected-resource` reachable | ✅ |
| JWKS endpoint | ✅ |
| Origin validation on `/mcp` (evil → 403) | ✅ |
| Host allowlist | ✅ |
| Tool annotations on all 16 tools | ✅ |
| Logo URL reachable + correct MIME | ✅ |
| Favicon URL reachable + correct MIME | ✅ |
| TLS via Let's Encrypt | ✅ |
| v0.4.0 deployed at `mcp.boniforce.de` | ✅ |

---

## 9. Outstanding (off-repo) before submitting

- [ ] Publish WP page `mcp-connector` (id 23983) → `https://www.boniforce.de/mcp-connector/`
- [ ] DPO sign-off + splice privacy addendum into `datenschutzerklaerung-2`
- [ ] Re-run verification table above against the production URLs
- [ ] Submit the form

---

## 10. Anticipated review questions

Be ready to answer these in the review thread — they speed up approval:

1. *"How are user credentials protected?"* — Fernet AES-128 at rest;
   key in server env var; never logged; `sha256(token)` is the user id.
2. *"What happens when a user revokes their Boniforce key?"* — Boniforce
   invalidates the key immediately; the next tool call returns 401;
   connector stops working until user pastes a new key.
3. *"Is any chat content forwarded?"* — No — only the structured
   arguments the model decides on.
4. *"What is the operator-token model for Sectorbench?"* — Server-side
   bearer token shared across users; end-users are anonymous from
   Sectorbench's perspective. No per-user Sectorbench account needed.
5. *"Is the server multi-tenant safe?"* — Yes — user identity bound to
   `sha256(api_key)`, audience-locked JWTs, per-user encrypted token
   storage in SQLite, OAuth-flow per user.
