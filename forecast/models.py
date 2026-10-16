"""Forecasting models for Phase 3: Holt-Winters, gradient boosting, and prediction intervals.

Every model is a forecaster(history, future) -> one prediction per future day,
the same interface as the baselines, so backtest.run_backtest scores them all
identically. `history` already ends at the cutoff (the engine slices it), so
nothing here can see the days being forecast.

Holt-Winters (statsmodels): exponential smoothing with a level, an optional
damped trend and a 7-day seasonal pattern. Chosen over SARIMA because it has
fewer choices to make (no (p,d,q)(P,D,Q) order search on ~200-300 days), its
three smoothing weights are easy to explain, and it re-weights recent data
automatically when the level shifts (e.g. the heating season starting).

Gradient boosting (scikit-learn HistGradientBoostingRegressor), DIRECT
multi-step strategy: one model, trained on (origin day t, horizon h) pairs, that
predicts day t+h from features known at t. Recursive forecasting (predict
tomorrow, feed it back as a lag, repeat) was rejected: its errors compound over
14 steps, and a one-off spike or dip gets copied forward. With the direct
strategy no prediction is ever used as an input.

Features for target day d = t + h, all known at the origin t:
- calendar of d: day of week, month, US federal holiday (known years ahead)
- horizon h
- lag_7 / lag_14: the most recent and second most recent same weekday observed by
  t. For h <= 7 that's exactly d-7 and d-14. For h = 8..14, d-7 hasn't happened
  yet at t, so they are d-14 and d-21 instead (the latest values that exist).
- roll_7: mean of the 7 days ending at t.
"""
import math
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from statsmodels.tools.sm_exceptions import ConvergenceWarning
from statsmodels.tsa.holtwinters import ExponentialSmoothing

import fc_common as fc

SEED = fc.SEED
HORIZON = 14
CONVERGENCE_WARNINGS = []  # (cutoff, message) for every Holt-Winters fit that warned


# --- Holt-Winters ------------------------------------------------------------

def holt_winters(trend=None, seasonal="add", window_weeks=None):
    """Forecaster factory. trend: None or "damped"; seasonal: "add" or "mul";
    window_weeks: fit on only the last N weeks (None = all history)."""
    def forecaster(history, future):
        y = history if window_weeks is None else history.iloc[-7 * window_weeks:]
        model = ExponentialSmoothing(
            y.astype(float).to_numpy(), trend="add" if trend == "damped" else None,
            damped_trend=trend == "damped", seasonal=seasonal, seasonal_periods=7,
            initialization_method="estimated")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            fit = model.fit()
        for w in caught:
            if issubclass(w.category, ConvergenceWarning):
                CONVERGENCE_WARNINGS.append((history.index[-1].date(), str(w.message)))
        return fit.forecast(len(future))
    return forecaster


# --- Gradient boosting ---------------------------------------------------------

FEATURES = ["horizon", "day_of_week", "month", "is_holiday", "lag_7", "lag_14", "roll_7"]


def calendar(days):
    days = pd.DatetimeIndex(days)
    return pd.DataFrame({
        "day_of_week": days.dayofweek,
        "month": days.month,
        "is_holiday": [int(d in fc.US_HOLIDAYS) for d in days.date],
    }, index=days)


def direct_features(y, origins, horizon=HORIZON):
    """Feature rows for every (origin, h) pair; uses only values of y up to each origin.

    Returns one row per (origin, h) with the target date `day` and the target
    value `y` (NaN when the target is after the end of y, e.g. the future).
    """
    pos = {d: i for i, d in enumerate(y.index)}
    values = y.to_numpy(dtype=float)
    rows = []
    for t in origins:
        i = pos[t]
        roll = values[i - 6:i + 1].mean() if i >= 6 else np.nan
        for h in range(1, horizon + 1):
            back = 7 * math.ceil(h / 7)          # days from target back to latest observed same weekday
            j1, j2 = i + h - back, i + h - back - 7
            rows.append((t, t + pd.DateOffset(days=h), h,
                         values[j1] if j1 >= 0 else np.nan, values[j2] if j2 >= 0 else np.nan, roll,
                         values[i + h] if i + h < len(values) else np.nan))
    df = pd.DataFrame(rows, columns=["origin", "day", "horizon", "lag_7", "lag_14", "roll_7", "y"])
    cal = calendar(df["day"])
    for col in cal.columns:
        df[col] = cal[col].to_numpy()
    return df


def gradient_boosting(target="relative", max_depth=3, learning_rate=0.05, max_iter=300, min_samples_leaf=20):
    """Forecaster factory.

    target="relative" predicts y / roll_7 (and divides the lags by roll_7 too),
    then multiplies back. Trees can't predict values outside the range they were
    trained on, so modelling ratios lets the forecast follow a level the model has
    never seen (e.g. the first cold weeks of the heating season).
    """
    def prepare(df):
        X = df[FEATURES].copy()
        if target == "relative":
            X["lag_7"] = X["lag_7"] / X["roll_7"]
            X["lag_14"] = X["lag_14"] / X["roll_7"]
        return X

    def forecaster(history, future):
        cutoff = history.index[-1]
        # Training rows: every origin whose target day is on or before the cutoff.
        train = direct_features(history, history.index).dropna()
        assert train["day"].max() <= cutoff, "training target after the cutoff"
        y = train["y"] / train["roll_7"] if target == "relative" else train["y"]
        model = HistGradientBoostingRegressor(
            max_depth=max_depth, learning_rate=learning_rate, max_iter=max_iter,
            min_samples_leaf=min_samples_leaf, random_state=SEED,
            # Day of week is a category. Month stays a number: test windows (Oct-Dec) include
            # months the model has never seen, and a number at least maps them to the nearest one.
            categorical_features=[FEATURES.index("day_of_week")])
        model.fit(prepare(train), y)
        # Prediction rows: origin = cutoff, h = 1..len(future)
        test = direct_features(history, [cutoff], horizon=len(future))
        pred = model.predict(prepare(test))
        if target == "relative":
            pred = pred * test["roll_7"].to_numpy()
        assert (test["day"].to_numpy() == future.to_numpy()).all()
        return pred
    return forecaster


# --- Prediction intervals ------------------------------------------------------

def empirical_interval(past, forecast_rows, cutoff, coverage=0.8):
    """Interval from the model's own past errors, using only windows finished by `cutoff`.

    past: backtest rows (cutoff, horizon, forecast, actual) from earlier cutoffs.
    Only windows whose last forecast day is on or before `cutoff` count: their
    outcome was known when this forecast was made. The interval is the forecast
    times the 10th and 90th percentiles of actual/forecast, computed separately
    for days 1-7 and 8-14 ahead (further ahead = less certain).
    """
    done = past[past["cutoff"] + np.timedelta64(HORIZON, "D") <= cutoff]  # explicit unit: BUILD_LOG 3.2
    if done.empty:
        raise ValueError(f"no finished backtest windows before {cutoff}")
    log_ratio = np.log(done["actual"] / done["forecast"])
    bucket = np.where(done["horizon"] <= 7, 0, 1)
    lo_q, hi_q = (1 - coverage) / 2, 1 - (1 - coverage) / 2
    q = {b: (log_ratio[bucket == b].quantile(lo_q), log_ratio[bucket == b].quantile(hi_q)) for b in (0, 1)}
    b = np.where(forecast_rows["horizon"] <= 7, 0, 1)
    lo = forecast_rows["forecast"].to_numpy() * np.exp([q[x][0] for x in b])
    hi = forecast_rows["forecast"].to_numpy() * np.exp([q[x][1] for x in b])
    return lo, hi, len(done["cutoff"].unique())
