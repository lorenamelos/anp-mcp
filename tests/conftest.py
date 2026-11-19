"""Synthetic dataset for the tests: small, hand-made numbers, no real ANP data."""

import importlib
from datetime import date

import duckdb
import pytest

# Monthly petroleum (bbl/d) per field group, latest month 2026-09.
# With months=6: recent = 2026-04..2026-09, before = 2025-10..2026-03.
# Months older than 2025-10 get a distinct value so a window leak shows up.
MONTHS = [date(2025, m, 1) for m in range(1, 13)] + [date(2026, m, 1) for m in range(1, 10)]
OLD = MONTHS[:9]  # 2025-01..2025-09
BEFORE = MONTHS[9:15]  # 2025-10..2026-03
RECENT = MONTHS[15:]  # 2026-04..2026-09

SERIES = {
    # mean before 800,000 -> mean recent 1,000,000 (+25.0%)
    "BÚZIOS": [100_000] * 9
    + [775_000, 785_000, 795_000, 805_000, 815_000, 825_000]
    + [950_000, 970_000, 990_000, 1_010_000, 1_030_000, 1_050_000],
    # 600,000 -> 540,000 (-10.0%)
    "MERO": [50_000] * 9 + [600_000] * 6 + [540_000] * 6,
    # 400,000 -> 300,000 (-25.0%)
    "TUPI": [50_000] * 9 + [400_000] * 6 + [300_000] * 6,
    # exactly at the floor before: 1,000 -> 400 (kept)
    "LIMIAR": [5_000] * 9 + [1_000] * 6 + [400] * 6,
    # below the floor in both windows (ignored), but large in old months
    "PEQUENO": [50_000] * 9 + [900] * 6 + [500] * 6,
}

NATIONAL = [
    # period, national_total, wells_total, rel_diff_total, flag
    ("2026-06-01", 1000.0, 1005.2, 0.0052, False),
    ("2026-07-01", 1000.0, 1015.2, 0.0152, True),
    ("2026-08-01", 1000.0, 979.5, -0.0205, True),
    ("2026-09-01", 1000.0, 997.0, -0.003, False),
]


@pytest.fixture
def server(tmp_path, monkeypatch):
    """The server module with ANP_MCP_DATA_DIR pointing to a synthetic dataset."""
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE fgm (field_group VARCHAR, period DATE, petroleum_bpd DOUBLE, active_wells INT)"
    )
    con.executemany(
        "INSERT INTO fgm VALUES (?, ?, ?, ?)",
        [
            (group, month, bpd, 10)
            for group, values in SERIES.items()
            for month, bpd in zip(MONTHS, values, strict=True)
        ],
    )
    con.execute(f"COPY fgm TO '{tmp_path / 'field_group_month.parquet'}' (FORMAT parquet)")

    con.execute(
        "CREATE TABLE nat (period DATE, national_total DOUBLE, wells_total DOUBLE, "
        "rel_diff_total DOUBLE, flag BOOLEAN)"
    )
    con.executemany("INSERT INTO nat VALUES (?, ?, ?, ?, ?)", NATIONAL)
    con.execute(f"COPY nat TO '{tmp_path / 'national_vs_fields.csv'}' (HEADER)")
    con.close()

    monkeypatch.setenv("ANP_MCP_DATA_DIR", str(tmp_path))
    from anp_mcp import server

    return importlib.reload(server)  # DATA_DIR is read at import time
