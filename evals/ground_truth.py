"""Ground truth for the evals, computed from the same dataset version the server reads.

Answers are never typed by hand: each key below is a query on the dataset files, so a new
dataset version updates the expected answers automatically. Run it to print the table:

    uv run python -m evals.ground_truth
"""

import duckdb

from anp_mcp.server import DATASET_REVISION, data_file

BBL_TO_M3 = 0.158987  # 1 barrel = 0.158987 m³


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"""
        CREATE VIEW g AS
        SELECT field_group, CAST(period AS DATE) AS period, petroleum_bpd, active_wells
        FROM read_parquet('{data_file("field_group_month.parquet")}')
    """)
    con.execute(f"""
        CREATE VIEW n AS SELECT * FROM read_csv_auto('{data_file("national_check.csv")}')
    """)
    return con


def one(con: duckdb.DuckDBPyConnection, sql: str):
    return con.execute(sql).fetchone()[0]


def ranking(con: duckdb.DuckDBPyConnection, months: int, n: int, direction: str) -> list[str]:
    """Same method as the top_changes tool: mean of the last window vs the previous one."""
    order = "ASC" if direction == "down" else "DESC"
    rows = con.execute(f"""
        WITH latest AS (SELECT max(period) AS p FROM g),
        w AS (
            SELECT field_group,
                   avg(petroleum_bpd) FILTER (WHERE period > p - INTERVAL ({months}) MONTH) AS recent,
                   avg(petroleum_bpd) FILTER (WHERE period <= p - INTERVAL ({months}) MONTH
                                                AND period > p - INTERVAL ({2 * months}) MONTH) AS before
            FROM g, latest GROUP BY field_group
        )
        SELECT field_group FROM w
        WHERE greatest(coalesce(recent, 0), coalesce(before, 0)) >= 1000
        ORDER BY recent - before {order} LIMIT {n}
    """).fetchall()
    return [r[0] for r in rows]


def compute() -> dict:
    con = connect()
    t = {"dataset_revision": DATASET_REVISION}
    t["latest_month"] = one(con, "SELECT strftime(max(period), '%Y-%m') FROM g")
    t["buzios_2026_09_bpd"] = one(con, "SELECT petroleum_bpd FROM g WHERE field_group='BÚZIOS' AND period='2026-09-01'")
    t["mero_2025_01_bpd"] = one(con, "SELECT petroleum_bpd FROM g WHERE field_group='MERO' AND period='2025-01-01'")
    t["tupi_latest_active_wells"] = one(con, "SELECT arg_max(active_wells, period) FROM g WHERE field_group='TUPI'")
    t["tupi_2025_mean_bpd"] = one(con, "SELECT avg(petroleum_bpd) FROM g WHERE field_group='TUPI' AND year(period)=2025")
    t["buzios_latest_bpd"] = one(con, "SELECT arg_max(petroleum_bpd, period) FROM g WHERE field_group='BÚZIOS'")
    t["top5_up_6m"] = ranking(con, 6, 5, "up")
    t["top5_down_12m"] = ranking(con, 12, 5, "down")
    t["bacalhau_change_6m_pct"] = one(con, """
        WITH latest AS (SELECT max(period) AS p FROM g)
        SELECT 100 * (avg(petroleum_bpd) FILTER (WHERE period > p - INTERVAL 6 MONTH)
                    / avg(petroleum_bpd) FILTER (WHERE period <= p - INTERVAL 6 MONTH
                                                   AND period > p - INTERVAL 12 MONTH) - 1)
        FROM g, latest WHERE field_group='BACALHAU'""")
    t["buzios_2026_mean_bpd"] = one(con, "SELECT avg(petroleum_bpd) FROM g WHERE field_group='BÚZIOS' AND year(period)=2026")
    t["mero_2026_mean_bpd"] = one(con, "SELECT avg(petroleum_bpd) FROM g WHERE field_group='MERO' AND year(period)=2026")
    t["buzios_first_month_above_1m"] = one(con, "SELECT strftime(min(period), '%Y-%m') FROM g WHERE field_group='BÚZIOS' AND petroleum_bpd > 1e6")
    t["tupi_peak_month"] = one(con, "SELECT strftime(arg_max(period, petroleum_bpd), '%Y-%m') FROM g WHERE field_group='TUPI'")
    t["mero_2026_08_total_bbl"] = one(con, "SELECT petroleum_bpd * 31 FROM g WHERE field_group='MERO' AND period='2026-08-01'")
    t["buzios_latest_m3d"] = t["buzios_latest_bpd"] * BBL_TO_M3
    t["recon_months"] = one(con, "SELECT count(*) FROM n")
    t["recon_months_outside_1pct"] = one(con, "SELECT sum(flag::INT) FROM n")
    t["recon_worst_month"] = one(con, "SELECT strftime(arg_max(period, abs(rel_diff_total)), '%Y-%m') FROM n")
    t["recon_worst_gap_pct"] = one(con, "SELECT 100 * arg_max(rel_diff_total, abs(rel_diff_total)) FROM n")
    # National record: the series exists in the dataset but no tool exposes it (yet).
    t["national_record_full_year"] = one(con, """
        SELECT arg_max(y, v) FROM (SELECT year(period) y, avg(national_total) v, count(*) m
                                   FROM n GROUP BY 1) WHERE m = 12""")
    t["national_record_month"] = one(con, "SELECT strftime(arg_max(period, national_total), '%Y-%m') FROM n")
    for year in (2023, 2024, 2025):
        t[f"national_{year}_mean_bpd"] = one(con, f"SELECT avg(national_total) FROM n WHERE year(period)={year}")
    t["national_first_month"] = one(con, "SELECT strftime(min(period), '%Y-%m') FROM n")
    return t


if __name__ == "__main__":
    for key, value in compute().items():
        print(f"{key:32} {value}")
