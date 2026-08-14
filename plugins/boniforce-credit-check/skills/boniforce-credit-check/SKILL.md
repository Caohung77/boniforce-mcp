---
name: boniforce-credit-check
description: >-
  Use Boniforce automatically for sophisticated German-company credit intelligence:
  Boniscore, creditworthiness, Bonität, credit limit, APPROVE/REVIEW/DECLINE
  assessment, financial and balance-sheet trends, company details, ownership,
  and SectorBench industry context. Trigger on requests to check, score, compare,
  explain, or visualize a German company's risk, including phrases such as
  "Boniscore", "Boniforce", "Bonitätsprüfung", "check this German company",
  "can I extend credit", "financial health", or "compare it with its sector".
  Do not use for private individuals, non-German companies, generic financial
  education, or sector-only questions.
---

# Boniforce Credit Intelligence

Use the Boniforce MCP tools to produce evidence-based, decision-ready company briefs. Never estimate, invent, or web-search a Boniscore.

## Availability and authorization

1. Confirm that tools from the `boniforce` MCP server are available. If unavailable, tell the user to connect `https://mcp.boniforce.de/mcp` using OAuth and retry in a new conversation.
2. Never request a Boniforce API key in chat. Authentication belongs in the OAuth screen.
3. Treat an explicit request to get, check, calculate, create, run, or show a current Boniscore as authorization for spending 75 Boniforce credits only when no reusable report exists.
4. Ask before creating a report when the request is only informational or ambiguous. Never create the same report twice.

## Core workflow

### 1. Reuse before spending

- Reuse a `report_id` already established for the company in the conversation.
- Otherwise call `list_reports` before searching or creating. Reuse a case-insensitive company match when it is completed and no more than 30 days old.
- If no reusable report exists, call `search_companies`; use `search_companies_advanced` only if needed. Resolve ambiguous matches with legal name, city, register type, number, and court.
- Do not spend 75 credits until the company is unambiguous.

### 2. Complete the report

- Immediately before calling `create_report`, send exactly one concise user-facing status update in the user's language. In German use: `Ich erstelle den aktuellen Bericht. Die Verarbeitung kann bis zu 120 Sekunden dauern.` Do not mention tools, workflow steps, polling, or internal processing. Do not send this notice when reusing an existing report.
- During report generation, rely on the live MCP App progress card and the MCP server's localized progress notifications when the client renders them. Do not add assistant-authored polling updates or repeat the waiting notice. If the client does not render either, continue the polling workflow silently until the report completes or all allowed polls are exhausted.
- Call `create_report` once using `search_result_id` when available and `wait_seconds=0`. This returns immediately so compatible clients can render the live progress card.
- If `done=false`, call `get_job_status` with the same `job_id` and `wait_seconds=40`, up to three times in the same turn.
- Use the inlined report when available; otherwise call `get_report` after completion. Never start a replacement report because polling is slow.

### 3. Build the full evidence pack

Once a `report_id` is available, call `get_credit_intelligence(report_id)` exactly once. This optimized tool retrieves the report, company details, financial statements, ratio analysis, and any exact WZ-matched SectorBench context concurrently.

- Do not call `get_report`, `get_company_details`, `get_report_financial_data`, or `get_report_financial_analysis` separately when the aggregate tool returned that layer.
- Fall back to separate calls only if `get_credit_intelligence` is unavailable or its `errors` object marks a required layer unavailable.
- Keep `include_news=false` by default. Set it to true only when the user requests current sector news or a briefing.

A 404 from either financial tool means that an indexed Bundesanzeiger filing is unavailable. Continue with the Boniscore and clearly mark the missing layer.

Do not call ownership tools by default. Call `get_company_shareholders` or `get_company_holdings` only on request and warn that an uncached refresh costs 25 credits. Use direct `get_financial_data` (25 credits) or `get_financial_analysis` (50 credits) only when the user explicitly wants financials without a full report.

### 4. Match and retrieve sector context

Read [references/sector-context.md](references/sector-context.md) and follow its evidence hierarchy and WZ mapping.

- Prefer the aggregate tool's `sector_match`, which uses explicit WZ/industry codes from company details or the report.
- Label the match as `verified`, `inferred`, or `unavailable`; always show the evidence and confidence.
- If `sector_match` is unavailable but the returned business purpose clearly supports one branch, infer it using the reference and retrieve only `get_branch`, `get_branch_history(months=12)`, and `get_branch_insolvency_history(months=12)` in one parallel batch.
- Use `list_branch_indicators` plus selected indicator histories only when they explain a material company risk or the user asks for a deeper driver analysis.
- If no defensible match exists, omit the sector section instead of guessing. Mention the absence only when the user explicitly requested a sector comparison.

### 5. Analyze the relationship

- Separate company facts from sector facts and interpretation.
- Compare direction and risk labels, not merely the two numeric scores.
- Classify the relationship as aligned strength, company resilience amid sector headwinds, company-specific weakness, compounded risk, or mixed/unclear.
- Explain the conclusion with two to four concrete drivers, such as profitability, equity, leverage, liquidity, sector score momentum, sector dimensions, and insolvency direction.
- Never average the Boniscore and SectorBench score or invent an adjusted/blended score. They measure different entities and methodologies.
- Base the credit recommendation on the returned Boniforce assessment and credit limit; use SectorBench as context, not as an undocumented override.

## Output

Read [references/output-format.md](references/output-format.md) and use the decision-brief template by default.

- Lead with the decision, Boniscore, credit limit, sector score, and relationship in one compact summary.
- Show financial and sector time series with compact Unicode bars/sparklines derived only from actual values. Use a Mermaid diagram only when the client renders Mermaid and it clarifies the relationship.
- Include exact fiscal years, currencies, units, report date, SectorBench `fetched_at`, match confidence, and missing-data caveats.
- Use `n/a` for unavailable values. Do not convert missing data to zero.
- Keep the first screen concise, then provide evidence tables and a short monitoring section.
- Present the professional result directly. Do not narrate which tools ran, which optional sections were skipped, or what else the user could ask to see.
- Match the user's language. Explain that the result is decision support, not a guarantee.

## Failure handling

- Authentication failure: ask the user to reconnect through OAuth; never request an API key in chat.
- No company match: ask for legal name, register court, or register number.
- Insufficient credits: state that no new report was created and direct the user to their Boniforce account.
- Tool/server failure: report the unavailable layer plainly and never substitute invented data.
