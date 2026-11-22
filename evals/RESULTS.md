# Eval results

Raw records of every run are in `runs/` (one JSON per answer). Each section below is one
version of the eval instrument; numbers are only compared within the same instrument.

## Instrument v1 (commit 89c4f2e): before and after

| | Before | After |
|---|---|---|
| Server | 4 tools, no instructions | 5 tools, server instructions (commit 1d5ff3c) |
| Run | `20261121T141647Z` | `20261121T193533Z` |
| Pass rate | **83% (52/63)** | **95% (60/63)** |
| Cost per answer (solver) | USD 0.018 | USD 0.022 (+20%: instructions and a fifth tool go with every call) |

| Category | Before | After |
|---|---|---|
| names and groups | 67% | 100% |
| trust | 50% | 100% |
| out of scope | 67% | 100% |
| tool vs memory | 33% | 100% |
| lookup | 100% | 75% |

The 3 failures after the change are instrument errors, not model errors: in q04 ("latest
month") the model used the new `national_production` tool, a valid source, and the grader
passed the answer, but the mechanical check required one of the four tools listed when the
case was written. With that list updated the run would be 63/63; reported here as a footnote
because the fix came after seeing the result.

Two weaknesses of instrument v1, found in these runs, are fixed in v2:
- The tool check named specific tools, so it failed when a better tool was added.
- The grader saw only the first 300 characters of each tool result, so it could not check
  that the numbers in an answer came from the tools.

### Baseline details (before)

| Setting | Value |
|---|---|
| Server | 4 tools, no server instructions (commit e9fbb67 + 33c3c9e) |
| Cases and rubrics | 21 cases, commit 89c4f2e (rubrics fixed before this run) |
| Model / grader | claude-sonnet-5-5 / claude-sonnet-5-5 |
| Repeats | 3 per case (63 answers) |
| Dataset | lorenamelos/anp-oil-production, v2026.09 |

#### Result

**83% pass (52/63)**. Cost USD 1.46 in total, USD 0.018 per answer (solver), 7.6 s per answer.

| Category | Pass rate |
|---|---|
| lookup, ranking, time series, units | 100% |
| names and groups | 67% |
| out of scope | 67% |
| trust | 50% |
| tool vs memory | 33% |

#### Failures and causes

| Case | Pass | What happened | Cause |
|---|---|---|---|
| q05 Lula | 0/3 | Search for "lula" returns nothing; the model uses its own knowledge to switch to TUPI and states the link as a fact of the data, with an invented reason ("contractual") | Tool gap: search does not look inside field groups |
| q21 National record | 1/3 | No tool exposes the national series; the model makes up to 7 tool calls trying to rebuild it, infers "2026" from field trends, and once claims from memory that every year since 2023 was a record (false: 2024 < 2023) | Tool gap: no national production tool |
| q20 Forecast | 0/3 | Leads with its own extrapolation ("about 1.2 million bbl/d") without saying the server has no forecasts | Missing context: nothing tells the model what the server does not do |
| q16 Trust | 0/3 | Reports the reconciliation correctly but overclaims ("does not lose or duplicate wells") and never says the project is independent and unofficial | Missing context: the tool returns numbers without what the check proves and does not prove |

Observations:
- A missing tool also costs money: q21 cost about 6x the average answer (USD 0.11) because the model kept searching.
- In every failure the answer is fluent and confident; the problem is provenance (where a claim came from), not language.

#### Improvements made (general, not tailored to a case)

1. `search_field_groups` also matches the fields inside each group (all 534 fields in `field_groups.csv`) and returns the matched field and the grouping reason from the data.
2. New tool `national_production`: ANP national total by month or year, onshore and offshore, flagging incomplete years.
3. Server `instructions` (sent at MCP initialisation) stating scope: historical ANP data, independent project, no forecasts, royalties or gas. `national_reconciliation` returns what the check proves and what it does not.

Client change for improvement 3: `run_evals.py` will pass the server instructions to the model, as desktop clients do. The baseline server sends no instructions, so this change does not affect the baseline.

## Instrument v2 (commit 0244ca6): the grader checks where each claim came from

Rule: **compare runs only within the same instrument.** v2 is stricter than v1, so its
numbers are lower for the same server; a v1 number next to a v2 number says nothing.

What changed in the instrument:
- The grader sees the full tool results (up to 6,000 characters each, not 300) and checks
  grounding: every figure presented as data must come from the tool results, directly or by
  simple arithmetic, or be labelled as the model's own knowledge. Otherwise `ungrounded`.
- The tool check accepts any tool, so adding a better tool can no longer fail a case.
- Prompt caching of the system prompt and tool definitions (cost only, not the grade).
- `--server-dir` runs an older server checkout, so "before" uses the same instrument as "after".

| | Before | After |
|---|---|---|
| Server | commit 33c3c9e (4 tools, no instructions) | commit e88c0cf (5 tools, instructions; server code as in 1d5ff3c) |
| Run | `20261121T225704Z_..._before` | `20261121T232831Z_..._after` |
| Pass rate | **78% (49/63)** | **90% (57/63)** |
| Cost per answer (solver) | USD 0.0141 | USD 0.0120 |
| Failures by type | 9 ungrounded, 3 invented, 1 wrong number, 1 not recorded¹ | 6 ungrounded |

¹ q21 r2: the grader returned no `error_type` and the run stopped there; the grader output is
now normalised (commit e88c0cf), so this cannot happen again.

Same gain as in v1 (+12 points), under a stricter grader. With prompt caching the cost per
answer of the improved server fell **45%**, from USD 0.0216 (v1, after) to USD 0.0120.

| Category | Before | After |
|---|---|---|
| names and groups | 56% | 100% |
| tool vs memory | 67% | 100% |
| out of scope | 67% | 89% |
| time series | 89% | 100% |
| trust | 50% | 50% |
| lookup | 83% | 83% |

All 6 failures after the change (q04 ×2, q16 ×3, q20 ×1) contain statements taken from the
server instructions (ANP publishes about two months later, petroleum = oil + condensate,
field grouping), which the model received and the grader did not. Four of them also add a
claim found nowhere: "recent months may be revised" (q16 ×3) and "December 2025 is the only
December in the series" (q20). v3 fixes the grader side.

## Instrument v3 (commit efd482e): the grader also receives the server instructions

What changed: the grader prompt includes the same server instructions the answering model
gets, as a valid source for statements ("(none)" when a server sends none). Everything else
is as in v2. Grader: claude-sonnet-5-5 for both runs. Server: commit efd482e.

| | Claude Sonnet 5.5 | Claude Haiku 5.5 |
|---|---|---|
| Run | `20261121T235123Z_..._after-v3` | `20261122T000332Z_..._haiku-v3` |
| Pass rate | **92% (58/63)** | **87% (55/63)** |
| Cost per answer (solver) | USD 0.0128 | USD 0.0006 (**1/21**) |
| Total cost, solver + grader | USD 1.40 | USD 0.59 (mostly the Sonnet grader) |
| Time per answer | 7.8 s | 5.3 s |
| Failures by type | 3 wrong number, 2 ungrounded | 5 wrong number, 2 ungrounded, 1 other |

Failures by case:
- Sonnet: q05 ×2 (right number, wrong detail about how Lula and Tupi are grouped), q06, q07,
  q11 (a percentage or a comparison computed wrongly around a correct headline number).
- Haiku: q09 ×3 (says Roncador's fall exceeds the other nine combined: ~30k vs ~48k bbl/d),
  q07, q20 (35% for a 33.2% rise), q13 (says SUL DE TUPI is outside the TUPI group; the
  search result says otherwise), q18 (an unsupported remark about royalties), q21 (other:
  presents years and volumes as ANP data without the caveat the rubric asks for).

### The kind of error changed

Before the server changes, the typical failure was an **invented** fact: a link between
fields or a national record stated from memory as if it were data (10 of 11 v1 failures).
With the improved server no run has an `invented` failure; what remains is mostly
**arithmetic around a correct number**: the headline figure matches the data and a derived
percentage, sum or comparison in the commentary is wrong. These are fewer and easier to see,
but they still mislead, and the grader is right to fail them.

### Possible next steps

- Say in the server instructions that recent months may be revised (the model keeps saying
  it; better to have it as a stated, grounded fact).
- Let `field_group_production` accept names without accents or case ("buzios" returns
  nothing today; the model recovers by calling the search first).
- A cheaper grader (e.g. Haiku), after an agreement test against the Sonnet grader on the
  same answers.
- Run the evals through the Batch API (half the price; results arrive within 24 hours).
