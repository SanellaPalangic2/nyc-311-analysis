"""Rolling-origin backtest engine and error metrics, shared by every forecasting phase.

Rolling-origin backtesting: pick a cutoff date, give the model only the data up
to and including that day, forecast the next HORIZON days, score against what
actually happened, move the cutoff forward a week and repeat. It mimics running
the forecast every week in production.

No leakage by construction: run_backtest() slices the series to
history = series[:cutoff] BEFORE calling the model, so a model never receives a
single value after the cutoff, and anything it computes (averages, lags,
fitted parameters) is from the past only.

Two sets of cutoffs, so Phase 3 can't tune on the test set:
- VALIDATION_CUTOFFS: 9 weekly cutoffs whose 14-day windows end before the
  test period starts. Use these to choose settings and compare model variants.
- TEST_CUTOFFS: 9 weekly cutoffs whose windows cover the last 10 weeks of 2025
  (Oct 23 - Dec 31). Used only to report final results.
"""
import numpy as np
import pandas as pd

HORIZON = 14  # days ahead
STEP = 7      # days between cutoffs
N_CUTOFFS = 9
LAST_DAY = pd.Timestamp("2025-12-31")


def weekly_cutoffs(last_cutoff, n=N_CUTOFFS, step=STEP):
    """n cutoffs, step days apart, ending at last_cutoff (oldest first)."""
    return [pd.Timestamp(last_cutoff) - pd.DateOffset(days=step * k) for k in reversed(range(n))]


# Last test cutoff: its 14-day window ends on the last day of data.
TEST_CUTOFFS = weekly_cutoffs(LAST_DAY - pd.DateOffset(days=HORIZON))
# Last validation cutoff: its window ends on the first test cutoff, the day before the first
# test window starts, so no day is forecast in both sets.
VALIDATION_CUTOFFS = weekly_cutoffs(TEST_CUTOFFS[0] - pd.DateOffset(days=HORIZON))


def run_backtest(series, forecaster, cutoffs, horizon=HORIZON, **context):
    """Forecast `horizon` days after each cutoff, seeing only data up to the cutoff.

    forecaster(history, future_days, **context) must return one prediction per
    future day. `history` is the series up to and including the cutoff; extra
    context (e.g. a holiday calendar) must be information known in advance.
    Returns one row per (cutoff, day) with the forecast and the actual value.
    """
    rows = []
    for cutoff in cutoffs:
        history = series.loc[:cutoff]
        assert history.index.max() == cutoff, "the series must contain the cutoff day"
        future = pd.date_range(cutoff + pd.DateOffset(days=1), periods=horizon, freq="D")
        pred = pd.Series(np.asarray(forecaster(history, future, **context), dtype=float), index=future)
        assert len(pred) == horizon and pred.notna().all(), "forecaster must return one value per day"
        rows.append(pd.DataFrame({
            "cutoff": cutoff, "day": future, "horizon": np.arange(1, horizon + 1),
            "forecast": pred.to_numpy(), "actual": series.reindex(future).to_numpy(),
        }))
    return pd.concat(rows, ignore_index=True)


# --- Metrics -----------------------------------------------------------------

def mae(actual, forecast):
    """Mean absolute error: average miss, in requests per day."""
    return float(np.mean(np.abs(np.asarray(actual) - np.asarray(forecast))))


def rmse(actual, forecast):
    """Root mean squared error: like MAE, but big misses count extra."""
    return float(np.sqrt(np.mean((np.asarray(actual) - np.asarray(forecast)) ** 2)))


def mape(actual, forecast):
    """Mean absolute percentage error: average miss as a % of the actual value."""
    actual = np.asarray(actual, dtype=float)
    if (actual == 0).any():
        raise ValueError("MAPE is undefined when an actual value is 0")
    return float(100 * np.mean(np.abs(actual - np.asarray(forecast)) / actual))


def score(results):
    """MAE, RMSE and MAPE over every (cutoff, day) row of a backtest."""
    a, f = results["actual"], results["forecast"]
    return {"mae": mae(a, f), "rmse": rmse(a, f), "mape": mape(a, f)}


def improvement_pct(model_error, reference_error):
    """% by which a model's error is lower than the reference's (positive = better)."""
    return 100 * (1 - model_error / reference_error)
