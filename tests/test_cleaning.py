"""Tests for the cleaning rules in src/cleaning.py.

Run:  pytest
Each test builds a tiny DataFrame by hand, so it's obvious what the right
answer should be.
"""
import pandas as pd

from cleaning import (
    add_season,
    blank_to_missing,
    classify_resolution,
    clean_borough,
    drop_duplicate_keys,
    flag_extreme,
    parse_timestamps,
)

NOW = pd.Timestamp("2026-10-10")


def make_requests(created_closed_pairs):
    """Build a parsed DataFrame from (created_date, closed_date) strings."""
    df = pd.DataFrame(created_closed_pairs, columns=["created_date", "closed_date"])
    return parse_timestamps(blank_to_missing(df))


def test_blank_strings_become_missing():
    df = blank_to_missing(pd.DataFrame({"borough": ["BRONX", "", "   "]}))
    assert df["borough"].isna().tolist() == [False, True, True]


def test_parse_timestamps_handles_good_and_bad_values():
    df = make_requests([
        ("2025-03-01T10:00:00.000", "2025-03-01T12:30:00.000"),
        ("2025-03-01T10:00:00.000", "not a date"),
    ])
    assert df.loc[0, "closed_at"] == pd.Timestamp("2025-03-01 12:30:00")
    assert pd.isna(df.loc[1, "closed_at"])  # bad value -> NaT, no crash


def test_duplicate_keys_keep_most_recently_updated():
    df = pd.DataFrame({
        "unique_key": ["1", "1", "2"],
        "resolution_action_updated_date": ["2025-01-01", "2025-02-01", "2025-01-01"],
        "status": ["Open", "Closed", "Closed"],
    })
    clean, dropped = drop_duplicate_keys(df)
    assert dropped == 1
    assert len(clean) == 2
    assert clean.loc[clean["unique_key"] == "1", "status"].item() == "Closed"


def test_classify_resolution_covers_every_case():
    df = make_requests([
        ("2025-03-01T10:00:00.000", "2025-03-01T12:30:00.000"),  # normal
        ("2025-03-01T10:00:00.000", ""),                         # still open
        ("2025-03-05T10:00:00.000", "2025-03-01T10:00:00.000"),  # closed before created
        ("2025-03-01T10:00:00.000", "1900-01-01T00:00:00.000"),  # placeholder
        ("2025-03-01T10:00:00.000", "2030-01-01T00:00:00.000"),  # in the future
        ("2025-03-01T10:00:00.000", "2025-03-01T10:00:00.000"),  # closed instantly
    ])
    df = classify_resolution(df, now=NOW)

    assert df["resolution_status"].tolist() == [
        "closed", "no_close_date", "closed_before_created",
        "placeholder_date", "placeholder_date", "closed",
    ]
    assert df.loc[0, "resolution_hours"] == 2.5
    # Only valid closed rows get a resolution time.
    assert df["resolution_hours"].isna().tolist() == [False, True, True, True, True, False]
    assert df["is_zero_duration"].tolist() == [False, False, False, False, False, True]


def test_clean_borough_standardizes_unknowns():
    df = clean_borough(pd.DataFrame({"borough": ["BROOKLYN", "Unspecified", pd.NA]}))
    assert df["borough"].tolist() == ["BROOKLYN", "UNSPECIFIED", "UNSPECIFIED"]
    assert df["is_borough_known"].tolist() == [True, False, False]


def test_add_season_uses_meteorological_seasons():
    dates = ["2025-01-15", "2025-03-01", "2025-06-30", "2025-11-30", "2025-12-01"]
    df = add_season(pd.DataFrame({"created_at": pd.to_datetime(dates)}))
    assert df["season"].tolist() == ["Winter", "Spring", "Summer", "Fall", "Winter"]


def test_flag_extreme_is_per_complaint_type_and_keeps_rows():
    # Type A: hours 1-8, plus one 100-hour request.
    # Q1 = 3, Q3 = 7, IQR = 4 -> fence = 7 + 3 * 4 = 19
    # Type B: always around 100 hours, so 100 is normal there.
    df = pd.DataFrame({
        "complaint_type": ["A"] * 9 + ["B"] * 3 + ["A"],
        "resolution_hours": [1, 2, 3, 4, 5, 6, 7, 8, 100] + [95, 100, 105] + [None],
    })
    out = flag_extreme(df)

    assert len(out) == len(df)  # nothing dropped
    assert out["is_extreme"].sum() == 1
    assert out.loc[8, "is_extreme"]           # 100h is extreme for A
    assert not out.loc[10, "is_extreme"]      # 100h is normal for B
    assert not out.loc[12, "is_extreme"]      # missing hours never flagged
