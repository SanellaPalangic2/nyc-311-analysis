"""Tests for the backtest engine (forecast/backtest.py) and baselines (forecast/baselines.py)."""
import numpy as np
import pandas as pd
import pytest

import backtest as bt
from baselines import metrics_table, naive, seasonal_naive, weekday_avg_4wk

DAYS = pd.date_range("2025-01-01", "2025-12-31", freq="D")
# value = day number, so every forecast reveals exactly which past day it copied
COUNTER = pd.Series(np.arange(len(DAYS), dtype=float), index=DAYS)


def test_model_only_sees_data_up_to_the_cutoff():
    seen = []

    def spy(history, future):
        seen.append((history.index.max(), future[0], len(future)))
        return np.zeros(len(future))

    cutoffs = [pd.Timestamp("2025-06-04"), pd.Timestamp("2025-06-11")]
    bt.run_backtest(COUNTER, spy, cutoffs)
    for (last_seen, first_future, n), cutoff in zip(seen, cutoffs):
        assert last_seen == cutoff                              # nothing after the cutoff
        assert first_future == cutoff + pd.DateOffset(days=1)    # forecasts start the next day
        assert n == bt.HORIZON


def test_backtest_rows_pair_each_forecast_with_its_actual():
    res = bt.run_backtest(COUNTER, naive, [pd.Timestamp("2025-06-04")])
    assert res["horizon"].tolist() == list(range(1, 15))
    assert res["actual"].tolist() == COUNTER["2025-06-05":"2025-06-18"].tolist()


def test_validation_and_test_windows_never_forecast_the_same_day():
    assert len(bt.VALIDATION_CUTOFFS) == len(bt.TEST_CUTOFFS) == 9
    last_test_day = bt.TEST_CUTOFFS[-1] + pd.DateOffset(days=bt.HORIZON)
    last_val_day = bt.VALIDATION_CUTOFFS[-1] + pd.DateOffset(days=bt.HORIZON)
    assert last_test_day == pd.Timestamp("2025-12-31")
    assert last_val_day <= bt.TEST_CUTOFFS[0]  # validation targets end before test targets start
    assert all((b - a).days == 7 for a, b in zip(bt.TEST_CUTOFFS, bt.TEST_CUTOFFS[1:]))


def future_after(cutoff):
    return pd.date_range(pd.Timestamp(cutoff) + pd.DateOffset(days=1), periods=14, freq="D")


def test_naive_repeats_the_last_value():
    history = COUNTER[:"2025-06-04"]
    assert (naive(history, future_after("2025-06-04")) == history.iloc[-1]).all()


def test_seasonal_naive_copies_the_latest_observed_same_weekday():
    history = COUNTER[:"2025-06-04"]
    future = future_after("2025-06-04")
    pred = seasonal_naive(history, future)
    # days 1-7 ahead copy 7 days earlier; days 8-14 copy 14 days earlier (last week is all we have)
    expected = [COUNTER[d - pd.DateOffset(days=7 if h <= 7 else 14)] for h, d in enumerate(future, 1)]
    assert pred.tolist() == expected
    assert all(COUNTER.index[int(p)].dayofweek == d.dayofweek for p, d in zip(pred, future))


def test_four_week_average_uses_the_last_four_observed_same_weekdays():
    history = COUNTER[:"2025-06-04"]
    future = future_after("2025-06-04")
    pred = weekday_avg_4wk(history, future)
    # 1 day ahead: lags 7, 14, 21, 28 -> mean lag 17.5; 8 days ahead: lags 14..35 -> mean 24.5
    assert pred[0] == pytest.approx(COUNTER[future[0]] - 17.5)
    assert pred[7] == pytest.approx(COUNTER[future[7]] - 24.5)


def test_metrics():
    actual, forecast = np.array([100.0, 200.0]), np.array([110.0, 170.0])
    assert bt.mae(actual, forecast) == pytest.approx(20)
    assert bt.rmse(actual, forecast) == pytest.approx(np.sqrt((100 + 900) / 2))
    assert bt.mape(actual, forecast) == pytest.approx((10 + 15) / 2)
    assert bt.improvement_pct(80, 100) == pytest.approx(20)
    with pytest.raises(ValueError):
        bt.mape([0, 1], [1, 1])


def test_metrics_table_scores_against_seasonal_naive():
    rows = []
    for model, err in [("seasonal_naive", 10.0), ("better", 5.0)]:
        rows.append(pd.DataFrame({"variant": "v", "split": "test", "series": "Total", "model": model,
                                  "cutoff": pd.Timestamp("2025-06-04"), "actual": [100.0, 100.0],
                                  "forecast": [100.0 + err, 100.0 - err]}))
    table = metrics_table(pd.concat(rows)).set_index("model")
    assert table.loc["seasonal_naive", "mae_improvement_pct"] == pytest.approx(0)
    assert table.loc["better", "mae_improvement_pct"] == pytest.approx(50)
