"""Tests for the Phase 1 checks in forecast/daily_series.py (synthetic series, no database)."""
import numpy as np
import pandas as pd
import pytest

import fc_common as fc
from daily_series import (check_calendar, complete_weeks, expected_clock_hours, flag_outliers, hotspot_table,
                  outlier_episodes, same_weekday_baseline, weekday_profile)

WEEK_SHAPE = np.array([1.1, 1.05, 1.0, 1.0, 1.0, 0.9, 0.95])  # Mon..Sun multipliers


def weekly_series(start="2025-01-06", weeks=10, level=1000, noise=0.0, seed=0):
    """A daily series with a fixed weekly shape, starting on a Monday."""
    days = pd.date_range(start, periods=7 * weeks, freq="D")
    rng = np.random.default_rng(seed)
    values = level * WEEK_SHAPE[days.dayofweek] * (1 + noise * rng.standard_normal(len(days)))
    return pd.Series(values, index=days)


def test_dst_days_have_the_right_number_of_clock_hours():
    assert expected_clock_hours("2025-03-09") == 23  # spring forward: 02:00 doesn't exist
    assert expected_clock_hours("2025-11-02") == 24  # fall back: 01:00 repeats, still 24 distinct
    assert expected_clock_hours("2025-06-01") == 24


def test_check_calendar_finds_missing_zero_and_partial_days():
    daily = pd.DataFrame({
        "day": pd.to_datetime(["2025-06-01", "2025-06-02", "2025-06-04", "2025-06-05"]),  # 06-03 missing
        "requests": [100, 0, 90, 40],
        "hours_with_data": [24, 0, 24, 10],  # 06-05 stops after 10 hours
        "first_created": pd.to_datetime(["2025-06-01 00:01", None, "2025-06-04 00:02", "2025-06-05 00:03"]),
        "last_created": pd.to_datetime(["2025-06-01 23:59", None, "2025-06-04 23:58", "2025-06-05 09:40"]),
    })
    out = check_calendar(daily, "2025-06-01", "2025-06-05")
    assert [str(d) for d in out["days_missing_from_calendar"]] == ["2025-06-03"]
    assert [str(d) for d in out["days_with_zero_requests"]] == ["2025-06-02"]
    assert out["days_missing_clock_hours"] == {"2025-06-05": 14}


def test_baseline_uses_same_weekday_neighbours_and_skips_the_day_itself():
    s = weekly_series()
    s.iloc[35] *= 5  # a huge spike must not move its own baseline
    base = same_weekday_baseline(s)
    assert base.iloc[35] == pytest.approx(s.iloc[28])  # neighbours are all equal on that weekday
    # Saturday's baseline comes from Saturdays only
    sat = s.index.dayofweek == 5
    assert np.allclose(base[sat].dropna(), 1000 * WEEK_SHAPE[5])


def test_baseline_needs_enough_neighbours_and_works_on_tables():
    s = weekly_series(weeks=3)  # each day has at most 2 same-weekday neighbours
    assert same_weekday_baseline(s).isna().all()
    s = weekly_series()
    table = pd.DataFrame({"a": s, "b": 2 * s})
    out = same_weekday_baseline(table)
    pd.testing.assert_series_equal(out["a"], same_weekday_baseline(s), check_names=False)
    pd.testing.assert_series_equal(out["b"], 2 * same_weekday_baseline(s), check_names=False)


def test_flag_outliers_finds_a_spike_and_a_dip_only():
    s = weekly_series(noise=0.03, seed=1)
    s.iloc[30] *= 1.6   # +60%
    s.iloc[45] *= 0.5   # -50%
    flags = flag_outliers(s)
    assert set(flags.index[flags["is_outlier"]]) == {s.index[30], s.index[45]}
    assert flags["robust_z"].iloc[30] > 0 > flags["robust_z"].iloc[45]


def test_complete_weeks_drops_partial_first_and_last_weeks():
    s = pd.Series(1, index=pd.date_range("2025-01-01", "2025-12-31", freq="D"))  # Wed to Wed
    weekly, partial = complete_weeks(s)
    assert partial == ["2024-12-30", "2025-12-29"]
    assert (weekly == 7).all() and len(weekly) == 51
    assert weekly.index[0] == pd.Timestamp("2025-01-06")  # first full Monday


def test_weekday_profile_recovers_the_weekly_shape():
    profile = weekday_profile(weekly_series())
    expected = 100 * (WEEK_SHAPE / WEEK_SHAPE.mean() - 1)
    assert np.allclose(profile.to_numpy(), expected)
    assert list(profile.index) == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def outlier(day, z, requests=150, baseline=100, holiday=None, hotspot=0, driver="Noise"):
    return {"day": pd.Timestamp(day).date(), "robust_z": z, "requests": requests, "baseline": baseline,
            "pct_vs_baseline": 100 * (requests / baseline - 1), "holiday": holiday,
            "top_complaint_type": driver, "hotspot_requests": hotspot}


def test_outlier_episodes_group_nearby_days_and_prefer_holiday_names():
    rows = outlier_episodes([
        outlier("2025-12-23", -4, 70), outlier("2025-12-25", -8, 50, holiday="Christmas Day"),
        outlier("2025-12-27", -4, 75),
        outlier("2025-06-24", 5, 160, driver="Water System"),
        outlier("2025-01-05", 7, 200, hotspot=90), outlier("2025-01-07", 5, 150, hotspot=30),
    ])
    assert [(str(r["start"]), str(r["end"]), r["n_outlier_days"]) for r in rows] == [
        ("2025-01-05", "2025-01-07", 2), ("2025-06-24", "2025-06-24", 1), ("2025-12-23", "2025-12-27", 3)]
    jan, jun, dec = rows
    assert dec["main_driver"] == "Christmas Day" and dec["direction"] == "low"
    assert dec["hotspot_share_of_excess_pct"] is None  # a dip has no "excess" to explain
    assert jun["main_driver"] == "Water System" and jun["peak_pct_vs_baseline"] == pytest.approx(60)
    assert jan["excess_requests"] == 150 and jan["hotspot_share_of_excess_pct"] == pytest.approx(80)


def test_hotspot_table_fills_zeros_and_total_counts_every_agency():
    days = pd.date_range("2025-01-01", periods=3, freq="D")
    hot = pd.DataFrame({"day": [days[0], days[0], days[2]], "agency": ["NYPD", "DOB", "NYPD"],
                        "hotspot_requests": [500, 120, 300]})
    out = hotspot_table(hot, days, [fc.TOTAL, "NYPD", "HPD"])
    assert out[fc.TOTAL].tolist() == [620, 0, 300]  # DOB isn't a column but still counts in Total
    assert out["NYPD"].tolist() == [500, 0, 300]
    assert out["HPD"].tolist() == [0, 0, 0]
