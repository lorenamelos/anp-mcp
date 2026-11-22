"""anp-mcp: an MCP server for ANP oil production data (open, read-only)."""

import logging
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import duckdb
from huggingface_hub import hf_hub_download
from mcp.server import MCPServer

# Open dataset on the Hugging Face Hub, pinned to one version so answers do not change
# silently when a new month is published.
DATASET_REPO = os.environ.get("ANP_MCP_DATASET", "lorenamelos/anp-oil-production")
DATASET_REVISION = os.environ.get("ANP_MCP_REVISION", "v2026.09")
LOCAL_DIR = os.environ.get("ANP_MCP_DATA_DIR")  # optional: read local files (development)

# The Hub client logs every cache check at INFO; keep stderr quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Sent to the client at initialisation; clients pass it to the model. It states what the
# server covers and, as important, what it does not, so gaps are not filled with guesses.
INSTRUCTIONS = f"""anp-mcp answers questions about Brazilian oil production using ANP's
well-level open data, harmonised by an independent project (not ANP, not official).
Dataset version {DATASET_REVISION}. ANP publishes each month about two months later.

Covered: petroleum (oil + condensate) production in bbl/d, monthly from 2005, by field group
(fields renamed, merged or split by contract are grouped; search also finds the old names)
and for Brazil as a whole (national_production), plus a consistency check against ANP's
national total (national_reconciliation).

Not covered: forecasts, royalties, natural gas, prices. For these, say the tools do not cover
them; do not present estimates or extrapolations as data. When a statement comes from your
own knowledge rather than from a tool result, say so."""


def data_file(name: str) -> str:
    """Path of a dataset file: local if ANP_MCP_DATA_DIR is set, else downloaded once from the Hub and cached."""
    if LOCAL_DIR:
        return str(Path(LOCAL_DIR).expanduser() / name)
    return hf_hub_download(
        repo_id=DATASET_REPO, filename=name, repo_type="dataset", revision=DATASET_REVISION
    )


try:
    __version__ = version("anp-mcp")
except PackageNotFoundError:  # running from source without the package installed
    __version__ = "0.0.0+unknown"

mcp = MCPServer("anp-mcp", instructions=INSTRUCTIONS, version=__version__)


def connect():
    con = duckdb.connect()
    con.execute(f"""
        CREATE VIEW field_group_month AS
        SELECT field_group, CAST(period AS DATE) AS period, petroleum_bpd, active_wells
        FROM read_parquet('{data_file("field_group_month.parquet")}')
    """)
    con.execute(f"""
        CREATE VIEW national_check AS
        SELECT period, national_total, wells_total, rel_diff_total, flag
        FROM read_csv_auto('{data_file("national_check.csv")}')
    """)
    con.execute(f"""
        CREATE VIEW national AS
        SELECT CAST(period AS DATE) AS period, national_terra, national_mar, national_total
        FROM read_csv_auto('{data_file("national_check.csv")}')
    """)
    con.execute(f"""
        CREATE VIEW field_groups AS
        SELECT field, field_group, grouped, reason
        FROM read_csv_auto('{data_file("field_groups.csv")}', all_varchar=true,
                           types={{'grouped': 'BOOLEAN'}})
    """)
    return con


def rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict]:
    """Run a query and return a list of dicts (column name -> value)."""
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


@mcp.tool()
def search_field_groups(text: str, limit: int = 10) -> dict:
    """Find oil field groups by name (case and accent insensitive), e.g. 'buzios', 'tupi'.

    Searches the group names and the names of the fields inside each group, so an old or
    contract name leads to the group that now carries its production. A field group is the
    unit used in the analysis: fields renamed, merged or split by contract are grouped.
    Returns each match with its latest month and petroleum production (bbl/d), the fields
    whose name matched, and, for grouped fields, the grouping reason recorded in the data.
    """
    con = connect()
    found = rows(
        con,
        """
        WITH q AS (SELECT '%' || strip_accents(lower(?)) || '%' AS pat),
        hits AS (
            SELECT f.field_group, f.field, f.grouped, f.reason,
                   strip_accents(lower(f.field)) LIKE q.pat AS field_hit
            FROM field_groups f, q
            WHERE strip_accents(lower(f.field)) LIKE q.pat
               OR strip_accents(lower(f.field_group)) LIKE q.pat
        ),
        per_group AS (
            SELECT field_group,
                   coalesce(list(DISTINCT field ORDER BY field)
                            FILTER (WHERE field_hit AND field <> field_group), []) AS matched_fields,
                   any_value(reason) FILTER (WHERE grouped) AS grouping_reason
            FROM hits GROUP BY field_group
        ),
        latest AS (
            SELECT field_group,
                   strftime(max(period), '%Y-%m') AS latest_month,
                   round(arg_max(petroleum_bpd, period), 0) AS latest_petroleum_bpd
            FROM field_group_month GROUP BY field_group
        )
        SELECT l.field_group, l.latest_month, l.latest_petroleum_bpd,
               p.matched_fields, left(p.grouping_reason, 300) AS grouping_reason
        FROM per_group p JOIN latest l USING (field_group)
        ORDER BY l.latest_petroleum_bpd DESC
        LIMIT ?
        """,
        [text, limit],
    )
    return {"matches": found, "unit": "bbl/d (oil + condensate)"}


@mcp.tool()
def field_group_production(field_group: str, start: str = "2024-01", end: str = "2026-12") -> dict:
    """Monthly petroleum production (bbl/d) and active wells of ONE field group.

    Use search_field_groups first if unsure of the exact name. Months are YYYY-MM.
    Petroleum = oil + condensate, daily rate averaged over the month.
    """
    con = connect()
    series = rows(
        con,
        """
        SELECT strftime(period, '%Y-%m') AS month,
               round(petroleum_bpd, 0) AS petroleum_bpd,
               active_wells
        FROM field_group_month
        WHERE field_group = ?
          AND period BETWEEN strptime(?, '%Y-%m') AND strptime(?, '%Y-%m')
        ORDER BY period
        """,
        [field_group, start, end],
    )
    return {"field_group": field_group, "unit": "bbl/d", "months": series}


@mcp.tool()
def top_changes(months: int = 6, n: int = 10, direction: str = "down") -> dict:
    """Field groups whose production changed the most recently.

    Method: average petroleum (bbl/d) in the last `months` months of data vs the
    `months` months before that. direction='down' lists the largest falls,
    'up' the largest rises. Groups below 1,000 bbl/d in both windows are ignored.
    """
    order = "ASC" if direction == "down" else "DESC"
    con = connect()
    latest = con.execute("SELECT max(period) FROM field_group_month").fetchone()[0]
    result = rows(
        con,
        f"""
        WITH w AS (
            SELECT field_group,
                   avg(petroleum_bpd) FILTER (
                       WHERE period > ?::DATE - INTERVAL (?) MONTH) AS recent,
                   avg(petroleum_bpd) FILTER (
                       WHERE period <= ?::DATE - INTERVAL (?) MONTH
                         AND period > ?::DATE - INTERVAL (2 * ?) MONTH) AS before
            FROM field_group_month
            GROUP BY field_group
        )
        SELECT field_group,
               round(before, 0) AS before_bpd,
               round(recent, 0) AS recent_bpd,
               round(recent - before, 0) AS change_bpd,
               round(100 * (recent - before) / nullif(before, 0), 1) AS change_pct
        FROM w
        WHERE greatest(coalesce(recent, 0), coalesce(before, 0)) >= 1000
        ORDER BY change_bpd {order}
        LIMIT ?
        """,
        [latest, months, latest, months, latest, months, n],
    )
    return {
        "latest_month": latest.strftime("%Y-%m"),
        "window_months": months,
        "method": "mean of last window vs mean of the previous window of the same length",
        "groups": result,
    }


@mcp.tool()
def national_reconciliation(details: bool = False) -> dict:
    """Trust check: does the sum of all wells reproduce ANP's official national total?

    Returns a summary (months compared, months outside +/-1%, worst month).
    Set details=True to also get every month (long output; use only if needed).
    """
    con = connect()
    summary = rows(
        con,
        """
        SELECT count(*) AS months_compared,
               sum(flag::INT) AS months_outside_1pct,
               strftime(arg_max(period, abs(rel_diff_total)), '%Y-%m') AS worst_month,
               round(100 * arg_max(rel_diff_total, abs(rel_diff_total)), 3) AS worst_gap_pct,
               strftime(min(period), '%Y-%m') AS first_month,
               strftime(max(period), '%Y-%m') AS last_month
        FROM national_check
        """,
    )[0]
    out = {
        "summary": summary,
        "definition": "gap = wells / national - 1 (petroleum, bbl/d)",
        "what_it_shows": "The sum of all wells reproduces ANP's official national total "
        "month by month, so no large volume is missing or double counted in total.",
        "what_it_does_not_show": "It checks totals only: errors in individual fields or "
        "wells that offset each other would not appear. It is not an audit by ANP.",
        "provenance": "Independent harmonisation of ANP open data; not an official ANP product.",
    }
    if details:
        out["months"] = rows(
            con,
            """
            SELECT strftime(period, '%Y-%m') AS month,
                   round(100 * rel_diff_total, 3) AS gap_pct, flag AS outside_1pct
            FROM national_check ORDER BY period
            """,
        )
    return out


@mcp.tool()
def national_production(
    start: str | None = None, end: str | None = None, frequency: str = "month"
) -> dict:
    """Brazil's total petroleum production from ANP's official national series (bbl/d).

    Petroleum = oil + condensate, daily rate, split into onshore and offshore. The series
    starts in 2005-01. frequency='month' gives each month (default range: from 2024-01);
    frequency='year' gives each year's day-weighted average (default range: whole series)
    with the months available, so an incomplete year (e.g. the current one) is flagged and
    should not be compared with full years. Months are YYYY-MM.
    """
    yearly = frequency == "year"
    start = start or ("2005-01" if yearly else "2024-01")
    end = end or "2100-12"
    con = connect()
    window = "period BETWEEN strptime(?, '%Y-%m') AND strptime(?, '%Y-%m')"
    if yearly:
        days = "day(last_day(period))"
        series = rows(
            con,
            f"""
            SELECT year(period) AS year,
                   count(*) AS months_available,
                   count(*) = 12 AS complete_year,
                   round(sum(national_total * {days}) / sum({days}), 0) AS petroleum_bpd,
                   round(sum(national_terra * {days}) / sum({days}), 0) AS onshore_bpd,
                   round(sum(national_mar * {days}) / sum({days}), 0) AS offshore_bpd
            FROM national WHERE {window}
            GROUP BY 1 ORDER BY 1
            """,
            [start, end],
        )
        return {"frequency": "year", "unit": "bbl/d (day-weighted yearly average)",
                "source": "ANP national series by state and location", "years": series}
    series = rows(
        con,
        f"""
        SELECT strftime(period, '%Y-%m') AS month,
               round(national_total, 0) AS petroleum_bpd,
               round(national_terra, 0) AS onshore_bpd,
               round(national_mar, 0) AS offshore_bpd
        FROM national WHERE {window}
        ORDER BY period
        """,
        [start, end],
    )
    return {"frequency": "month", "unit": "bbl/d",
            "source": "ANP national series by state and location", "months": series}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
