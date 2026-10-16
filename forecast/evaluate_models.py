"""Forecast Phase 3: tune Holt-Winters and gradient boosting, compare every model, add intervals.

Run:  python forecast/evaluate_models.py      (about 5 minutes; needs daily_series.py first)

1. Tuning: a small grid of settings per model, scored ONLY on the validation
   cutoffs (Aug 13 - Oct 8). The best setting per model is fixed before any
   test window is looked at.
2. Comparison: baselines + tuned models on the same validation and test
   backtests, for the total series as recorded and excluding hotspots.
3. Best model: lowest validation MAE (again, chosen without the test set).
4. 80% prediction intervals for the best model from its own past errors
   (models.empirical_interval): for each cutoff, only backtest windows that had
   finished by that cutoff are used. Coverage = share of actual values that land
   inside the interval; for a well-calibrated 80% interval it should be near 80%.

Outputs: data/processed/backtest_models.csv, results/forecast/figures/f3*.png,
         results/forecast/data/phase3_models.json, results/forecast/forecast.md
"""
import itertools
import time

import matplotlib.dates
import numpy as np
import pandas as pd

import backtest as bt
import baselines
import fc_common as fc
import models
import report
from common import INK, INK_2, MUTED, PALETTE, plt, subtitle

SERIES = fc.TOTAL
HW_GRID = [dict(trend=t, seasonal=s, window_weeks=w)
           for t, s, w in itertools.product([None, "damped"], ["add", "mul"], [None, 12])]
GBM_GRID = [dict(target=t, max_depth=d, learning_rate=lr)
            for t, d, lr in itertools.product(["absolute", "relative"], [2, 4], [0.03, 0.1])]
# Interval calibration: every Wednesday from early March (enough history for both
# models) to the last test cutoff. Validation and test cutoffs are among them.
CALIBRATION_CUTOFFS = list(pd.date_range("2025-03-05", bt.TEST_CUTOFFS[-1], freq="7D"))
COVERAGE = 0.8


def describe(name, params):
    if name == "holt_winters":
        trend = "damped trend" if params["trend"] == "damped" else "no trend"
        window = "all history" if params["window_weeks"] is None else f"last {params['window_weeks']} weeks"
        return f"{params['seasonal']} weekly season, {trend}, {window}"
    return f"{params['target']} target, depth {params['max_depth']}, learning rate {params['learning_rate']}"


def tune(name, factory, grid, y):
    """Score each setting on the validation cutoffs; return all scores and the best setting."""
    rows = []
    for params in grid:
        start = time.time()
        res = bt.run_backtest(y, factory(**params), bt.VALIDATION_CUTOFFS)
        rows.append({"model": name, "params": params, "setting": describe(name, params), **bt.score(res),
                     "seconds": round(time.time() - start, 1)})
        print(f"  {name} {describe(name, params)}: MAE {rows[-1]['mae']:.0f}")
    best = min(rows, key=lambda r: r["mae"])
    return rows, best["params"]


def interval_rows(results, model, split_cutoffs):
    """Attach an 80% interval to every forecast at the given cutoffs (past errors only)."""
    past = results[results["model"] == model]
    out = []
    for cutoff in split_cutoffs:
        rows = past[past["cutoff"] == cutoff].copy()
        lo, hi, n_windows = models.empirical_interval(past, rows, cutoff, COVERAGE)
        out.append(rows.assign(lo=lo, hi=hi, calibration_windows=n_windows))
    return pd.concat(out, ignore_index=True)


def coverage_stats(rows):
    inside = (rows["actual"] >= rows["lo"]) & (rows["actual"] <= rows["hi"])
    return {
        "coverage_pct": 100 * inside.mean(),
        "below_pct": 100 * (rows["actual"] < rows["lo"]).mean(),
        "above_pct": 100 * (rows["actual"] > rows["hi"]).mean(),
        "mean_width": float((rows["hi"] - rows["lo"]).mean()),
        "mean_width_pct_of_forecast": float(100 * ((rows["hi"] - rows["lo"]) / rows["forecast"]).mean()),
        "n": len(rows),
        "min_calibration_windows": int(rows["calibration_windows"].min()),
    }


def error_sources(rows, outlier_days):
    """Split each model's absolute error by where it comes from (test rows, one series).

    - event day: the forecast day itself was a Phase 1 outlier day (no model could know)
    - copied event: one of the two latest same weekdays observed at the cutoff (the
      lag_7 / lag_14 days) was an outlier day, so the event can leak into the forecast
    - normal: neither
    The outlier list is used only to diagnose errors after the fact, never as a model input.
    """
    out_days = pd.DatetimeIndex(pd.to_datetime(outlier_days))
    back = 7 * np.ceil(rows["horizon"] / 7).astype(int)
    lag1 = rows["day"] - pd.to_timedelta(back, unit="D")
    lag2 = lag1 - np.timedelta64(7, "D")
    category = np.where(rows["day"].isin(out_days), "event day",
                        np.where(lag1.isin(out_days) | lag2.isin(out_days), "copied event", "normal"))
    abs_err = (rows["actual"] - rows["forecast"]).abs()
    result = {}
    for model, g in rows.assign(category=category, abs_err=abs_err).groupby("model", sort=False):
        total = g["abs_err"].sum()
        result[model] = {c: {"n": int((g["category"] == c).sum()),
                             "mae": float(g.loc[g["category"] == c, "abs_err"].mean()),
                             "share_of_error_pct": float(100 * g.loc[g["category"] == c, "abs_err"].sum() / total)}
                         for c in ["normal", "copied event", "event day"]}
    return result


# --- Figures -----------------------------------------------------------------

def plot_intervals(rows, label):
    """Two panels: forecasts 1-7 and 8-14 days ahead, each tiled across the test period."""
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, sharey=True)
    for ax, (lo_h, hi_h) in zip(axes, [(1, 7), (8, 14)]):
        r = rows[rows["horizon"].between(lo_h, hi_h)].sort_values("day")
        ax.fill_between(r["day"], r["lo"], r["hi"], color=PALETTE[0], alpha=0.18, linewidth=0,
                        label=f"{COVERAGE:.0%} prediction interval")
        ax.plot(r["day"], r["forecast"], color=PALETTE[0], linewidth=2, label=f"Forecast ({label})")
        ax.plot(r["day"], r["actual"], color=INK, linewidth=1.4, label="Actual")
        out = r[(r["actual"] < r["lo"]) | (r["actual"] > r["hi"])]
        ax.scatter(out["day"], out["actual"], s=30, color=PALETTE[1], zorder=3, edgecolor="white",
                   linewidth=1.2, label="Actual outside the interval")
        inside = 100 * (1 - len(out) / len(r))
        ax.text(0.005, 0.96, f"{lo_h}-{hi_h} days ahead: {inside:.0f}% of actuals inside the interval",
                transform=ax.transAxes, fontsize=10, fontweight="bold", va="top", color=INK)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        for c in bt.TEST_CUTOFFS:
            ax.axvline(c + pd.DateOffset(days=lo_h), color=MUTED, linewidth=0.5, linestyle=":")
    axes[0].set_title(f"Test period: {label} forecasts with {COVERAGE:.0%} intervals, {SERIES}", pad=24)
    subtitle(axes[0], "Each 7-day stretch comes from a different weekly cutoff (dotted lines). "
                      "Intervals use only errors from windows that had finished before each cutoff.")
    axes[-1].xaxis.set_major_locator(matplotlib.dates.WeekdayLocator(byweekday=0, interval=2))
    axes[-1].xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
    axes[-1].legend(loc="upper center", ncol=4, bbox_to_anchor=(0.5, -0.12))
    fig.tight_layout()
    fc.save_figure(fig, "f3a_best_model_intervals.png")


def plot_comparison(table, labels, order):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for ax, split in zip(axes, ["validation", "test"]):
        t = table[(table["split"] == split) & (table["variant"] == "as_recorded")].set_index("model").loc[order]
        y = np.arange(len(order))[::-1]
        ax.barh(y, t["mae"], color=[PALETTE[0] if m in ("holt_winters", "gradient_boosting") else MUTED
                                    for m in order], height=0.6)
        for yi, v in zip(y, t["mae"]):
            ax.text(v + t["mae"].max() * 0.01, yi, f"{v:,.0f}", va="center", fontsize=9, color=INK_2)
        ax.set_yticks(y, [labels[m] for m in order])
        ax.set_title(f"{split.capitalize()} windows", fontsize=11)
        ax.set_xlabel("MAE (requests per day)")
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.set_xlim(0, t["mae"].max() * 1.18)
        ax.grid(axis="y", visible=False)
    fig.suptitle(f"Average miss per day, {SERIES} (lower is better). Grey = baselines, blue = Phase 3 models",
                 x=0.01, ha="left", fontweight="bold", fontsize=12)
    fig.tight_layout()
    fc.save_figure(fig, "f3b_model_comparison.png")


# --- Main --------------------------------------------------------------------

def main():
    daily = {v: fc.load_daily(exclude_hotspots=ex) for v, ex in baselines.VARIANTS.items()}
    y = daily["as_recorded"][SERIES]

    print("Tuning on validation cutoffs only:")
    hw_scores, hw_best = tune("holt_winters", models.holt_winters, HW_GRID, y)
    gbm_scores, gbm_best = tune("gradient_boosting", models.gradient_boosting, GBM_GRID, y)

    candidates = {**baselines.BASELINES,
                  "holt_winters": models.holt_winters(**hw_best),
                  "gradient_boosting": models.gradient_boosting(**gbm_best)}
    labels = {**baselines.LABELS, "holt_winters": "Holt-Winters (tuned)",
              "gradient_boosting": "Gradient boosting (tuned)"}

    # Same backtest for every model: both variants, every calibration cutoff (which
    # includes all validation and test cutoffs).
    frames = []
    for variant, d in daily.items():
        for name, f in candidates.items():
            res = bt.run_backtest(d[SERIES], f, CALIBRATION_CUTOFFS)
            frames.append(res.assign(variant=variant, series=SERIES, model=name))
    allres = pd.concat(frames, ignore_index=True)
    allres.to_csv(fc.DAILY_FILE.parent / "backtest_models.csv", index=False)

    split_of = {**{c: "validation" for c in bt.VALIDATION_CUTOFFS}, **{c: "test" for c in bt.TEST_CUTOFFS}}
    scored = allres[allres["cutoff"].isin(split_of)].assign(split=lambda df: df["cutoff"].map(split_of))
    table = baselines.metrics_table(scored)

    val = table[(table["split"] == "validation") & (table["variant"] == "as_recorded")]
    best_model = val.loc[val["mae"].idxmin(), "model"]
    print(f"Best on validation: {best_model}")

    # Intervals for the best model, both splits, both variants
    intervals, coverage = {}, {}
    for variant in daily:
        r = allres[allres["variant"] == variant]
        for split, cutoffs in baselines.SPLITS.items():
            rows = interval_rows(r, best_model, cutoffs)
            intervals[(variant, split)] = rows
            coverage[f"{variant}/{split}"] = coverage_stats(rows)
            for b, (lo_h, hi_h) in {"1-7": (1, 7), "8-14": (8, 14)}.items():
                coverage[f"{variant}/{split}"][f"coverage_pct_h{b}"] = 100 * float(
                    rows[rows["horizon"].between(lo_h, hi_h)]
                    .pipe(lambda x: ((x["actual"] >= x["lo"]) & (x["actual"] <= x["hi"])).mean()))

    test_rows = intervals[("as_recorded", "test")]
    misses = test_rows[(test_rows["actual"] < test_rows["lo"]) | (test_rows["actual"] > test_rows["hi"])]
    miss_days = misses.groupby("day").agg(n=("cutoff", "size"), actual=("actual", "first"),
                                          forecast=("forecast", "mean"), lo=("lo", "mean"), hi=("hi", "mean"))
    per_window = {str(c.date()): {m: bt.mae(g2["actual"], g2["forecast"]) for m, g2 in g.groupby("model", sort=False)}
                  for c, g in scored[(scored["variant"] == "as_recorded") & (scored["split"] == "test")].groupby("cutoff")}

    order = list(candidates)
    phase1 = fc.load_json("phase1_data.json")
    test_all = scored[(scored["split"] == "test") & (scored["variant"] == "as_recorded")]
    sources = error_sources(test_all, [o["day"] for o in phase1["outliers_total"]])
    fc.save_json("phase3_models.json", {
        "series": SERIES,
        "labels": labels,
        "order": order,
        "tuning": {"holt_winters": hw_scores, "gradient_boosting": gbm_scores},
        "chosen_params": {"holt_winters": hw_best, "gradient_boosting": gbm_best},
        "chosen_settings": {"holt_winters": describe("holt_winters", hw_best),
                            "gradient_boosting": describe("gradient_boosting", gbm_best)},
        "metrics": table.round(2).to_dict(orient="records"),
        "best_model": best_model,
        "coverage_target_pct": 100 * COVERAGE,
        "interval_coverage": coverage,
        "interval_misses_test": [{"day": d.date(), **{k: round(float(v)) for k, v in row.items()}}
                                 for d, row in miss_days.iterrows()],
        "per_window_mae_test": per_window,
        "error_sources_test": sources,
        "calibration_cutoffs": [c.date() for c in CALIBRATION_CUTOFFS],
        "holt_winters_convergence_warnings": len(models.CONVERGENCE_WARNINGS),
    })
    # Small file for the dashboard (Phase 5): best model's test forecasts with intervals.
    test_rows.assign(model=best_model).to_csv(fc.DAILY_FILE.parent / "best_model_intervals.csv", index=False)

    plot_intervals(test_rows, labels[best_model])
    plot_comparison(table, labels, order)
    report.write()


if __name__ == "__main__":
    main()
