"""anp-mcp: an MCP server for ANP oil production data (open, read-only)."""

import os
from pathlib import Path

import duckdb
from huggingface_hub import hf_hub_download
from mcp.server import MCPServer

# Open dataset on the Hugging Face Hub, pinned to one version so answers do not change
# silently when a new month is published.
DATASET_REPO = os.environ.get("ANP_MCP_DATASET", "lorenamelos/anp-oil-production")
DATASET_REVISION = os.environ.get("ANP_MCP_REVISION", "v2026.09")
LOCAL_DIR = os.environ.get("ANP_MCP_DATA_DIR")  # optional: read local files (development)


def data_file(name: str) -> str:
    """Path of a dataset file: local if ANP_MCP_DATA_DIR is set, else downloaded once from the Hub and cached."""
    if LOCAL_DIR:
        return str(Path(LOCAL_DIR).expanduser() / name)
    return hf_hub_download(
        repo_id=DATASET_REPO, filename=name, repo_type="dataset", revision=DATASET_REVISION
    )


mcp = MCPServer("anp-mcp")


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
    return con


def rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict]:
    """Run a query and return a list of dicts (column name -> value)."""
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


@mcp.tool()
def search_field_groups(text: str, limit: int = 10) -> dict:
    """Find oil field groups by name (case and accent insensitive), e.g. 'buzios', 'tupi'.

    A field group is the physical/economic unit used in the analysis: fields renamed,
    merged or split by contract are grouped (e.g. TUPI includes LULA). Returns each
    match with its latest month and petroleum production (bbl/d).
    """
    con = connect()
    found = rows(
        con,
        """
        SELECT field_group,
               strftime(max(period), '%Y-%m') AS latest_month,
               round(arg_max(petroleum_bpd, period), 0) AS latest_petroleum_bpd
        FROM field_group_month
        WHERE strip_accents(lower(field_group)) LIKE '%' || strip_accents(lower(?)) || '%'
        GROUP BY field_group
        ORDER BY latest_petroleum_bpd DESC
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
    out = {"summary": summary, "definition": "gap = wells / national - 1 (petroleum, bbl/d)"}
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
