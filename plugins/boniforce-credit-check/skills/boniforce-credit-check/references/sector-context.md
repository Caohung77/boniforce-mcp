# Company-to-SectorBench context

## Evidence hierarchy

Assign the SectorBench branch with the strongest available evidence:

1. **Verified:** an explicit WZ code in `primary_industry_code`, `industry_codes`, `wz_code`, or an equivalent field maps to a covered branch.
2. **Inferred:** no WZ code exists, but a returned business purpose or explicit industry label clearly matches one branch. Name the evidence and use low or medium confidence.
3. **Unavailable:** evidence is missing, ambiguous, or maps outside SectorBench coverage. Do not infer from the company name alone unless it contains an unmistakable regulated/activity term and the result is labeled low confidence.

Normalize a code such as `WZ2025 62.20`, `WZ08-62`, or `C29` to its two-digit division before mapping. If multiple codes exist, prioritize the principal/primary code. If none is marked primary, use the code best supported by the returned business purpose and disclose that choice.

## WZ mapping

This mapping mirrors SectorBench production coverage:

| SectorBench key | Label | Core WZ divisions | Broader coverage / caveat |
|---|---|---:|---|
| `automotive` | Automotive | 29 | 30 is a broader other-vehicle match; label medium confidence |
| `healthcare` | Healthcare | 86, 87, 88 | 21 (pharma) is broader coverage |
| `construction` | Construction | 41, 42, 43 | — |
| `renewable_energy` | Renewable energy | 35 | WZ 35 covers general energy supply; confirm renewable activity from purpose when possible |
| `logistics` | Logistics & transport | 49, 50, 51, 52, 53 | — |
| `fintech` | FinTech & financial services | 64, 65, 66 | Includes conventional finance and insurance |
| `it_services` | IT & information services | 62, 63 | 61 is broader telecom coverage |
| `retail` | Retail | 47 | Excludes motor-vehicle trade |
| `hospitality` | Hospitality | 55, 56 | — |
| `manufacturing` | Manufacturing | 10–33 | Use only after checking the more specific automotive mapping |

When a code fits both manufacturing and a specialist branch, prefer the specialist branch. Do not force uncovered divisions into the nearest branch.

## Sector retrieval

For a matched company, retrieve:

- `get_branch` for the current score, rank, risk level, confidence, and dimensions;
- `get_branch_history` for 12-month score direction;
- `get_branch_insolvency_history` for 12-month case direction;
- `get_branch_news` for current drivers and cited risks.

Use the freshest dates returned by each tool. Do not describe monthly news as an independent fact unless its citations support the statement.

## Relationship model

Use Boniforce's returned assessment/label and SectorBench's returned risk level/trend. Avoid hard-coded score thresholds when the APIs already provide labels.

| Company evidence | Sector evidence | Relationship | Meaning |
|---|---|---|---|
| Strong / APPROVE | Strong or improving | Aligned strength | Company and operating environment reinforce each other |
| Strong / APPROVE | Weak or deteriorating | Company resilience amid sector headwinds | Company currently outperforms a difficult environment; monitor durability |
| Weak / REVIEW or DECLINE | Strong or improving | Company-specific weakness | The issue appears more idiosyncratic than sector-wide |
| Weak / REVIEW or DECLINE | Weak or deteriorating | Compounded risk | Company weakness and sector pressure reinforce each other |
| Mixed, stale, or incomplete | Mixed or stale | Mixed/unclear | More evidence or fresher data is required |

Support the relationship with actual drivers. Prefer:

- company: profit trend, equity/equity ratio, liabilities/leverage, liquidity, balance-sheet size, receivables, returned ratio scores;
- sector: 12-month score delta, rank/rank delta, weakest dimensions, insolvency direction, and cited current risks.

Never claim causation. Use language such as "consistent with", "adds pressure", "provides a tailwind", or "suggests company-specific risk".
