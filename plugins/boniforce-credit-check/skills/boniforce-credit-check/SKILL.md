---
name: boniforce-credit-check
description: Use Boniforce automatically for requests about a German company's Boniscore, creditworthiness, Bonität, Kreditlimit, payment risk, APPROVE/REVIEW/DECLINE assessment, balance-sheet history, or financial analysis. Trigger on phrases such as "Boniscore", "Boniforce", "Bonitätsprüfung", "check this German company", and "can I extend credit to this company". Do not use for private individuals, non-German companies, generic financial education, or sector-only questions.
---

# Boniforce Credit Check

Use the Boniforce MCP tools to retrieve live German company credit data. Never estimate, invent, or web-search a Boniscore.

## Availability and authentication

1. Confirm that tools from the `boniforce` MCP server are available.
2. If unavailable, tell the user to connect `https://mcp.boniforce.de/mcp` using OAuth and retry in a new conversation.
3. Never ask the user to paste a Boniforce API key into the conversation. Authentication belongs in the OAuth connection screen.

## Authorization rule

- Treat an explicit request to **get, check, calculate, create, run, or show** a current Boniscore as authorization to perform the workflow below, including spending one Boniforce credit only when a reusable report does not exist.
- Do not create a report when the user is merely asking how Boniforce or Boniscore works.
- Ask for confirmation before creating a report if the request is ambiguous about whether the user wants a live check.
- Never create a second report for the same company when a suitable recent report or a report ID from the conversation is available.

## Boniscore workflow

Follow this sequence exactly.

### 1. Reuse known context

- If the conversation already contains a `report_id` for the requested company, call `get_report` with it.
- Otherwise, call `list_reports` before any company search or report creation.
- Match company names case-insensitively. If a completed matching report is no more than 30 days old, reuse its `report_id` with `get_report`. This is free and immediate.
- Treat failed, incomplete, or older reports as non-reusable.

### 2. Identify the company

- If no reusable report exists, call `search_companies` with the company name.
- Use register court, register type, register number, city, and legal name supplied by the user to select the exact result.
- If several plausible matches remain, show a concise numbered list and ask the user to choose. Do not spend a credit until the company is unambiguous.
- If there is one clear match, continue without unnecessary confirmation.

### 3. Create and finish a report

- Call `create_report` once with the selected company's returned fields verbatim and `wait_seconds=40`.
- Inspect `done` after the call. If `done=false`, call `get_job_status` immediately with the same `job_id` and `wait_seconds=40`.
- If it remains incomplete, call `get_job_status` once more. Keep all polling in the same user turn.
- When completed, use the inlined report when present; otherwise call `get_report` with the returned `report_id`.
- After the create call plus two status calls, report an unusual delay instead of creating another report.

### 4. Answer clearly

Return the available:

- exact legal company name and register identity;
- Boniscore from 0–100, explaining that higher is better;
- score label or risk class;
- recommended credit limit;
- APPROVE, REVIEW, or DECLINE assessment;
- report date and whether an existing report was reused or a new one was created.

Mention that the result is decision support, not a guarantee. Do not expose access tokens, API keys, internal authentication data, or raw secrets.

## Follow-up questions

- Reuse the same `report_id` for follow-up questions about the company.
- Use `get_report_financial_data` for annual figures and balance-sheet history.
- Use `get_report_financial_analysis` for derived ratios and interpretation.
- Explain a 404 from either financial-detail tool as unavailable Bundesanzeiger filing data; the Boniscore itself can still be valid.

## Failure handling

- Authentication failure: ask the user to reconnect Boniforce through OAuth; do not request their API key in chat.
- No company match: ask for the legal name, register court, or register number.
- Insufficient Boniforce credits: state that no new report was created and direct the user to their Boniforce account.
- Tool or server failure: report the error plainly and never substitute an invented score.
