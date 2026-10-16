"""Tests for forecast/models.py and the error diagnosis in forecast/evaluate_models.py."""
import numpy as np
import pandas as pd
import pytest

import backtest as bt
import models
from evaluate_models import error_sources

DAYS = pd.date_range("2025-01-01", "2025-08-31", freq="D")
COUNTER = pd.Series(np.arange(len(DAYS), dtype=float), index=DAYS)
SHAPE = np.array([1.1, 1.05, 1.0, 1.0, 1.0, 0.9, 0.95])


def weekly(level=1000.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    return pd.Series(level * SHAPE[DAYS.dayofweek] * (1 + noise * rng.standard_normal(len(DAYS))), index=DAYS)


def test_direct_features_use_the_latest_observed_same_weekday():
    t = pd.Timestamp("2025-06-04")
    f = models.direct_features(COUNTER, [t]).set_index("horizon")
    for h in range(1, 15):
        day = t + pd.DateOffset(days=h)
        back = 7 if h <= 7 else 14            # day-7 isn't observed yet when h > 7
        assert f.loc[h, "day"] == day
        assert f.loc[h, "lag_7"] == COUNTER[day - pd.DateOffset(days=back)]
        assert f.loc[h, "lag_14"] == COUNTER[day - pd.DateOffset(days=back + 7)]
        assert COUNTER.index[int(f.loc[h, "lag_7"])] <= t   # every lag is on or before the origin
    assert f["roll_7"].iloc[0] == pytest.approx(COUNTER[t - pd.DateOffset(days=6):t].mean())


def test_direct_features_ignore_everything_after_the_origin():
    t = pd.Timestamp("2025-06-04")
    changed = COUNTER.copy()
    changed[t + pd.DateOffset(days=1):] = -999.0   # rewrite the future
    cols = ["lag_7", "lag_14", "roll_7", "day_of_week", "month", "is_holiday"]
    pd.testing.assert_frame_equal(models.direct_features(COUNTER, [t])[cols],
                                  models.direct_features(changed, [t])[cols])


def test_holiday_flag():
    cal = models.calendar(pd.to_datetime(["2025-12-24", "2025-12-25", "2025-07-04"]))
    assert cal["is_holiday"].tolist() == [0, 1, 1]


def test_gradient_boosting_learns_a_weekly_pattern_and_is_reproducible():
    y = weekly(noise=0.02, seed=3)
    cutoff = pd.Timestamp("2025-08-13")
    run1 = bt.run_backtest(y, models.gradient_boosting(), [cutoff])
    run2 = bt.run_backtest(y, models.gradient_boosting(), [cutoff])
    assert np.array_equal(run1["forecast"], run2["forecast"])        # fixed seed
    assert bt.mape(run1["actual"], run1["forecast"]) < 5


def test_gradient_boosting_never_trains_on_days_after_the_cutoff():
    y = weekly(noise=0.02, seed=4)
    cutoff = pd.Timestamp("2025-08-13")
    poisoned = y.copy()
    poisoned[cutoff + pd.DateOffset(days=1):] *= 100   # if the model peeked, forecasts would explode
    a = bt.run_backtest(y, models.gradient_boosting(), [cutoff])["forecast"]
    b = bt.run_backtest(poisoned, models.gradient_boosting(), [cutoff])["forecast"]
    assert np.allclose(a, b)


def test_holt_winters_recovers_a_clean_weekly_pattern():
    y = weekly()
    res = bt.run_backtest(y, models.holt_winters(trend=None, seasonal="add"), [pd.Timestamp("2025-08-13")])
    assert bt.mape(res["actual"], res["forecast"]) < 1


def backtest_rows(cutoff, ratio):
    days = pd.date_range(pd.Timestamp(cutoff) + pd.DateOffset(days=1), periods=14, freq="D")
    return pd.DataFrame({"cutoff": pd.Timestamp(cutoff), "day": days, "horizon": range(1, 15),
                         "forecast": 100.0, "actual": 100.0 * ratio})


def test_interval_uses_only_windows_finished_by_the_cutoff():
    cutoff = pd.Timestamp("2025-06-25")
    past = pd.concat([backtest_rows("2025-06-04", 1.1), backtest_rows("2025-06-11", 0.9),
                      backtest_rows("2025-06-18", 50.0)])     # ends 07-02: not finished at the cutoff
    lo, hi, n_windows = models.empirical_interval(past, backtest_rows(cutoff, 1.0), cutoff)
    assert n_windows == 2
    assert lo.min() > 85 and hi.max() < 115                   # the 50x window was ignored
    with pytest.raises(ValueError):
        models.empirical_interval(past, backtest_rows("2025-06-10", 1.0), pd.Timestamp("2025-06-10"))


def test_error_sources_categories():
    rows = pd.DataFrame({
        "model": "m", "horizon": [1, 1, 8],
        "day": pd.to_datetime(["2025-12-25", "2025-12-22", "2025-12-29"]),
        "actual": [100.0, 100.0, 100.0], "forecast": [150.0, 120.0, 110.0],
    })
    # 12-25 is an event; 12-22 copies 12-15 (lag_7); 12-29 (8 days ahead) copies 12-15 via its first lag
    out = error_sources(rows, ["2025-12-25", "2025-12-15"])["m"]
    assert out["event day"]["n"] == 1 and out["copied event"]["n"] == 2 and out["normal"]["n"] == 0
    assert out["event day"]["share_of_error_pct"] == pytest.approx(50 / 80 * 100)
