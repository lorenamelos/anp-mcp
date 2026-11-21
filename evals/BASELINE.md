# Eval baseline (before tool improvements)

Run `20261121T141647Z_claude-sonnet-5-5` (raw records in `baseline/`, one JSON per answer).

| Setting | Value |
|---|---|
| Server | 4 tools, no server instructions (commit e9fbb67 + 33c3c9e) |
| Cases and rubrics | 21 cases, commit 89c4f2e (rubrics fixed before this run) |
| Model / grader | claude-sonnet-5-5 / claude-sonnet-5-5 |
| Repeats | 3 per case (63 answers) |
| Dataset | lorenamelos/anp-oil-production, v2026.09 |

## Result

**83% pass (52/63)**. Cost USD 1.46 in total, USD 0.018 per answer (solver), 7.6 s per answer.

| Category | Pass rate |
|---|---|
| lookup, ranking, time series, units | 100% |
| names and groups | 67% |
| out of scope | 67% |
| trust | 50% |
| tool vs memory | 33% |

## Failures and causes

| Case | Pass | What happened | Cause |
|---|---|---|---|
| q05 Lula | 0/3 | Search for "lula" returns nothing; the model uses its own knowledge to switch to TUPI and states the link as a fact of the data, with an invented reason ("contractual") | Tool gap: search does not look inside field groups |
| q21 National record | 1/3 | No tool exposes the national series; the model makes up to 7 tool calls trying to rebuild it, infers "2026" from field trends, and once claims from memory that every year since 2023 was a record (false: 2024 < 2023) | Tool gap: no national production tool |
| q20 Forecast | 0/3 | Leads with its own extrapolation ("about 1.2 million bbl/d") without saying the server has no forecasts | Missing context: nothing tells the model what the server does not do |
| q16 Trust | 0/3 | Reports the reconciliation correctly but overclaims ("does not lose or duplicate wells") and never says the project is independent and unofficial | Missing context: the tool returns numbers without what the check proves and does not prove |

Observations:
- A missing tool also costs money: q21 cost about 6x the average answer (USD 0.11) because the model kept searching.
- In every failure the answer is fluent and confident; the problem is provenance (where a claim came from), not language.

## Planned improvements (general, not tailored to a case)

1. `search_field_groups` also matches the fields inside each group (all 534 fields in `field_groups.csv`) and returns the matched field and the grouping reason from the data.
2. New tool `national_production`: ANP national total by month or year, onshore and offshore, flagging incomplete years.
3. Server `instructions` (sent at MCP initialisation) stating scope: historical ANP data, independent project, no forecasts, royalties or gas. `national_reconciliation` returns what the check proves and what it does not.

Client change for improvement 3: `run_evals.py` will pass the server instructions to the model, as desktop clients do. The baseline server sends no instructions, so this change does not affect the baseline.
