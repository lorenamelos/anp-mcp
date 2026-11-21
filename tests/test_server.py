"""Tests for the tools, called as plain Python functions."""

import pytest


def names(result: dict, key: str = "groups") -> list[str]:
    return [g["field_group"] for g in result[key]]


# search_field_groups


@pytest.mark.parametrize("text", ["buzios", "BUZIOS", "Búzios", "uzi"])
def test_search_is_case_and_accent_insensitive(server, text):
    result = server.search_field_groups(text)
    assert names(result, "matches") == ["BÚZIOS"]
    match = result["matches"][0]
    assert match["latest_month"] == "2026-09"
    assert match["latest_petroleum_bpd"] == 1_050_000


def test_search_orders_by_latest_production_and_respects_limit(server):
    result = server.search_field_groups("", limit=3)
    assert names(result, "matches") == ["BÚZIOS", "MERO", "TUPI"]


def test_search_no_match(server):
    assert server.search_field_groups("xyz")["matches"] == []


def test_search_finds_a_field_inside_a_group(server):
    result = server.search_field_groups("lula")
    assert names(result, "matches") == ["TUPI"]
    match = result["matches"][0]
    assert match["matched_fields"] == ["LULA"]
    assert "Renamed LULA -> TUPI" in match["grouping_reason"]


def test_search_by_group_name_lists_matching_member_fields(server):
    match = server.search_field_groups("tupi")["matches"][0]
    assert match["field_group"] == "TUPI"
    assert match["matched_fields"] == ["ANC_TUPI"]  # the group's own name is not repeated


def test_search_ungrouped_field_has_no_reason(server):
    match = server.search_field_groups("mero")["matches"][0]
    assert match["matched_fields"] == []
    assert match["grouping_reason"] is None


# field_group_production


def test_production_respects_start_and_end(server):
    result = server.field_group_production("MERO", start="2026-02", end="2026-05")
    assert result["field_group"] == "MERO"
    assert [m["month"] for m in result["months"]] == ["2026-02", "2026-03", "2026-04", "2026-05"]
    assert [m["petroleum_bpd"] for m in result["months"]] == [600_000, 600_000, 540_000, 540_000]
    assert all(m["active_wells"] == 10 for m in result["months"])


def test_production_single_month(server):
    result = server.field_group_production("TUPI", start="2026-09", end="2026-09")
    assert result["months"] == [{"month": "2026-09", "petroleum_bpd": 300_000, "active_wells": 10}]


def test_production_default_range(server):
    months = [m["month"] for m in server.field_group_production("TUPI")["months"]]
    assert months[0] == "2025-01" and months[-1] == "2026-09"  # all synthetic data is in range


def test_production_unknown_group(server):
    assert server.field_group_production("NOPE")["months"] == []


# top_changes


def test_top_changes_windows_and_percentages(server):
    result = server.top_changes(months=6, n=10, direction="up")
    assert result["latest_month"] == "2026-09"
    assert result["window_months"] == 6
    by_name = {g["field_group"]: g for g in result["groups"]}
    assert by_name["BÚZIOS"] == {
        "field_group": "BÚZIOS",
        "before_bpd": 800_000,
        "recent_bpd": 1_000_000,
        "change_bpd": 200_000,
        "change_pct": 25.0,
    }
    assert by_name["MERO"]["change_pct"] == -10.0
    assert by_name["TUPI"]["change_bpd"] == -100_000
    assert by_name["TUPI"]["change_pct"] == -25.0


def test_top_changes_shorter_window(server):
    # months=3: recent = Jul..Sep 2026 (1,010k, 1,030k, 1,050k), before = Apr..Jun (950k..990k)
    result = server.top_changes(months=3, n=1, direction="up")
    assert result["groups"] == [
        {
            "field_group": "BÚZIOS",
            "before_bpd": 970_000,
            "recent_bpd": 1_030_000,
            "change_bpd": 60_000,
            "change_pct": 6.2,
        }
    ]


def test_top_changes_direction(server):
    down = names(server.top_changes(direction="down"))
    up = names(server.top_changes(direction="up"))
    assert down == ["TUPI", "MERO", "LIMIAR", "BÚZIOS"]
    assert up == list(reversed(down))
    assert names(server.top_changes(direction="down", n=2)) == ["TUPI", "MERO"]


def test_top_changes_floor(server):
    groups = names(server.top_changes(n=100))
    assert "PEQUENO" not in groups  # below 1,000 bbl/d in both windows
    assert "LIMIAR" in groups  # exactly 1,000 bbl/d in one window


# national_reconciliation


def test_reconciliation_summary_only_by_default(server):
    result = server.national_reconciliation()
    assert "months" not in result
    assert result["summary"] == {
        "months_compared": 4,
        "months_outside_1pct": 2,
        "worst_month": "2026-08",
        "worst_gap_pct": -2.05,
        "first_month": "2026-06",
        "last_month": "2026-09",
    }


def test_reconciliation_states_scope_and_provenance(server):
    result = server.national_reconciliation()
    assert "not an audit" in result["what_it_does_not_show"]
    assert "not an official" in result["provenance"]


def test_reconciliation_details(server):
    result = server.national_reconciliation(details=True)
    assert "summary" in result
    assert result["months"] == [
        {"month": "2026-06", "gap_pct": 0.52, "outside_1pct": False},
        {"month": "2026-07", "gap_pct": 1.52, "outside_1pct": True},
        {"month": "2026-08", "gap_pct": -2.05, "outside_1pct": True},
        {"month": "2026-09", "gap_pct": -0.3, "outside_1pct": False},
    ]


# national_production


def test_national_monthly(server):
    result = server.national_production(start="2026-07", end="2026-08")
    assert result["months"] == [
        {"month": "2026-07", "petroleum_bpd": 1000, "onshore_bpd": 100, "offshore_bpd": 900},
        {"month": "2026-08", "petroleum_bpd": 1000, "onshore_bpd": 100, "offshore_bpd": 900},
    ]


def test_national_yearly_flags_incomplete_year(server):
    result = server.national_production(frequency="year")
    assert result["years"] == [
        {"year": 2026, "months_available": 4, "complete_year": False,
         "petroleum_bpd": 1000, "onshore_bpd": 100, "offshore_bpd": 900},
    ]


def test_server_declares_its_scope(server):
    assert "Not covered: forecasts, royalties, natural gas" in server.INSTRUCTIONS
