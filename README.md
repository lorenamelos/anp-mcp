# anp-mcp

An MCP server that lets AI assistants (Claude, Cursor, and any MCP client) answer
questions about **Brazilian oil production**, using ANP's well-level data harmonised
from 2005 to today.

ANP publishes production per well, but not through an API: files change format
over time, wells and contracts get renamed, and the online query is behind a captcha.
This server works on a cleaned, harmonised snapshot of that data, so you can simply ask:

> *"Which oil fields grew and declined the most in the last 6 months?"*

| Largest rises (Apr–Sep 2026 vs Oct 2025–Mar 2026) | Before (bbl/d) | Recent (bbl/d) | Change |
|---|---|---|---|
| Búzios | 836,755 | 1,056,771 | +26.3% |
| Mero | 653,417 | 750,764 | +14.9% |
| Bacalhau | 32,119 | 99,492 | +209.8% |

## Can you trust the numbers?

The sum of all wells reproduces ANP's official national production **within ±1% in
every one of the 261 months** from January 2005 to September 2026 (worst month:
+0.52%, March 2008). The server exposes this check as a tool, so the assistant can
show it whenever it answers.

## Tools

| Tool | What it answers |
|---|---|
| `search_field_groups` | Find a field by name (accent-insensitive): *"buzios"* → BÚZIOS |
| `field_group_production` | Monthly production (bbl/d) and active wells of one field |
| `top_changes` | Fields that grew or declined the most over the last N months, with the method |
| `national_reconciliation` | The trust check above: summary by default, every month on request |

A **field group** is the unit used here: fields that were renamed, merged or split by
contract are grouped (e.g. TUPI includes LULA), so a series does not break when ANP
changes a name.

## How it works

```text
ANP raw files  ->  harmonisation pipeline  ->  open dataset (Parquet snapshot)
                                                     |
                     your AI assistant  <-  anp-mcp (DuckDB, runs on your machine)
```

The server reads the dataset with DuckDB: no database to install, nothing sent to
any third-party server. Units: petroleum = oil + condensate, daily rate (bbl/d).

## Install

> The open dataset is being published. Until then, `ANP_MCP_DATA_DIR` must point to
> a local copy of the dataset files.

Add to your MCP client configuration (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "anp-mcp": {
      "command": "uv",
      "args": ["--directory", "/path/to/anp-mcp", "run", "anp-mcp"],
      "env": { "ANP_MCP_DATA_DIR": "/path/to/dataset" }
    }
  }
}
```

## Limitations

- A **snapshot**, not a live feed: ANP publishes each month about two months later,
  and the dataset is updated by release.
- Production only (petroleum, wells, fields). Gas detail and royalties are planned.
- Field groups follow documented, rule-based grouping; another analyst could group
  some edge cases differently.

## Data and licence

Data: Agência Nacional do Petróleo, Gás Natural e Biocombustíveis (ANP), public data.
This is an independent project, not affiliated with ANP. Code: MIT.

Built by [Lorena Melo](https://www.linkedin.com/in/lorenameloengr/).