"""Tests for build_survival_table in analysis/survival.py."""
import pandas as pd

from survival import build_survival_table

SNAPSHOT = pd.Timestamp("2026-01-01 00:00:00")


def test_events_censoring_and_exclusions():
    df = pd.DataFrame({
        "created_at": pd.to_datetime(["2025-12-31 00:00", "2025-12-31 00:00", "2025-12-31 00:00",
                                      "2025-12-31 00:00", "2025-12-31 00:00"]),
        "resolution_status": ["closed", "no_close_date", "no_close_date",
                              "closed_before_created", "placeholder_date"],
        "status": ["Closed", "Open", "Closed", "Closed", "Closed"],
        "resolution_hours": [5.0, None, None, None, None],
    })
    table, counts = build_survival_table(df, SNAPSHOT)

    # Closed -> event with its own time; still open -> censored at the snapshot (24 h later)
    assert table["event"].tolist() == [1, 0]
    assert table["duration_hours"].tolist() == [5.0, 24.0]
    # "Closed" with no date, closed-before-created and placeholder are left out and counted
    assert counts == {
        "rows_in_clean_table": 5, "events_closed": 1, "censored_still_open": 1,
        "excluded_closed_status_without_date": 1, "excluded_closed_before_created": 1,
        "excluded_placeholder_date": 1, "rows_used": 2,
    }
